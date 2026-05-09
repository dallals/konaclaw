import pytest
from kc_connectors.base import MessageEnvelope, Connector


def test_envelope_has_required_fields():
    m = MessageEnvelope(channel="telegram", chat_id="42", sender_id="user42",
                        content="hi", attachments=[])
    assert m.channel == "telegram"


def test_connector_is_abstract():
    with pytest.raises(TypeError):
        Connector("test")  # type: ignore
