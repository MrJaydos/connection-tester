#!/usr/bin/env python3
"""
Internet connection monitor with Telegram alerts.

Polls a couple of well-known hosts on a short interval. When the connection
drops it records *when* it dropped. As soon as connectivity returns it sends a
single, combined Telegram message like:

    Internet went down at 2026-07-25 14:03:11 BST,
    was down for 4m 27s,
    and is now back up and running.

Why "combined on recovery"?  This is meant to run on the very network you are
monitoring (e.g. a small box at home). While the internet is down the container
cannot reach Telegram anyway, so the only moment it *can* tell you anything is
once the link is restored -- which is exactly the one message you asked for.

Everything is configured through environment variables (see README / .env.example).
No third-party packages required -- standard library only.
"""

import json
import os
import socket
import sys
import time
from datetime import datetime, timedelta
from urllib import error, parse, request


def env(name, default=None):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def env_bool(name, default=False):
    value = env(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
CHAT_ID = env("TELEGRAM_CHAT_ID")

TARGETS = [t.strip() for t in env("TARGETS", "1.1.1.1:443,8.8.8.8:53").split(",") if t.strip()]
INTERVAL_SECONDS = float(env("INTERVAL_SECONDS", "5"))
CHECK_TIMEOUT = float(env("CHECK_TIMEOUT", "1.5"))
# Consecutive failed checks before we declare the internet "down". Guards against
# a single dropped packet triggering a false alarm.
FAIL_THRESHOLD = int(env("FAIL_THRESHOLD", "3"))
STATE_FILE = env("STATE_FILE", "/data/state.json")
STARTUP_PING = env_bool("STARTUP_PING", True)

# After the connection recovers, measure download throughput. Handy for spotting
# when you've dropped onto a slower 4G backup line instead of your main WAN.
SPEED_TEST = env_bool("SPEED_TEST", True)
# A file to download for the measurement. Cloudflare's endpoint lets you ask for
# an exact number of bytes and has no auth. Reads are capped at SPEED_TEST_BYTES
# regardless, so this stays cheap even on a metered 4G backup.
SPEED_TEST_URL = env("SPEED_TEST_URL", "https://speed.cloudflare.com/__down?bytes=10000000")
SPEED_TEST_BYTES = int(env("SPEED_TEST_BYTES", "10000000"))  # ~10 MB
SPEED_TEST_TIMEOUT = float(env("SPEED_TEST_TIMEOUT", "30"))
# If the measured speed is below this (Mbps), flag it as "probably the 4G backup".
# Defaults to 100: a fibre main line sits well above it and a 4G backup well
# below, so a recovery on the backup gets flagged. Set to 0 to disable the
# warning (you still get the raw number).
SPEED_TEST_SLOW_MBPS = float(env("SPEED_TEST_SLOW_MBPS", "100"))

# Listen for Telegram commands (e.g. "/speedtest") and respond on demand.
# Only messages from TELEGRAM_CHAT_ID are acted on.
LISTEN_COMMANDS = env_bool("LISTEN_COMMANDS", True)


def log(message):
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"[{stamp}] {message}", flush=True)


# ---------------------------------------------------------------------------
# Connectivity check
# ---------------------------------------------------------------------------
def is_online():
    """Return True if a TCP connection to any target succeeds."""
    for target in TARGETS:
        host, _, port = target.rpartition(":")
        if not host:  # no port given, default to 53
            host, port = target, "53"
        try:
            with socket.create_connection((host, int(port)), timeout=CHECK_TIMEOUT):
                return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def telegram_send(text, retries=12, backoff=5):
    """
    Send a message via the Telegram Bot API.

    Retries with backoff because when the internet has just come back, DNS /
    routing may take a few seconds to settle -- we don't want to lose the
    recovery message.
    """
    if not BOT_TOKEN or not CHAT_ID:
        log("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set -- cannot send message.")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = parse.urlencode(
        {
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode()

    for attempt in range(1, retries + 1):
        try:
            req = request.Request(url, data=payload, method="POST")
            with request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    return True
                log(f"Telegram responded HTTP {resp.status} (attempt {attempt}/{retries}).")
        except error.HTTPError as exc:
            # 4xx usually means bad token/chat id -- retrying won't help.
            body = exc.read().decode("utf-8", "replace")
            log(f"Telegram HTTP {exc.code}: {body} (attempt {attempt}/{retries}).")
            if 400 <= exc.code < 500:
                return False
        except (error.URLError, OSError) as exc:
            log(f"Telegram send failed: {exc} (attempt {attempt}/{retries}).")

        if attempt < retries:
            time.sleep(backoff)
    return False


def telegram_get_updates(offset=None, timeout=0):
    """
    Fetch new messages sent to the bot via the Telegram getUpdates API.

    Returns a list of update objects (possibly empty) or None on error.
    `offset` acknowledges everything before it; `timeout` enables long polling.
    """
    if not BOT_TOKEN:
        return None
    params = {"timeout": timeout, "allowed_updates": '["message"]'}
    if offset is not None:
        params["offset"] = offset
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?" + parse.urlencode(params)
    try:
        with request.urlopen(url, timeout=timeout + 15) as resp:
            data = json.load(resp)
        if data.get("ok"):
            return data.get("result", [])
        log(f"getUpdates returned not-ok: {data}")
    except (error.URLError, OSError, ValueError) as exc:
        log(f"getUpdates failed: {exc}")
    return None


# ---------------------------------------------------------------------------
# State (survives container restarts if /data is a volume)
# ---------------------------------------------------------------------------
def save_down_since(when):
    try:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(STATE_FILE, "w") as fh:
            json.dump({"down_since": when.isoformat()}, fh)
    except OSError as exc:
        log(f"Could not write state file {STATE_FILE}: {exc}")


def load_down_since():
    try:
        with open(STATE_FILE) as fh:
            data = json.load(fh)
        return datetime.fromisoformat(data["down_since"])
    except (OSError, ValueError, KeyError):
        return None


def clear_state():
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def human_duration(delta):
    total = int(delta.total_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def run_speed_test():
    """
    Roughly measure download throughput by fetching a file and timing it.

    Returns (mbps, bytes_read, seconds) or None if the test could not be run.
    Reads at most SPEED_TEST_BYTES so it stays cheap on a metered 4G backup.
    """
    try:
        req = request.Request(SPEED_TEST_URL, headers={"User-Agent": "connection-tester/1.0"})
        start = time.monotonic()
        read = 0
        with request.urlopen(req, timeout=SPEED_TEST_TIMEOUT) as resp:
            while read < SPEED_TEST_BYTES:
                chunk = resp.read(min(65536, SPEED_TEST_BYTES - read))
                if not chunk:
                    break
                read += len(chunk)
        elapsed = time.monotonic() - start
    except (error.URLError, OSError, ValueError) as exc:
        log(f"Speed test failed: {exc}")
        return None

    if read == 0 or elapsed <= 0:
        log("Speed test produced no usable data.")
        return None

    mbps = (read * 8) / elapsed / 1_000_000
    return mbps, read, elapsed


def speed_test_message(result, title="Speed test after recovery"):
    mbps, read, elapsed = result
    megabytes = read / 1_000_000
    text = (
        f"📶 <b>{title}</b>\n\n"
        f"Download: <b>{mbps:.1f} Mbps</b>\n"
        f"({megabytes:.1f} MB in {elapsed:.1f}s)"
    )
    if SPEED_TEST_SLOW_MBPS > 0 and mbps < SPEED_TEST_SLOW_MBPS:
        text += (
            f"\n\n⚠️ That's below <b>{SPEED_TEST_SLOW_MBPS:.0f} Mbps</b> — "
            "you may be on the 4G backup rather than your main line."
        )
    return text


def recovery_message(down_since, back_at):
    duration = human_duration(back_at - down_since)
    return (
        "🟢 <b>Internet is back up</b>\n\n"
        f"It went down at <b>{fmt(down_since)}</b>,\n"
        f"was down for <b>{duration}</b>,\n"
        f"and is now back up and running as of <b>{fmt(back_at)}</b>."
    )


# ---------------------------------------------------------------------------
# Incoming Telegram commands
# ---------------------------------------------------------------------------
SPEED_COMMANDS = {"/speedtest", "/speed", "/test"}
SPEED_PHRASES = {"speedtest", "speed test", "speed", "test"}
HELP_COMMANDS = {"/help", "/start"}
STATUS_COMMANDS = {"/status"}


def help_message():
    return (
        "🤖 <b>Connection monitor</b>\n\n"
        "Commands:\n"
        "• /speedtest — run a download speed test now\n"
        "• /status — is the connection up right now?\n"
        "• /help — show this message\n\n"
        "You'll also get an automatic message whenever the connection drops and "
        "recovers (with a speed test, to flag the 4G backup)."
    )


def status_message(down_since):
    now = datetime.now().astimezone()
    if down_since:
        return (
            "🔴 <b>Internet is currently down</b>\n\n"
            f"Down since {fmt(down_since)} ({human_duration(now - down_since)} ago)."
        )
    return f"🟢 <b>Internet is up</b>\nAs of {fmt(now)}."


def run_command_speed_test():
    """Run an on-demand speed test and reply with the result."""
    log("Received /speedtest command.")
    telegram_send("⏳ Running a speed test, one moment...", retries=2, backoff=3)
    result = run_speed_test()
    if result:
        log(f"On-demand speed test: {result[0]:.1f} Mbps.")
        telegram_send(speed_test_message(result, title="Speed test"))
    else:
        telegram_send("⚠️ Speed test could not be completed. Please try again in a moment.")


def handle_command(text, down_since):
    """Act on a single incoming message's text. Unknown messages are ignored."""
    words = text.strip().lower().split()
    if not words:
        return
    # Normalise "/speedtest@YourBot" -> "/speedtest".
    first = words[0].split("@", 1)[0]
    phrase = " ".join([first] + words[1:])

    if first in SPEED_COMMANDS or phrase in SPEED_PHRASES:
        run_command_speed_test()
    elif first in STATUS_COMMANDS or phrase == "status":
        telegram_send(status_message(down_since))
    elif first in HELP_COMMANDS or phrase == "help":
        telegram_send(help_message())
    # Anything else: stay quiet so we don't spam on normal chatter.


def process_commands(offset, down_since):
    """
    Poll Telegram for new messages and act on any recognised commands.

    Only messages from the configured CHAT_ID are honoured. Returns the new
    offset to pass on the next call.
    """
    updates = telegram_get_updates(offset)
    if not updates:
        return offset
    for upd in updates:
        offset = upd["update_id"] + 1
        msg = upd.get("message") or {}
        text = msg.get("text", "")
        chat = str(msg.get("chat", {}).get("id", ""))
        if not text:
            continue
        if CHAT_ID and chat != str(CHAT_ID):
            log(f"Ignoring command from unauthorized chat {chat}.")
            continue
        handle_command(text, down_since)
    return offset


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    log("Connection monitor starting.")
    log(f"Targets: {', '.join(TARGETS)}")
    log(f"Interval: {INTERVAL_SECONDS}s | timeout: {CHECK_TIMEOUT}s | fail threshold: {FAIL_THRESHOLD}")
    if SPEED_TEST:
        log(f"Post-recovery speed test: on (up to {SPEED_TEST_BYTES / 1_000_000:.0f} MB "
            f"from {SPEED_TEST_URL}).")
    else:
        log("Post-recovery speed test: off.")
    if LISTEN_COMMANDS:
        log("Command listener: on (/speedtest, /status, /help).")

    if not BOT_TOKEN or not CHAT_ID:
        log("WARNING: TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID are not set. "
            "The monitor will run but cannot send alerts until they are configured.")

    # Resume an outage that was in progress when the container last stopped.
    down_since = load_down_since()
    if down_since:
        log(f"Resuming: an outage was in progress since {fmt(down_since)} (from state file).")

    if STARTUP_PING and BOT_TOKEN and CHAT_ID:
        now = datetime.now().astimezone()
        if telegram_send(
            "✅ <b>Connection monitor is online</b>\n"
            f"Watching your internet as of {fmt(now)}.\n"
            "You'll get a message here if it drops and recovers.\n"
            "Send /speedtest any time to check your speed, or /help for commands.",
            retries=2,
            backoff=3,
        ):
            log("Startup ping sent to Telegram.")
        else:
            log("Startup ping could not be sent (check token / chat id / connectivity).")

    # Skip any commands that queued up while we were offline/restarting, so a
    # restart doesn't replay old "/speedtest" messages.
    update_offset = None
    if LISTEN_COMMANDS and BOT_TOKEN and CHAT_ID:
        recent = telegram_get_updates(offset=-1)
        if recent:
            update_offset = recent[-1]["update_id"] + 1

    consecutive_fails = 0
    first_fail_at = None

    while True:
        online = is_online()
        now = datetime.now().astimezone()

        if online:
            if down_since is not None:
                log(f"RECONNECTED. Was down since {fmt(down_since)}.")
                if telegram_send(recovery_message(down_since, now)):
                    log("Recovery message delivered to Telegram.")
                else:
                    log("Failed to deliver recovery message after retries.")

                # Sometimes the link comes back on the 4G backup rather than the
                # main WAN. Measure the speed and report it as a separate message.
                if SPEED_TEST:
                    log("Running post-recovery speed test...")
                    result = run_speed_test()
                    if result:
                        log(f"Speed test: {result[0]:.1f} Mbps "
                            f"({result[1] / 1_000_000:.1f} MB in {result[2]:.1f}s).")
                        if telegram_send(speed_test_message(result)):
                            log("Speed test message delivered to Telegram.")
                        else:
                            log("Failed to deliver speed test message after retries.")
                    else:
                        log("Speed test could not be completed.")

                down_since = None
                clear_state()
            consecutive_fails = 0
            first_fail_at = None
        else:
            consecutive_fails += 1
            if first_fail_at is None:
                first_fail_at = now
                log("A connectivity check failed; watching...")
            if down_since is None and consecutive_fails >= FAIL_THRESHOLD:
                down_since = first_fail_at
                save_down_since(down_since)
                log(f"DISCONNECTED. Internet down since {fmt(down_since)} "
                    f"({consecutive_fails} consecutive failed checks).")

        # Check for any commands you've messaged the bot (e.g. /speedtest).
        # Only works while we're online -- there's no route out during an outage.
        if LISTEN_COMMANDS and BOT_TOKEN and CHAT_ID and online:
            update_offset = process_commands(update_offset, down_since)

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Monitor stopped.")
        sys.exit(0)
