# Architecture

The app is intentionally smaller than OpenOutreach because it has one job:
open Discord in a browser, listen to visible incoming channel messages, and
notify Slack when a message matches criteria.

## Flow

```text
Playwright persistent Chromium profile
  -> Discord channel DOM
  -> EXTRACT_MESSAGES_SCRIPT
  -> MessageStore.upsert_message()
  -> classify_product_intent_with_context()
  -> should_forward_message()
  -> SlackNotifier.post_message()
  -> Slack incoming webhook
```

## Modules

- `app.py` owns the Playwright browser lifecycle, page polling, and dedupe.
- `browser_dom.py` extracts Discord message rows from the live DOM and converts
  them into normalized dataclasses.
- `store.py` persists messages in SQLite, idempotent on Discord message ID,
  tracks forwarded state, and exposes recent same-author/channel context.
- `lead_intent.py` scores whether a message looks like interest in a
  FedRAMP software product using the current message plus recent context.
- `models.py` contains Discord-message dataclasses used by the rest of the app.
- `criteria.py` is the only place forwarding rules should live.
- `slack.py` builds and posts Slack Block Kit payloads.
- `conf.py` loads env vars into a typed settings object.

## Operational Notes

The process is long-running and should be supervised by your host's process
manager when deployed. For local development, `make run` is enough.

Slack delivery failures are logged and swallowed so a temporary Slack outage
does not disconnect the Discord listener.
