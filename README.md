# Discord Slack Listener

Lean Discord-to-Slack listener inspired by the OpenOutreach realtime path.
It opens Discord in a persistent Playwright browser profile, watches the live
channel DOM for inbound messages, and posts matched messages to Slack. It also
has a supervised mode for restart/error/health alerts. It does not send Discord
messages.

## What It Does

1. Opens the configured Discord channel URL in Chromium.
2. Reuses a persistent browser profile so you only log in once.
3. Extracts visible message rows from the Discord DOM.
4. Stores every seen message in SQLite, idempotent by Discord message ID.
5. Stores `author_key` for same-author context when Discord exposes a stable ID.
6. Scores FedRAMP product/tool interest using the message plus recent context.
7. Dedupes Slack notifications by stored forwarded state.
8. Applies criteria from `discord_slack_listener/criteria.py`.
9. Posts matching new messages to a matches Slack webhook.
10. Posts listener errors, restarts, and stale-session health alerts to ops Slack.

The criteria layer uses channel/guild allowlists and keyword/regex prefilters,
then requires strong FedRAMP-product intent before posting to the matches
Slack channel. Add future business logic in `should_forward_message` or
`lead_intent.py` without changing the Discord client or Slack poster.

## Setup

```bash
python3.13 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in:

- `SLACK_WEBHOOK_URL` for ops/errors/health
- `SLACK_MATCHES_WEBHOOK_URL` for matched Discord messages
- `DISCORD_CHANNEL_URL`, or `DISCORD_GUILD_IDS` + `DISCORD_CHANNEL_IDS`
- optionally `MATCH_KEYWORDS`, `MATCH_REGEX`
- optionally `IGNORE_AUTHOR_KEYWORDS=Boundera` to suppress internal replies

On first run, log into Discord in the opened Chromium window. The session is
stored in `BROWSER_PROFILE_DIR`.

## Run

```bash
make run
```

or:

```bash
.venv/bin/python -m discord_slack_listener
```

For a long-running process that restarts the listener after unexpected crashes
and only runs it during active hours:

```bash
make daemon
```

or:

```bash
.venv/bin/python -m discord_slack_listener daemon
```

Active hours default to `ACTIVE_START_HOUR=9`, `ACTIVE_END_HOUR=21`, and
`ACTIVE_TIMEZONE=America/Toronto`. When `daemon` starts, it asks whether to run
a catch-up backfill first. If no `yes` is entered within 5 seconds, it skips
backfill and starts the supervisor.

The supervisor also checks the current git upstream every
`GIT_UPDATE_POLL_SECONDS` seconds, default `300`. If new commits are available,
it runs `git pull --ff-only`, installs `requirements.txt` changes, posts an ops
Slack notification, and restarts the listener child. Set
`GIT_UPDATE_POLL_SECONDS=0` to disable.

## Backfill

Backfill defaults to the last 90 days:

```bash
make backfill
```

or:

```bash
.venv/bin/python -m discord_slack_listener backfill --days 90
```

Catch-up mode ignores the day window and scrolls until Discord stops yielding
older unseen messages, or until the safety scroll cap is reached:

```bash
.venv/bin/python -m discord_slack_listener backfill --catchup
```

Messages are stored in `DATABASE_PATH`, default `data/messages.sqlite3`.
Backfill does not post to Slack.

Query it directly:

```bash
sqlite3 data/messages.sqlite3 \
  "select created_at, author_name, substr(content, 1, 120) from discord_messages order by created_at desc limit 20;"
```

Find likely product-interest messages:

```bash
sqlite3 data/messages.sqlite3 \
  "select lead_intent_score, lead_intent_level, author_name, substr(content,1,120), jump_url from discord_messages where lead_intent_score >= 4 order by lead_intent_score desc, created_at desc;"
```

## Test

```bash
make test
```

## Project Structure

```text
discord_slack_listener/
  __main__.py       # module entrypoint
  app.py            # Playwright browser lifecycle
  browser_dom.py    # Discord DOM extraction
  conf.py           # env loading and typed settings
  criteria.py       # forwarding rules and future extension point
  lead_intent.py    # FedRAMP product/tool interest classifier
  models.py         # normalized message dataclasses
  store.py          # SQLite persistence
  slack.py          # Slack payloads, match posts, ops/error alerts
  logging.py        # logging setup
docs/
  configuration.md
tests/
```
