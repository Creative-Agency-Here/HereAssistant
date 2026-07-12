"""Чистая сборка финального HTML и файлов ответа."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from utils.markdown import html_escape, markdown_to_html

from .message_formatting import make_preview


@dataclass(frozen=True, slots=True)
class FinalAttachment:
    data: bytes
    filename: str


@dataclass(frozen=True, slots=True)
class FinalPayload:
    html: str
    attachments: tuple[FinalAttachment, ...] = ()


def prepare_final_payload(
    answer: str,
    *,
    header_html: str,
    signature: str,
    chain: Sequence[object],
    rich_done: bool,
    long_text_limit: int,
    long_steps_limit: int,
    preview_limit: int,
    timestamp: str,
) -> FinalPayload:
    attachments: list[FinalAttachment] = []
    if not rich_done and len(answer) > long_text_limit:
        display_answer = (
            make_preview(answer, preview_limit) + "\n\n📄 _Полный ответ — в прикреплённом файле_"
        )
        attachments.append(FinalAttachment(("\ufeff" + answer).encode(), f"answer-{timestamp}.md"))
    else:
        display_answer = answer

    body_html = markdown_to_html(display_answer)
    signature_html = html_escape(signature)
    chain_block = ""
    if chain and not rich_done:
        base_length = len(header_html) + len(body_html) + len(signature_html)
        inline_lines = [html_escape(f"{index}. {item}") for index, item in enumerate(chain, 1)]
        inline = (
            f"\n\n📋 Шаги ({len(chain)})\n"
            f"<blockquote expandable>{chr(10).join(inline_lines)}</blockquote>"
        )
        if len(chain) <= long_steps_limit and base_length + len(inline) <= 3900:
            chain_block = inline
        else:
            chain_block = f"\n\n📋 Шаги ({len(chain)}) — <i>в прикреплённом файле</i>"
            steps = "\n".join(f"{index}. {item}" for index, item in enumerate(chain, 1))
            attachments.append(
                FinalAttachment(("\ufeff" + steps).encode(), f"steps-{timestamp}.txt")
            )

    return FinalPayload(
        html=header_html + body_html + chain_block + signature_html,
        attachments=tuple(attachments),
    )
