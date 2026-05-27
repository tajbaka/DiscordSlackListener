from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from discord_slack_listener.app import is_active_now, seconds_until_active
from discord_slack_listener.conf import Settings


def settings(**overrides) -> Settings:
    values = {
        "discord_channel_url": "https://discord.com/channels/1/10",
        "slack_webhook_url": "https://hooks.slack.test/ops",
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


def test_active_hours_include_start_and_exclude_end() -> None:
    cfg = settings()
    tz = ZoneInfo("America/Toronto")

    assert is_active_now(cfg, now=datetime(2026, 5, 26, 9, 0, tzinfo=tz))
    assert is_active_now(cfg, now=datetime(2026, 5, 26, 20, 59, tzinfo=tz))
    assert not is_active_now(cfg, now=datetime(2026, 5, 26, 21, 0, tzinfo=tz))


def test_seconds_until_next_active_start() -> None:
    cfg = settings()
    tz = ZoneInfo("America/Toronto")

    wait = seconds_until_active(
        cfg,
        now=datetime(2026, 5, 26, 21, 30, tzinfo=tz),
    )

    assert wait == 11.5 * 60 * 60
