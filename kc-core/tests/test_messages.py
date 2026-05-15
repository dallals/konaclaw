from pathlib import Path

from kc_core.messages import (
    UserMessage,
    ImageRef,
    to_openai_dict,
    to_native_dict,
)


def test_user_message_default_images_is_empty_tuple():
    m = UserMessage(content="hi")
    assert m.images == ()


def test_user_message_with_images_carries_them():
    refs = (ImageRef(path=Path("/tmp/a.png"), mime="image/png"),)
    m = UserMessage(content="hi", images=refs)
    assert m.images == refs


def test_to_openai_dict_plain_user_text():
    m = UserMessage(content="hi")
    assert to_openai_dict(m) == {"role": "user", "content": "hi"}


def test_to_openai_dict_multimodal_when_images_present(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfakebytes")
    m = UserMessage(content="describe", images=(ImageRef(path=p, mime="image/png"),))
    d = to_openai_dict(m)
    assert d["role"] == "user"
    assert isinstance(d["content"], list)
    assert d["content"][0] == {"type": "text", "text": "describe"}
    assert d["content"][1]["type"] == "image_url"
    assert d["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_to_native_dict_emits_images_field(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfakebytes")
    m = UserMessage(content="describe", images=(ImageRef(path=p, mime="image/png"),))
    d = to_native_dict(m)
    assert d["role"] == "user"
    assert d["content"] == "describe"
    assert isinstance(d["images"], list)
    assert len(d["images"]) == 1
    import base64
    assert base64.b64decode(d["images"][0]) == p.read_bytes()
