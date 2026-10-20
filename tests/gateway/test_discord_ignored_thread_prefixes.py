"""``discord.ignored_thread_name_prefixes``: drop threads by name before any history is read.

``ignored_channels`` matches ids, but a bridge or integration can create threads on the fly with a
recognisable name. A thread whose name starts with a listed prefix is dropped in ``_handle_message``
next to the ignored-channels gate, before auto-threading, the history backfill and the session
source. The key is config.yaml only and an absent key changes nothing.
"""

import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import hermes_yaml as yaml
import plugins.platforms.discord.adapter as discord_platform
from gateway.config import Platform, PlatformConfig, load_gateway_config
from plugins.platforms.discord.adapter import DiscordAdapter
from plugins.platforms.discord.adapter_config_readers import channel_ignored

PREFIX = "[draft] "


class FakeDMChannel:
    def __init__(self, channel_id: int = 1, name: str = "dm"):
        self.id = channel_id
        self.name = name


class FakeTextChannel:
    def __init__(self, channel_id: int = 900, name: str = "general"):
        self.id = channel_id
        self.name = name
        self.guild = SimpleNamespace(name="Server")
        self.topic = None


class FakeThread:
    def __init__(self, channel_id: int = 4242, name: str = "thread", parent=None):
        self.id = channel_id
        self.name = name
        self.parent = parent
        self.parent_id = getattr(parent, "id", None)
        self.guild = getattr(parent, "guild", None) or SimpleNamespace(name="Server")
        self.topic = None


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(discord_platform.discord, "DMChannel", FakeDMChannel, raising=False)
    monkeypatch.setattr(discord_platform.discord, "Thread", FakeThread, raising=False)
    monkeypatch.setenv("DISCORD_REQUIRE_MENTION", "true")
    monkeypatch.setenv("DISCORD_HISTORY_BACKFILL", "true")
    monkeypatch.delenv("DISCORD_IGNORED_CHANNELS", raising=False)
    monkeypatch.delenv("DISCORD_FREE_RESPONSE_CHANNELS", raising=False)

    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="fake-token"))
    adapter._client = SimpleNamespace(user=SimpleNamespace(id=999))
    adapter._text_batch_delay_seconds = 0
    adapter.handle_message = AsyncMock()
    adapter._fetch_channel_context = AsyncMock(return_value="earlier text")
    return adapter


def _mentioned_thread_message(adapter, thread_name: str):
    bot_user = adapter._client.user
    thread = FakeThread(name=thread_name, parent=FakeTextChannel())
    return SimpleNamespace(
        id=123, content=f"<@{bot_user.id}> hi", mentions=[bot_user], attachments=[], reference=None,
        created_at=datetime.now(UTC), channel=thread,
        author=SimpleNamespace(id=42, display_name="User", name="User"),
    )


@pytest.mark.asyncio
async def test_prefixed_thread_is_dropped_before_history_is_fetched(adapter):
    adapter.config.extra["ignored_thread_name_prefixes"] = [PREFIX]

    handled = await adapter._handle_message(_mentioned_thread_message(adapter, f"{PREFIX}visitor"))

    assert handled is False
    adapter._fetch_channel_context.assert_not_awaited()
    adapter.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_thread_without_the_prefix_still_dispatches(adapter):
    adapter.config.extra["ignored_thread_name_prefixes"] = [PREFIX]

    await adapter._handle_message(_mentioned_thread_message(adapter, "release planning"))

    adapter.handle_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_absent_key_leaves_every_thread_open(adapter):
    adapter.config.extra.pop("ignored_thread_name_prefixes", None)

    await adapter._handle_message(_mentioned_thread_message(adapter, f"{PREFIX}visitor"))

    adapter.handle_message.assert_awaited_once()


@pytest.mark.parametrize("value, name, ignored", [
    ("abc", "abc thread", True),
    (["x, y"], "x, y thread", True),
    ([""], "anything", False),
    (["  "], "  spaced", False),
    ([123], "123", False),
    (None, "anything", False),
])
def test_prefix_matching(adapter, value, name, ignored):
    adapter.config.extra["ignored_thread_name_prefixes"] = value

    assert channel_ignored(set(), set(), adapter.config.extra, FakeThread(name=name), True) is ignored


def test_yaml_key_reaches_the_adapter_without_an_env_var(monkeypatch, tmp_path):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"discord": {"ignored_thread_name_prefixes": [PREFIX, "a, b"]}}), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    before = set(os.environ)

    config = load_gateway_config()

    assert config.platforms[Platform.DISCORD].extra["ignored_thread_name_prefixes"] == [PREFIX, "a, b"]
    assert not any("THREAD_NAME" in key for key in set(os.environ) - before)
