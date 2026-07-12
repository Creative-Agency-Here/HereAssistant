from handlers.message_final import prepare_final_payload


def prepare(answer: str, **overrides: object):
    values = {
        "header_html": "🤖 model\n\n",
        "signature": "\n\n— model · 1.0с",
        "chain": [],
        "rich_done": False,
        "long_text_limit": 20,
        "long_steps_limit": 2,
        "preview_limit": 10,
        "timestamp": "123456",
    }
    values.update(overrides)
    return prepare_final_payload(answer, **values)  # type: ignore[arg-type]


def test_short_answer_and_steps_are_rendered_inline() -> None:
    payload = prepare("**готово**", chain=["Read <a>", "Write b"])

    assert payload.html.startswith("🤖 model\n\n<b>готово</b>")
    assert "📋 Шаги (2)" in payload.html
    assert "Read &lt;a&gt;" in payload.html
    assert payload.html.endswith("— model · 1.0с")
    assert payload.attachments == ()


def test_long_answer_uses_preview_and_bom_markdown_attachment() -> None:
    answer = "0123456789" * 4

    payload = prepare(answer)

    assert "Полный ответ — в прикреплённом файле" in payload.html
    assert payload.attachments[0].filename == "answer-123456.md"
    assert payload.attachments[0].data.startswith(b"\xef\xbb\xbf")
    assert payload.attachments[0].data.decode("utf-8-sig") == answer


def test_long_chain_moves_steps_to_bom_text_attachment() -> None:
    payload = prepare("ok", chain=["one", "two", "three"])

    assert "Шаги (3) — <i>в прикреплённом файле</i>" in payload.html
    assert payload.attachments[0].filename == "steps-123456.txt"
    assert payload.attachments[0].data.decode("utf-8-sig") == "1. one\n2. two\n3. three"


def test_rich_done_does_not_duplicate_steps_or_create_answer_file() -> None:
    payload = prepare("x" * 30, chain=["one"], rich_done=True)

    assert "Шаги" not in payload.html
    assert payload.attachments == ()
