import pytest
from unittest.mock import AsyncMock, MagicMock
from kc_connectors.telegram_adapter import TelegramConnector


@pytest.mark.asyncio
async def test_send_calls_bot_send_message():
    c = TelegramConnector(token="T0K", allowlist={"42"})
    c._app = MagicMock()
    c._app.bot.send_message = AsyncMock()
    await c.send(chat_id="42", content="hello", attachments=None)
    c._app.bot.send_message.assert_awaited_once()
    kwargs = c._app.bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == 42
    assert kwargs["text"] == "hello"


@pytest.mark.asyncio
async def test_send_to_non_allowlisted_raises():
    c = TelegramConnector(token="T0K", allowlist={"42"})
    c._app = MagicMock()
    c._app.bot.send_message = AsyncMock()
    with pytest.raises(PermissionError, match="not allowlisted"):
        await c.send(chat_id="999", content="hi", attachments=None)


@pytest.mark.asyncio
async def test_inbound_from_unallowlisted_dropped():
    received = []
    async def cb(env): received.append(env)
    c = TelegramConnector(token="T0K", allowlist={"42"})
    c._on_envelope = cb
    fake_update = MagicMock()
    fake_update.effective_chat.id = 999
    fake_update.effective_user.id = 1
    fake_update.message.text = "hi"
    fake_update.message.photo = []
    fake_update.message.document = None
    await c._handle_update(fake_update, MagicMock())
    assert received == []


@pytest.mark.asyncio
async def test_inbound_from_allowlisted_forwarded():
    received = []
    async def cb(env): received.append(env)
    c = TelegramConnector(token="T0K", allowlist={"42"})
    c._on_envelope = cb
    fake_update = MagicMock()
    fake_update.effective_chat.id = 42
    fake_update.effective_user.id = 7
    fake_update.message.text = "hello"
    await c._handle_update(fake_update, MagicMock())
    assert len(received) == 1
    env = received[0]
    assert env.channel == "telegram"
    assert env.chat_id == "42"
    assert env.sender_id == "7"
    assert env.content == "hello"
