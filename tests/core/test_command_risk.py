"""Тесты модуля оценки разрушительности shell-команд."""

from pathlib import Path

from core.command_risk import RiskLevel, assess


def _rules(result: object) -> set[str]:
    """Множество идентификаторов правил из результата assess."""
    return {f.rule for f in result.findings}  # type: ignore[attr-defined]


# ---------- правило 1: системный корень ----------


def test_rule_1_system_root(tmp_path: Path) -> None:
    r = assess("rm -rf /", cwd=str(tmp_path), home=str(tmp_path / "h"))
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_root" in _rules(r)


def test_rule_1_var_root(tmp_path: Path) -> None:
    r = assess("rm -rf /var", cwd=str(tmp_path), home=str(tmp_path / "h"))
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_root" in _rules(r)


# ---------- правило 2: внутри рекурсивно защищённого ----------


def test_rule_2_protected_recursive(tmp_path: Path) -> None:
    r = assess(
        "rm -rf /etc/nginx/conf.d",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_recursive" in _rules(r)


def test_rule_2_usr_local(tmp_path: Path) -> None:
    r = assess(
        "rm -rf /usr/local/bin/tool",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_recursive" in _rules(r)


# ---------- правило 3: цель равна $HOME ----------


def test_rule_3_home_tilde(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    r = assess("rm -rf ~", cwd=str(tmp_path), home=home)
    assert r.level is RiskLevel.CATASTROPHIC
    assert "home_root" in _rules(r)


def test_rule_3_home_var(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    r = assess("rm -rf $HOME", cwd=str(tmp_path), home=home)
    assert r.level is RiskLevel.CATASTROPHIC
    assert "home_root" in _rules(r)


# ---------- правило 4: внутри чувствительных dotdir ----------


def test_rule_4_ssh(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    r = assess("rm -rf ~/.ssh/id_rsa", cwd=str(tmp_path), home=home)
    assert r.level is RiskLevel.CATASTROPHIC
    assert "sensitive_dotdir" in _rules(r)


def test_rule_4_aws(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    r = assess("rm -rf ~/.aws/credentials", cwd=str(tmp_path), home=home)
    assert r.level is RiskLevel.CATASTROPHIC
    assert "sensitive_dotdir" in _rules(r)


# ---------- правило 5: точное совпадение с защищёнными ----------


def test_rule_5_config(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    r = assess("rm -rf ~/.config", cwd=str(tmp_path), home=home)
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_dotdir_exact" in _rules(r)


def test_rule_5_documents(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    r = assess("rm -rf ~/Documents", cwd=str(tmp_path), home=home)
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_dotdir_exact" in _rules(r)


# ---------- правило 6: запись в /dev/* ----------


def test_rule_6_dev_write(tmp_path: Path) -> None:
    r = assess(
        "dd if=/dev/zero of=/dev/sda bs=1M",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC
    assert "dev_write" in _rules(r)


def test_rule_6_dev_null_safe(tmp_path: Path) -> None:
    r = assess(
        "echo foo > /dev/null",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.SAFE


# ---------- правило 7: глоб в защищённом каталоге ----------


def test_rule_7_protected_glob(tmp_path: Path) -> None:
    r = assess("rm -rf /etc/*", cwd=str(tmp_path), home=str(tmp_path / "h"))
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_glob" in _rules(r)


# ---------- правило 8: прочий глоб ----------


def test_rule_8_other_glob(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    r = assess("rm -rf /opt/foo/*", cwd=cwd, home=str(tmp_path / "h"))
    assert r.level is RiskLevel.CONFIRM
    assert "glob_target" in _rules(r)


def test_rule_8_question_mark(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    r = assess("rm -rf /opt/fo?", cwd=cwd, home=str(tmp_path / "h"))
    assert r.level is RiskLevel.CONFIRM
    assert "glob_target" in _rules(r)


# ---------- правило 9: неразрешимая подстановка ----------


def test_rule_9_unresolved_var(tmp_path: Path) -> None:
    r = assess(
        "rm -rf $SOME_DIR/data",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CONFIRM
    assert "unresolved_substitution" in _rules(r)


def test_rule_9_backtick(tmp_path: Path) -> None:
    r = assess(
        "rm -rf `pwd`/data",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CONFIRM
    assert "unresolved_substitution" in _rules(r)


# ---------- правило 10: цель вне cwd ----------


def test_rule_10_outside_cwd(tmp_path: Path) -> None:
    cwd = str(tmp_path / "project")
    r = assess("rm -rf /opt/other/file", cwd=cwd, home=str(tmp_path / "h"))
    assert r.level is RiskLevel.CONFIRM
    assert "outside_cwd" in _rules(r)


# ---------- правило 11: рекурсивное удаление внутри cwd ----------


def test_rule_11_recursive_cwd(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    r = assess("rm -rf subdir", cwd=cwd, home=str(tmp_path / "h"))
    assert r.level is RiskLevel.LOW
    assert "recursive_cwd" in _rules(r)


def test_rule_11_non_recursive_safe(tmp_path: Path) -> None:
    """rm без -r внутри cwd — безопасно."""
    cwd = str(tmp_path)
    r = assess("rm file.txt", cwd=cwd, home=str(tmp_path / "h"))
    assert r.level is RiskLevel.SAFE


# ---------- правило 12: временные каталоги ----------


def test_rule_12_tmp_safe(tmp_path: Path) -> None:
    r = assess(
        "rm -rf /tmp/build_cache",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.SAFE
    assert r.findings == ()


def test_rule_12_var_tmp(tmp_path: Path) -> None:
    r = assess(
        "rm -rf /var/tmp/old",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.SAFE


# ---------- правило 13: pipe stdin ----------


def test_rule_13_pipe_stdin(tmp_path: Path) -> None:
    r = assess(
        "find . -name '*.bak' | xargs rm",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CONFIRM
    assert "pipe_stdin" in _rules(r)


# ---------- правило 14: разрушающая команда без цели ----------


def test_rule_14_no_target(tmp_path: Path) -> None:
    r = assess("rm -rf", cwd=str(tmp_path), home=str(tmp_path / "h"))
    assert r.level is RiskLevel.CONFIRM
    assert "no_target" in _rules(r)


# ---------- правило 15: обёртка без команды ----------


def test_rule_15_empty_wrapper(tmp_path: Path) -> None:
    r = assess("sudo", cwd=str(tmp_path), home=str(tmp_path / "h"))
    assert r.level is RiskLevel.CONFIRM
    assert "empty_wrapper" in _rules(r)


def test_rule_15_env_flags_only(tmp_path: Path) -> None:
    r = assess("env -i", cwd=str(tmp_path), home=str(tmp_path / "h"))
    assert r.level is RiskLevel.CONFIRM
    assert "empty_wrapper" in _rules(r)


# ---------- обёртки ----------


def test_wrapper_sudo(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    r = assess("sudo rm -rf ~", cwd=str(tmp_path), home=home)
    assert r.level is RiskLevel.CATASTROPHIC
    assert "home_root" in _rules(r)


def test_wrapper_sudo_with_user(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    r = assess("sudo -u root rm -rf ~", cwd=str(tmp_path), home=home)
    assert r.level is RiskLevel.CATASTROPHIC
    assert "home_root" in _rules(r)


def test_wrapper_env_var(tmp_path: Path) -> None:
    r = assess(
        "env FOO=bar rm -rf /etc/old",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_recursive" in _rules(r)


def test_wrapper_timeout(tmp_path: Path) -> None:
    r = assess(
        "timeout 5 rm -rf /etc/old",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_recursive" in _rules(r)


def test_wrapper_nice(tmp_path: Path) -> None:
    r = assess(
        "nice -n 10 rm -rf /etc/old",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_recursive" in _rules(r)


def test_wrapper_chain(tmp_path: Path) -> None:
    """sudo env FOO=1 rm -rf / — цепочка обёрток."""
    r = assess(
        "sudo env FOO=1 rm -rf /",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_root" in _rules(r)


# ---------- sh -c ----------


def test_sh_c(tmp_path: Path) -> None:
    r = assess(
        'sh -c "rm -rf /"',
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_root" in _rules(r)


def test_bash_c_nested(tmp_path: Path) -> None:
    r = assess(
        "bash -c 'rm -rf /etc/nginx'",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_recursive" in _rules(r)


def test_sudo_sh_c(tmp_path: Path) -> None:
    r = assess(
        'sudo sh -c "rm -rf /"',
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_root" in _rules(r)


def test_sh_c_chain_inside(tmp_path: Path) -> None:
    """sh -c с цепочкой внутри."""
    r = assess(
        'bash -c "rm -rf /etc/a && rm -rf /usr/b"',
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC
    rules = _rules(r)
    assert "protected_recursive" in rules


# ---------- цепочки ----------


def test_chain_and(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    r = assess(
        "rm -rf subdir_a && rm -rf subdir_b",
        cwd=cwd,
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.LOW
    assert r.findings[0].rule == "recursive_cwd"
    assert r.findings[1].rule == "recursive_cwd"


def test_chain_mixed(tmp_path: Path) -> None:
    """Цепочка: максимум по сегментам."""
    r = assess(
        "rm -rf /tmp/a && rm -rf /etc/b",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC


def test_chain_semicolon(tmp_path: Path) -> None:
    r = assess(
        "echo ok; rm -rf /etc/x",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC


# ---------- редиректы ----------


def test_redirect_overwrite(tmp_path: Path) -> None:
    """> файл считается целью."""
    r = assess(
        "echo evil > /etc/passwd",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_recursive" in _rules(r)


def test_redirect_force(tmp_path: Path) -> None:
    """>| файл считается целью."""
    r = assess(
        "echo evil >| /etc/passwd",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_recursive" in _rules(r)


def test_redirect_append_not_counted(tmp_path: Path) -> None:
    """>> файл НЕ считается целью."""
    r = assess(
        "echo log >> /etc/passwd",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.SAFE


# ---------- dd of= ----------


def test_dd_of(tmp_path: Path) -> None:
    r = assess(
        "dd if=/dev/zero of=/dev/sda bs=4M",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC
    assert "dev_write" in _rules(r)


def test_dd_of_file(tmp_path: Path) -> None:
    """dd of= с обычным файлом вне cwd."""
    r = assess(
        "dd if=/dev/zero of=/opt/image.raw bs=1M",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CONFIRM
    assert "outside_cwd" in _rules(r)


# ---------- безопасные команды ----------


def test_safe_ls(tmp_path: Path) -> None:
    r = assess("ls -la", cwd=str(tmp_path), home=str(tmp_path / "h"))
    assert r.level is RiskLevel.SAFE
    assert r.findings == ()


def test_safe_git_status(tmp_path: Path) -> None:
    r = assess("git status", cwd=str(tmp_path), home=str(tmp_path / "h"))
    assert r.level is RiskLevel.SAFE


def test_safe_pytest(tmp_path: Path) -> None:
    r = assess("pytest -q tests/", cwd=str(tmp_path), home=str(tmp_path / "h"))
    assert r.level is RiskLevel.SAFE


def test_safe_git_clean_is_destructive(tmp_path: Path) -> None:
    """git clean — разрушающая, без цели → CONFIRM."""
    r = assess("git clean -fd", cwd=str(tmp_path), home=str(tmp_path / "h"))
    assert r.level is RiskLevel.CONFIRM
    assert "no_target" in _rules(r)


def test_safe_find_without_delete(tmp_path: Path) -> None:
    """find без -delete/-exec — не разрушающая."""
    r = assess(
        "find . -name '*.py'",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.SAFE


def test_find_delete(tmp_path: Path) -> None:
    """find -delete — разрушающая."""
    cwd = str(tmp_path)
    r = assess("find . -name '*.bak' -delete", cwd=cwd, home=str(tmp_path / "h"))
    assert r.level is RiskLevel.LOW
    assert "recursive_cwd" in _rules(r)


def test_chmod_recursive(tmp_path: Path) -> None:
    """chmod -R — разрушающая."""
    r = assess(
        "chmod -R 777 /etc/app",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_recursive" in _rules(r)


# ---------- explanation ----------


def test_explanation_no_command(tmp_path: Path) -> None:
    """explanation() НЕ содержит самой команды."""
    cmd = "rm -rf /etc/nginx"
    r = assess(cmd, cwd=str(tmp_path), home=str(tmp_path / "h"))
    text = r.explanation()
    assert cmd not in text
    assert "rm" not in text
    assert len(text) > 0


def test_explanation_safe(tmp_path: Path) -> None:
    r = assess("ls", cwd=str(tmp_path), home=str(tmp_path / "h"))
    assert r.explanation() == "Команда безопасна"


def test_explanation_multiline(tmp_path: Path) -> None:
    """Несколько находок — несколько строк."""
    r = assess(
        "rm -rf /etc/a && rm -rf /usr/b",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    lines = r.explanation().split("\n")
    assert len(lines) == 2


# ---------- раскрытие путей ----------


def test_dotdot_traversal(tmp_path: Path) -> None:
    """.. нормализуется лексически."""
    r = assess(
        "rm -rf /tmp/../etc/passwd",
        cwd=str(tmp_path),
        home=str(tmp_path / "h"),
    )
    assert r.level is RiskLevel.CATASTROPHIC
    assert "protected_recursive" in _rules(r)


def test_home_braces(tmp_path: Path) -> None:
    """${HOME} раскрывается."""
    home = str(tmp_path / "home")
    r = assess("rm -rf ${HOME}", cwd=str(tmp_path), home=home)
    assert r.level is RiskLevel.CATASTROPHIC
    assert "home_root" in _rules(r)


def test_relative_path_resolved(tmp_path: Path) -> None:
    """Относительный путь раскрывается относительно cwd."""
    cwd = str(tmp_path)
    r = assess("rm -rf ./build", cwd=cwd, home=str(tmp_path / "h"))
    assert r.level is RiskLevel.LOW
    assert "recursive_cwd" in _rules(r)


# --- Регрессии, найденные при приёмке модуля ---


def test_leading_env_assignment_does_not_hide_the_command() -> None:
    """`FOO=bar rm -rf /` — присваивание перед командой прятало её целиком."""
    assert (
        assess("FOO=bar rm -rf /", cwd="/w/p", home="/Users/tester").level is RiskLevel.CATASTROPHIC
    )
    assert (
        assess("FOO=bar BAR=baz rm -rf ~", cwd="/w/p", home="/Users/tester").level
        is RiskLevel.CATASTROPHIC
    )


def test_leading_assignment_keeps_safe_commands_safe() -> None:
    assert (
        assess("VERSION=1.2 make build", cwd="/w/p", home="/Users/tester").level is RiskLevel.SAFE
    )


def test_protected_directory_itself_is_catastrophic() -> None:
    """Удалить `/var/lib` целиком опаснее, чем его подпапку."""
    assert (
        assess("rm -rf /var/lib", cwd="/w/p", home="/Users/tester").level is RiskLevel.CATASTROPHIC
    )
    assert assess("rm -rf /etc", cwd="/w/p", home="/Users/tester").level is RiskLevel.CATASTROPHIC


def test_wrapper_chain_with_assignment_reaches_the_target() -> None:
    assessment = assess("env FOO=bar sudo rm -rf /var/lib", cwd="/w/p", home="/Users/tester")
    assert assessment.level is RiskLevel.CATASTROPHIC
