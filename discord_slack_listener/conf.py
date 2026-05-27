from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from re import Pattern
import re

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT_DIR / ".env"


def _truthy(value: str | None, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv_strings(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _csv_ints(value: str | None) -> frozenset[int]:
    ids: set[int] = set()
    for part in _csv_strings(value):
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise ValueError(f"Expected numeric Discord id, got {part!r}") from exc
    return frozenset(ids)


@dataclass(frozen=True)
class Settings:
    discord_channel_url: str
    slack_webhook_url: str
    slack_matches_webhook_url: str
    bridge_name: str
    discord_guild_ids: frozenset[int]
    discord_channel_ids: frozenset[int]
    browser_profile_dir: Path
    database_path: Path
    browser_headless: bool
    poll_interval_seconds: float
    notify_recent_seconds: int
    no_message_alert_seconds: int
    supervisor_restart_delay_seconds: int
    git_update_poll_seconds: int
    active_start_hour: int
    active_end_hour: int
    active_timezone: str
    backfill_days: int
    backfill_scrolls: int
    backfill_settle_seconds: float
    ignore_bots: bool
    ignore_author_keywords: tuple[str, ...]
    match_keywords: tuple[str, ...]
    match_regex: Pattern[str] | None
    llm_api_key: str
    ai_model: str
    llm_api_base: str
    log_level: str

    @property
    def has_content_filters(self) -> bool:
        return bool(self.match_keywords or self.match_regex)


def load_settings() -> Settings:
    load_dotenv(ENV_FILE)
    regex_raw = os.getenv("MATCH_REGEX", "").strip()
    regex = re.compile(regex_raw, re.IGNORECASE) if regex_raw else None
    guild_ids = _csv_ints(os.getenv("DISCORD_GUILD_IDS"))
    channel_ids = _csv_ints(os.getenv("DISCORD_CHANNEL_IDS"))
    channel_url = os.getenv("DISCORD_CHANNEL_URL", "").strip()
    if not channel_url and guild_ids and channel_ids:
        channel_url = (
            "https://discord.com/channels/"
            f"{next(iter(guild_ids))}/{next(iter(channel_ids))}"
        )

    return Settings(
        discord_channel_url=channel_url,
        slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL", "").strip(),
        slack_matches_webhook_url=(
            os.getenv("SLACK_MATCHES_WEBHOOK_URL", "").strip()
            or os.getenv("SLACK_REPLIES_WEBHOOK_URL", "").strip()
        ),
        bridge_name=os.getenv("BRIDGE_NAME", "discord-slack-listener").strip()
        or "discord-slack-listener",
        discord_guild_ids=guild_ids,
        discord_channel_ids=channel_ids,
        browser_profile_dir=Path(
            os.getenv("BROWSER_PROFILE_DIR", "data/discord-profile").strip()
        ),
        database_path=Path(os.getenv("DATABASE_PATH", "data/messages.sqlite3").strip()),
        browser_headless=_truthy(os.getenv("BROWSER_HEADLESS"), default=False),
        poll_interval_seconds=float(os.getenv("POLL_INTERVAL_SECONDS", "5") or 5),
        notify_recent_seconds=int(os.getenv("NOTIFY_RECENT_SECONDS", "120") or 120),
        no_message_alert_seconds=int(
            os.getenv("NO_MESSAGE_ALERT_SECONDS", "10800") or 10800
        ),
        supervisor_restart_delay_seconds=int(
            os.getenv("SUPERVISOR_RESTART_DELAY_SECONDS", "30") or 30
        ),
        git_update_poll_seconds=int(os.getenv("GIT_UPDATE_POLL_SECONDS", "300") or 300),
        active_start_hour=int(os.getenv("ACTIVE_START_HOUR", "9") or 9),
        active_end_hour=int(os.getenv("ACTIVE_END_HOUR", "21") or 21),
        active_timezone=os.getenv("ACTIVE_TIMEZONE", "America/Toronto").strip()
        or "America/Toronto",
        backfill_days=int(os.getenv("BACKFILL_DAYS", "90") or 90),
        backfill_scrolls=int(os.getenv("BACKFILL_SCROLLS", "200") or 200),
        backfill_settle_seconds=float(os.getenv("BACKFILL_SETTLE_SECONDS", "1.0") or 1.0),
        ignore_bots=_truthy(os.getenv("IGNORE_BOTS"), default=True),
        ignore_author_keywords=_csv_strings(os.getenv("IGNORE_AUTHOR_KEYWORDS")),
        match_keywords=_csv_strings(os.getenv("MATCH_KEYWORDS")),
        match_regex=regex,
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        ai_model=os.getenv("AI_MODEL", "").strip(),
        llm_api_base=os.getenv("LLM_API_BASE", "").strip(),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )
