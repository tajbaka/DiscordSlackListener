from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from discord_slack_listener.conf import Settings
from discord_slack_listener.criteria import should_forward_message
from discord_slack_listener.models import DiscordAttachment, DiscordMessage


def settings(**overrides) -> Settings:
    values = {
        "discord_channel_url": "https://discord.com/channels/1/10",
        "slack_webhook_url": "https://hooks.slack.test/abc",
        "slack_matches_webhook_url": "https://hooks.slack.test/matches",
        "bridge_name": "test-bridge",
        "discord_guild_ids": frozenset(),
        "discord_channel_ids": frozenset(),
        "browser_profile_dir": Path("data/discord-profile"),
        "database_path": Path("data/messages.sqlite3"),
        "browser_headless": False,
        "poll_interval_seconds": 5.0,
        "notify_recent_seconds": 120,
        "no_message_alert_seconds": 10800,
        "supervisor_restart_delay_seconds": 30,
        "active_start_hour": 9,
        "active_end_hour": 21,
        "active_timezone": "America/Toronto",
        "backfill_days": 90,
        "backfill_scrolls": 200,
        "backfill_settle_seconds": 1.0,
        "ignore_bots": True,
        "match_keywords": (),
        "match_regex": None,
        "llm_api_key": "",
        "ai_model": "",
        "llm_api_base": "",
        "log_level": "INFO",
    }
    values.update(overrides)
    return Settings(**values)


def message(**overrides) -> DiscordMessage:
    values = {
        "id": 123,
        "guild_id": 1,
        "guild_name": "Acme",
        "channel_id": 10,
        "channel_name": "leads",
        "author_id": 42,
        "author_name": "sender",
        "author_key": "discord_user:42",
        "author_is_bot": False,
        "content": "hello world",
        "jump_url": "https://discord.com/channels/1/10/123",
        "created_at": datetime.now(timezone.utc),
        "attachments": (),
    }
    values.update(overrides)
    return DiscordMessage(**values)


def test_forwards_when_no_content_filters_are_configured() -> None:
    decision = should_forward_message(message(), settings())

    assert decision.should_forward is True
    assert decision.reason == "no content filters configured"


def test_ignores_bot_authors_by_default() -> None:
    decision = should_forward_message(message(author_is_bot=True), settings())

    assert decision.should_forward is False
    assert "bot" in decision.reason


def test_applies_guild_and_channel_allowlists() -> None:
    cfg = settings(discord_guild_ids=frozenset({2}), discord_channel_ids=frozenset({10}))

    decision = should_forward_message(message(guild_id=1, channel_id=10), cfg)

    assert decision.should_forward is False
    assert decision.reason == "guild not allowed"


def test_matches_keywords_case_insensitively() -> None:
    cfg = settings(match_keywords=("urgent",))

    decision = should_forward_message(message(content="This is URGENT"), cfg)

    assert decision.should_forward is True
    assert decision.reason == "matched keyword: urgent"


def test_matches_regex() -> None:
    cfg = settings(match_regex=re.compile(r"\bSOC\s?2\b", re.IGNORECASE))

    decision = should_forward_message(message(content="Need help with soc2"), cfg)

    assert decision.should_forward is True
    assert decision.reason == "matched regex"


def test_attachment_filenames_participate_in_matching() -> None:
    cfg = settings(match_keywords=("invoice",))
    msg = message(
        content="",
        attachments=(DiscordAttachment(filename="invoice.pdf", url="https://x.test/i"),),
    )

    decision = should_forward_message(msg, cfg)

    assert decision.should_forward is True
