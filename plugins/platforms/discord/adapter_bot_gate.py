"""Scope for bot authors when ``allow_bots`` is on: which bots, and in which channels.

``allow_bots: mentions`` admits every bot that @mentions this one, in every guild the bot sits in.
Two optional keys in ``config.yaml`` (``discord:`` or ``platforms.discord.extra``) narrow that for
BOT authors only; human authors are never affected:

- ``allowed_bot_ids``: when non-empty, a bot author's user id must be listed.
- ``bot_channels``: when non-empty, the message must sit in a listed channel, or in a thread whose
  parent channel is listed.

Both default to unset, which keeps today's behaviour. They are config keys only (no env var) and are
read from the adapter's own ``config.extra``, so a multiplexed profile never sees another profile's
value.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)

BOT_GATE_KEYS = ("allowed_bot_ids", "bot_channels")


def seed_bot_gate_extra(discord_cfg: dict, platform_extra_cfg: dict) -> dict:
    """The bot-gate keys set in ``discord:`` (preferred) or ``platforms.discord.extra``, for ``extra``."""
    seeded = {}
    for key in BOT_GATE_KEYS:
        value = discord_cfg.get(key)
        if value is None:
            value = platform_extra_cfg.get(key)
        if value is not None:
            seeded[key] = value
    return seeded


def bot_author_in_scope(adapter: Any, author_id: Any, channel: Any) -> bool:
    """True when a bot author passes ``allowed_bot_ids`` and ``bot_channels`` (both unset: always True).

    A gate that cannot be read drops the message: the point of the gate is to refuse, so an error
    never admits."""
    extra = getattr(getattr(adapter, "config", None), "extra", None)
    if not isinstance(extra, dict) or not any(extra.get(key) is not None for key in BOT_GATE_KEYS):
        return True
    try:
        allowed_ids = adapter._gate_csv_set(extra.get("allowed_bot_ids"))
        rooms = adapter._gate_csv_set(extra.get("bot_channels"))
        if allowed_ids and str(author_id) not in allowed_ids:
            return False
        if rooms:
            here = {str(getattr(channel, "id", "") or ""), str(adapter._get_parent_channel_id(channel) or "")}
            if not (here - {""}) & rooms:
                return False
    except (AttributeError, TypeError, ValueError):
        logger.warning("[%s] bot gate unreadable, dropping the bot message", getattr(adapter, "name", "discord"))
        return False
    return True
