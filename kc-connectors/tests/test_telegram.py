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
    assert kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_send_renders_markdown_to_html():
    c = TelegramConnector(token="T0K", allowlist={"42"})
    c._app = MagicMock()
    c._app.bot.send_message = AsyncMock()
    await c.send(chat_id="42", content="**bold** message", attachments=None)
    kwargs = c._app.bot.send_message.call_args.kwargs
    assert kwargs["text"] == "<b>bold</b> message"
    assert kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_send_falls_back_to_plain_text_on_html_error():
    """If Telegram rejects the HTML (e.g., a stray tag the converter missed),
    the connector retries with plain text so the user still gets the message."""
    c = TelegramConnector(token="T0K", allowlist={"42"})
    c._app = MagicMock()
    # First call (with parse_mode=HTML) raises; second (plain text) succeeds.
    c._app.bot.send_message = AsyncMock(side_effect=[Exception("Bad Request: can't parse entities"), None])
    await c.send(chat_id="42", content="**oops**", attachments=None)
    assert c._app.bot.send_message.await_count == 2
    # Second call has no parse_mode and uses raw content.
    second = c._app.bot.send_message.call_args_list[1].kwargs
    assert second["text"] == "**oops**"
    assert "parse_mode" not in second


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
