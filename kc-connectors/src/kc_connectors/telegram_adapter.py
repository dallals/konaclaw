from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional
from kc_connectors.base import Connector, InboundCallback, MessageEnvelope
from kc_connectors._telegram_format import md_to_telegram_html


logger = logging.getLogger(__name__)


class TelegramConnector(Connector):
    capabilities = {"send"}

    def __init__(
        self,
        token: str,
        allowlist: set[str],
        inbox_dir: Optional[Path] = None,
    ) -> None:
        super().__init__(name="telegram")
        self.token = token
        self.allowlist = set(allowlist)  # set of chat_id strings
        self.inbox_dir = inbox_dir
        self._app = None
        self._on_envelope: Optional[InboundCallback] = None

    async def start(self, supervisor) -> None:
        from telegram.ext import Application, MessageHandler, filters
        self._on_envelope = supervisor.handle_inbound  # async callable
        self._app = Application.builder().token(self.token).build()
        self._app.add_handler(MessageHandler(filters.ALL, self._handle_update))
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

    async def stop(self) -> None:
        if self._app is None:
            return
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

    async def _handle_update(self, update, context) -> None:
        chat_id = str(update.effective_chat.id)
        if chat_id not in self.allowlist:
            return  # spec: silently drop messages from non-allowlisted chats
        text = update.message.text or ""
        attachments: list[Path] = []
        # (Attachment download into inbox_dir is a v0.2 polish; v1 forwards text only.)
        env = MessageEnvelope(
            channel=self.name,
            chat_id=chat_id,
            sender_id=str(update.effective_user.id),
            content=text,
            attachments=attachments,
        )
        if self._on_envelope is not None:
            await self._on_envelope(env)

    async def send(self, chat_id: str, content: str, attachments=None) -> None:
        if chat_id not in self.allowlist:
            raise PermissionError(f"chat {chat_id} not allowlisted")
        # Render markdown → Telegram HTML so **bold** and tables come through
        # as formatted text instead of literal asterisks. If the rendered HTML
        # trips Telegram's strict parser (rare — usually a stray `<` in code
        # the converter missed), retry once as plain text so the user still
        # gets the message.
        html = md_to_telegram_html(content)
        try:
            await self._app.bot.send_message(
                chat_id=int(chat_id), text=html, parse_mode="HTML",
            )
        except Exception:
            logger.warning(
                "telegram send failed with parse_mode=HTML; retrying as plain text",
                exc_info=True,
            )
            await self._app.bot.send_message(chat_id=int(chat_id), text=content)
