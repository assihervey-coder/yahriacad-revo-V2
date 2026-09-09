"""Découpage de datasheets en chunks métadonnés pour l'index vectoriel.

Stratégie déterministe : découpe par pages (sauts de formulaire ``\\f`` ou
marqueurs « Page N »), puis par sections (titres Markdown ou lignes numérotées
type « 3.2 Electrical Characteristics »), puis accumulation de paragraphes
jusqu'à une taille cible de ~512 tokens (≈ 4 caractères/token), avec un
chevauchement de 50 tokens entre chunks consécutifs pour ne jamais couper un
contexte au milieu d'un tableau de caractéristiques.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

CHARS_PER_TOKEN = 4          # approximation standard pour l'anglais technique
DEFAULT_TARGET_TOKENS = 512
DEFAULT_OVERLAP_TOKENS = 50

_SECTION_RE = re.compile(
    r"^\s*(#{1,4}\s+\S|\d+(\.\d+)*\s+[A-ZÀ-Ü]|[A-ZÀ-Ü][A-ZÀ-Ü \-/]{6,})\s*$"
)
_PAGE_MARKER_RE = re.compile(r"^\s*(?:page|p\.)\s*(\d+)\s*$", re.IGNORECASE)


@dataclass
class Document:
    """Unité de retrieval : contenu textuel + métadonnées de provenance."""

    content: str
    metadata: dict = field(default_factory=dict)   # {source, page, section}

    def citation(self) -> str:
        """Citation courte « [source p.X] » exigée sur chaque affirmation."""
        page = self.metadata.get("page", 1)
        return f"[{self.metadata.get('source', '?')} p.{page}]"


def _split_paragraphs(text: str) -> List[str]:
    """Découpe brut en paragraphes (lignes vides séparatrices)."""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _hard_split(paragraph: str, max_chars: int, overlap_chars: int) -> List[str]:
    """Découpe forcée d'un paragraphe trop long, avec recouvrement."""
    parts: List[str] = []
    start = 0
    while start < len(paragraph):
        end = min(start + max_chars, len(paragraph))
        # coupe de préférence sur une fin de phrase ou d'espace
        if end < len(paragraph):
            dot = paragraph.rfind(". ", start, end)
            if dot > start + max_chars // 2:
                end = dot + 1
        parts.append(paragraph[start:end].strip())
        if end >= len(paragraph):
            break
        start = max(end - overlap_chars, start + 1)
    return [p for p in parts if p]


def chunk_datasheet(text: str, source: str,
                    target_tokens: int = DEFAULT_TARGET_TOKENS,
                    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS) -> List[Document]:
    """Découpe un document (datasheet) en :class:`Document` prêts à indexer.

    ``target_tokens`` ~512 (≈ 2048 caractères) ; ``overlap_tokens`` ~50
    (≈ 200 caractères) — chaque chunk porte {source, page, section}.
    """
    target_chars = max(200, target_tokens * CHARS_PER_TOKEN)
    overlap_chars = max(0, overlap_tokens * CHARS_PER_TOKEN)
    docs: List[Document] = []

    # 1) pages : sauts de formulaire ou marqueurs « Page N »
    pages: List[tuple] = []          # (page_number, text)
    current_no, buffer = 1, []
    for line in text.splitlines():
        marker = _PAGE_MARKER_RE.match(line)
        if line == "\f" or marker:
            pages.append((current_no, "\n".join(buffer)))
            if marker:
                current_no = int(marker.group(1))
            else:
                current_no += 1
            buffer = []
        else:
            buffer.append(line)
    pages.append((current_no, "\n".join(buffer)))

    # 2) sections puis accumulation de paragraphes
    for page_no, page_text in pages:
        section, chunk, chunk_len, section_name = "", [], 0, "général"
        pending_first: str | None = None

        def flush(first: str | None = None) -> None:
            nonlocal chunk, chunk_len
            body = "\n\n".join(chunk).strip()
            if body:
                content = body if first is None else f"{first}\n\n{body}"
                docs.append(Document(content=content,
                                     metadata={"source": source, "page": page_no,
                                               "section": section_name}))
            chunk, chunk_len = [], 0

        for paragraph in _split_paragraphs(page_text):
            first_line = paragraph.splitlines()[0].strip()
            if _SECTION_RE.match(first_line):
                flush(pending_first if chunk else None)
                pending_first, section_name = first_line, first_line[:80]
                paragraph = "\n".join(paragraph.splitlines()[1:]).strip() or first_line
            if len(paragraph) > target_chars:
                flush(pending_first if chunk else None)
                pending_first = None
                for piece in _hard_split(paragraph, target_chars, overlap_chars):
                    docs.append(Document(content=piece,
                                         metadata={"source": source, "page": page_no,
                                                   "section": section_name}))
                continue
            if chunk_len + len(paragraph) > target_chars and chunk:
                flush(pending_first)
                pending_first = None
                # chevauchement : on ré-amorce avec la fin du chunk précédent
                tail = "\n\n".join(chunk)[-overlap_chars:]
                if tail.strip():
                    chunk, chunk_len = [tail], len(tail)
            chunk.append(paragraph)
            chunk_len += len(paragraph) + 2
        flush(pending_first if chunk else None)
    return docs
