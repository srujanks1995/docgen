from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ElementKind = Literal["paragraph", "table", "image", "math"]


@dataclass
class ParagraphElement:
    kind: Literal["paragraph"] = "paragraph"
    prompt: str = ""
    json_key: str = ""  # key in bundle JSON used as context


@dataclass
class FileParagraphElement:
    """Body text loaded verbatim from a UTF-8 file inside the ZIP (no LLM)."""

    kind: Literal["paragraph_file"] = "paragraph_file"
    text_path: str = ""


@dataclass
class TableElement:
    kind: Literal["table"] = "table"
    csv_path: str = ""
    caption: str = ""


@dataclass
class ImageElement:
    kind: Literal["image"] = "image"
    img_path: str = ""
    caption: str = ""


@dataclass
class MathBlockElement:
    """Formula and/or derivation authored in Word and supplied as .docx snippet files in the ZIP."""

    kind: Literal["math"] = "math"
    docx_path: str = ""
    derivation_docx_paths: list[str] = field(default_factory=list)
    caption: str = ""


DocElement = ParagraphElement | FileParagraphElement | TableElement | ImageElement | MathBlockElement


@dataclass
class SectionNode:
    """Hierarchical section (supports 1, 1.1, 1.1.1 via path)."""

    section_id: str  # e.g. "1.2.3"
    name: str
    elements: list[DocElement] = field(default_factory=list)
    children: list[SectionNode] = field(default_factory=list)


@dataclass
class DocumentConfig:
    title: str
    root_sections: list[SectionNode]
