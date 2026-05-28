from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DiscordAttachment:
    filename: str
    url: str


@dataclass(frozen=True)
class DiscordMessage:
    id: str
    guild_id: int | None
    guild_name: str
    channel_id: int | None
    channel_name: str
    author_id: int | None
    author_name: str
    author_key: str
    author_is_bot: bool
    content: str
    jump_url: str
    created_at: datetime
    attachments: tuple[DiscordAttachment, ...] = ()

    @property
    def location_label(self) -> str:
        if self.guild_name:
            return f"{self.guild_name} / #{self.channel_name}"
        return f"Direct message / #{self.channel_name}"

    @property
    def text_for_matching(self) -> str:
        attachment_text = " ".join(a.filename for a in self.attachments)
        return " ".join(part for part in (self.content, attachment_text) if part)

    @property
    def snippet(self) -> str:
        text = (self.content or "").strip().replace("\n", " ")
        if not text and self.attachments:
            text = " ".join(a.url for a in self.attachments)
        if len(text) > 900:
            return text[:897] + "..."
        return text


@dataclass(frozen=True)
class DiscordDirectMessageConversation:
    id: str
    recipient_name: str
    unread: bool
    jump_url: str
