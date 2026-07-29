"""Оценка разрушительности shell-команд: детерминированный анализ без обращений к ФС.

Модуль разбирает команду на сегменты (по &&, ||, ;, |, переводу строки),
токенизирует каждый сегмент с учётом кавычек, снимает обёртки (sudo, env, …)
и рекурсивно заходит внутрь sh -c "…".  Для каждой цели разрушающей команды
и каждого редиректа > / >| применяется таблица правил; итог — максимум.
"""

from __future__ import annotations

import enum
import os
import posixpath
import re
from dataclasses import dataclass

# ---------- константы ----------

_MAX_DEPTH = 3

_SYSTEM_ROOTS: frozenset[str] = frozenset(
    {
        "/",
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/home",
        "/lib",
        "/opt",
        "/proc",
        "/root",
        "/sbin",
        "/sys",
        "/usr",
        "/var",
        "/Applications",
        "/System",
        "/Library",
        "/Users",
    }
)

_PROTECTED_RECURSIVE: tuple[str, ...] = (
    "/etc",
    "/usr",
    "/var/lib",
    "/System",
    "/Library",
    "/boot",
    "/dev",
    "/proc",
    "/sys",
)

_SENSITIVE_DOTDIRS: tuple[str, ...] = (
    ".ssh",
    ".gnupg",
    ".aws",
    ".kube",
    ".docker",
)

_PROTECTED_DOTDIRS_EXACT: tuple[str, ...] = (
    ".config",
    ".claude",
    ".codex",
    ".qwen",
    ".gemini",
    ".local",
    ".local/share",
    "Documents",
    "Desktop",
)

_TEMP_DIRS: tuple[str, ...] = ("/tmp", "/var/tmp", "/private/tmp")

_SAFE_DEVICES: frozenset[str] = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr"})

_DESTRUCTIVE_VERBS: frozenset[str] = frozenset(
    {
        "rm",
        "rmdir",
        "shred",
        "unlink",
        "truncate",
        "dd",
        "mkfs",
        "fdisk",
        "parted",
        "wipefs",
        "srm",
    }
)

_WRAPPERS: frozenset[str] = frozenset(
    {
        "sudo",
        "doas",
        "env",
        "nice",
        "nohup",
        "time",
        "timeout",
        "xargs",
        "command",
        "builtin",
        "stdbuf",
        "setsid",
        "ionice",
    }
)

_SHELLS: frozenset[str] = frozenset({"sh", "bash", "zsh"})

# Флаги обёрток, принимающие аргумент (следующий токен пропускается).
_WRAPPER_FLAG_ARGS: dict[str, frozenset[str]] = {
    "sudo": frozenset({"-u", "-g", "-C", "-U"}),
    "doas": frozenset({"-u"}),
    "env": frozenset({"-u", "-C"}),
    "nice": frozenset({"-n"}),
    "stdbuf": frozenset({"-i", "-o", "-e"}),
    "ionice": frozenset({"-n", "-c", "-p"}),
    "timeout": frozenset({"-k", "-s"}),
    "xargs": frozenset({"-I", "-n", "-P", "-d", "-E", "-L"}),
}

# Флаги разрушающих глаголов, принимающие аргумент.
_VERB_FLAG_ARGS: dict[str, frozenset[str]] = {
    "truncate": frozenset({"-s", "--size"}),
}

_RULE_DESCRIPTIONS: dict[str, str] = {
    "protected_root": "системный корень",
    "protected_recursive": "защищённый системный каталог",
    "home_root": "домашний каталог",
    "sensitive_dotdir": "каталог с чувствительными данными",
    "protected_dotdir_exact": "защищённый каталог конфигурации",
    "dev_write": "запись в устройство",
    "protected_glob": "глоб в защищённом каталоге",
    "glob_target": "глоб в цели",
    "unresolved_substitution": "неразрешимая подстановка",
    "outside_cwd": "цель вне рабочего каталога",
    "recursive_cwd": "рекурсивное удаление в рабочем каталоге",
    "pipe_stdin": "данные из pipe в разрушающую команду",
    "no_target": "разрушающая команда без цели",
    "empty_wrapper": "обёртка без команды",
}

# ---------- публичный контракт ----------


class RiskLevel(enum.Enum):
    """Уровень опасности; порядок значений — по возрастанию угрозы."""

    SAFE = 0
    LOW = 1
    CONFIRM = 2
    CATASTROPHIC = 3


@dataclass(frozen=True)
class RiskFinding:
    """Одна находка: какое правило сработало и на какой цели."""

    rule: str
    target: str
    level: RiskLevel


@dataclass(frozen=True)
class RiskAssessment:
    """Итог оценки: максимальный уровень и все находки."""

    level: RiskLevel
    findings: tuple[RiskFinding, ...]

    def explanation(self) -> str:
        """Человекочитаемое объяснение, одна строка на finding."""
        if not self.findings:
            return "Команда безопасна"
        lines: list[str] = []
        for f in self.findings:
            desc = _RULE_DESCRIPTIONS.get(f.rule, f.rule)
            target_part = f": {f.target}" if f.target else ""
            lines.append(f"[{f.level.name}] {desc}{target_part}")
        return "\n".join(lines)


def describe_rule(rule: str) -> str:
    """Человекочитаемое название правила.

    Единственный источник описаний: потребители не заводят свои словари, иначе
    они молча разъезжаются с набором правил и показывают сырые идентификаторы.
    """
    return _RULE_DESCRIPTIONS.get(rule, rule)


def assess(
    command: str,
    *,
    cwd: str | None = None,
    home: str | None = None,
) -> RiskAssessment:
    """Оценить разрушительность shell-команды.

    *cwd* и *home* передаются явно; при ``None`` берутся из окружения.
    Никаких обращений к реальной ФС: пути раскрываются лексически.
    """
    effective_cwd = posixpath.normpath(cwd if cwd is not None else os.getcwd())
    effective_home = posixpath.normpath(home if home is not None else os.path.expanduser("~"))
    findings = _assess_command(
        command,
        cwd=effective_cwd,
        home=effective_home,
        piped=False,
        depth=0,
    )
    if not findings:
        return RiskAssessment(level=RiskLevel.SAFE, findings=())
    max_level = max(findings, key=lambda f: f.level.value).level
    return RiskAssessment(level=max_level, findings=tuple(findings))


# ---------- разбор на сегменты и токенизация ----------


def _split_segments(command: str) -> list[tuple[str, bool]]:
    """Разбить команду на сегменты по &&, ||, ;, |, переводу строки.

    Возвращает список пар ``(сегмент, после_pipe)``.
    """
    segments: list[tuple[str, bool]] = []
    buf: list[str] = []
    after_pipe = False
    i = 0
    in_sq = False
    in_dq = False

    def _flush() -> None:
        nonlocal after_pipe
        seg = "".join(buf).strip()
        if seg:
            segments.append((seg, after_pipe))
        buf.clear()
        after_pipe = False

    while i < len(command):
        ch = command[i]

        if ch == "\\" and not in_sq:
            buf.append(ch)
            if i + 1 < len(command):
                buf.append(command[i + 1])
                i += 2
            else:
                i += 1
            continue

        if ch == "'" and not in_dq:
            in_sq = not in_sq
            buf.append(ch)
            i += 1
            continue

        if ch == '"' and not in_sq:
            in_dq = not in_dq
            buf.append(ch)
            i += 1
            continue

        if in_sq or in_dq:
            buf.append(ch)
            i += 1
            continue

        # Редиректы: поглощаем >, >>, >| целиком, чтобы | не стал pipe
        if ch == ">":
            buf.append(ch)
            i += 1
            while i < len(command) and command[i] in (">", "|"):
                buf.append(command[i])
                i += 1
            continue

        if ch == "&" and i + 1 < len(command) and command[i + 1] == "&":
            _flush()
            i += 2
            continue

        if ch == "|" and i + 1 < len(command) and command[i + 1] == "|":
            _flush()
            i += 2
            continue

        if ch == "|":
            seg = "".join(buf).strip()
            if seg:
                segments.append((seg, after_pipe))
            buf.clear()
            after_pipe = True
            i += 1
            continue

        if ch in (";", "\n"):
            _flush()
            i += 1
            continue

        buf.append(ch)
        i += 1

    _flush()
    return segments


def _tokenize(segment: str) -> list[str]:
    """Разбить сегмент на токены с учётом кавычек и экранирования."""
    tokens: list[str] = []
    cur: list[str] = []
    i = 0
    in_sq = False
    in_dq = False

    while i < len(segment):
        ch = segment[i]

        if ch == "\\" and not in_sq:
            if i + 1 < len(segment):
                cur.append(segment[i + 1])
                i += 2
            else:
                i += 1
            continue

        if ch == "'" and not in_dq:
            in_sq = not in_sq
            i += 1
            continue

        if ch == '"' and not in_sq:
            in_dq = not in_dq
            i += 1
            continue

        if ch in (" ", "\t") and not in_sq and not in_dq:
            if cur:
                tokens.append("".join(cur))
                cur = []
            i += 1
            continue

        cur.append(ch)
        i += 1

    if cur:
        tokens.append("".join(cur))
    return tokens


def _extract_redirects(
    tokens: list[str],
) -> tuple[list[str], list[str]]:
    """Извлечь цели редиректов.  ``>`` и ``>|`` считаются, ``>>`` — нет."""
    clean: list[str] = []
    targets: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        # >> или N>> — не цель
        m = re.match(r"^[0-9]*>>(.*)$", tok)
        if m:
            if not m.group(1) and i + 1 < len(tokens):
                i += 1
            i += 1
            continue
        # > или >| или N> или N>|
        m = re.match(r"^[0-9]*>\|?(.*)$", tok)
        if m:
            rest = m.group(1)
            if rest:
                targets.append(rest)
            elif i + 1 < len(tokens):
                targets.append(tokens[i + 1])
                i += 1
            i += 1
            continue
        clean.append(tok)
        i += 1
    return clean, targets


# ---------- анализ команды ----------


def _assess_command(
    command: str,
    *,
    cwd: str,
    home: str,
    piped: bool,
    depth: int,
) -> list[RiskFinding]:
    """Оценить команду (возможно, составную — с &&, ||, ;, |)."""
    segments = _split_segments(command)
    findings: list[RiskFinding] = []
    for idx, (seg, seg_piped) in enumerate(segments):
        effective = piped if idx == 0 else seg_piped
        findings.extend(_assess_simple(seg, cwd=cwd, home=home, piped=effective, depth=depth))
    return findings


def _assess_simple(
    segment: str,
    *,
    cwd: str,
    home: str,
    piped: bool,
    depth: int,
) -> list[RiskFinding]:
    """Оценить один простой сегмент (без операторов)."""
    tokens = _tokenize(segment)
    if not tokens:
        return []

    tokens, redirect_targets = _extract_redirects(tokens)
    if not tokens:
        return []

    # Снятие обёрток (sudo, env, …)
    stripped = _strip_wrappers(tokens)
    if not stripped:
        return [RiskFinding(rule="empty_wrapper", target="", level=RiskLevel.CONFIRM)]

    # Рекурсия в sh -c "…"
    if depth < _MAX_DEPTH:
        inner = _extract_shell_c(stripped)
        if inner is not None:
            return _assess_command(inner, cwd=cwd, home=home, piped=piped, depth=depth + 1)

    findings: list[RiskFinding] = []

    # Цели редиректов проверяются всегда (запись через > опасна сама по себе)
    for rt in redirect_targets:
        f = _check_target(rt, cwd=cwd, home=home, is_recursive=False)
        if f is not None:
            findings.append(f)

    is_destructive, targets, is_recursive = _destructive_info(stripped)
    if not is_destructive:
        return findings

    # Правило 13: pipe stdin. Для `tee` не применяется: чтение из конвейера —
    # его штатный режим, а не признак опасности. Иначе безобидная запись
    # `echo x | tee notes.txt` давала бы предупреждение, и их перестали бы читать.
    if piped and posixpath.basename(stripped[0]) != "tee":
        findings.append(RiskFinding(rule="pipe_stdin", target="", level=RiskLevel.CONFIRM))

    # Правило 14: нет определимой цели
    if not targets:
        findings.append(RiskFinding(rule="no_target", target="", level=RiskLevel.CONFIRM))
        return findings

    for t in targets:
        f = _check_target(t, cwd=cwd, home=home, is_recursive=is_recursive)
        if f is not None:
            findings.append(f)

    return findings


def _strip_wrappers(tokens: list[str]) -> list[str]:
    """Снять обёртки и вернуть токены внутренней команды."""
    result = list(tokens)

    while result:
        # Ведущие присваивания окружения (`FOO=bar rm -rf /`) — не команда, а
        # префикс к ней. Без их снятия разрушающий глагол вообще не находился.
        while result and "=" in result[0] and not result[0].startswith("="):
            result = result[1:]
        if not result:
            break
        verb = posixpath.basename(result[0])
        if verb not in _WRAPPERS:
            break
        result = result[1:]
        flag_args = _WRAPPER_FLAG_ARGS.get(verb, frozenset())
        is_timeout = verb == "timeout"
        skipped_positional = False
        skip_next = False
        cut = 0

        for j, tok in enumerate(result):
            if skip_next:
                skip_next = False
                cut = j + 1
                continue
            if tok.startswith("-"):
                if tok in flag_args:
                    skip_next = True
                cut = j + 1
                continue
            if "=" in tok and not tok.startswith("="):
                cut = j + 1
                continue
            if is_timeout and not skipped_positional:
                skipped_positional = True
                cut = j + 1
                continue
            break

        result = result[cut:]

    return result


def _extract_shell_c(tokens: list[str]) -> str | None:
    """Если команда — sh/bash/zsh -c "…", вернуть внутреннюю команду."""
    if not tokens:
        return None
    verb = posixpath.basename(tokens[0])
    if verb not in _SHELLS:
        return None
    for i in range(1, len(tokens)):
        if tokens[i] == "-c" and i + 1 < len(tokens):
            return tokens[i + 1]
    return None


def _destructive_info(
    tokens: list[str],
) -> tuple[bool, list[str], bool]:
    """Определить: (разрушающая_ли, цели, рекурсивность)."""
    if not tokens:
        return False, [], False

    verb = posixpath.basename(tokens[0])
    args = tokens[1:]

    # mkfs.ext4, mkfs.xfs, …
    if verb in _DESTRUCTIVE_VERBS or verb.startswith("mkfs"):
        if verb == "dd":
            targets = [a.split("=", 1)[1] for a in args if a.startswith("of=")]
            return True, targets, False
        targets = _positional_targets(verb, args)
        return True, targets, _has_recursive_flag(args)

    if verb == "find":
        if "-delete" in args or "-exec" in args or "-execdir" in args:
            paths: list[str] = []
            for a in args:
                if a.startswith("-"):
                    break
                paths.append(a)
            return True, paths, True
        return False, [], False

    if verb == "git" and args and args[0] == "clean":
        return True, [], True

    # Перезапись файла чужими руками: `echo x | tee /etc/passwd` уничтожает
    # содержимое не хуже rm, но tee не выглядит разрушающим глаголом.
    if verb == "tee":
        targets = [a for a in _positional_targets(verb, args) if a]
        return (bool(targets), targets, False)

    # Синхронизация с удалением: пустой источник вычищает каталог назначения.
    if verb == "rsync" and any(a == "--delete" or a.startswith("--delete-") for a in args):
        positional = _positional_targets(verb, args)
        # Цель — последний позиционный аргумент, источники не трогаем.
        return (bool(positional), positional[-1:], True)

    # Распаковка поверх каталога: `tar -xf a.tar -C /` перезаписывает системные файлы.
    if verb in ("tar", "unzip") and any(a in ("-C", "-d", "--directory") for a in args):
        targets = []
        for index, a in enumerate(args):
            if a in ("-C", "-d", "--directory") and index + 1 < len(args):
                targets.append(args[index + 1])
        return (bool(targets), targets, True)

    if verb in ("chmod", "chown"):
        if any(a in ("-R", "-r", "--recursive") for a in args):
            pos = _positional_targets(verb, args)
            # Первый позиционный — mode/owner, остальные — цели
            return True, pos[1:], True
        return False, [], False

    return False, [], False


def _positional_targets(verb: str, args: list[str]) -> list[str]:
    """Позиционные аргументы, пропуская флаги и их значения."""
    flag_args = _VERB_FLAG_ARGS.get(verb, frozenset())
    targets: list[str] = []
    skip_next = False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a.startswith("-"):
            if a in flag_args:
                skip_next = True
            continue
        targets.append(a)
    return targets


def _has_recursive_flag(args: list[str]) -> bool:
    """Есть ли среди аргументов флаг рекурсивности (-r, -R, --recursive)."""
    return any(
        a == "--recursive" or (a.startswith("-") and not a.startswith("--") and "r" in a.lower())
        for a in args
    )


# ---------- анализ пути ----------


def _expand_path(raw: str, home: str, cwd: str) -> str:
    """Лексически раскрыть ~, $HOME, ${HOME} и нормализовать .."""
    result = raw
    if result == "~":
        result = home
    elif result.startswith("~/"):
        result = home + result[1:]
    result = re.sub(r"\$\{HOME\}", home, result)
    result = re.sub(r"\$HOME(?![A-Za-z0-9_])", home, result)
    if not result.startswith("/"):
        result = cwd + "/" + result
    return posixpath.normpath(result)


def _is_inside(parent: str, child: str) -> bool:
    """``child`` строго внутри ``parent`` (лексически)."""
    return child.startswith(parent.rstrip("/") + "/")


def _is_temp_path(path: str) -> bool:
    """Путь внутри одного из временных каталогов."""
    return any(path == d or _is_inside(d, path) for d in _TEMP_DIRS)


def _glob_parent(expanded: str) -> str:
    """Часть пути до первого сегмента с глобом.

    Нужна, чтобы `rm -rf ~/.ssh/*` наследовал защиту каталога `~/.ssh`: сам глоб
    не делает цель менее опасной.
    """
    segments = expanded.split("/")
    kept: list[str] = []
    for segment in segments:
        if "*" in segment or "?" in segment:
            break
        kept.append(segment)
    parent = "/".join(kept)
    if not parent:
        return "/" if expanded.startswith("/") else ""
    return parent


def _glob_in_protected(expanded: str) -> bool:
    """Глоб ``*`` непосредственно в защищённом каталоге (пр. 7)."""
    if expanded == "/*":
        return True
    for d in _PROTECTED_RECURSIVE:
        if expanded == d + "/*":
            return True
    for d in _SYSTEM_ROOTS:
        if d != "/" and expanded == d + "/*":
            return True
    return False


def _check_target(
    raw_target: str,
    *,
    cwd: str,
    home: str,
    is_recursive: bool,
) -> RiskFinding | None:
    """Проверить одну цель по правилам 1–12.  Вернуть finding или None."""
    # Чужой домашний каталог (`~root`, `~deploy`) лексически не раскрывается: мы
    # не знаем его реального пути. Раньше такая цель приклеивалась к cwd и
    # выглядела своей — `rm -rf ~root/.ssh` оценивался как обычная уборка.
    if raw_target.startswith("~") and not raw_target.startswith("~/") and raw_target != "~":
        tail = raw_target.split("/", 1)[1] if "/" in raw_target else ""
        sensitive = any(tail == dot or tail.startswith(dot + "/") for dot in _SENSITIVE_DOTDIRS)
        return RiskFinding(
            rule="sensitive_dotdir" if sensitive else "outside_cwd",
            target=raw_target,
            level=RiskLevel.CATASTROPHIC if sensitive else RiskLevel.CONFIRM,
        )
    expanded = _expand_path(raw_target, home, cwd)

    # Безопасные устройства
    if expanded in _SAFE_DEVICES:
        return None

    # Правило 12: временные каталоги — SAFE
    if _is_temp_path(expanded):
        return None

    # Правило 9: неразрешимая подстановка
    if "$" in expanded or "`" in expanded:
        return RiskFinding(
            rule="unresolved_substitution",
            target=raw_target,
            level=RiskLevel.CONFIRM,
        )

    # Правила 7 / 8: глобы
    if "*" in expanded or "?" in expanded:
        if _glob_in_protected(expanded):
            return RiskFinding(
                rule="protected_glob",
                target=raw_target,
                level=RiskLevel.CATASTROPHIC,
            )
        # Глоб не должен ослаблять защиту каталога, в котором он раскрывается:
        # `rm -rf ~/.ssh/*` уничтожает ровно то же, что `rm -rf ~/.ssh`.
        # Раньше ветка глоба возвращала CONFIRM раньше проверок $HOME и ключей,
        # и такая команда проходила мимо блокировки.
        parent = _glob_parent(expanded)
        if parent:
            inherited = _check_target(parent, cwd=cwd, home=home, is_recursive=True)
            if inherited is not None and inherited.level is RiskLevel.CATASTROPHIC:
                return RiskFinding(
                    rule=inherited.rule,
                    target=raw_target,
                    level=RiskLevel.CATASTROPHIC,
                )
        return RiskFinding(
            rule="glob_target",
            target=raw_target,
            level=RiskLevel.CONFIRM,
        )

    # Правило 1: системный корень
    if expanded in _SYSTEM_ROOTS:
        return RiskFinding(
            rule="protected_root",
            target=raw_target,
            level=RiskLevel.CATASTROPHIC,
        )

    # Правило 6: запись в /dev/* (до пр. 2, т.к. /dev есть в обоих)
    if _is_inside("/dev", expanded):
        return RiskFinding(
            rule="dev_write",
            target=raw_target,
            level=RiskLevel.CATASTROPHIC,
        )

    # Правило 2: сам защищённый каталог или что-то внутри него. Равенство важно
    # отдельно: удалить `/var/lib` целиком опаснее, чем `/var/lib/docker`, и
    # проверка «строго внутри» такую цель пропускала.
    if any(expanded == d or _is_inside(d, expanded) for d in _PROTECTED_RECURSIVE):
        return RiskFinding(
            rule="protected_recursive",
            target=raw_target,
            level=RiskLevel.CATASTROPHIC,
        )

    # Правило 3: цель равна $HOME
    if expanded == home:
        return RiskFinding(
            rule="home_root",
            target=raw_target,
            level=RiskLevel.CATASTROPHIC,
        )

    # Правило 4: сам чувствительный каталог или что-то внутри него. Равенство
    # обязательно: `rm -rf ~/.ssh` сносит все ключи разом и не может быть мягче,
    # чем удаление одной папки внутри него.
    for dot in _SENSITIVE_DOTDIRS:
        protected = home + "/" + dot
        if expanded == protected or _is_inside(protected, expanded):
            return RiskFinding(
                rule="sensitive_dotdir",
                target=raw_target,
                level=RiskLevel.CATASTROPHIC,
            )

    # Правило 5: точное совпадение с защищёнными dotdir / каталогами
    for dot in _PROTECTED_DOTDIRS_EXACT:
        if expanded == home + "/" + dot:
            return RiskFinding(
                rule="protected_dotdir_exact",
                target=raw_target,
                level=RiskLevel.CATASTROPHIC,
            )

    # Правило 10: вне cwd и не во временном каталоге
    if expanded != cwd and not _is_inside(cwd, expanded):
        return RiskFinding(
            rule="outside_cwd",
            target=raw_target,
            level=RiskLevel.CONFIRM,
        )

    # Правило 11: рекурсивное удаление внутри cwd
    if is_recursive and (expanded == cwd or _is_inside(cwd, expanded)):
        return RiskFinding(
            rule="recursive_cwd",
            target=raw_target,
            level=RiskLevel.LOW,
        )

    return None
