from __future__ import annotations

import json
import logging
import time
import traceback
from contextlib import contextmanager
from urllib import request
from urllib.error import URLError

from discord_slack_listener.conf import Settings
from discord_slack_listener.lead_intent import LeadIntent
from discord_slack_listener.models import DiscordMessage

logger = logging.getLogger(__name__)

_RECENT_ERRORS: dict[tuple[str, str, str], float] = {}
_ERROR_DEDUPE_WINDOW_SECONDS = 300


def _escape_mrkdwn(value: str) -> str:
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _post_to_slack(webhook_url: str, payload: dict, label: str) -> None:
    if not webhook_url:
        return

    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                logger.warning("Slack webhook returned HTTP %s for %s", resp.status, label)
    except (URLError, TimeoutError) as exc:
        logger.warning("Slack webhook failed for %s: %s", label, exc)


class SlackNotifier:
    def __init__(self, settings: Settings):
        self.settings = settings

    def post_message(
        self,
        message: DiscordMessage,
        *,
        reason: str,
        lead_intent: LeadIntent | None = None,
    ) -> None:
        if not self.settings.slack_matches_webhook_url:
            logger.info(
                "Slack matches webhook unset; matched Discord message %s not posted",
                message.id,
            )
            return

        payload = build_slack_payload(
            message,
            bridge_name=self.settings.bridge_name,
            reason=reason,
            lead_intent=lead_intent,
        )
        _post_to_slack(
            self.settings.slack_matches_webhook_url,
            payload,
            f"match ({message.id})",
        )

    def notify_degraded(self, *, title: str, detail: str) -> None:
        if not self.settings.slack_webhook_url:
            return

        payload = build_degraded_payload(
            bridge_name=self.settings.bridge_name,
            title=title,
            detail=detail,
        )
        _post_to_slack(self.settings.slack_webhook_url, payload, f"degraded ({title})")

    def notify_error(
        self,
        workflow: str,
        exc: BaseException,
        *,
        context: dict | None = None,
    ) -> None:
        if not self.settings.slack_webhook_url:
            return

        payload = build_error_payload(
            workflow,
            exc,
            context=context,
        )
        if payload is None:
            return
        _post_to_slack(self.settings.slack_webhook_url, payload, f"error ({workflow})")

    @contextmanager
    def notify_on_error(self, workflow: str, context: dict | None = None):
        try:
            yield
        except Exception as exc:
            self.notify_error(workflow, exc, context=context)
            raise


def build_slack_payload(
    message: DiscordMessage,
    *,
    bridge_name: str,
    reason: str,
    lead_intent: LeadIntent | None = None,
) -> dict:
    author = _escape_mrkdwn(message.author_name)
    location = _escape_mrkdwn(message.location_label)
    snippet = _escape_mrkdwn(message.snippet or "(no text)")
    fallback = f"Discord message from {message.author_name} in {message.location_label}"

    context = [
        {"type": "mrkdwn", "text": f"*Bridge:* {_escape_mrkdwn(bridge_name)}"},
        {"type": "mrkdwn", "text": f"*Source:* {location}"},
        {"type": "mrkdwn", "text": f"*Reason:* {_escape_mrkdwn(reason)}"},
    ]
    if lead_intent and lead_intent.is_interesting:
        context.append({
            "type": "mrkdwn",
            "text": f"*Product intent:* {_escape_mrkdwn(lead_intent.summary)}",
        })

    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":speech_balloon: *Discord message from {author}*",
            },
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": f"> {snippet}"}},
        {"type": "context", "elements": context},
    ]

    if message.attachments:
        links = "\n".join(
            f"<{_escape_mrkdwn(a.url)}|{_escape_mrkdwn(a.filename or 'attachment')}>"
            for a in message.attachments[:5]
        )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Attachments:*\n{links}"},
        })

    if message.jump_url:
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open in Discord"},
                    "url": message.jump_url,
                },
            ],
        })

    return {"text": fallback, "blocks": blocks}


def build_degraded_payload(*, bridge_name: str, title: str, detail: str) -> dict:
    bridge = _escape_mrkdwn(bridge_name)
    safe_title = _escape_mrkdwn(title)
    safe_detail = _escape_mrkdwn(detail)
    fallback = f"Discord listener alert: {title}"
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":warning: *{safe_title}*"},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": safe_detail}},
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"*Bridge:* {bridge}"}],
        },
    ]
    return {"text": fallback, "blocks": blocks}


def build_error_payload(
    workflow: str,
    exc: BaseException,
    *,
    context: dict | None = None,
) -> dict | None:
    exc_type = type(exc).__name__
    tb_frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    last_frame = (
        f"{tb_frames[-1].filename}:{tb_frames[-1].lineno}:{tb_frames[-1].name}"
        if tb_frames
        else ""
    )
    key = (workflow, exc_type, last_frame)

    now = time.time()
    for recent_key in list(_RECENT_ERRORS):
        if now - _RECENT_ERRORS[recent_key] > _ERROR_DEDUPE_WINDOW_SECONDS:
            del _RECENT_ERRORS[recent_key]
    if key in _RECENT_ERRORS:
        logger.debug("Slack error notify deduped: %s", key)
        return None
    _RECENT_ERRORS[key] = now

    tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    if len(tb_text) > 2800:
        tb_text = tb_text[:2800] + "\n...(truncated)"

    exc_summary = f"{exc_type}: {exc}"
    safe_workflow = _escape_mrkdwn(workflow)
    safe_summary = _escape_mrkdwn(exc_summary[:200])
    fallback = f"Discord listener error in {workflow}: {exc_summary[:200]}"

    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":rotating_light: *{safe_workflow} crashed* - `{safe_summary}`",
            },
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": f"```{tb_text}```"}},
    ]
    if context:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"*{_escape_mrkdwn(str(k))}:* `{_escape_mrkdwn(str(v))}`",
                    }
                    for k, v in context.items()
                ],
            }
        )

    return {"text": fallback, "blocks": blocks}
