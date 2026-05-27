from __future__ import annotations

from dataclasses import dataclass

from discord_slack_listener.conf import Settings
from discord_slack_listener.models import DiscordMessage


@dataclass(frozen=True)
class CriteriaDecision:
    should_forward: bool
    reason: str


def should_forward_message(
    message: DiscordMessage,
    settings: Settings,
) -> CriteriaDecision:
    """Decide whether a Discord message should be forwarded to Slack.

    This is the main extension point for future business criteria. Keep
    Discord API details out of this function; it should operate on the
    normalized DiscordMessage dataclass so it stays easy to test.
    """
    if settings.ignore_bots and message.author_is_bot:
        return CriteriaDecision(False, "ignored bot/webhook author")

    if settings.discord_guild_ids and message.guild_id not in settings.discord_guild_ids:
        return CriteriaDecision(False, "guild not allowed")

    if (
        settings.discord_channel_ids
        and message.channel_id not in settings.discord_channel_ids
    ):
        return CriteriaDecision(False, "channel not allowed")

    text = message.text_for_matching
    if not settings.has_content_filters:
        return CriteriaDecision(True, "no content filters configured")

    lowered = text.lower()
    for keyword in settings.match_keywords:
        if keyword.lower() in lowered:
            return CriteriaDecision(True, f"matched keyword: {keyword}")

    if settings.match_regex and settings.match_regex.search(text):
        return CriteriaDecision(True, "matched regex")

    return CriteriaDecision(False, "no content criteria matched")
