"""KonaClaw file-ingestion package."""

from kc_attachments.capability import VisionCapabilityCache
from kc_attachments.store import AttachmentNotFound, AttachmentRecord, AttachmentStore
from kc_attachments.sniff import UnsupportedTypeError, sniff_mime
from kc_attachments.wiring import attach_attachments_to_agent

__all__ = [
    "AttachmentNotFound",
    "AttachmentRecord",
    "AttachmentStore",
    "UnsupportedTypeError",
    "VisionCapabilityCache",
    "attach_attachments_to_agent",
    "sniff_mime",
]
