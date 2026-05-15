from __future__ import annotations
from pathlib import Path

import filetype

# Importing the parsers modules populates REGISTRY as a side effect.
from .parsers import REGISTRY
from .parsers import text as _text  # noqa: F401
from .parsers import pdf_parser as _pdf  # noqa: F401
from .parsers import docx_parser as _docx  # noqa: F401
from .parsers import xlsx_parser as _xlsx  # noqa: F401
from .parsers import image_parser as _image  # noqa: F401


class UnsupportedTypeError(Exception):
    """Raised when a file's detected mime has no registered parser."""


_TEXT_EXTENSIONS = {".txt": "text/plain", ".md": "text/markdown", ".log": "text/x-log"}


def sniff_mime(source: Path) -> str:
    """Best-effort mime detection.

    First tries magic-byte sniffing via the `filetype` library. Falls back to
    a small extension-keyed map for plain-text formats (which `filetype` does
    not handle).
    """
    kind = filetype.guess(str(source))
    if kind is not None:
        return kind.mime
    ext = source.suffix.lower()
    if ext in _TEXT_EXTENSIONS:
        return _TEXT_EXTENSIONS[ext]
    raise UnsupportedTypeError(f"unsupported file type: {source.name!r}")


def dispatch_parser(mime: str):
    """Returns the registered Parser for the given mime, else raises."""
    parser = REGISTRY.get(mime)
    if parser is None:
        raise UnsupportedTypeError(f"unsupported mime: {mime!r}")
    return parser
