from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any


class ZipBundle:
    """Extracted upload: JSON dict, resolved paths for CSVs and images."""

    def __init__(self, root: Path, data: dict[str, Any]) -> None:
        self.root = root
        self.data = data

    @classmethod
    def from_zip(cls, zip_path: Path) -> ZipBundle:
        tmp = Path(tempfile.mkdtemp(prefix="docgen_zip_"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp)

        json_files = list(tmp.rglob("*.json"))
        if not json_files:
            raise ValueError("ZIP must contain exactly one JSON file (none found).")
        if len(json_files) > 1:
            raise ValueError(
                "ZIP must contain exactly one JSON file "
                f"(found {len(json_files)}: {[str(p) for p in json_files]})."
            )
        with open(json_files[0], encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Root JSON must be an object (dictionary).")
        return cls(tmp, data)

    def find_template_docx(self) -> Path | None:
        """Return the only .docx in the ZIP, if exactly one exists.

        Assumption (per project convention): user provides a single Word template in the ZIP
        which contains the desired header/footer. If multiple .docx files exist, callers
        must disambiguate at a higher level (e.g., math snippets).
        """
        docxs = list(self.root.rglob("*.docx"))
        if len(docxs) == 1:
            return docxs[0]
        return None

    def resolve(self, rel: str) -> Path:
        """Resolve a path relative to ZIP root (POSIX or OS separators)."""
        rel_norm = rel.replace("\\", "/").lstrip("/")
        # try exact relative to root
        p = (self.root / rel_norm).resolve()
        if p.is_file():
            return p
        # basename match anywhere (helps messy zips)
        name = Path(rel_norm).name
        matches = list(self.root.rglob(name))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous path {rel!r}: multiple files named {name!r}.")
        raise FileNotFoundError(f"Not found in ZIP: {rel!r}")
