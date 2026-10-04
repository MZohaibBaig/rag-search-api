import re

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_BLOCK_BREAK = re.compile(r"\n\s*\n")


def _is_heading(line: str) -> bool:
    """A short line with no sentence punctuation at the end, or a markdown '#' line."""
    line = line.strip()
    if line.startswith("#"):
        return True
    return (0 < len(line) <= 60 and len(line.split()) <= 8
            and not line.endswith((".", "!", "?", ";", ",", ":")))


def _label(title: str, heading: str) -> str:
    return " - ".join(p for p in (title, heading) if p)


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """Split text into (heading label, paragraphs). A heading is the first line of a block
    (blocks are separated by blank lines). A lone heading directly followed by another
    heading at the very start is treated as the document title and kept on every section."""
    blocks = [b.strip() for b in _BLOCK_BREAK.split(text) if b.strip()]
    sections: list[tuple[str, list[str]]] = [("", [])]
    title = heading = ""
    for i, block in enumerate(blocks):
        first, _, rest = block.partition("\n")
        if _is_heading(first):
            name = first.lstrip("#").strip()
            if i == 0 and not rest.strip() and len(blocks) > 1 and _is_heading(blocks[1].partition("\n")[0]):
                title = name
            else:
                heading = name
            sections.append((_label(title, heading), []))
            block = rest.strip()
        if block:
            sections[-1][1].append(" ".join(block.split()))
    return [s for s in sections if s[1]]


def _sentences(paragraph: str, limit: int) -> list[str]:
    """Split on sentence ends; wrap anything still longer than `limit` at word boundaries."""
    out = []
    for sentence in _SENTENCE_BREAK.split(paragraph):
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            out.append(sentence)
    return out


def _length(sentences: list[str]) -> int:
    return sum(len(s) for s in sentences) + max(len(sentences) - 1, 0)


def _overlap_tail(sentences: list[str], overlap: int) -> list[str]:
    """Trailing whole sentences that fit in `overlap` characters."""
    tail: list[str] = []
    for sentence in reversed(sentences):
        if _length([sentence] + tail) > overlap:
            break
        tail.insert(0, sentence)
    return tail


def _section_chunks(paragraphs: list[str], chunk_size: int, overlap: int) -> list[str]:
    units = [(s, j == 0) for p in paragraphs for j, s in enumerate(_sentences(p, chunk_size))]
    chunks: list[str] = []
    current: list[str] = []
    for sentence, starts_paragraph in units:
        too_long = _length(current + [sentence]) > chunk_size
        # Prefer to cut at a paragraph boundary once the chunk is reasonably full
        paragraph_cut = starts_paragraph and _length(current) >= chunk_size // 2
        if current and (too_long or paragraph_cut):
            chunks.append(" ".join(current))
            current = _overlap_tail(current, overlap)
            if _length(current + [sentence]) > chunk_size:
                current = []  # the overlap tail leaves no room for the next sentence
        current.append(sentence)
    if current:
        chunks.append(" ".join(current))
    return chunks


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """
    Split text into overlapping chunks that respect section, paragraph and sentence boundaries.

    Chunks never cross a section heading. Each chunk is prefixed with its heading (and the
    document title, when there is one) so the heading is embedded and searchable with the text.

    Args:
        text: The text to chunk.
        chunk_size: Target maximum chunk size in characters (excluding the heading prefix).
        overlap: Max characters of trailing sentences repeated at the start of the next chunk.

    Returns:
        List of text chunks.
    """
    chunks = []
    for label, paragraphs in _sections(text):
        for body in _section_chunks(paragraphs, chunk_size, overlap):
            chunks.append(f"{label}\n{body}" if label else body)
    if not chunks and text.strip():
        # Nothing but heading-looking lines (e.g. a list of short phrases): chunk it as plain text
        paragraphs = [" ".join(b.split()) for b in _BLOCK_BREAK.split(text) if b.strip()]
        chunks = _section_chunks(paragraphs, chunk_size, overlap)
    return chunks
