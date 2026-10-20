"""Opt-in ``discord.resolve_outbound_mentions``: ``@Name`` in outgoing text becomes ``<@id>``.

Models write a friendly ``@Display Name`` instead of the raw ``<@id>`` Discord needs, so a bot's
attempt to ping a user or another bot is inert text. With the setting on, the adapter rewrites
names that match a guild member on every outgoing path: ``send()`` (including forum starter
posts) and the final ``edit_message()`` of a streamed reply. Off (the default) changes nothing.
"""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig


def _ensure_discord_mock():
    if "discord" in sys.modules and hasattr(sys.modules["discord"], "__file__"):
        return
    discord_mod = MagicMock()
    discord_mod.Intents.default.return_value = MagicMock()
    discord_mod.Client = MagicMock
    discord_mod.File = MagicMock
    discord_mod.DMChannel = type("DMChannel", (), {})
    discord_mod.Thread = type("Thread", (), {})
    discord_mod.ForumChannel = type("ForumChannel", (), {})
    ext_mod = MagicMock()
    commands_mod = MagicMock()
    commands_mod.Bot = MagicMock
    ext_mod.commands = commands_mod
    sys.modules.setdefault("discord", discord_mod)
    sys.modules.setdefault("discord.ext", ext_mod)
    sys.modules.setdefault("discord.ext.commands", commands_mod)


_ensure_discord_mock()

from plugins.platforms.discord.adapter import DiscordAdapter, _apply_yaml_config
from plugins.platforms.discord.adapter_mentions import resolve_outbound_mentions


def _member(uid, *, name, display=None, global_name=None):
    return SimpleNamespace(id=uid, name=name, display_name=display or name, global_name=global_name)


GUILD = SimpleNamespace(members=[
    _member(1, name="alice", display="Alice"),
    _member(2, name="support", display="Support"),
    _member(3, name="support-bot", display="Support Bot", global_name="Helper"),
])


def _adapter(*, resolve):
    extra = {"resolve_outbound_mentions": resolve} if resolve is not None else {}
    return DiscordAdapter(PlatformConfig(enabled=True, token="***", extra=extra))


def _wire(adapter, channel):
    adapter._client = SimpleNamespace(get_channel=lambda _cid: channel, fetch_channel=AsyncMock(return_value=channel))


def _text_channel(guild=GUILD):
    sent = []

    async def send(*, content, reference=None):
        sent.append(content)
        return SimpleNamespace(id=900 + len(sent))

    return SimpleNamespace(id=555, guild=guild, send=AsyncMock(side_effect=send)), sent


@pytest.mark.parametrize("flag", [True, "true", "yes", "1"])
@pytest.mark.asyncio
async def test_send_rewrites_names_to_real_mentions(flag):
    adapter = _adapter(resolve=flag)
    channel, sent = _text_channel()
    _wire(adapter, channel)

    result = await adapter.send("555", "Hi @alice and @Support Bot, also @helper")

    assert result.success is True
    assert sent == ["Hi <@1> and <@3>, also <@3>"]


@pytest.mark.parametrize("flag", [None, False, "false", "off"])
@pytest.mark.asyncio
async def test_send_is_unchanged_when_the_setting_is_off(flag):
    adapter = _adapter(resolve=flag)
    channel, sent = _text_channel()
    _wire(adapter, channel)

    await adapter.send("555", "Hi @alice")

    assert sent == ["Hi @alice"]


@pytest.mark.asyncio
async def test_send_leaves_dms_and_unknown_names_alone():
    adapter = _adapter(resolve=True)
    channel, sent = _text_channel(guild=None)
    _wire(adapter, channel)
    await adapter.send("555", "Hi @alice")

    channel, sent_guild = _text_channel()
    _wire(adapter, channel)
    await adapter.send("555", "mail me@alice.example, ping @nobody, keep <@1> and @everyone")

    assert sent == ["Hi @alice"]
    assert sent_guild == ["mail me@alice.example, ping @nobody, keep <@1> and @everyone"]


@pytest.mark.asyncio
async def test_forum_starter_posts_are_resolved_too():
    adapter = _adapter(resolve=True)
    thread = SimpleNamespace(id=77, thread=SimpleNamespace(id=77, send=AsyncMock()), message=SimpleNamespace(id=78))
    forum = SimpleNamespace(id=555, guild=GUILD, type=SimpleNamespace(value=15), create_thread=AsyncMock(return_value=thread))
    _wire(adapter, forum)

    await adapter.send("555", "Question for @alice")

    assert forum.create_thread.await_args.kwargs["content"] == "Question for <@1>"


@pytest.mark.asyncio
async def test_final_stream_edit_is_resolved_but_a_partial_one_is_not():
    """A mid-stream prefix such as ``@Su`` can match the wrong member, so only the final edit resolves."""
    adapter = _adapter(resolve=True)
    edits = []
    message = SimpleNamespace(id=42, edit=AsyncMock(side_effect=lambda *, content: edits.append(content)))
    channel, _ = _text_channel()
    channel.get_partial_message = MagicMock(return_value=message)
    _wire(adapter, channel)

    await adapter.edit_message("555", "42", "Hi @alice", finalize=False)
    await adapter.edit_message("555", "42", "Hi @alice", finalize=True)

    assert edits == ["Hi @alice", "Hi <@1>"]


def test_the_longest_name_wins_and_emails_are_untouched():
    assert resolve_outbound_mentions("@Support Bot and @Support", GUILD) == "<@3> and <@2>"
    assert resolve_outbound_mentions("write to team@support.example", GUILD) == "write to team@support.example"
    assert resolve_outbound_mentions("no mention", GUILD) == "no mention"


def test_the_yaml_key_reaches_the_adapter():
    seeded = _apply_yaml_config({}, {"resolve_outbound_mentions": True})

    assert seeded == {"resolve_outbound_mentions": True}
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***", extra=seeded))
    assert adapter._resolves_outbound_mentions() is True
    assert _adapter(resolve=None)._resolves_outbound_mentions() is False
