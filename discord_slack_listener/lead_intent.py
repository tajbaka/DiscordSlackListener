from __future__ import annotations

import re
from dataclasses import dataclass
from collections.abc import Sequence

from discord_slack_listener.models import DiscordMessage


@dataclass(frozen=True)
class LeadIntent:
    level: str
    score: int
    product_areas: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def is_interesting(self) -> bool:
        return self.score >= 4

    @property
    def should_notify(self) -> bool:
        return self.score >= 6

    @property
    def summary(self) -> str:
        if self.level == "none":
            return "none"
        areas = ", ".join(self.product_areas) or "general"
        reasons = "; ".join(self.reasons)
        return f"{self.level}: {areas} ({reasons})"


PRODUCT_AREA_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ConMon", re.compile(r"\b(conmon|continuous monitoring|iscm|vdr|vulnerability detection|vuln(?:erability)? scan|nessus|tenable)\b", re.I)),
    ("GRC/compliance tracking", re.compile(r"\b(grc|tracking fedramp compliance|change management|incident tracking|compliance platform)\b", re.I)),
    ("SSP/scoping", re.compile(r"\b(ssp|system security plan|appendix a|boundary|scope|corporate services|customer responsibility matrix|crm)\b", re.I)),
    ("Evidence/package", re.compile(r"\b(evidence|boe|boa|artifact|package|oscal|export|3pao|assessor)\b", re.I)),
    ("KSI/20x", re.compile(r"\b(20x|ksi|key security indicator|fedramp 20x|collaborative continuous monitoring)\b", re.I)),
    ("FedRAMP path/cost", re.compile(r"\b(cost|how long|how much|consultant|small team|employees|become fedramp|fedramp equivalent|authorization)\b", re.I)),
    ("AI SaaS boundary", re.compile(r"\b(ai platform|agent|chatgpt|claude|copilot|cui|web search|safely put cui|fedramp moderate equivalency)\b", re.I)),
)

BUYING_INTENT_PATTERNS: tuple[tuple[int, re.Pattern[str], str], ...] = (
    (5, re.compile(r"\b(looking for|recommend|options for|anyone using|getting pushed|evaluating|need a|want a|shopping for)\b", re.I), "actively evaluating tools/options"),
    (4, re.compile(r"\b(is it possible|what would it cost|how long would it take|how much does|what are some ways|how do we)\b", re.I), "asks implementation/cost/path question"),
    (3, re.compile(r"\b(working on|working towards|we currently|we need to|have to work towards|trying to)\b", re.I), "describes active project"),
    (2, re.compile(r"\b(manual work|without manual|pain|confusing|difficult|friction|second guessing|not clear|unclear)\b", re.I), "expresses compliance pain"),
)

NEGATIVE_CONTEXT = re.compile(
    r"\b(joke|lol|haha|meme|town hall|article|conference|job|usajobs|notices?)\b",
    re.I,
)


def classify_product_intent(message: DiscordMessage) -> LeadIntent:
    """Classify whether a Discord message suggests interest in a FedRAMP product.

    This is deliberately deterministic so the listener can run without an LLM.
    An LLM classifier can later wrap or replace this function while preserving
    the LeadIntent shape.
    """
    text = message.text_for_matching.strip()
    if not text:
        return LeadIntent("none", 0, (), ("empty message",))

    product_areas = tuple(
        area for area, pattern in PRODUCT_AREA_PATTERNS if pattern.search(text)
    )
    if not product_areas:
        return LeadIntent("none", 0, (), ("no FedRAMP product area matched",))

    score = min(len(product_areas), 3)
    reasons: list[str] = [f"matched product area: {area}" for area in product_areas[:3]]

    for points, pattern, reason in BUYING_INTENT_PATTERNS:
        if pattern.search(text):
            score += points
            reasons.append(reason)
            break

    if "FedRAMP path/cost" in product_areas and re.search(r"\b(cost|how long|how much|employees)\b", text, re.I):
        score += 2
        reasons.append("explicit cost/timeline/team-size signal")

    if "SSP/scoping" in product_areas and re.search(
        r"\b(question|does this fall|boundary|scope|corporate services|section 7|connect(?:ing)? to)\b",
        text,
        re.I,
    ):
        score += 3
        reasons.append("explicit SSP/scoping question")

    if "GRC/compliance tracking" in product_areas and "ConMon" in product_areas:
        score += 2
        reasons.append("direct GRC + ConMon tooling overlap")

    if "AI SaaS boundary" in product_areas and re.search(r"\bworking|platform|safely|moderate equivalency\b", text, re.I):
        score += 2
        reasons.append("AI SaaS FedRAMP boundary/readiness signal")

    if NEGATIVE_CONTEXT.search(text) and score < 6:
        score = max(score - 2, 1)
        reasons.append("reduced for likely general discussion/context")

    if score >= 9:
        level = "high"
    elif score >= 6:
        level = "strong"
    elif score >= 4:
        level = "possible"
    elif score >= 2:
        level = "context"
    else:
        level = "none"

    return LeadIntent(
        level=level,
        score=score,
        product_areas=product_areas,
        reasons=tuple(reasons),
    )


FOLLOWUP_CONTEXT_PATTERNS = re.compile(
    r"\b(for both parts|what would help|how much|how long|cost|can you clarify|"
    r"what do you mean|that query|makes sense|thanks|appreciate)\b",
    re.I,
)


def classify_product_intent_with_context(
    message: DiscordMessage,
    *,
    same_author_messages: Sequence[DiscordMessage] = (),
    channel_messages: Sequence[DiscordMessage] = (),
) -> LeadIntent:
    """Classify current message using recent same-author and channel context.

    The current message remains primary. Context can upgrade short follow-ups
    when the same author is already discussing a product-relevant FedRAMP need.
    """
    current = classify_product_intent(message)
    if current.score >= 6:
        return current

    same_author_intents = [
        classify_product_intent(m)
        for m in same_author_messages
        if m.id != message.id
    ]
    channel_intents = [
        classify_product_intent(m)
        for m in channel_messages
        if m.id != message.id
    ]
    best_same_author = max(same_author_intents, key=lambda i: i.score, default=None)
    best_channel = max(channel_intents, key=lambda i: i.score, default=None)

    text = message.text_for_matching.strip()
    word_count = len(text.split())
    is_followup = bool(FOLLOWUP_CONTEXT_PATTERNS.search(text)) or (
        text.endswith("?") and word_count <= 14
    )

    if (
        best_same_author
        and best_same_author.score >= 6
        and is_followup
        and not _is_generic_fallback_author(message.author_key)
    ):
        score = max(current.score, min(best_same_author.score - 1, 8))
        areas = current.product_areas or best_same_author.product_areas
        reasons = list(current.reasons if current.level != "none" else ())
        reasons.append("upgraded from recent same-author product-interest context")
        return LeadIntent(
            level=_level_for_score(score),
            score=score,
            product_areas=areas,
            reasons=tuple(reasons),
        )

    if (
        best_channel
        and best_channel.score >= 8
        and is_followup
        and current.score >= 2
    ):
        score = max(current.score, 4)
        areas = current.product_areas or best_channel.product_areas
        reasons = list(current.reasons)
        reasons.append("supported by recent channel product-interest context")
        return LeadIntent(
            level=_level_for_score(score),
            score=score,
            product_areas=areas,
            reasons=tuple(reasons),
        )

    return current


def _level_for_score(score: int) -> str:
    if score >= 9:
        return "high"
    if score >= 6:
        return "strong"
    if score >= 4:
        return "possible"
    if score >= 2:
        return "context"
    return "none"


def _is_generic_fallback_author(author_key: str) -> bool:
    return author_key in {"display:user", "display:unknown", "display:"}
