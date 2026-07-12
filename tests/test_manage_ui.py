import manage_ui
from manage_ui import badge, box_bot, box_mid, box_top, line, strip_ansi


def test_strip_ansi_removes_color_sequences_only() -> None:
    assert strip_ansi(f"{manage_ui.G}green{manage_ui.X} plain") == "green plain"


def test_box_width_accounts_for_invisible_ansi() -> None:
    rendered = box_mid(f"{manage_ui.G}status{manage_ui.X}", width=20)

    assert len(strip_ansi(rendered)) == 20
    assert strip_ansi(box_top(20)) == "╭" + "─" * 18 + "╮"
    assert strip_ansi(box_bot(20)) == "╰" + "─" * 18 + "╯"


def test_line_and_badge_keep_requested_content() -> None:
    assert strip_ansi(line("=", 5)) == "====="
    assert strip_ansi(badge("OK")) == " OK "


def test_getch_falls_back_to_input_when_stdin_has_no_descriptor(monkeypatch) -> None:
    monkeypatch.setattr(manage_ui.sys.stdin, "fileno", lambda: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr("builtins.input", lambda: "answer")

    assert manage_ui.getch() == "a"
