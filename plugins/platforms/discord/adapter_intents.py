"""Gateway-intent helpers for the Discord adapter: when Server Members is needed and what to tell the
operator when Discord rejects a privileged intent.

Re-exported from ``plugins.platforms.discord.adapter`` (callers and tests import them there).
"""


def _needs_server_members_intent(
    allowed_user_ids: set[str] | list[str] | None, allowed_role_ids: set[str] | list[str] | None,
) -> bool:
    """True when Server Members intent is needed: username allowlist entries (not IDs / ``*``)
    or role allowlists needing member lookups. Message Content is always requested."""
    entries = allowed_user_ids or ()
    if any(entry != "*" and not str(entry).isdigit() for entry in entries):
        return True
    return bool(allowed_role_ids)


def _format_privileged_intents_guidance(*, needs_members: bool) -> str:
    """Actionable fix text when Discord rejects privileged Gateway Intents."""
    lines = [
        "Discord rejected the connection because privileged Gateway Intents "
        "are not enabled for this bot in the Developer Portal.",
        "Hermes is requesting:",
        "  - Message Content Intent (required to read message text)",
    ]
    if needs_members:
        lines.append(
            "  - Server Members Intent (required for username allowlists, "
            "DISCORD_ALLOWED_ROLES and/or discord.resolve_outbound_mentions)"
        )
    lines.extend(
        [
            "Fix: https://discord.com/developers/applications → your application "
            "→ Bot → Privileged Gateway Intents → enable the intent(s) listed "
            "above → Save Changes, then restart the gateway.",
            "Docs: https://hermes-agent.nousresearch.com/docs/user-guide/messaging/discord",
        ]
    )
    return "\n".join(lines)
