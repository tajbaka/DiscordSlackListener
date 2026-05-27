from __future__ import annotations

from pathlib import Path

from discord_slack_listener.browser_dom import message_from_browser_payload
from discord_slack_listener.conf import Settings


def settings() -> Settings:
    return Settings(
        discord_channel_url="https://discord.com/channels/579151027169918986/885567780924043334",
        slack_webhook_url="https://hooks.slack.test/abc",
        slack_matches_webhook_url="https://hooks.slack.test/matches",
        bridge_name="test-bridge",
        discord_guild_ids=frozenset({579151027169918986}),
        discord_channel_ids=frozenset({885567780924043334}),
        browser_profile_dir=Path("data/discord-profile"),
        database_path=Path("data/messages.sqlite3"),
        browser_headless=False,
        poll_interval_seconds=5.0,
        notify_recent_seconds=120,
        no_message_alert_seconds=10800,
        supervisor_restart_delay_seconds=30,
        git_update_poll_seconds=300,
        active_start_hour=9,
        active_end_hour=21,
        active_timezone="America/Toronto",
        backfill_days=90,
        backfill_scrolls=200,
        backfill_settle_seconds=1.0,
        ignore_bots=True,
        ignore_author_keywords=(),
        match_keywords=("FedRAMP",),
        match_regex=None,
        llm_api_key="",
        ai_model="",
        llm_api_base="",
        log_level="INFO",
    )


def test_message_from_browser_payload_builds_jump_url_and_links() -> None:
    msg = message_from_browser_payload(
        {
            "id": "1507550930504257536",
            "channel_id": "885567780924043334",
            "author_name": "pete-gov",
            "avatar_url": "https://cdn.discordapp.com/avatars/1114932911578300416/avatar.webp?size=160",
            "content": "FedRAMP is just one step.",
            "created_at": "2026-05-23T01:09:10.774Z",
            "links": [{"filename": "example", "url": "https://example.test"}],
        },
        settings(),
    )

    assert msg.id == "1507550930504257536"
    assert msg.guild_id == 579151027169918986
    assert msg.channel_id == 885567780924043334
    assert msg.channel_name == "885567780924043334"
    assert msg.author_name == "pete-gov"
    assert msg.author_id == 1114932911578300416
    assert msg.author_key == "discord_user:1114932911578300416"
    assert msg.jump_url.endswith("/885567780924043334/1507550930504257536")
    assert msg.attachments[0].url == "https://example.test"
