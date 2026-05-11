from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from docgen.models import (
    DocumentConfig,
    FileParagraphElement,
    ImageElement,
    MathBlockElement,
    ParagraphElement,
    SectionNode,
    TableElement,
)


def _parse_element(raw: dict[str, Any]) -> Any:
    kind = (raw.get("kind") or raw.get("type") or "").lower()
    if not kind:
        if raw.get("text_path") or raw.get("para_text_file") or raw.get("text_file"):
            kind = "paragraph_file"
        elif raw.get("para_prompt") is not None or raw.get("prompt") is not None:
            kind = "paragraph"
        elif raw.get("csv_path") or raw.get("csv"):
            kind = "table"
        elif raw.get("img_path"):
            kind = "image"
        elif (
            raw.get("formula_docx")
            or raw.get("math_docx")
            or raw.get("derivation_docx")
            or raw.get("derivation_docx_paths")
        ):
            kind = "math"
    if kind in ("paragraph_file", "para_from_file", "text_file", "file_paragraph"):
        return FileParagraphElement(
            text_path=str(
                raw.get("text_path") or raw.get("para_text_file") or raw.get("file_path") or raw.get("path") or ""
            ),
        )
    if kind in ("paragraph", "para", "para_element"):
        return ParagraphElement(
            prompt=str(raw.get("para_prompt") or raw.get("prompt") or ""),
            json_key=str(raw.get("para_input_from_json") or raw.get("json_key") or ""),
        )
    if kind in ("table", "table_element"):
        return TableElement(
            csv_path=str(raw.get("csv_path") or raw.get("csv") or ""),
            caption=str(
                raw.get("table_name") or raw.get("tbale_name") or raw.get("caption") or "Table"
            ),
        )
    if kind in ("image", "img", "img_element"):
        return ImageElement(
            img_path=str(raw.get("img_path") or raw.get("path") or ""),
            caption=str(raw.get("img_name") or raw.get("caption") or "Figure"),
        )
    if kind in ("math", "formula", "equation", "derivation"):
        docx_path = str(
            raw.get("docx_path") or raw.get("formula_docx") or raw.get("math_docx") or ""
        )
        deriv_raw = raw.get("derivation_docx") or raw.get("derivation_docx_paths") or raw.get("derivations")
        deriv_paths: list[str] = []
        if isinstance(deriv_raw, list):
            deriv_paths = [str(x).strip() for x in deriv_raw if str(x).strip()]
        elif isinstance(deriv_raw, str) and deriv_raw.strip():
            deriv_paths = [deriv_raw.strip()]
        return MathBlockElement(
            docx_path=docx_path,
            derivation_docx_paths=deriv_paths,
            caption=str(raw.get("caption") or raw.get("math_name") or ""),
        )
    raise ValueError(f"Unknown element kind: {raw!r}")


def _collect_elements(block: dict[str, Any]) -> list[Any]:
    """Best-effort parse for informal YAML; prefer explicit ``elements`` lists."""
    out: list[Any] = []
    for key, val in block.items():
        lk = key.lower()
        if lk in ("elements", "blocks", "content"):
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        out.append(_parse_element(item))
            continue
        if isinstance(val, dict) and ("kind" in val or "type" in val):
            out.append(_parse_element(val))
        elif isinstance(val, dict) and ("para_prompt" in val or lk == "para_element"):
            out.append(
                ParagraphElement(
                    prompt=str(val.get("para_prompt") or val.get("prompt") or ""),
                    json_key=str(val.get("para_input_from_json") or val.get("json_key") or ""),
                )
            )
        elif isinstance(val, dict) and ("csv_path" in val or lk == "table_element"):
            out.append(
                TableElement(
                    csv_path=str(val.get("csv_path") or val.get("csv") or ""),
                    caption=str(
                        val.get("table_name")
                        or val.get("tbale_name")
                        or val.get("caption")
                        or "Table"
                    ),
                )
            )
        elif isinstance(val, dict) and ("img_path" in val or lk == "img_element"):
            out.append(
                ImageElement(
                    img_path=str(val.get("img_path") or val.get("path") or ""),
                    caption=str(val.get("img_name") or val.get("caption") or "Figure"),
                )
            )
        elif isinstance(val, dict) and (
            val.get("text_path") or val.get("para_text_file") or val.get("text_file")
        ):
            out.append(
                FileParagraphElement(
                    text_path=str(
                        val.get("text_path")
                        or val.get("para_text_file")
                        or val.get("text_file")
                        or val.get("file_path")
                        or ""
                    ),
                )
            )
        elif isinstance(val, dict) and (
            val.get("formula_docx")
            or val.get("math_docx")
            or val.get("derivation_docx")
            or val.get("derivation_docx_paths")
            or (str(val.get("kind") or val.get("type") or "").lower() == "math" and val.get("docx_path"))
        ):
            out.append(_parse_element(val))
    return out


def _parse_section_tree(raw: dict[str, Any], path_ids: list[str]) -> SectionNode:
    name = str(raw.get("Section_name") or raw.get("name") or raw.get("section_name") or "Section")
    explicit_id = raw.get("number") or raw.get("id") or raw.get("section_id")
    if explicit_id:
        section_id = str(explicit_id)
    else:
        section_id = ".".join(path_ids) if path_ids else "1"

    elements: list[Any] = []
    if "elements" in raw and isinstance(raw["elements"], list):
        for item in raw["elements"]:
            if isinstance(item, dict):
                elements.append(_parse_element(item))
    else:
        elements.extend(_collect_elements(raw))

    children: list[SectionNode] = []
    subs = raw.get("sections") or raw.get("children") or []
    if isinstance(subs, list):
        for i, child in enumerate(subs, start=1):
            if not isinstance(child, dict):
                continue
            cid = str(child.get("number") or child.get("id") or str(i))
            child_path = path_ids + [cid]
            children.append(_parse_section_tree(child, child_path))
    elif isinstance(subs, dict):
        for i, (k, child) in enumerate(subs.items(), start=1):
            if not isinstance(child, dict):
                continue
            cid = str(child.get("number") or child.get("id") or k.replace("section", "").strip("_") or str(i))
            child_path = path_ids + [cid]
            children.append(_parse_section_tree(child, child_path))

    return SectionNode(section_id=section_id, name=name, elements=elements, children=children)


def load_document_config(yaml_path: Path) -> DocumentConfig:
    with open(yaml_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError("YAML root must be a mapping.")

    title = str(cfg.get("title") or cfg.get("document_title") or "Generated Document")

    roots: list[SectionNode] = []

    if "sections" in cfg:
        sec = cfg["sections"]
        if isinstance(sec, list):
            for i, block in enumerate(sec, start=1):
                if isinstance(block, dict):
                    sid = str(block.get("number") or block.get("id") or str(i))
                    roots.append(_parse_section_tree(block, [sid]))
        elif isinstance(sec, dict):
            for i, (k, block) in enumerate(sec.items(), start=1):
                if not isinstance(block, dict):
                    continue
                sid = str(block.get("number") or block.get("id") or k.replace("section", "").strip("_") or str(i))
                roots.append(_parse_section_tree(block, [sid]))
    else:
        # Flat keys like section1, section2
        def _section_sort_key(x: str) -> tuple[int, str]:
            m = re.search(r"(\d+)$", str(x))
            return (int(m.group(1)) if m else 0, str(x))

        for k in sorted(cfg.keys(), key=_section_sort_key):
            if not str(k).lower().startswith("section"):
                continue
            block = cfg[k]
            if not isinstance(block, dict):
                continue
            sid = str(block.get("number") or block.get("id") or "".join(filter(str.isdigit, str(k))) or "1")
            if not sid or sid == "None":
                sid = "1"
            roots.append(_parse_section_tree(block, [sid]))

    if not roots:
        raise ValueError("No sections found in YAML (expected 'sections' or 'sectionN' keys).")

    return DocumentConfig(title=title, root_sections=roots)
