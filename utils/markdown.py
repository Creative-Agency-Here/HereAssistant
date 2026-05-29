"""Конвертация Markdown в Telegram HTML.

Telegram HTML поддерживает теги: b, i, u, s, code, pre, a, blockquote, tg-spoiler.
Идея: эскейпим спецсимволы HTML, потом парные markdown-маркеры превращаем
в соответствующие теги. Неполные маркеры (например, мид-стрим `**`) остаются
как текст — Telegram их не упадёт парсить.
"""

import re
from typing import Optional


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def markdown_to_html(text: str) -> str:
    if not text:
        return ""

    placeholders: list[tuple[str, str]] = []  # (content, tag)

    def stash(content: str, tag: str) -> str:
        placeholders.append((content, tag))
        return f"\x00PH{len(placeholders) - 1}\x00"

    # 1) Вырезаем код-блоки и inline-код ДО эскейпа, чтобы внутри не сломать символы
    text = re.sub(r"```([a-zA-Z0-9_+\-]*)\n?([\s\S]*?)```",
                  lambda m: stash(m.group(2), "pre"), text)
    text = re.sub(r"`([^`\n]+?)`", lambda m: stash(m.group(1), "code"), text)

    # 2) Эскейпим весь оставшийся текст
    text = html_escape(text)

    # 3) **жирный**, __жирный__
    text = re.sub(r"\*\*([^\n*]+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__([^\n_]+?)__", r"<b>\1</b>", text)

    # 4) *курсив*, _курсив_  (только если рядом нет ещё одной звезды/подчёркивания)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!_)_([^_\n]+?)_(?!_)", r"<i>\1</i>", text)

    # 5) [text](url)
    text = re.sub(
        r"\[([^\]\n]+)\]\(([^)\s]+)\)",
        lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>',
        text,
    )

    # 6) ### Заголовки → жирный
    text = re.sub(r"^\s{0,3}#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # 7) Списки * - + → •
    text = re.sub(r"^(\s*)[\*\-\+]\s+", r"\1• ", text, flags=re.MULTILINE)

    # 8) Блок-цитаты: подряд идущие строки с «> » (после эскейпа — «&gt; »)
    def quotify(match: re.Match) -> str:
        block = match.group(0).rstrip("\n")
        cleaned = re.sub(r"^&gt;\s?", "", block, flags=re.MULTILINE)
        return f"<blockquote>{cleaned}</blockquote>\n"

    text = re.sub(r"(?:^&gt;[^\n]*(?:\n|$))+", quotify, text, flags=re.MULTILINE)

    # 9) Возвращаем код-плейсхолдеры
    for i, (content, tag) in enumerate(placeholders):
        safe = html_escape(content)
        repl = f"<pre>{safe}</pre>" if tag == "pre" else f"<code>{safe}</code>"
        text = text.replace(f"\x00PH{i}\x00", repl, 1)

    return text


def split_for_telegram(text: str, limit: int = 4000) -> list[str]:
    """Разбить длинный текст на куски с учётом переносов строк."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut == -1 or cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks
