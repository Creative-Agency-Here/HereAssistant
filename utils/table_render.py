"""Извлечение markdown-таблиц из текста и рендер в PNG.

Markdown-таблицы в Telegram выводятся как сплошной текст с `|` и нечитаемы.
Поэтому такие таблицы мы вырезаем из ответа и шлём отдельным сообщением — картинкой.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("bridge.table")


# ---------- ПАРСИНГ ----------

_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _is_separator(line: str) -> bool:
    return bool(_SEPARATOR_RE.match(line))


def _split_row(line: str) -> list[str]:
    """| a | b | c |  →  ['a', 'b', 'c']  (с учётом экранированных \\|)."""
    s = line.strip()
    s = s.replace("\\|", "\x00PIPE\x00")
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    cells = [c.strip().replace("\x00PIPE\x00", "|") for c in s.split("|")]
    return cells


def _clean_cell(text: str) -> str:
    """Снимаем markdown-форматирование внутри ячейки: **bold**, *i*, `code`."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)_([^_\n]+?)_(?!_)", r"\1", text)
    text = re.sub(r"`([^`\n]+?)`", r"\1", text)
    return text


def extract_markdown_tables(text: str) -> list[tuple[int, int, list[list[str]]]]:
    """Возвращает [(start_line_idx, end_line_idx_exclusive, rows), ...].

    rows — список строк, где первая — заголовок. Разделитель уже удалён.
    Индексы — по строкам исходного текста (для последующего удаления/замены).
    """
    if not text:
        return []
    lines = text.split("\n")
    out: list[tuple[int, int, list[list[str]]]] = []
    i = 0
    n = len(lines)
    while i < n:
        if _is_table_row(lines[i]) and i + 1 < n and _is_separator(lines[i + 1]):
            header = _split_row(lines[i])
            j = i + 2
            data: list[list[str]] = []
            while j < n and _is_table_row(lines[j]) and not _is_separator(lines[j]):
                row = _split_row(lines[j])
                # выравниваем число ячеек под заголовок
                if len(row) < len(header):
                    row += [""] * (len(header) - len(row))
                elif len(row) > len(header):
                    row = row[: len(header)]
                data.append(row)
                j += 1
            if data:  # таблица только если есть хотя бы одна строка данных
                rows = [[_clean_cell(c) for c in header]] + [
                    [_clean_cell(c) for c in r] for r in data
                ]
                out.append((i, j, rows))
            i = j
        else:
            i += 1
    return out


# ---------- РЕНДЕР ----------

_FONT_CANDIDATES = [
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]


def _load_fonts(size: int = 18) -> tuple[ImageFont.ImageFont, ImageFont.ImageFont]:
    for regular, bold in _FONT_CANDIDATES:
        if Path(regular).exists():
            try:
                rf = ImageFont.truetype(regular, size)
                bf = ImageFont.truetype(bold, size) if Path(bold).exists() else rf
                return rf, bf
            except Exception:
                continue
    # последний фолбэк — встроенный bitmap-шрифт (без кириллицы, но не упадём)
    f = ImageFont.load_default()
    return f, f


def _measure(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    if not text:
        return 0, 0
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap_cell(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Перенос длинных ячеек по словам, чтобы не выходить за max_width."""
    if not text:
        return [""]
    out: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            out.append("")
            continue
        w, _ = _measure(draw, paragraph, font)
        if w <= max_width:
            out.append(paragraph)
            continue
        words = paragraph.split(" ")
        line = ""
        for word in words:
            candidate = (line + " " + word).strip() if line else word
            cw, _ = _measure(draw, candidate, font)
            if cw <= max_width or not line:
                line = candidate
            else:
                out.append(line)
                line = word
        if line:
            out.append(line)
    return out or [""]


def render_table_png(rows: list[list[str]],
                     max_col_width: int = 420,
                     font_size: int = 18) -> bytes:
    """Нарисовать markdown-таблицу как PNG. rows[0] — заголовок."""
    if not rows or not rows[0]:
        raise ValueError("empty table")

    pad_x, pad_y = 14, 9
    border = (203, 213, 224)
    header_bg = (45, 55, 72)
    header_fg = (255, 255, 255)
    row_bg_even = (255, 255, 255)
    row_bg_odd = (249, 250, 251)
    text_fg = (31, 41, 55)
    line_color = (229, 231, 235)

    # промежуточный canvas чтобы измерить текст
    tmp = Image.new("RGB", (1, 1), "white")
    draw_tmp = ImageDraw.Draw(tmp)
    regular, bold = _load_fonts(font_size)

    n_cols = len(rows[0])
    # выравниваем число колонок везде
    rows = [r + [""] * (n_cols - len(r)) if len(r) < n_cols else r[:n_cols] for r in rows]

    # 1) обернуть каждую ячейку, посчитать ширины колонок
    wrapped: list[list[list[str]]] = []  # rows × cols × lines
    col_widths = [0] * n_cols
    for ri, row in enumerate(rows):
        font = bold if ri == 0 else regular
        wrow: list[list[str]] = []
        for ci, cell in enumerate(row):
            lines = _wrap_cell(draw_tmp, cell, font, max_col_width)
            wrow.append(lines)
            for ln in lines:
                w, _ = _measure(draw_tmp, ln, font)
                if w > col_widths[ci]:
                    col_widths[ci] = w
        wrapped.append(wrow)

    # ширина колонки = ширина текста + 2*pad_x, минимум — 60
    col_widths = [max(60, w + 2 * pad_x) for w in col_widths]
    total_width = sum(col_widths) + 1  # +1 на правую границу

    # 2) высоты строк
    line_h = _measure(draw_tmp, "Ayёg", regular)[1] + 4
    row_heights: list[int] = []
    for wrow in wrapped:
        max_lines = max(len(c) for c in wrow)
        row_heights.append(max_lines * line_h + 2 * pad_y)
    total_height = sum(row_heights) + 1

    # 3) рисуем
    img = Image.new("RGB", (total_width, total_height), "white")
    draw = ImageDraw.Draw(img)

    y = 0
    for ri, wrow in enumerate(wrapped):
        rh = row_heights[ri]
        if ri == 0:
            bg = header_bg
            fg = header_fg
            font = bold
        else:
            bg = row_bg_odd if ri % 2 else row_bg_even
            fg = text_fg
            font = regular
        # фон строки
        draw.rectangle([0, y, total_width - 1, y + rh], fill=bg)

        x = 0
        for ci, lines in enumerate(wrow):
            cw = col_widths[ci]
            ty = y + pad_y
            for ln in lines:
                draw.text((x + pad_x, ty), ln, fill=fg, font=font)
                ty += line_h
            # вертикальная разделительная линия (между колонками)
            if ci < n_cols - 1 and ri > 0:
                draw.line([(x + cw, y), (x + cw, y + rh)], fill=line_color, width=1)
            x += cw

        # нижняя граница строки
        if ri > 0 and ri < len(wrapped) - 1:
            draw.line([(0, y + rh), (total_width - 1, y + rh)], fill=line_color, width=1)
        y += rh

    # внешняя рамка
    draw.rectangle([0, 0, total_width - 1, total_height - 1], outline=border, width=1)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------- ВЫСОКОУРОВНЕВЫЙ ХЕЛПЕР ----------

def replace_tables_with_placeholders(text: str,
                                     placeholder: str = "📊 _таблица отправлена картинкой_"
                                     ) -> tuple[str, list[bytes]]:
    """Найти таблицы, заменить каждую на короткий placeholder, вернуть (новый_текст, [png, ...])."""
    tables = extract_markdown_tables(text)
    if not tables:
        return text, []
    lines = text.split("\n")
    pngs: list[bytes] = []
    new_lines: list[str] = []
    i = 0
    n = len(lines)
    # tables отсортированы по возрастанию start
    t_idx = 0
    while i < n:
        if t_idx < len(tables) and tables[t_idx][0] == i:
            start, end, rows = tables[t_idx]
            try:
                pngs.append(render_table_png(rows))
                new_lines.append(placeholder)
            except Exception as e:
                log.warning("render_table_png failed: %s — leaving table as text", e)
                new_lines.extend(lines[start:end])
            i = end
            t_idx += 1
        else:
            new_lines.append(lines[i])
            i += 1
    return "\n".join(new_lines), pngs
