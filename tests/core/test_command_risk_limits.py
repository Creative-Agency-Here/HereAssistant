"""Границы защитного классификатора: приёмы обхода, которые он НЕ ловит.

Каждый тест фиксирует ФАКТИЧЕСКОЕ поведение assess() на момент написания.
Если приём проходит незамеченным — это известная граница лексического анализа,
а не баг: классификатор не выполняет код и не раскрывает переменные оболочки.
"""

from __future__ import annotations

from core.command_risk import RiskLevel, assess

_CWD = "/w/project"
_HOME = "/Users/tester"

# Зафиксированное число приёмов, проходящих как SAFE или LOW.
# Если классификатор улучшат — уменьшить; если ухудшат — тест покраснеет.
_UNNOTICED_COUNT = 4


def _level(command: str) -> RiskLevel:
    """Уровень риска для команды с фиксированными cwd/home."""
    return assess(command, cwd=_CWD, home=_HOME).level


# ---------- приёмы, проходящие незамеченными (SAFE) ----------
#
# Общий признак: команду исполняет чужой интерпретатор либо строка собирается
# в рантайме. Лексический анализ здесь бессилен по определению — он не выполняет
# код. Закрыть их можно только песочницей уровня ОС, а не разбором команды.


def test_base64_pipe_to_sh() -> None:
    # base64-декодирование в sh: классификатор не декодирует payload
    assert _level("echo cm0gLXJmIC8K | base64 -d | sh") is RiskLevel.SAFE


def test_python3_c_shutil_rmtree() -> None:
    # rmtree через python3 -c: чужой интерпретатор непрозрачен для лексера
    assert _level('python3 -c "import shutil; shutil.rmtree(chr(47))"') is RiskLevel.SAFE


def test_eval_printf_base64() -> None:
    # eval + printf + base64: двойная косвенность, лексер видит только eval
    assert _level('eval "$(printf %s cm0gLXJmIC8K | base64 -d)"') is RiskLevel.SAFE


def test_perl_e_system() -> None:
    # system() внутри perl -e: чужой интерпретатор непрозрачен
    assert _level('perl -e system("rm -rf /")') is RiskLevel.SAFE


def test_rsync_delete_over_target() -> None:
    # rsync --delete поверх чужого каталога: цель вне рабочего каталога
    assert _level("rsync --delete empty_dir/ /important_dir/") is RiskLevel.CONFIRM
    # ...а поверх системного — уже катастрофа
    assert _level("rsync --delete empty_dir/ /etc/") is RiskLevel.CATASTROPHIC


def test_tee_overwrite_system_file() -> None:
    # tee уничтожает содержимое не хуже rm, цель проверяется как у записи
    assert _level("echo x | tee /etc/passwd") is RiskLevel.CATASTROPHIC
    # обычная запись через tee не должна шуметь: чтение из pipe — штатный режим tee
    assert _level("echo x | tee notes.txt") is RiskLevel.SAFE


def test_tar_extract_over_root() -> None:
    # tar -C /: распаковка поверх корня перезаписывает системные файлы
    assert _level("tar -xf a.tar -C /") is RiskLevel.CATASTROPHIC
    # распаковка внутрь проекта — обычная работа
    assert _level("tar -xf a.tar -C ./build") is RiskLevel.LOW


# ---------- приёмы, которые классификатор замечает ----------


def test_cmd_variable_expansion() -> None:
    # CMD="rm -rf /"; $CMD: присваивание съедается, $CMD не раскрывается
    assert _level('CMD="rm -rf /"; $CMD') is RiskLevel.CONFIRM


def test_indirect_variable_expansion() -> None:
    # A=rm; B=-rf; C=/; $A $B $C: косвенное раскрытие переменных
    assert _level("A=rm; B=-rf; C=/; $A $B $C") is RiskLevel.CONFIRM


def test_git_clean_xffd() -> None:
    # git clean -xffd: распознаётся как разрушающая без цели → CONFIRM
    assert _level("git clean -xffd") is RiskLevel.CONFIRM


def test_find_exec_rm_root() -> None:
    # find / -exec rm: единственный приём, ловящийся как CATASTROPHIC
    assert _level('find / -name "*" -exec rm {} +') is RiskLevel.CATASTROPHIC


# ---------- счётчик незамеченных приёмов ----------


def test_unnoticed_count_has_not_grown() -> None:
    # Если число выросло — классификатор деградировал, нужно чинить.
    # Если уменьшилось — обновить _UNNOTICED_COUNT вниз.
    commands: list[str] = [
        "echo cm0gLXJmIC8K | base64 -d | sh",
        'python3 -c "import shutil; shutil.rmtree(chr(47))"',
        'CMD="rm -rf /"; $CMD',
        'eval "$(printf %s cm0gLXJmIC8K | base64 -d)"',
        'perl -e system("rm -rf /")',
        "A=rm; B=-rf; C=/; $A $B $C",
        'find / -name "*" -exec rm {} +',
        "rsync --delete empty_dir/ /important_dir/",
        "git clean -xffd",
        "echo x | tee /etc/passwd",
        "tar -xf a.tar -C /",
    ]
    unnoticed: list[str] = [
        cmd for cmd in commands if _level(cmd) in (RiskLevel.SAFE, RiskLevel.LOW)
    ]
    assert len(unnoticed) == _UNNOTICED_COUNT, (
        f"Незамеченных приёмов {len(unnoticed)}, ожидалось {_UNNOTICED_COUNT}. Список: {unnoticed}"
    )
