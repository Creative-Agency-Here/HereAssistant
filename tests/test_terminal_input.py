from io import StringIO

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import fragment_list_to_text

from terminal_input import SlashCommandCompleter, TerminalPrompt


async def test_non_tty_prompt_preserves_multiline_paste() -> None:
    value = "Первая строка\nВторая строка"
    prompt = TerminalPrompt(
        input_stream=StringIO(),
        output_stream=StringIO(),
        fallback=lambda _label: value,
    )

    assert await prompt.read("› ") == value


def test_slash_command_completer_filters_and_describes_commands() -> None:
    completer = SlashCommandCompleter((("/permissions", "режим песочницы"), ("/status", "статус")))

    completions = list(
        completer.get_completions(Document("/per", cursor_position=4), CompleteEvent())
    )

    assert [item.text for item in completions] == ["/permissions"]
    assert fragment_list_to_text(completions[0].display_meta) == "режим песочницы"
    assert not list(
        completer.get_completions(Document("текст /per", cursor_position=10), CompleteEvent())
    )
