"""KonaClaw shared folder package.

Exposes a read/write surface over ~/Desktop/KonaShared/ (configurable via
KC_SHARED_ROOT) split into:

  originals/                  — user-managed, Kona reads only.
  kona-edits/conv<id>-<ts>/   — Kona writes here, one subfolder per conversation,
                                lazy-created on first write.

Reads can target either subtree by relative path. Writes are scoped to the
current conversation's edits subfolder (path-traversal rejected).
"""

from kc_shared.recall import RecallEntry, RecallIndex
from kc_shared.store import (
    SharedFileError,
    SharedFileNotFound,
    SharedPathOutOfScope,
    SharedStore,
)
from kc_shared.wiring import attach_shared_to_agent

__all__ = [
    "RecallEntry",
    "RecallIndex",
    "SharedFileError",
    "SharedFileNotFound",
    "SharedPathOutOfScope",
    "SharedStore",
    "attach_shared_to_agent",
]
