from __future__ import annotations

import argparse
import logging
import select
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psutil
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Page
from playwright.sync_api import sync_playwright

from discord_slack_listener.browser_dom import (
    EXTRACT_MESSAGES_SCRIPT,
    message_from_browser_payload,
)
from discord_slack_listener.conf import ROOT_DIR, Settings, load_settings
from discord_slack_listener.criteria import should_forward_message
from discord_slack_listener.lead_intent import classify_product_intent_with_context
from discord_slack_listener.lead_intent import LeadIntent
from discord_slack_listener.logging import setup_logging
from discord_slack_listener.slack import SlackNotifier
from discord_slack_listener.single_instance import SingleInstanceGuard
from discord_slack_listener.store import MessageStore

logger = logging.getLogger(__name__)

LISTENER_MARKER = "-m discord_slack_listener listen"
DAEMON_MARKER = "-m discord_slack_listener daemon"


def run_browser_listener(settings: Settings) -> None:
    if not settings.discord_channel_url:
        raise SystemExit("DISCORD_CHANNEL_URL or DISCORD_GUILD_IDS + DISCORD_CHANNEL_IDS is required")

    settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
    slack = SlackNotifier(settings)
    store = MessageStore(settings.database_path)
    seen_message_ids: set[str] = store.all_message_ids()
    started_at = datetime.now(timezone.utc)
    last_new_message_at = started_at
    no_message_alerted = False
    extraction_failures = 0
    recent_cutoff = started_at - timedelta(seconds=settings.notify_recent_seconds)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(settings.browser_profile_dir),
            headless=settings.browser_headless,
            viewport={"width": 1440, "height": 1000},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(settings.discord_channel_url, wait_until="domcontentloaded")
        logger.info("Opened Discord channel: %s", settings.discord_channel_url)
        logger.info("If Discord asks you to log in, complete login in the browser window.")

        try:
            page.wait_for_selector('li[id^="chat-messages-"]', timeout=60_000)
        except PlaywrightTimeoutError:
            logger.warning(
                "No Discord messages found yet. This usually means the browser is not logged in "
                "or the channel is inaccessible."
            )
            slack.notify_degraded(
                title="Discord session unavailable",
                detail=(
                    "The listener opened the channel but could not find any Discord "
                    "message rows within 60 seconds. The browser may be logged out, "
                    "the session may be challenged, or the channel may be inaccessible."
                ),
            )

        first_scan = True
        while True:
            try:
                payloads = extract_payloads(page)
                extraction_failures = 0
            except Exception as exc:
                extraction_failures += 1
                logger.exception("Failed to extract Discord messages from DOM")
                if extraction_failures == 1 or extraction_failures % 12 == 0:
                    slack.notify_error(
                        "listener:extract",
                        exc,
                        context={"consecutive_failures": extraction_failures},
                    )
                time.sleep(settings.poll_interval_seconds)
                continue

            if first_scan:
                for payload in payloads:
                    message = message_from_browser_payload(payload, settings)
                    lead_intent = classify_message_with_context(store, message)
                    store.upsert_message(message, lead_intent=lead_intent)
                    seen_message_ids.add(message.id)
                logger.info(
                    "Seeded and stored %d existing Discord messages; listening for new ones",
                    len(seen_message_ids),
                )
                first_scan = False
                time.sleep(settings.poll_interval_seconds)
                continue

            for payload in payloads:
                message = message_from_browser_payload(payload, settings)
                if message.id in seen_message_ids:
                    continue
                lead_intent = classify_message_with_context(store, message)
                store.upsert_message(message, lead_intent=lead_intent)
                last_new_message_at = datetime.now(timezone.utc)
                no_message_alerted = False
                if lead_intent.is_interesting:
                    logger.info(
                        "Discord message %s product-intent %s",
                        message.id,
                        lead_intent.summary,
                    )
                seen_message_ids.add(message.id)

                if message.created_at < recent_cutoff:
                    logger.debug("Discord message %s stored but not notified; older than startup window", message.id)
                    continue
                if store.has_been_forwarded(message.id):
                    logger.debug("Discord message %s already forwarded", message.id)
                    continue

                exclusion_reason = store.slack_notification_exclusion_reason(message)
                if exclusion_reason:
                    logger.info(
                        "Discord message %s not forwarded to Slack: %s",
                        message.id,
                        exclusion_reason,
                    )
                    continue

                decision = should_forward_message(message, settings)
                if not decision.should_forward:
                    if (
                        decision.reason == "no content criteria matched"
                        and lead_intent.should_notify
                    ):
                        logger.info(
                            "Discord message %s bypassed content prefilter; product-intent %s",
                            message.id,
                            lead_intent.summary,
                        )
                    else:
                        logger.debug("Discord message %s skipped: %s", message.id, decision.reason)
                        continue

                if not lead_intent.should_notify:
                    logger.debug("Discord message %s skipped: %s", message.id, decision.reason)
                    continue

                reason = (
                    "qualified product intent after prefilter: "
                    if decision.should_forward
                    else "qualified product intent from active conversation: "
                ) + decision.reason
                logger.info("Discord message %s matched: %s", message.id, reason)
                slack.post_message(
                    message,
                    reason=reason,
                    lead_intent=lead_intent,
                )
                store.mark_forwarded(message.id, reason)

            if settings.no_message_alert_seconds > 0:
                now = datetime.now(timezone.utc)
                quiet_for = now - last_new_message_at
                if (
                    not no_message_alerted
                    and quiet_for.total_seconds() >= settings.no_message_alert_seconds
                ):
                    hours = quiet_for.total_seconds() / 3600
                    logger.warning(
                        "No new Discord messages stored for %.0f seconds; sending ops alert",
                        quiet_for.total_seconds(),
                    )
                    slack.notify_degraded(
                        title="Discord listener has seen no new messages",
                        detail=(
                            f"No new Discord messages have been stored for {hours:.1f} hours. "
                            "This may be normal channel quiet, but it can also indicate a stale "
                            "browser session or broken Discord DOM selectors."
                        ),
                    )
                    no_message_alerted = True

            time.sleep(settings.poll_interval_seconds)


def run_backfill(
    settings: Settings,
    *,
    days: int | None = None,
    max_scrolls: int | None = None,
    catchup: bool = False,
) -> int:
    if not settings.discord_channel_url:
        raise SystemExit("DISCORD_CHANNEL_URL or DISCORD_GUILD_IDS + DISCORD_CHANNEL_IDS is required")

    days = settings.backfill_days if days is None else days
    max_scrolls = settings.backfill_scrolls if max_scrolls is None else max_scrolls
    cutoff = None if catchup else datetime.now(timezone.utc) - timedelta(days=days)
    settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
    store = MessageStore(settings.database_path)
    before_count = store.count_messages()

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(settings.browser_profile_dir),
            headless=settings.browser_headless,
            viewport={"width": 1440, "height": 1000},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(settings.discord_channel_url, wait_until="domcontentloaded")
        logger.info("Opened Discord channel for backfill: %s", settings.discord_channel_url)
        if catchup:
            logger.info("Backfilling until Discord stops yielding older unseen messages")
        else:
            logger.info("Backfilling messages from the last %d days", days)

        try:
            page.wait_for_selector('li[id^="chat-messages-"]', timeout=60_000)
        except PlaywrightTimeoutError:
            logger.warning(
                "No Discord messages found yet. This usually means the browser is not logged in "
                "or the channel is inaccessible."
            )
            return 0

        stagnant_scans = 0
        previous_total = store.count_messages()
        previous_oldest: datetime | None = None
        for scroll_index in range(max_scrolls + 1):
            messages = [
                message_from_browser_payload(payload, settings)
                for payload in extract_payloads(page)
            ]
            for message in messages:
                store.upsert_message(
                    message,
                    lead_intent=classify_message_with_context(store, message),
                )

            oldest = min((m.created_at for m in messages), default=None)
            total = store.count_messages()
            logger.info(
                "Backfill scan %d/%d: visible=%d stored_total=%d oldest_visible=%s",
                scroll_index,
                max_scrolls,
                len(messages),
                total,
                oldest.isoformat() if oldest else "none",
            )
            if cutoff and oldest and oldest <= cutoff:
                logger.info("Reached cutoff %s; stopping backfill", cutoff.isoformat())
                break
            if total == previous_total and oldest == previous_oldest:
                stagnant_scans += 1
            else:
                stagnant_scans = 0
            if stagnant_scans >= 5:
                logger.info("No new/older messages after %d scans; stopping backfill", stagnant_scans)
                break
            if scroll_index >= max_scrolls:
                logger.info("Reached max scroll cap (%d); stopping backfill", max_scrolls)
                break

            previous_total = total
            previous_oldest = oldest
            scroll_messages_to_top(page)
            page.wait_for_timeout(settings.backfill_settle_seconds * 1000)

        context.close()

    added = store.count_messages() - before_count
    logger.info("Backfill complete: added %d new rows, total %d", added, store.count_messages())
    return added


def run_supervisor(settings: Settings) -> None:
    slack = SlackNotifier(settings)
    if should_run_catchup_prompt(timeout_seconds=5):
        with slack.notify_on_error("supervisor:catchup-backfill"):
            run_backfill(settings, max_scrolls=settings.backfill_scrolls, catchup=True)
    else:
        logger.info("Catch-up backfill skipped")

    child: subprocess.Popen | None = None
    restart_count = 0
    off_hours_alerted = False
    last_update_check = 0.0
    while True:
        try:
            if should_check_for_updates(settings, last_update_check):
                last_update_check = time.monotonic()
                if pull_git_update(slack):
                    if child and child.poll() is None:
                        logger.info("Stopping Discord listener child after git update")
                        child.terminate()
                        try:
                            child.wait(timeout=20)
                        except subprocess.TimeoutExpired:
                            logger.warning("Discord listener child did not terminate; killing")
                            child.kill()
                            child.wait(timeout=20)
                    child = None

            if not is_active_now(settings):
                if child and child.poll() is None:
                    logger.info("Outside active hours; stopping Discord listener child")
                    child.terminate()
                    try:
                        child.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        logger.warning("Discord listener child did not terminate; killing")
                        child.kill()
                        child.wait(timeout=20)
                    child = None
                if not off_hours_alerted:
                    slack.notify_degraded(
                        title="Discord listener paused outside active hours",
                        detail=active_hours_detail(settings),
                    )
                    off_hours_alerted = True
                sleep_for = min(seconds_until_active(settings), 300)
                logger.info("Outside active hours; sleeping %.0f seconds", sleep_for)
                time.sleep(max(1, sleep_for))
                continue

            off_hours_alerted = False
            if child and child.poll() is None:
                time.sleep(5)
                continue

            if child is not None:
                code = child.returncode
                restart_count += 1
                logger.warning("Discord listener child exited with code %s", code)
                slack.notify_degraded(
                    title="Discord listener child exited",
                    detail=(
                        f"The listener child exited with code {code}. "
                        f"Restart #{restart_count} will run in "
                        f"{settings.supervisor_restart_delay_seconds} seconds."
                    ),
                )
                child = None
                time.sleep(settings.supervisor_restart_delay_seconds)

            logger.info("Starting Discord listener child")
            child = subprocess.Popen(
                [sys.executable, "-m", "discord_slack_listener", "listen"],
                cwd=str(ROOT_DIR),
            )
            logger.info("Discord listener child started with pid=%s", child.pid)
        except KeyboardInterrupt:
            logger.info("Discord listener supervisor stopped")
            if child and child.poll() is None:
                child.terminate()
            raise
        except Exception as exc:
            logger.exception("Discord supervisor loop failed")
            slack.notify_error(
                "listener:supervisor",
                exc,
                context={"restart_count": restart_count},
            )
            time.sleep(settings.supervisor_restart_delay_seconds)


def should_check_for_updates(settings: Settings, last_update_check: float) -> bool:
    if settings.git_update_poll_seconds <= 0:
        return False
    return (time.monotonic() - last_update_check) >= settings.git_update_poll_seconds


def pull_git_update(slack: SlackNotifier) -> bool:
    """Fast-forward this checkout from its upstream branch.

    Returns True when code changed and the listener child should be restarted.
    """
    if not is_git_checkout():
        return False

    upstream = git_upstream_ref()
    if upstream is None:
        logger.debug("No git upstream tracking branch; skipping update check")
        return False

    fetch = run_git(["fetch"])
    if fetch.returncode != 0:
        detail = fetch.stderr.strip() or fetch.stdout.strip()
        logger.warning("Discord listener git fetch failed: %s", detail)
        slack.notify_degraded(
            title="Discord listener git fetch failed",
            detail=f"```{detail[:2500]}```",
        )
        return False

    behind = commits_behind(upstream)
    if behind == 0:
        logger.debug("Discord listener up to date with %s", upstream)
        return False

    before = git_head()
    logger.warning("Found %d new Discord listener commit(s) on %s; pulling", behind, upstream)
    pull = run_git(["pull", "--ff-only"])
    if pull.returncode != 0:
        detail = pull.stderr.strip() or pull.stdout.strip()
        logger.warning("Discord listener git pull failed: %s", detail)
        slack.notify_degraded(
            title="Discord listener git pull failed",
            detail=f"```{detail[:2500]}```",
        )
        return False

    after = git_head()
    changed_files = git_changed_files(before, after)
    if "requirements.txt" in changed_files:
        install = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        if install.returncode != 0:
            detail = install.stderr.strip() or install.stdout.strip()
            logger.warning("Discord listener requirements install failed: %s", detail)
            slack.notify_degraded(
                title="Discord listener dependency install failed",
                detail=f"```{detail[:2500]}```",
            )
            return False

    logger.warning("Updated Discord listener %s -> %s; restarting child", before[:8], after[:8])
    slack.notify_degraded(
        title="Discord listener updated code",
        detail=f"Pulled {behind} commit(s) from `{upstream}` and restarted the listener child.",
    )
    return True


def run_git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        check=False,
    )


def is_git_checkout() -> bool:
    try:
        result = run_git(["rev-parse", "--is-inside-work-tree"])
    except FileNotFoundError:
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def git_upstream_ref() -> str | None:
    result = run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def commits_behind(upstream: str) -> int:
    result = run_git(["rev-list", "--count", f"HEAD..{upstream}"])
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip() or 0)
    except ValueError:
        return 0


def git_head() -> str:
    result = run_git(["rev-parse", "HEAD"])
    return result.stdout.strip() if result.returncode == 0 else ""


def git_changed_files(before: str, after: str) -> set[str]:
    if not before or not after or before == after:
        return set()
    result = run_git(["diff", "--name-only", before, after])
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def should_run_catchup_prompt(*, timeout_seconds: int) -> bool:
    prompt = (
        "Run catch-up backfill before launching daemon? "
        f"[y/N] auto-no in {timeout_seconds}s: "
    )
    if not sys.stdin.isatty():
        logger.info("No interactive TTY; skipping catch-up backfill prompt")
        return False
    print(prompt, end="", flush=True)
    readable, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
    if not readable:
        print("no")
        return False
    answer = sys.stdin.readline().strip().lower()
    return answer in {"y", "yes"}


def is_active_now(settings: Settings, *, now: datetime | None = None) -> bool:
    local_now = _local_now(settings, now=now)
    start = settings.active_start_hour
    end = settings.active_end_hour
    if start == end:
        return True
    if start < end:
        return start <= local_now.hour < end
    return local_now.hour >= start or local_now.hour < end


def seconds_until_active(settings: Settings, *, now: datetime | None = None) -> float:
    local_now = _local_now(settings, now=now)
    if is_active_now(settings, now=local_now):
        return 0.0

    candidate = local_now.replace(
        hour=settings.active_start_hour,
        minute=0,
        second=0,
        microsecond=0,
    )
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return (candidate - local_now).total_seconds()


def active_hours_detail(settings: Settings) -> str:
    return (
        "The supervised Discord listener is configured to run from "
        f"{settings.active_start_hour}:00 to {settings.active_end_hour}:00 "
        f"in {settings.active_timezone}. It will restart at the next active window."
    )


def _local_now(settings: Settings, *, now: datetime | None = None) -> datetime:
    tz = ZoneInfo(settings.active_timezone)
    current = now or datetime.now(tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    return current.astimezone(tz)


def acquire_process_guard(settings: Settings, *, command: str) -> SingleInstanceGuard:
    if command == "daemon":
        marker = DAEMON_MARKER
        pidfile = settings.database_path.parent / "discord-listener-daemon.pid"
        matcher = _matches_daemon_process
    else:
        marker = LISTENER_MARKER
        pidfile = settings.database_path.parent / "discord-listener.pid"
        matcher = _matches_listener_process
    guard = SingleInstanceGuard(
        pidfile=pidfile,
        marker=marker,
        logger=logger,
        match_process=matcher,
    )
    guard.acquire()
    return guard


def _matches_listener_process(proc: psutil.Process) -> bool:
    return _matches_repo_process(proc, required="listen")


def _matches_daemon_process(proc: psutil.Process) -> bool:
    return _matches_repo_process(proc, required="daemon")


def _matches_repo_process(proc: psutil.Process, *, required: str) -> bool:
    try:
        cmdline = proc.cmdline()
        if "-m" not in cmdline or "discord_slack_listener" not in cmdline:
            return False
        if required not in cmdline:
            return False
        return Path(proc.cwd()) == ROOT_DIR
    except (psutil.Error, OSError):
        return False


def extract_payloads(page: Page) -> list[dict]:
    payloads = page.evaluate(EXTRACT_MESSAGES_SCRIPT)
    return payloads if isinstance(payloads, list) else []


def classify_message_with_context(
    store: MessageStore,
    message,
) -> LeadIntent:
    created_at = message.created_at.isoformat()
    same_author = store.recent_same_author_messages(
        author_key=message.author_key,
        before_created_at=created_at,
        limit=10,
    )
    channel_context = store.recent_channel_messages(
        channel_id=message.channel_id,
        before_created_at=created_at,
        limit=10,
    )
    return classify_product_intent_with_context(
        message,
        same_author_messages=same_author,
        channel_messages=channel_context,
    )


def scroll_messages_to_top(page: Page) -> None:
    page.evaluate(
        """
        () => {
          const scroller = document.querySelector('ol[data-list-id="chat-messages"]');
          let target = scroller;
          while (target && target !== document.body) {
            if (target.scrollHeight > target.clientHeight) break;
            target = target.parentElement;
          }
          if (target && target.scrollHeight > target.clientHeight) {
            target.scrollTop = Math.max(0, target.scrollTop - Math.max(target.clientHeight, 800));
          }
          window.scrollBy(0, -1200);
        }
        """
    )
    page.mouse.wheel(0, -2500)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="discord-slack-listener")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("listen")
    subparsers.add_parser("daemon")
    subparsers.add_parser("supervise")
    backfill_parser = subparsers.add_parser("backfill")
    backfill_parser.add_argument("--days", type=int, default=None)
    backfill_parser.add_argument("--max-scrolls", type=int, default=None)
    backfill_parser.add_argument("--catchup", action="store_true")
    args = parser.parse_args(argv)

    settings = load_settings()
    setup_logging(settings.log_level)

    if not settings.slack_webhook_url:
        logger.warning("SLACK_WEBHOOK_URL is unset; ops alerts will be logged only")
    if not settings.slack_matches_webhook_url:
        logger.warning("SLACK_MATCHES_WEBHOOK_URL is unset; matches will be logged only")

    if args.command == "backfill":
        slack = SlackNotifier(settings)
        with slack.notify_on_error("backfill"):
            run_backfill(
                settings,
                days=args.days,
                max_scrolls=args.max_scrolls,
                catchup=args.catchup,
            )
        return 0

    if args.command in {"daemon", "supervise"}:
        guard = acquire_process_guard(settings, command="daemon")
        try:
            run_supervisor(settings)
            return 0
        finally:
            guard.release()

    slack = SlackNotifier(settings)
    guard = acquire_process_guard(settings, command="listen")
    try:
        with slack.notify_on_error("listener"):
            run_browser_listener(settings)
        return 0
    finally:
        guard.release()
