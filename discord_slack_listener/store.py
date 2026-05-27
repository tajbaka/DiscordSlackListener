from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from discord_slack_listener.models import DiscordMessage
from discord_slack_listener.models import DiscordAttachment
from discord_slack_listener.lead_intent import LeadIntent


@dataclass(frozen=True)
class UpsertResult:
    created: bool
    changed: bool


class MessageStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS discord_messages (
                id TEXT PRIMARY KEY,
                guild_id INTEGER,
                guild_name TEXT NOT NULL,
                channel_id INTEGER,
                channel_name TEXT NOT NULL,
                author_id INTEGER,
                author_name TEXT NOT NULL,
                author_key TEXT NOT NULL DEFAULT '',
                author_is_bot INTEGER NOT NULL,
                content TEXT NOT NULL,
                jump_url TEXT NOT NULL,
                created_at TEXT NOT NULL,
                attachments_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                forwarded_at TEXT,
                matched_reason TEXT,
                lead_intent_level TEXT,
                lead_intent_score INTEGER,
                lead_intent_areas TEXT,
                lead_intent_reasons TEXT,
                lead_intent_evaluated_at TEXT
            )
            """
        )
        self._ensure_column("discord_messages", "lead_intent_level", "TEXT")
        self._ensure_column("discord_messages", "author_key", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("discord_messages", "lead_intent_score", "INTEGER")
        self._ensure_column("discord_messages", "lead_intent_areas", "TEXT")
        self._ensure_column("discord_messages", "lead_intent_reasons", "TEXT")
        self._ensure_column("discord_messages", "lead_intent_evaluated_at", "TEXT")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_discord_messages_created_at "
            "ON discord_messages(created_at)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_discord_messages_channel_created "
            "ON discord_messages(channel_id, created_at)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_discord_messages_author_created "
            "ON discord_messages(author_key, created_at)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_discord_messages_lead_intent "
            "ON discord_messages(lead_intent_score, lead_intent_level)"
        )
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def upsert_message(
        self,
        message: DiscordMessage,
        *,
        lead_intent: LeadIntent | None = None,
    ) -> UpsertResult:
        now = _utc_now()
        attachments_json = json.dumps(
            [{"filename": a.filename, "url": a.url} for a in message.attachments],
            ensure_ascii=False,
        )
        existing = self.conn.execute(
            "SELECT content, attachments_json FROM discord_messages WHERE id = ?",
            (message.id,),
        ).fetchone()
        if existing is None:
            self.conn.execute(
                """
                INSERT INTO discord_messages (
                    id, guild_id, guild_name, channel_id, channel_name,
                    author_id, author_name, author_key, author_is_bot, content, jump_url,
                    created_at, attachments_json, first_seen_at, last_seen_at,
                    lead_intent_level, lead_intent_score, lead_intent_areas,
                    lead_intent_reasons, lead_intent_evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.guild_id,
                    message.guild_name,
                    message.channel_id,
                    message.channel_name,
                    message.author_id,
                    message.author_name,
                    message.author_key,
                    int(message.author_is_bot),
                    message.content,
                    message.jump_url,
                    _dt_to_iso(message.created_at),
                    attachments_json,
                    now,
                    now,
                    lead_intent.level if lead_intent else None,
                    lead_intent.score if lead_intent else None,
                    json.dumps(lead_intent.product_areas) if lead_intent else None,
                    json.dumps(lead_intent.reasons) if lead_intent else None,
                    now if lead_intent else None,
                ),
            )
            self.conn.commit()
            return UpsertResult(created=True, changed=True)

        changed = (
            existing["content"] != message.content
            or existing["attachments_json"] != attachments_json
        )
        self.conn.execute(
            """
            UPDATE discord_messages
            SET guild_id = ?, guild_name = ?, channel_id = ?, channel_name = ?,
                author_id = ?, author_name = ?, author_key = ?, author_is_bot = ?,
                content = ?, jump_url = ?, created_at = ?, attachments_json = ?,
                last_seen_at = ?,
                lead_intent_level = COALESCE(?, lead_intent_level),
                lead_intent_score = COALESCE(?, lead_intent_score),
                lead_intent_areas = COALESCE(?, lead_intent_areas),
                lead_intent_reasons = COALESCE(?, lead_intent_reasons),
                lead_intent_evaluated_at = COALESCE(?, lead_intent_evaluated_at)
            WHERE id = ?
            """,
            (
                message.guild_id,
                message.guild_name,
                message.channel_id,
                message.channel_name,
                message.author_id,
                message.author_name,
                message.author_key,
                int(message.author_is_bot),
                message.content,
                message.jump_url,
                _dt_to_iso(message.created_at),
                attachments_json,
                now,
                lead_intent.level if lead_intent else None,
                lead_intent.score if lead_intent else None,
                json.dumps(lead_intent.product_areas) if lead_intent else None,
                json.dumps(lead_intent.reasons) if lead_intent else None,
                now if lead_intent else None,
                message.id,
            ),
        )
        self.conn.commit()
        return UpsertResult(created=False, changed=changed)

    def has_been_forwarded(self, message_id: str) -> bool:
        row = self.conn.execute(
            "SELECT forwarded_at FROM discord_messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        return bool(row and row["forwarded_at"])

    def mark_forwarded(self, message_id: str, reason: str) -> None:
        self.conn.execute(
            """
            UPDATE discord_messages
            SET forwarded_at = ?, matched_reason = ?
            WHERE id = ?
            """,
            (_utc_now(), reason, message_id),
        )
        self.conn.commit()

    def count_messages(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM discord_messages").fetchone()
        return int(row["c"])

    def all_message_ids(self) -> set[str]:
        rows = self.conn.execute("SELECT id FROM discord_messages").fetchall()
        return {str(row["id"]) for row in rows}

    def recent_same_author_messages(
        self,
        *,
        author_key: str,
        before_created_at: str,
        limit: int = 10,
    ) -> list[DiscordMessage]:
        if not author_key:
            return []
        rows = self.conn.execute(
            """
            SELECT * FROM discord_messages
            WHERE author_key = ? AND created_at < ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (author_key, before_created_at, limit),
        ).fetchall()
        return [_row_to_message(row) for row in reversed(rows)]

    def recent_channel_messages(
        self,
        *,
        channel_id: int | None,
        before_created_at: str,
        limit: int = 10,
    ) -> list[DiscordMessage]:
        if channel_id is None:
            return []
        rows = self.conn.execute(
            """
            SELECT * FROM discord_messages
            WHERE channel_id = ? AND created_at < ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (channel_id, before_created_at, limit),
        ).fetchall()
        return [_row_to_message(row) for row in reversed(rows)]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dt_to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _row_to_message(row: sqlite3.Row) -> DiscordMessage:
    try:
        attachment_rows = json.loads(row["attachments_json"] or "[]")
    except json.JSONDecodeError:
        attachment_rows = []
    return DiscordMessage(
        id=row["id"],
        guild_id=row["guild_id"],
        guild_name=row["guild_name"],
        channel_id=row["channel_id"],
        channel_name=row["channel_name"],
        author_id=row["author_id"],
        author_name=row["author_name"],
        author_key=row["author_key"] or f"display:{row['author_name'].lower()}",
        author_is_bot=bool(row["author_is_bot"]),
        content=row["content"],
        jump_url=row["jump_url"],
        created_at=datetime.fromisoformat(row["created_at"]),
        attachments=tuple(
            DiscordAttachment(
                filename=str(a.get("filename") or "attachment"),
                url=str(a.get("url") or ""),
            )
            for a in attachment_rows
            if isinstance(a, dict)
        ),
    )
