"""Bot authors are scoped by ``allowed_bot_ids`` and ``bot_channels``.

``allow_bots: mentions`` admits every bot that @mentions this one, in every guild it sits in. Two
optional config.yaml keys narrow that for BOT authors only: ``allowed_bot_ids`` (the author must be
listed) and ``bot_channels`` (the message must sit in a listed channel, or a thread whose parent is
listed). Both apply in message admission and in the history backfill. Unset keeps today's behaviour.
They are config keys only: no env var reads them.

The tests drive the real entry points: ``_discord_message_admission``, ``_fetch_channel_context`` and
the ``_apply_yaml_config`` bridge.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import discord
import pytest

from gateway.config import PlatformConfig
from gateway.platforms.helpers import MessageDeduplicator
from plugins.platforms.discord import adapter as discord_platform
from plugins.platforms.discord.adapter import DiscordAdapter

SELF_ID = 999
TRUSTED_BOT_ID = 5001
OTHER_BOT_ID = 5002
ROOM_ID = 7001
OTHER_CHANNEL_ID = 7002
THREAD_ID = 7003

_GATE_VARS = (
    "DISCORD_ALLOW_BOTS",
    "DISCORD_ALLOWED_BOT_IDS",
    "DISCORD_BOT_CHANNELS",
    "DISCORD_ALLOWED_USERS",
    "DISCORD_ALLOWED_CHANNELS",
    "DISCORD_IGNORED_CHANNELS",
    "DISCORD_IGNORE_NO_MENTION",
    "DISCORD_BOTS_REQUIRE_INLINE_MENTION",
    "GATEWAY_ALLOW_ALL_USERS",
    "GATEWAY_ALLOWED_USERS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _GATE_VARS:
        monkeypatch.delenv(var, raising=False)


def _adapter(**extra) -> DiscordAdapter:
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="fake-token", extra=dict(extra)))
    adapter._client = SimpleNamespace(user=SimpleNamespace(id=SELF_ID, bot=True))
    adapter._dedup = MessageDeduplicator()
    adapter._is_allowed_user = Mock(return_value=True)
    return adapter


def _channel(channel_id, parent_id=None):
    return SimpleNamespace(id=channel_id, parent_id=parent_id, name="room", guild=SimpleNamespace(name="g"))


def _bot_message(adapter, *, author_id, channel, bot=True):
    return SimpleNamespace(
        id=123,
        author=SimpleNamespace(id=author_id, bot=bot, display_name="b", name="b"),
        channel=channel,
        content=f"<@{SELF_ID}> hello",
        mentions=[adapter._client.user],
        type=discord.MessageType.default,
        guild=SimpleNamespace(id=1),
        attachments=[],
    )


def _admit(adapter, message) -> bool:
    return adapter._discord_message_admission(message, claim=False)[0]


# --- admission -----------------------------------------------------------------------------


def test_unset_keys_keep_admitting_any_mentioning_bot():
    adapter = _adapter(allow_bots="mentions")
    assert _admit(adapter, _bot_message(adapter, author_id=OTHER_BOT_ID, channel=_channel(OTHER_CHANNEL_ID)))


def test_allowed_bot_ids_admits_the_listed_bot_and_drops_another():
    adapter = _adapter(allow_bots="mentions", allowed_bot_ids=[str(TRUSTED_BOT_ID)])
    channel = _channel(ROOM_ID)
    assert _admit(adapter, _bot_message(adapter, author_id=TRUSTED_BOT_ID, channel=channel))
    assert not _admit(adapter, _bot_message(adapter, author_id=OTHER_BOT_ID, channel=channel))


def test_bot_channels_admits_the_room_and_its_threads_only():
    adapter = _adapter(allow_bots="mentions", bot_channels=[str(ROOM_ID)])
    assert _admit(adapter, _bot_message(adapter, author_id=TRUSTED_BOT_ID, channel=_channel(ROOM_ID)))
    assert _admit(adapter, _bot_message(adapter, author_id=TRUSTED_BOT_ID, channel=_channel(THREAD_ID, parent_id=ROOM_ID)))
    assert not _admit(adapter, _bot_message(adapter, author_id=TRUSTED_BOT_ID, channel=_channel(OTHER_CHANNEL_ID)))
    assert not _admit(
        adapter, _bot_message(adapter, author_id=TRUSTED_BOT_ID, channel=_channel(THREAD_ID, parent_id=OTHER_CHANNEL_ID))
    )


def test_both_keys_must_pass():
    adapter = _adapter(allow_bots="mentions", allowed_bot_ids=[str(TRUSTED_BOT_ID)], bot_channels=[str(ROOM_ID)])
    assert _admit(adapter, _bot_message(adapter, author_id=TRUSTED_BOT_ID, channel=_channel(ROOM_ID)))
    assert not _admit(adapter, _bot_message(adapter, author_id=TRUSTED_BOT_ID, channel=_channel(OTHER_CHANNEL_ID)))
    assert not _admit(adapter, _bot_message(adapter, author_id=OTHER_BOT_ID, channel=_channel(ROOM_ID)))


def test_a_comma_separated_string_works_like_a_list():
    adapter = _adapter(allow_bots="mentions", allowed_bot_ids=f"{TRUSTED_BOT_ID}, 42")
    assert _admit(adapter, _bot_message(adapter, author_id=TRUSTED_BOT_ID, channel=_channel(ROOM_ID)))
    assert not _admit(adapter, _bot_message(adapter, author_id=OTHER_BOT_ID, channel=_channel(ROOM_ID)))


def test_env_vars_are_not_a_way_to_set_the_keys(monkeypatch):
    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "mentions")
    monkeypatch.setenv("DISCORD_ALLOWED_BOT_IDS", str(TRUSTED_BOT_ID))
    monkeypatch.setenv("DISCORD_BOT_CHANNELS", str(ROOM_ID))
    adapter = _adapter()
    assert _admit(adapter, _bot_message(adapter, author_id=OTHER_BOT_ID, channel=_channel(OTHER_CHANNEL_ID)))


def test_allow_bots_none_still_drops_a_listed_bot():
    adapter = _adapter(allow_bots="none", allowed_bot_ids=[str(TRUSTED_BOT_ID)])
    assert not _admit(adapter, _bot_message(adapter, author_id=TRUSTED_BOT_ID, channel=_channel(ROOM_ID)))


def test_an_unreadable_key_drops_the_message(monkeypatch):
    adapter = _adapter(allow_bots="mentions", bot_channels=[str(ROOM_ID)])
    monkeypatch.setattr(adapter, "_gate_csv_set", Mock(side_effect=TypeError("boom")))
    assert not _admit(adapter, _bot_message(adapter, author_id=TRUSTED_BOT_ID, channel=_channel(ROOM_ID)))


def test_the_keys_do_not_touch_human_authors():
    adapter = _adapter(allow_bots="mentions", allowed_bot_ids=[str(TRUSTED_BOT_ID)], bot_channels=[str(ROOM_ID)])
    message = _bot_message(adapter, author_id=42, channel=_channel(OTHER_CHANNEL_ID), bot=False)
    assert _admit(adapter, message)


# --- history backfill ----------------------------------------------------------------------


class _HistoryChannel:
    def __init__(self, channel_id, messages, parent_id=None):
        self.id = channel_id
        self.parent_id = parent_id
        self._messages = messages

    def history(self, *, limit, before, after=None, oldest_first=None):
        rows = sorted((m for m in self._messages if m.id < before.id), key=lambda m: m.id, reverse=True)

        async def _iter():
            for row in rows[:limit]:
                yield row

        return _iter()


def _history_message(author_id, content, msg_id):
    return SimpleNamespace(
        id=msg_id,
        author=SimpleNamespace(id=author_id, bot=True, display_name=f"a{author_id}", name=f"a{author_id}"),
        content=content,
        attachments=[],
        type=discord.MessageType.default,
    )


@pytest.mark.asyncio
async def test_backfill_leaves_out_an_unlisted_bots_text(monkeypatch):
    monkeypatch.setattr(discord_platform.discord, "Thread", type("FakeThread", (), {}), raising=False)
    adapter = _adapter(allow_bots="mentions", allowed_bot_ids=[str(TRUSTED_BOT_ID)], history_backfill_limit=10)
    channel = _HistoryChannel(ROOM_ID, [
        _history_message(OTHER_BOT_ID, "unlisted bot text", 3),
        _history_message(TRUSTED_BOT_ID, "listed bot text", 2),
    ])
    result = await adapter._fetch_channel_context(channel, before=SimpleNamespace(id=10))
    assert "listed bot text" in result
    assert "unlisted bot text" not in result


@pytest.mark.asyncio
async def test_backfill_without_keys_still_lists_every_bot(monkeypatch):
    monkeypatch.setattr(discord_platform.discord, "Thread", type("FakeThread", (), {}), raising=False)
    adapter = _adapter(allow_bots="mentions", history_backfill_limit=10)
    channel = _HistoryChannel(ROOM_ID, [_history_message(OTHER_BOT_ID, "any bot text", 3)])
    result = await adapter._fetch_channel_context(channel, before=SimpleNamespace(id=10))
    assert "any bot text" in result


# --- config.yaml bridge --------------------------------------------------------------------


def test_discord_block_keys_are_seeded_into_extra_without_any_env_write(monkeypatch):
    seeded = discord_platform._apply_yaml_config(
        {}, {"allow_bots": "mentions", "allowed_bot_ids": [str(TRUSTED_BOT_ID)], "bot_channels": [str(ROOM_ID)]}
    )
    assert seeded["allowed_bot_ids"] == [str(TRUSTED_BOT_ID)]
    assert seeded["bot_channels"] == [str(ROOM_ID)]
    import os

    assert "DISCORD_ALLOWED_BOT_IDS" not in os.environ and "DISCORD_BOT_CHANNELS" not in os.environ


def test_platform_extra_keys_are_seeded_and_the_discord_block_wins():
    yaml_cfg = {"platforms": {"discord": {"extra": {"allowed_bot_ids": ["1"], "bot_channels": ["2"]}}}}
    seeded = discord_platform._apply_yaml_config(yaml_cfg, {"allowed_bot_ids": ["9"]})
    assert seeded["allowed_bot_ids"] == ["9"]
    assert seeded["bot_channels"] == ["2"]


def test_unset_keys_seed_nothing():
    seeded = discord_platform._apply_yaml_config({}, {"allow_bots": "mentions"})
    assert "allowed_bot_ids" not in seeded and "bot_channels" not in seeded
