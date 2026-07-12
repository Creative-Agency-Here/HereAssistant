from html.parser import HTMLParser

import pytest

from utils.markdown import html_escape, markdown_to_html, split_for_telegram


def test_html_escape_handles_all_telegram_html_metacharacters() -> None:
    assert html_escape('<tag attr="x">Tom & Jerry</tag>') == (
        '&lt;tag attr="x"&gt;Tom &amp; Jerry&lt;/tag&gt;'
    )


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("**жирный**", "<b>жирный</b>"),
        ("__тоже жирный__", "<b>тоже жирный</b>"),
        ("*курсив*", "<i>курсив</i>"),
        ("_тоже курсив_", "<i>тоже курсив</i>"),
        ("# Заголовок", "<b>Заголовок</b>"),
        ("- первый\n+ второй\n* третий", "• первый\n• второй\n• третий"),
        ("[ссылка](https://example.com)", '<a href="https://example.com">ссылка</a>'),
        ("> строка 1\n> строка 2", "<blockquote>строка 1\nстрока 2</blockquote>\n"),
    ],
)
def test_markdown_constructs(markdown: str, expected: str) -> None:
    assert markdown_to_html(markdown) == expected


def test_code_is_escaped_but_not_formatted_inside() -> None:
    markdown = "`<x> **не жирный** & value`\n```python\nif a < b:\n    print('&')\n```"

    assert markdown_to_html(markdown) == (
        "<code>&lt;x&gt; **не жирный** &amp; value</code>\n"
        "<pre>if a &lt; b:\n    print('&amp;')\n</pre>"
    )


def test_raw_html_and_user_text_are_escaped() -> None:
    payload = '<script>alert("x")</script> **safe**'

    result = markdown_to_html(payload)

    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    assert result.endswith("<b>safe</b>")


def test_empty_and_incomplete_markdown_remain_safe() -> None:
    assert markdown_to_html("") == ""
    assert markdown_to_html("незакрытый **маркер") == "незакрытый **маркер"
    assert markdown_to_html("незакрытый `код") == "незакрытый `код"


def test_split_returns_single_short_chunk() -> None:
    assert split_for_telegram("коротко", limit=20) == ["коротко"]


def test_split_prefers_newline_and_preserves_content() -> None:
    text = "первая строка\nвторая строка\nтретья строка"

    chunks = split_for_telegram(text, limit=28)

    assert chunks == ["первая строка\nвторая строка", "третья строка"]
    assert "\n".join(chunks) == text
    assert all(len(chunk) <= 28 for chunk in chunks)


def test_split_hard_cuts_long_line_without_data_loss() -> None:
    text = "x" * 25

    chunks = split_for_telegram(text, limit=10)

    assert chunks == ["x" * 10, "x" * 10, "x" * 5]
    assert "".join(chunks) == text


class BalancedHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, _attrs) -> None:
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        assert self.stack and self.stack.pop() == tag

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def parse_balanced(chunk: str) -> str:
    parser = BalancedHTML()
    parser.feed(chunk)
    assert parser.stack == []
    return "".join(parser.text)


def test_html_split_closes_and_reopens_active_tags() -> None:
    html = "<blockquote expandable><b>" + "x" * 50 + "</b></blockquote>"

    chunks = split_for_telegram(html, limit=60)

    assert len(chunks) > 1
    assert all(len(chunk) <= 60 for chunk in chunks)
    assert "".join(parse_balanced(chunk) for chunk in chunks) == "x" * 50
    assert all(chunk.endswith("</b></blockquote>") for chunk in chunks)


def test_html_split_never_breaks_entities_or_link_attributes() -> None:
    html = '<a href="https://example.com?a=1&amp;b=2">' + "word &amp; " * 8 + "</a>"

    chunks = split_for_telegram(html, limit=75)

    assert len(chunks) > 1
    assert all(len(chunk) <= 75 for chunk in chunks)
    assert all("&am" not in chunk.replace("&amp;", "") for chunk in chunks)
    assert "".join(parse_balanced(chunk) for chunk in chunks) == "word & " * 8
