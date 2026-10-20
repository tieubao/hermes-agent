"""config.yaml readers for the Discord adapter: DM role-auth guild opt-in and button-view timeout.

Re-exported from ``plugins.platforms.discord.adapter`` (callers and tests import them there).
"""
from typing import Any, Optional

from gateway.platforms._shared import decode_json_list_literal


def _read_dm_role_auth_guild() -> Optional[int]:
    """Return the guild ID opted-in for DM role-based auth, or None (secure default). Read from
    config.yaml ``discord.dm_role_auth_guild`` only (behavioral, not a secret); int or numeric string."""
    try:
        from hermes_cli.config import read_raw_config
        cfg = read_raw_config() or {}
        discord_cfg = cfg.get("discord", {}) or {}
        raw = discord_cfg.get("dm_role_auth_guild")
    except Exception:
        return None
    if raw is None or raw == "":
        return None
    try:
        guild_id = int(raw)
    except (TypeError, ValueError):
        return None
    return guild_id if guild_id > 0 else None


# Default timeout for Discord button views when ``approvals.discord_prompt_timeout`` is unset;
# Discord interaction tokens expire at ~15 minutes, so 900s is the practical ceiling.
_DISCORD_PROMPT_TIMEOUT_DEFAULT = 300
_DISCORD_PROMPT_TIMEOUT_MIN = 30
_DISCORD_PROMPT_TIMEOUT_MAX = 900


def _read_discord_prompt_timeout() -> int:
    """Timeout (seconds) for Discord button views from ``approvals.discord_prompt_timeout``
    (default 300), clamped to [MIN, MAX] so a typo can't make prompts vanish or outlive tokens."""
    raw: Any = None
    try:
        from hermes_cli.config import read_raw_config
        cfg = read_raw_config() or {}
        approvals_cfg = cfg.get("approvals", {}) or {}
        raw = approvals_cfg.get("discord_prompt_timeout")
    except Exception:
        return _DISCORD_PROMPT_TIMEOUT_DEFAULT
    if raw is None or raw == "":
        return _DISCORD_PROMPT_TIMEOUT_DEFAULT
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return _DISCORD_PROMPT_TIMEOUT_DEFAULT
    if seconds < _DISCORD_PROMPT_TIMEOUT_MIN:
        return _DISCORD_PROMPT_TIMEOUT_MIN
    if seconds > _DISCORD_PROMPT_TIMEOUT_MAX:
        return _DISCORD_PROMPT_TIMEOUT_MAX
    return seconds


def gate_csv_set(raw: Any) -> set:
    """A gate value (CSV string, list, or JSON-array literal) as a set of stripped entries."""
    if raw is None:
        return set()
    raw = decode_json_list_literal(raw)
    if isinstance(raw, list):
        return {str(part).strip() for part in raw if str(part).strip()}
    return {part.strip() for part in str(raw).split(",") if part.strip()}


def seed_thread_name_prefixes(seeded_extra: dict, discord_cfg: dict) -> None:
    """Copy ``discord.ignored_thread_name_prefixes`` into the adapter ``extra`` when it is set."""
    if "ignored_thread_name_prefixes" in discord_cfg:
        seeded_extra["ignored_thread_name_prefixes"] = discord_cfg["ignored_thread_name_prefixes"]


def channel_ignored(ignored_channels: set, channel_keys: set, extra: Any, channel: Any, is_thread: bool) -> bool:
    """The ignored-channels gate: a listed channel id (or ``*``), or a thread whose name starts with
    an entry of ``discord.ignored_thread_name_prefixes`` (config.yaml only, no env var).

    The prefixes are a list, not CSV, so a prefix may contain a comma; blank and non-string entries
    never match."""
    if "*" in ignored_channels or channel_keys & ignored_channels:
        return True
    raw = extra.get("ignored_thread_name_prefixes") if is_thread and isinstance(extra, dict) else None
    prefixes = [raw] if isinstance(raw, str) else raw
    name = str(getattr(channel, "name", "") or "")
    return isinstance(prefixes, (list, tuple)) and any(
        isinstance(p, str) and p.strip() and name.startswith(p) for p in prefixes)
