# Configuration

Configuration is loaded from `.env` at the project root.

| Variable | Required | Purpose |
| --- | --- | --- |
| `DISCORD_CHANNEL_URL` | yes | Discord web channel URL to open. |
| `SLACK_WEBHOOK_URL` | yes | Ops Slack webhook for errors, restarts, and health alerts. |
| `SLACK_MATCHES_WEBHOOK_URL` | yes | Slack webhook for matched Discord messages. Falls back to `SLACK_REPLIES_WEBHOOK_URL` if set. |
| `SLACK_DM_WEBHOOK_URL` | no | Slack webhook for unread Discord DM alerts. Falls back to `SLACK_WEBHOOK_URL`. |
| `DISCORD_GUILD_IDS` | no | Comma-separated guild/server IDs to allow and build URL. |
| `DISCORD_CHANNEL_IDS` | no | Comma-separated channel IDs to allow and build URL. |
| `DISCORD_DM_LISTENER_ENABLED` | no | Opens a second Discord tab for DM unread alerts. Defaults to `true`. |
| `DISCORD_DM_URL` | no | Discord DM/home URL to open. Defaults to `https://discord.com/channels/@me`. |
| `BROWSER_PROFILE_DIR` | no | Persistent Chromium profile path. |
| `DATABASE_PATH` | no | SQLite database path. Defaults to `data/messages.sqlite3`. |
| `BROWSER_HEADLESS` | no | Defaults to `false`; keep false for interactive login. |
| `POLL_INTERVAL_SECONDS` | no | DOM polling interval. Defaults to `5`. |
| `NOTIFY_RECENT_SECONDS` | no | Live listener only Slack-notifies messages this recent relative to startup. |
| `NO_MESSAGE_ALERT_SECONDS` | no | Ops alert when no new Discord message is stored for this many seconds. Defaults to `10800` (3 hours). |
| `SUPERVISOR_RESTART_DELAY_SECONDS` | no | Delay before supervised mode restarts after a crash. Defaults to `30`. |
| `GIT_UPDATE_POLL_SECONDS` | no | Supervisor git fetch/pull interval. Set `0` to disable. Defaults to `300`. |
| `ACTIVE_START_HOUR` | no | Supervised listener start hour, inclusive. Defaults to `9`. |
| `ACTIVE_END_HOUR` | no | Supervised listener end hour, exclusive. Defaults to `21`. |
| `ACTIVE_TIMEZONE` | no | Timezone for active hours. Defaults to `America/Toronto`. |
| `BACKFILL_DAYS` | no | Backfill window. Defaults to `90`. |
| `BACKFILL_SCROLLS` | no | Safety cap for upward scroll attempts. Defaults to `200`. |
| `BACKFILL_SETTLE_SECONDS` | no | Wait after each backfill scroll. Defaults to `1.0`. |
| `BRIDGE_NAME` | no | Label rendered in Slack context. |
| `IGNORE_BOTS` | no | Defaults to `true`; skips bot/webhook authors. |
| `IGNORE_AUTHOR_KEYWORDS` | no | Comma-separated author-name substrings to ignore, e.g. `Boundera`. |
| `MATCH_KEYWORDS` | no | Comma-separated case-insensitive substrings. |
| `MATCH_REGEX` | no | Case-insensitive Python regex. |
| `LLM_API_KEY` | no | Reserved for later LLM-based criteria. |
| `AI_MODEL` | no | Reserved model name for later LLM-based criteria. |
| `LLM_API_BASE` | no | Optional LLM base URL for compatible providers. |
| `LOG_LEVEL` | no | Defaults to `INFO`. |

## Known Channel

From the Discord HTML shared during scaffolding:

- Channel: `#fedramp`
- Channel ID: `885567780924043334`
- Likely guild/server ID: `579151027169918986`

Use either direct URL:

```dotenv
DISCORD_CHANNEL_URL=https://discord.com/channels/579151027169918986/885567780924043334
```

or IDs:

```dotenv
DISCORD_GUILD_IDS=579151027169918986
DISCORD_CHANNEL_IDS=885567780924043334
```

The listener parses the live Discord web DOM at runtime. The IDs are used for
allowlisting and for building Discord jump links in Slack.

## Discord Login

No Discord bot token is used. On first run, the app opens Chromium to the
channel URL. Log into Discord there. The session persists under
`BROWSER_PROFILE_DIR` for future runs.

## Filtering Model

Filtering runs in this order:

1. Drop bot/webhook authors when `IGNORE_BOTS=true`.
2. Drop author names matching `IGNORE_AUTHOR_KEYWORDS`.
3. Apply guild allowlist if `DISCORD_GUILD_IDS` is set.
4. Apply channel allowlist if `DISCORD_CHANNEL_IDS` is set.
5. Apply keyword/regex content prefilters to message text and attachment
   filenames.
6. Forward to matches Slack only when product intent is strong enough.

Future business criteria should go in
`discord_slack_listener/criteria.py:should_forward_message` or
`discord_slack_listener/lead_intent.py`.

## SQLite

Every seen message is stored in `discord_messages`, keyed by Discord message
ID. Live listener mode stores messages and posts to Slack only for recent,
matched, not-yet-forwarded messages. Backfill mode stores messages only and
never posts to Slack.

## Slack Routing

Use two or three incoming webhooks:

- `SLACK_WEBHOOK_URL`: ops channel for crashes, browser/session trouble,
  supervised restarts, and stale-listener alerts.
- `SLACK_MATCHES_WEBHOOK_URL`: lead/match channel for Discord messages that
  meet the forwarding criteria.
- `SLACK_DM_WEBHOOK_URL`: unread DM alerts. If unset, DM alerts use
  `SLACK_WEBHOOK_URL`.

The listener also accepts OpenOutreach-style `SLACK_REPLIES_WEBHOOK_URL` as a
fallback for `SLACK_MATCHES_WEBHOOK_URL`.

## DM Listener

The live listener opens a second Chromium tab at `DISCORD_DM_URL`, defaulting
to `https://discord.com/channels/@me`. On startup it stores the current DM
state without notifying. After that, it posts to Slack when a DM conversation
transitions from read to unread, then stays quiet for that conversation until
the unread state clears and reappears.

## Supervisor And Catch-Up

Run supervised mode with:

```bash
.venv/bin/python -m discord_slack_listener daemon
```

The supervisor starts the browser listener only during `ACTIVE_START_HOUR` to
`ACTIVE_END_HOUR`. Outside that window, it terminates the child process and
sleeps until the next active start.

The supervisor also checks the current git branch's upstream every
`GIT_UPDATE_POLL_SECONDS`. When the checkout is behind, it runs
`git pull --ff-only`, installs `requirements.txt` if that file changed, posts
an ops Slack notification, and restarts the listener child.

On startup, supervised mode prompts for catch-up backfill. If `yes` is not
entered within 5 seconds, it defaults to no and launches the supervisor. Catch-up
mode is also available directly:

```bash
.venv/bin/python -m discord_slack_listener backfill --catchup
```

Catch-up mode does not stop by days. It scrolls backward until the visible
history stops changing for repeated scans, or the configured scroll cap is hit.
