"""Document review tools for leases, purchase agreements, and disclosures.

Every path here originates from model output, so it is resolved and confined to
``DOCUMENTS_DIR`` before any read. Traversal (``..``), symlinks pointing out of
the tree, and absolute paths outside the root are all rejected.
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain.tools import tool
from langchain_core.tools import BaseTool

from real_estate_agent.config import DOCUMENTS_DIR

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv"}
_PDF_SUFFIXES = {".pdf"}

# Roughly 60k tokens of text; past this a document belongs in chunks.
_MAX_TEXT_BYTES = 240_000


def _resolve_within_documents(filename: str) -> Path:
    """Resolve ``filename`` under DOCUMENTS_DIR, or raise if it escapes."""
    candidate = (DOCUMENTS_DIR / filename).resolve()
    root = DOCUMENTS_DIR.resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(
            f"Refusing to read {filename!r}: resolves outside the documents directory."
        )
    return candidate


def make_document_tools() -> list[BaseTool]:
    """Return the document-review tools."""

    @tool
    def list_documents() -> str:
        """List the documents available for review, as JSON.

        Call this before extracting text so you know what is actually on disk
        rather than guessing at filenames.
        """
        root = DOCUMENTS_DIR.resolve()
        if not root.exists():
            return json.dumps({"count": 0, "documents": [], "directory": str(root)})

        entries = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            entries.append(
                {
                    "filename": str(path.relative_to(root)),
                    "size_bytes": path.stat().st_size,
                    "suffix": path.suffix.lower(),
                    "supported": path.suffix.lower() in _TEXT_SUFFIXES | _PDF_SUFFIXES,
                }
            )
        return json.dumps(
            {"count": len(entries), "directory": str(root), "documents": entries}, indent=2
        )

    @tool
    def extract_document_text(filename: str, max_pages: int = 40) -> str:
        """Extract readable text from a document in the documents directory.

        Supports PDF (.pdf) and plain text (.txt, .md, .markdown, .csv). Keep
        this list in step with `_TEXT_SUFFIXES` and `_PDF_SUFFIXES` above — a
        docstring is prompt surface, so an extension missing here is one the
        model believes it cannot read. For PDFs the text
        is returned page by page with page markers so you can cite locations
        precisely when flagging a clause.

        Args:
            filename: Path relative to the documents directory, e.g.
                "123-main-lease.pdf". Must stay inside that directory.
            max_pages: Cap on the number of PDF pages to extract.
        """
        try:
            path = _resolve_within_documents(filename)
        except ValueError as exc:
            return json.dumps({"error": str(exc)})

        if not path.is_file():
            return json.dumps(
                {"error": f"{filename!r} does not exist. Call list_documents first."}
            )

        suffix = path.suffix.lower()

        if suffix in _TEXT_SUFFIXES:
            # Capped like the PDF branch. An uncapped read drops an entire
            # multi-megabyte export into the reviewer's context as one message.
            raw = path.read_bytes()
            truncated = len(raw) > _MAX_TEXT_BYTES
            text = raw[:_MAX_TEXT_BYTES].decode("utf-8", errors="replace")
            return json.dumps(
                {
                    "filename": filename,
                    "format": "text",
                    "characters": len(text),
                    "total_bytes": len(raw),
                    "truncated": truncated,
                    "truncation_note": (
                        f"Only the first {_MAX_TEXT_BYTES} bytes are shown. Say so in "
                        "your findings rather than treating this as the whole document."
                        if truncated
                        else None
                    ),
                    "text": text,
                },
                indent=2,
            )

        if suffix in _PDF_SUFFIXES:
            try:
                from pypdf import PdfReader
            except ImportError:  # pragma: no cover - dependency is declared
                return json.dumps({"error": "pypdf is not installed."})

            reader = PdfReader(str(path))
            total_pages = len(reader.pages)
            pages = []
            for index, page in enumerate(reader.pages[:max_pages], start=1):
                pages.append({"page": index, "text": page.extract_text() or ""})
            return json.dumps(
                {
                    "filename": filename,
                    "format": "pdf",
                    "total_pages": total_pages,
                    "pages_extracted": len(pages),
                    "truncated": total_pages > len(pages),
                    "pages": pages,
                },
                indent=2,
            )

        return json.dumps(
            {
                "error": f"Unsupported file type {suffix!r}.",
                "supported": sorted(_TEXT_SUFFIXES | _PDF_SUFFIXES),
            }
        )

    return [list_documents, extract_document_text]
