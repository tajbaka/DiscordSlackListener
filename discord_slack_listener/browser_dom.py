from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from discord_slack_listener.conf import Settings
from discord_slack_listener.models import DiscordAttachment, DiscordMessage


EXTRACT_MESSAGES_SCRIPT = """
() => {
  const items = Array.from(document.querySelectorAll('li[id^="chat-messages-"]'));
  let lastAuthor = '';
  let lastAvatarUrl = '';
  return items.map((item) => {
    const article = item.querySelector('[role="article"]');
    if (!article) return null;

    const idParts = item.id.split('-');
    const messageId = idParts[idParts.length - 1] || '';
    const channelId = idParts[idParts.length - 2] || '';

    const labelledBy = (article.getAttribute('aria-labelledby') || '').split(/\\s+/);
    const usernameId = labelledBy.find((id) => id.startsWith('message-username-'));
    const timestampId = labelledBy.find((id) => id.startsWith('message-timestamp-'));
    const usernameEl = usernameId
      ? document.getElementById(usernameId)
      : article.querySelector('h3 [id^="message-username-"]');
    const timestampEl = timestampId
      ? document.getElementById(timestampId)
      : article.querySelector('time[id^="message-timestamp-"]');

    const contentEl = article.querySelector(
      '[class*="contents_"] > div[id^="message-content-"], [class*="contents_"] div[id^="message-content-"]'
    );
    const content = contentEl ? (contentEl.innerText || contentEl.textContent || '').trim() : '';
    const usernameSource = usernameEl
      ? (usernameEl.matches('[data-text]') ? usernameEl : usernameEl.querySelector('[data-text]'))
      : null;
    const author = usernameSource
      ? (usernameSource.getAttribute('data-text') || usernameSource.textContent || '').trim()
      : lastAuthor;
    if (author) lastAuthor = author;
    const avatar = article.querySelector('[class*="contents_"] img[class*="avatar_"], img[class*="avatar_"]');
    const avatarUrl = avatar ? (avatar.getAttribute('src') || '') : lastAvatarUrl;
    if (avatarUrl) lastAvatarUrl = avatarUrl;
    const createdAt = timestampEl ? (timestampEl.getAttribute('datetime') || '') : '';

    const links = Array.from(article.querySelectorAll('a[href^="http"]')).map((a) => ({
      filename: (a.textContent || a.getAttribute('title') || a.href || 'link').trim(),
      url: a.href,
    }));

    return {
      id: messageId,
      channel_id: channelId,
      author_name: author,
      avatar_url: avatarUrl,
      content,
      created_at: createdAt,
      links,
    };
  }).filter((msg) => msg && msg.id && (msg.content || msg.links.length));
}
"""


def _first_id(ids: frozenset[int]) -> int | None:
    return next(iter(ids)) if ids else None


def _parse_datetime(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _channel_name_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    if len(parts) >= 3 and parts[0] == "channels":
        return parts[2]
    return "discord"


def _author_key(author_name: str, avatar_url: str) -> tuple[int | None, str]:
    match = None
    if avatar_url:
        import re

        match = re.search(r"/(?:avatars|users)/(\d+)/", avatar_url)
    if match:
        user_id = int(match.group(1))
        return user_id, f"discord_user:{user_id}"
    normalized = " ".join(author_name.lower().split()) or "unknown"
    return None, f"display:{normalized}"


def message_from_browser_payload(
    payload: dict,
    settings: Settings,
) -> DiscordMessage:
    channel_id_raw = payload.get("channel_id") or ""
    try:
        channel_id = int(channel_id_raw)
    except (TypeError, ValueError):
        channel_id = _first_id(settings.discord_channel_ids)

    guild_id = _first_id(settings.discord_guild_ids)
    jump_url = ""
    if guild_id and channel_id and payload.get("id"):
        jump_url = f"https://discord.com/channels/{guild_id}/{channel_id}/{payload['id']}"
    author_name = str(payload.get("author_name") or "Unknown")
    author_id, author_key = _author_key(author_name, str(payload.get("avatar_url") or ""))

    return DiscordMessage(
        id=str(payload.get("id") or ""),
        guild_id=guild_id,
        guild_name="Discord",
        channel_id=channel_id,
        channel_name=_channel_name_from_url(settings.discord_channel_url),
        author_id=author_id,
        author_name=author_name,
        author_key=author_key,
        author_is_bot=False,
        content=str(payload.get("content") or ""),
        jump_url=jump_url,
        created_at=_parse_datetime(str(payload.get("created_at") or "")),
        attachments=tuple(
            DiscordAttachment(
                filename=str(link.get("filename") or "link"),
                url=str(link.get("url") or ""),
            )
            for link in payload.get("links", [])
            if link.get("url")
        ),
    )
