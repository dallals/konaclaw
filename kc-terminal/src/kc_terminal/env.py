"""Build the child process environment for terminal_run.

Strips KonaClaw-namespaced secrets via prefix match (case-sensitive). Everything
else is preserved by default -- including third-party tool credentials the agent
legitimately needs (PATH, HOME, GITHUB_TOKEN, AWS_*, ssh-agent vars).

Security model: the default prefix list covers KonaClaw's own runtime config and
the third-party-API keys KonaClaw itself uses. It is NOT an exhaustive list of
"all secrets in any env" -- callers wanting broader stripping should pass a
custom secret_prefixes tuple.
"""
from __future__ import annotations
from typing import Iterable


DEFAULT_SECRET_PREFIXES: tuple[str, ...] = (
    "ANTHROPIC_", "OPENAI_", "DEEPSEEK_", "GROQ_",
    "SUPABASE_",  "KONA_",   "KC_",
    "GOOGLE_OAUTH_", "GCAL_", "GMAIL_",
    "TELEGRAM_",  "ZAPIER_",
    "STRIPE_",    "TWILIO_", "SENDGRID_",
)


def build_child_env(
    parent: dict[str, str],
    secret_prefixes: Iterable[str],
) -> dict[str, str]:
    prefixes = tuple(secret_prefixes)
    return {k: v for k, v in parent.items() if not any(k.startswith(p) for p in prefixes)}
