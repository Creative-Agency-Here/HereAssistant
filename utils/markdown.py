"""Конвертация Markdown в Telegram HTML.

Telegram HTML поддерживает теги: b, i, u, s, code, pre, a, blockquote, tg-spoiler.
Идея: эскейпим спецсимволы HTML, потом парные markdown-маркеры превращаем
в соответствующие теги. Неполные маркеры (например, мид-стрим `**`) остаются
как текст — Telegram их не упадёт парсить.
"""

import re


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
    text = re.sub(
        r"```([a-zA-Z0-9_+\-]*)\n?([\s\S]*?)```", lambda m: stash(m.group(2), "pre"), text
    )
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
    if re.search(r"</?[a-zA-Z][^>]*>|&(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);", text):
        return _split_html(text, limit)
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


def _split_html(text: str, limit: int) -> list[str]:
    """Режет Telegram HTML, закрывая и переоткрывая активные теги."""
    if limit < 16:
        raise ValueError("HTML chunk limit is too small")
    tokens = re.findall(r"<[^>]+>|&(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);|[^<&]+|[<&]", text)
    chunks: list[str] = []
    current = ""
    stack: list[tuple[str, str]] = []

    def closing(tags: list[tuple[str, str]]) -> str:
        return "".join(f"</{name}>" for name, _opening in reversed(tags))

    def emit() -> None:
        nonlocal current
        if not current:
            return
        chunks.append(current + closing(stack))
        current = "".join(opening for _name, opening in stack)

    for token in tokens:
        tag = _html_tag(token)
        if tag is not None:
            name, is_closing, is_self_closing = tag
            future_stack = list(stack)
            if is_closing:
                for index in range(len(future_stack) - 1, -1, -1):
                    if future_stack[index][0] == name:
                        del future_stack[index:]
                        break
            elif not is_self_closing:
                future_stack.append((name, token))
            if len(current) + len(token) + len(closing(future_stack)) > limit:
                emit()
            current += token
            stack = future_stack
            continue

        if token.startswith("&") and token.endswith(";"):
            if len(current) + len(token) + len(closing(stack)) > limit:
                emit()
            current += token
            continue

        offset = 0
        while offset < len(token):
            available = limit - len(current) - len(closing(stack))
            if available <= 0:
                emit()
                available = limit - len(current) - len(closing(stack))
            if available <= 0:
                raise ValueError("HTML tag nesting exceeds chunk limit")
            piece = token[offset : offset + available]
            # Entity tokens are atomic due tokenizer; ordinary text prefers a newline.
            if len(piece) == available and offset + available < len(token):
                newline = piece.rfind("\n")
                if newline >= available // 2:
                    piece = piece[: newline + 1]
            current += piece
            offset += len(piece)
            if offset < len(token):
                emit()
    emit()
    return chunks


def _html_tag(token: str) -> tuple[str, bool, bool] | None:
    match = re.fullmatch(r"<\s*(/?)\s*([a-zA-Z][\w-]*)(?:\s[^>]*)?\s*(/?)>", token)
    if not match:
        return None
    return match.group(2).lower(), bool(match.group(1)), bool(match.group(3))
