from pathlib import Path

from kc_core.tools import ToolRegistry

from kc_attachments import attach_attachments_to_agent
from kc_attachments.store import AttachmentStore


def test_attach_registers_two_tools(tmp_path: Path):
    store = AttachmentStore(root=tmp_path)
    registry = ToolRegistry()
    attach_attachments_to_agent(
        registry=registry,
        store=store,
        conversation_id="conv_1",
        vision_for_active_model=True,
    )
    assert registry.get("read_attachment") is not None
    assert registry.get("list_attachments") is not None
