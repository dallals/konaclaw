import json
from pathlib import Path

import pytest

from kc_core.agent import translate_image_sentinel
from kc_core.messages import UserMessage, ToolResultMessage, ImageRef


def test_translate_sentinel_vision_capable_returns_user_turn(tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nbytes")
    sentinel = json.dumps({
        "type": "image",
        "path": str(img),
        "ocr_markdown": "fallback OCR text",
    })
    out = translate_image_sentinel(
        sentinel,
        tool_call_id="call_1",
        vision_for_active_model=True,
    )
    assert out.tool_result.tool_call_id == "call_1"
    assert "image rendered" in out.tool_result.content.lower()
    assert out.follow_up is not None
    assert isinstance(out.follow_up, UserMessage)
    assert len(out.follow_up.images) == 1
    assert out.follow_up.images[0].path == img
    assert out.follow_up.images[0].mime == "image/png"


def test_translate_sentinel_no_vision_substitutes_ocr_text():
    sentinel = json.dumps({
        "type": "image",
        "path": "/nonexistent/x.png",
        "ocr_markdown": "OCR text here",
    })
    out = translate_image_sentinel(
        sentinel,
        tool_call_id="call_1",
        vision_for_active_model=False,
    )
    assert out.tool_result.content == "OCR text here"
    assert out.follow_up is None


def test_translate_non_sentinel_passes_through():
    out = translate_image_sentinel(
        '{"type":"text","markdown":"hi"}',
        tool_call_id="call_1",
        vision_for_active_model=True,
    )
    assert out.tool_result.content == '{"type":"text","markdown":"hi"}'
    assert out.follow_up is None


def test_translate_unparseable_json_passes_through():
    out = translate_image_sentinel(
        "not json",
        tool_call_id="call_1",
        vision_for_active_model=True,
    )
    assert out.tool_result.content == "not json"
    assert out.follow_up is None
