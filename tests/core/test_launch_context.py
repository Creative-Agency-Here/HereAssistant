from core.launch_context import detect_terminal_app, hereassistant_surface


def test_vscode_integration_and_terminal_are_independent() -> None:
    assert hereassistant_surface("task-123") == "hereassistant_vscode"
    assert detect_terminal_app({"VSCODE_INJECTION": "1"}) == "vscode"


def test_terminal_names_are_normalized_and_unknown_values_are_dropped() -> None:
    assert detect_terminal_app({"TERM_PROGRAM": "ghostty"}) == "ghostty"
    assert detect_terminal_app({"TERM_PROGRAM": "Apple_Terminal"}) == "apple_terminal"
    assert detect_terminal_app({"TERM_PROGRAM": "private-shell-name"}) is None


def test_plain_chat_uses_cli_surface() -> None:
    assert hereassistant_surface(None) == "hereassistant_cli"
