from __future__ import annotations

from datetime import datetime, timezone

from discord_slack_listener.models import (
    DiscordAttachment,
    DiscordDirectMessageConversation,
    DiscordMessage,
)
from discord_slack_listener.slack import build_degraded_payload, build_error_payload
from discord_slack_listener.slack import build_dm_alert_payload, build_slack_payload


def test_build_slack_payload_contains_message_context() -> None:
    message = DiscordMessage(
        id=123,
        guild_id=1,
        guild_name="Acme",
        channel_id=10,
        channel_name="leads",
        author_id=42,
        author_name="Arian",
        author_key="discord_user:42",
        author_is_bot=False,
        content="hello <world>",
        jump_url="https://discord.com/channels/1/10/123",
        created_at=datetime.now(timezone.utc),
        attachments=(DiscordAttachment(filename="brief.pdf", url="https://x.test/brief"),),
    )

    payload = build_slack_payload(
        message,
        bridge_name="test-bridge",
        reason="matched keyword: hello",
    )

    assert payload["text"] == "Discord message from Arian in Acme / #leads"
    rendered = str(payload["blocks"])
    assert "test-bridge" in rendered
    assert "matched keyword: hello" in rendered
    assert "hello &lt;world&gt;" in rendered
    assert "brief.pdf" in rendered
    assert "Open in Discord" in rendered


def test_build_degraded_payload_contains_bridge_and_detail() -> None:
    payload = build_degraded_payload(
        bridge_name="discord-prod",
        title="Discord listener has seen no new messages",
        detail="No messages for 3.0 hours.",
    )

    rendered = str(payload["blocks"])
    assert "discord-prod" in rendered
    assert "No messages for 3.0 hours." in rendered


def test_build_dm_alert_payload_contains_recipient_and_link() -> None:
    payload = build_dm_alert_payload(
        DiscordDirectMessageConversation(
            id="123",
            recipient_name="Jane <Acme>",
            unread=True,
            jump_url="https://discord.com/channels/@me/123",
        ),
        bridge_name="test-bridge",
    )

    rendered = str(payload["blocks"])
    assert payload["text"] == "Discord DM from Jane <Acme>"
    assert "Jane &lt;Acme&gt;" in rendered
    assert "Open Discord DM" in rendered


def test_build_error_payload_dedupes_same_exception() -> None:
    try:
        raise RuntimeError("browser broke")
    except RuntimeError as exc:
        first = build_error_payload("listener", exc, context={"attempt": 1})
        second = build_error_payload("listener", exc, context={"attempt": 1})

    assert first is not None
    assert second is None
    assert "browser broke" in str(first["blocks"])
