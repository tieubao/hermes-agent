"""Outbound ``@Name`` -> ``<@id>`` resolution for the Discord adapter.

Models write a friendly ``@Display Name`` instead of the raw ``<@id>`` Discord needs, so a bot's
attempt to ping a user or another bot comes out as inert text. The adapter calls
``resolve_outbound_mentions`` on outgoing text when ``discord.resolve_outbound_mentions`` is on
(config.yaml only; default off, so the adapter behaves as before).
"""
import re
from typing import Any


def resolve_outbound_mentions(content: str, guild: Any) -> str:
    """Rewrite readable ``@Name`` references in ``content`` into real user mentions.

    Matching is against the guild's cached members (name / display_name / global_name,
    case-insensitive), longest name first so ``@support bot`` wins over a member named ``support``.
    Already-formed ``<@id>`` tokens, emails (``me@host``) and unknown names are left untouched;
    ``@everyone`` and roles stay governed by ``allowed_mentions``. Returns ``content`` unchanged
    without a guild (DMs) or without an ``@``.
    """
    if not content or "@" not in content or guild is None:
        return content
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for member in getattr(guild, "members", None) or []:
        uid = str(member.id)
        for name in (getattr(member, "display_name", None), getattr(member, "global_name", None),
                     getattr(member, "name", None)):
            if name and (name.lower(), uid) not in seen:
                seen.add((name.lower(), uid))
                pairs.append((name, uid))
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    for name, uid in pairs:
        pattern = re.compile(r"(?<![\w<@])@" + re.escape(name) + r"(?![\w])", re.IGNORECASE)
        content = pattern.sub(f"<@{uid}>", content)
    return content


def outbound_mentions_yaml(discord_cfg: dict) -> dict:
    """``discord.resolve_outbound_mentions`` as adapter ``extra``; config.yaml only, no env var."""
    key = "resolve_outbound_mentions"
    return {key: discord_cfg[key]} if key in discord_cfg else {}


class DiscordOutboundMentionsMixin:
    config: Any

    def _resolves_outbound_mentions(self) -> bool:
        """``discord.resolve_outbound_mentions`` (per-profile ``config.extra``); default off."""
        extra = getattr(self.config, "extra", None)
        value = extra.get("resolve_outbound_mentions") if isinstance(extra, dict) else None
        return str(value).strip().lower() in {"true", "1", "yes", "on"}

    def _resolve_outbound_mentions(self, content: str, channel: Any) -> str:
        """``content`` with ``@Name`` rewritten to ``<@id>`` for ``channel``'s guild when enabled."""
        if not self._resolves_outbound_mentions():
            return content
        return resolve_outbound_mentions(content, getattr(channel, "guild", None))
