from __future__ import annotations

from datetime import datetime, timezone

from discord_slack_listener.lead_intent import classify_product_intent
from discord_slack_listener.lead_intent import classify_product_intent_with_context
from discord_slack_listener.models import DiscordMessage


def message(content: str) -> DiscordMessage:
    return DiscordMessage(
        id="1",
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
        attachments=(),
    )


def same_author_message(content: str, message_id: str) -> DiscordMessage:
    msg = message(content)
    return DiscordMessage(
        id=message_id,
        guild_id=msg.guild_id,
        guild_name=msg.guild_name,
        channel_id=msg.channel_id,
        channel_name=msg.channel_name,
        author_id=msg.author_id,
        author_name=msg.author_name,
        author_key="discord_user:42",
        author_is_bot=msg.author_is_bot,
        content=msg.content,
        jump_url=msg.jump_url,
        created_at=msg.created_at,
        attachments=msg.attachments,
    )


def test_catches_grc_conmon_tool_search() -> None:
    intent = classify_product_intent(message(
        "I'm looking for options for a GRC and ConMon platform that can be "
        "used for tracking FedRAMP compliance and continuous actions such as "
        "change management and incident tracking."
    ))

    assert intent.level in {"strong", "high"}
    assert "GRC/compliance tracking" in intent.product_areas
    assert "ConMon" in intent.product_areas


def test_catches_small_team_cost_path_question() -> None:
    intent = classify_product_intent(message(
        "Is it possible to become/have a FedRAMP equivalent with only 4 "
        "employees? What would it roughly cost and how long would it take?"
    ))

    assert intent.level in {"strong", "high"}
    assert "FedRAMP path/cost" in intent.product_areas


def test_catches_ai_saas_cui_boundary_question() -> None:
    intent = classify_product_intent(message(
        "We are working on securing an AI platform toward FedRAMP moderate "
        "equivalency. Users can copy/paste CUI and the agent performs web search."
    ))

    assert intent.level in {"strong", "high"}
    assert "AI SaaS boundary" in intent.product_areas


def test_catches_conmon_tool_evaluation() -> None:
    intent = classify_product_intent(message(
        "Anyone using Tenable/Nessus for ConMon? Getting pushed in that direction "
        "and looking for feedback from people who have used them."
    ))

    assert intent.level in {"possible", "strong", "high"}
    assert "ConMon" in intent.product_areas


def test_catches_ssp_scoping_pain_as_possible() -> None:
    intent = classify_product_intent(message(
        "SSP question. I have a FedRAMP CSO and a backend application connecting "
        "to a non-cloud API. Does this fall under Corporate Services in Section 7?"
    ))

    assert intent.level in {"possible", "strong", "high"}
    assert "SSP/scoping" in intent.product_areas


def test_strong_product_intent_is_required_for_notification() -> None:
    intent = classify_product_intent(message(
        "Good morning! Looking at the FedRAMP Authorization Boundary Guidance. "
        "Trying to reconcile CM-12 with where to store GRC evidence like "
        "Tenable scan results, POA&M data, and document artifacts before they "
        "are uploaded to the GRC platform as evidence."
    ))

    assert intent.should_notify is True


def test_general_keyword_reply_is_not_notification_worthy() -> None:
    intent = classify_product_intent(message(
        "Are you already FedRAMP moderate or in the process of it?"
    ))

    assert intent.should_notify is False


def test_context_upgrades_short_followup_from_same_author() -> None:
    previous = same_author_message(
        "Is it possible to become FedRAMP equivalent with only 4 employees? "
        "What would it roughly cost and how long would it take?",
        "prev",
    )
    current = same_author_message("What would help narrow it down?", "current")

    intent = classify_product_intent_with_context(
        current,
        same_author_messages=(previous,),
    )

    assert intent.level in {"possible", "strong"}
    assert "FedRAMP path/cost" in intent.product_areas
    assert any("same-author" in reason for reason in intent.reasons)
