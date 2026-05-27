from __future__ import annotations

from datetime import datetime, timezone

from discord_slack_listener.models import DiscordAttachment, DiscordMessage
from discord_slack_listener.store import MessageStore


def message(message_id: str = "1", content: str = "hello") -> DiscordMessage:
    return DiscordMessage(
        id=message_id,
        guild_id=123,
        guild_name="Discord",
        channel_id=456,
        channel_name="fedramp",
        author_id=None,
        author_name="user",
        author_key="display:user",
        author_is_bot=False,
        content=content,
        jump_url="https://discord.com/channels/123/456/1",
        created_at=datetime.now(timezone.utc),
        attachments=(DiscordAttachment(filename="link", url="https://example.test"),),
    )


def test_store_upsert_is_idempotent(tmp_path) -> None:
    store = MessageStore(tmp_path / "messages.sqlite3")

    first = store.upsert_message(message())
    second = store.upsert_message(message())

    assert first.created is True
    assert second.created is False
    assert store.count_messages() == 1


def test_store_tracks_forwarded_state(tmp_path) -> None:
    store = MessageStore(tmp_path / "messages.sqlite3")
    store.upsert_message(message("42"))

    assert store.has_been_forwarded("42") is False

    store.mark_forwarded("42", "matched keyword: FedRAMP")

    assert store.has_been_forwarded("42") is True


def test_store_fetches_recent_same_author_messages(tmp_path) -> None:
    store = MessageStore(tmp_path / "messages.sqlite3")
    store.upsert_message(message("1", "first"))
    store.upsert_message(message("2", "second"))

    recent = store.recent_same_author_messages(
        author_key="display:user",
        before_created_at=message("3").created_at.isoformat(),
        limit=5,
    )

    assert [m.id for m in recent] == ["1", "2"]
    assert all(m.author_key == "display:user" for m in recent)
