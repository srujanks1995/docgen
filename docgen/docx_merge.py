"""Merge block-level OOXML from one Word document into another (formulas, paragraphs, tables)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn


def append_body_from_external_docx(target: Document, source_path: Path) -> None:
    """Append every body block from ``source_path`` except the source section properties.

    Use small snippet .docx files (formula only, derivation only) created in Word so equations
    stay native. Embedded images or OLE in snippets may not resolve if they depend on the
    snippet file's package relationships.
    """
    src = Document(str(source_path))
    dst_body = target._element.body
    for child in src._element.body:
        if child.tag == qn("w:sectPr"):
            continue
        dst_body.append(deepcopy(child))
