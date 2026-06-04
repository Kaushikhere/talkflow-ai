import re

from app.config import KB_CHUNK_SIZE

_PAGE_BREAK = re.compile(r"\f+")
_PARAGRAPH_BREAK = re.compile(r"\n{2,}")


def default_overlap(chunk_size: int) -> int:
    return max(50, chunk_size // 5)


def _split_pages(text: str) -> list[str]:
    if "\f" in text:
        blocks = _PAGE_BREAK.split(text)
    else:
        blocks = _PARAGRAPH_BREAK.split(text)
    return [b.strip() for b in blocks if b.strip()]


def _window_chunk(block: str, chunk_size: int, overlap: int) -> list[str]:
    if len(block) <= chunk_size:
        return [block]

    chunks: list[str] = []
    start = 0
    stride = max(1, chunk_size - overlap)
    while start < len(block):
        piece = block[start : start + chunk_size].strip()
        if piece:
            chunks.append(piece)
        if start + chunk_size >= len(block):
            break
        start += stride
    return chunks


def chunk_text(
    text: str,
    *,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    size = chunk_size if chunk_size is not None else KB_CHUNK_SIZE
    ov = overlap if overlap is not None else default_overlap(size)

    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []

    result: list[str] = []
    for block in _split_pages(normalized):
        result.extend(_window_chunk(block, size, ov))

    return result
