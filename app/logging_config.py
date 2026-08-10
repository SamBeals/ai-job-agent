"""Process-wide logging setup with secret redaction.

Discord webhook URLs, API keys, and bot tokens must never appear in logs —
including httpx's default INFO request line that embeds the full URL.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

# Patterns for credentials that may appear in log messages / exception text.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Discord webhooks: /webhooks/{id}/{token}
    re.compile(
        r"https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+",
        re.I,
    ),
    # Discord bot tokens (rough shape)
    re.compile(r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{20,}\b"),
    # Common bearer / api key headers in logged URLs or messages
    re.compile(r"(?i)(authorization:\s*bearer\s+)(\S+)"),
    re.compile(r"(?i)(x-api-key[=:\s]+)(\S+)"),
    re.compile(r"(?i)(app_key=)([^&\s]+)"),
    re.compile(r"(?i)(api[_-]?key=)([^&\s]+)"),
    re.compile(r"(?i)(openai[_-]?api[_-]?key[=:\s]+)(\S+)"),
)

_REDACTED_WEBHOOK = "https://discord.com/api/webhooks/[REDACTED]"
_REDACTED = "[REDACTED]"


def redact_secrets(text: str) -> str:
    """Return text with known secret patterns replaced."""
    if not text:
        return text
    out = text
    # Webhooks first (full URL)
    out = _SECRET_PATTERNS[0].sub(_REDACTED_WEBHOOK, out)
    # Bot token-ish
    out = _SECRET_PATTERNS[1].sub(_REDACTED, out)
    for pat in _SECRET_PATTERNS[2:]:
        out = pat.sub(lambda m: f"{m.group(1)}{_REDACTED}", out)
    return out


class SecretRedactingFilter(logging.Filter):
    """Logging filter that redacts secrets from record messages and args."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            # Format first, then redact, so placeholders like api_key=%s cannot
            # be mistaken for secret values and leave dangling args.
            try:
                rendered = record.getMessage()
            except Exception:  # noqa: BLE001
                rendered = str(record.msg)
            scrubbed = redact_secrets(rendered)
            for secret in list(_DYNAMIC_SECRETS):
                if secret in scrubbed:
                    scrubbed = scrubbed.replace(secret, _REDACTED)
            record.msg = scrubbed
            record.args = ()
            if record.exc_text:
                record.exc_text = redact_secrets(record.exc_text)
                for secret in list(_DYNAMIC_SECRETS):
                    if secret in record.exc_text:
                        record.exc_text = record.exc_text.replace(secret, _REDACTED)
        except Exception:  # noqa: BLE001
            # Never break logging because redaction failed
            pass
        return True


def configure_logging(
    *,
    level: int = logging.INFO,
    format: str = "%(asctime)s %(levelname)s [%(name)s] %(message)s",
    extra_secret_values: Iterable[str] | None = None,
) -> None:
    """Configure root logging once for bot/worker entrypoints.

    - Application logs stay at INFO by default.
    - httpx / httpcore request lines are raised to WARNING (avoids URL leakage).
    - A process-wide SecretRedactingFilter is attached to the root logger.
    """
    root = logging.getLogger()
    # Avoid stacking duplicate handlers on reload
    if not root.handlers:
        logging.basicConfig(level=level, format=format)
    else:
        root.setLevel(level)

    # Quiet HTTP client libraries — their INFO lines include full request URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _ensure_redacting_filter(root)
    # Also attach to common handlers created by basicConfig
    for handler in list(root.handlers):
        _ensure_redacting_filter(handler)

    # Register additional exact secret strings (e.g. from settings at startup)
    for value in extra_secret_values or ():
        register_secret_value(value)


_DYNAMIC_SECRETS: list[str] = []


def register_secret_value(value: str | None) -> None:
    """Register an exact secret string for redaction (webhook URL, API key, etc.)."""
    if not value or not str(value).strip():
        return
    secret = str(value).strip()
    if secret not in _DYNAMIC_SECRETS and len(secret) >= 8:
        _DYNAMIC_SECRETS.append(secret)


def _ensure_redacting_filter(target: logging.Logger | logging.Handler) -> None:
    for existing in getattr(target, "filters", []):
        if isinstance(existing, SecretRedactingFilter):
            return
    target.addFilter(SecretRedactingFilter())
