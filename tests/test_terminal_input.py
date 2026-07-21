from io import StringIO

from terminal_input import TerminalPrompt


async def test_non_tty_prompt_preserves_multiline_paste() -> None:
    value = "Первая строка\nВторая строка"
    prompt = TerminalPrompt(
        input_stream=StringIO(),
        output_stream=StringIO(),
        fallback=lambda _label: value,
    )

    assert await prompt.read("› ") == value
