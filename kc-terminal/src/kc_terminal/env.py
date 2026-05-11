from __future__ import annotations
from typing import Iterable


DEFAULT_SECRET_PREFIXES: tuple[str, ...] = (
    "ANTHROPIC_", "OPENAI_", "DEEPSEEK_", "GROQ_",
    "SUPABASE_",  "KONA_",   "KC_",
    "GOOGLE_OAUTH_", "GCAL_", "GMAIL_",
    "TELEGRAM_BOT_TOKEN", "ZAPIER_",
    "STRIPE_",    "TWILIO_", "SENDGRID_",
)


def build_child_env(
    parent: dict[str, str],
    secret_prefixes: Iterable[str],
) -> dict[str, str]:
    prefixes = tuple(secret_prefixes)
    return {k: v for k, v in parent.items() if not any(k.startswith(p) for p in prefixes)}
