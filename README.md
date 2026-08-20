# Connection Tester

Monitors your internet connection and sends a **single, combined Telegram
message when it recovers** — telling you when it went down, how long it was
down, and that it's back:

> 🟢 **Internet is back up**
>
> It went down at **2026-07-25 14:03:11 BST**,
> was down for **4m 27s**,
> and is now back up and running as of **2026-07-25 14:07:38 BST**.

It can also run a **download speed test the moment the connection returns** and
send the result as a second message — handy for catching when the link has come
back on a slower **4G backup** instead of your main line:

> 📶 **Speed test after recovery**
>
> Download: **12.3 Mbps**
> (5.0 MB in 3.2s)
>
> ⚠️ That's below **50 Mbps** — you may be on the 4G backup rather than your main line.

(Set `SPEED_TEST_SLOW_MBPS` to your rough main-line speed to get that warning;
the raw number is always sent. See the config table below.)

There are two ways to run it:

- **`monitor.py` + Docker** — the containerized monitor with Telegram alerts,
  designed to be deployed with [Coolify](https://coolify.io/). This is what
  you want.
- **`ConnectionTester.ps1`** — the original Windows PowerShell script that just
  logs drops/recoveries to a text file locally. Kept for quick local use.

---

## How it works (and an important gotcha)

The monitor probes a couple of well-known hosts (`1.1.1.1:443` and `8.8.8.8:53`
by default) every few seconds. If several checks in a row fail it records the
time as the start of an outage. The moment a check succeeds again, it computes
the duration and sends one Telegram message.

**Run it on the network you want to watch.** The whole point is to detect *your*
connection dropping — so deploy it on a machine that sits behind the router
you care about (a home server, a mini PC, a Pi running Coolify, etc.).

Because that machine loses internet exactly when you do, it **can't** message
Telegram *during* the outage — there's no route out. That's why alerts are sent
**on recovery** rather than at the moment of the drop: recovery is the first
instant the message can actually get through. This matches the "one message
that says it went down and is now back up" behaviour you want, and it's the
only thing that can work when the monitor shares your connection.

> If you instead host it somewhere with a *separate* internet connection (a
> cloud VPS), it will monitor *that* connection, not your home/office one.

---

## 1. Create a Telegram bot

1. In Telegram, message [@BotFather](https://t.me/BotFather) and send `/newbot`.
   Follow the prompts and copy the **bot token** it gives you
   (looks like `123456789:AAE...`).
2. Send any message to your new bot (this lets it message you back).
3. Get your **chat id**: either message [@userinfobot](https://t.me/userinfobot),
   or open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and read
   the `"chat":{"id":...}` value.

You now have `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## 2. Deploy on Coolify

This repo has a `Dockerfile` and a `docker-compose.yaml`, so Coolify can deploy
it either way.

**Option A — Docker Compose (recommended):**

1. In Coolify: **+ New → Resource → Docker Compose** (or "Public/Private
   Repository" and point it at this repo — Coolify will detect the compose file).
2. Add these **Environment Variables**:
   - `TELEGRAM_BOT_TOKEN` (required)
   - `TELEGRAM_CHAT_ID` (required)
   - `TZ` — e.g. `Europe/London` or `America/New_York` (for correct timestamps)
   - Optional tuning: `INTERVAL_SECONDS`, `CHECK_TIMEOUT`, `FAIL_THRESHOLD`,
     `TARGETS`, `STARTUP_PING` (see table below)
3. Deploy. You should get a "✅ Connection monitor is online" ping in Telegram,
   confirming the bot is wired up.

**Option B — Dockerfile only:**

1. In Coolify: **+ New → Resource → Dockerfile** (or a Git repo with the
   Dockerfile build pack).
2. Set the same environment variables as above.
3. (Optional) Add a persistent volume mounted at `/data` so an in-progress
   outage survives a container restart.
4. Deploy.

## 3. Run it anywhere with plain Docker

```bash
cp .env.example .env      # then edit .env with your token, chat id, and TZ
docker compose up -d --build
docker compose logs -f    # watch it work
```

Or without compose:

```bash
docker build -t connection-tester .
docker run -d --restart unless-stopped \
  -e TELEGRAM_BOT_TOKEN=123456789:AAE... \
  -e TELEGRAM_CHAT_ID=987654321 \
  -e TZ=Europe/London \
  -v connection-tester-data:/data \
  --name connection-tester connection-tester
```

## Configuration

| Variable             | Required | Default             | Description                                                        |
| -------------------- | :------: | ------------------- | ------------------------------------------------------------------ |
| `TELEGRAM_BOT_TOKEN` |   yes    | —                   | Bot token from @BotFather.                                         |
| `TELEGRAM_CHAT_ID`   |   yes    | —                   | Chat/user id to send alerts to.                                    |
| `TZ`                 |    no    | `UTC`               | Timezone for timestamps, e.g. `Europe/London`.                     |
| `INTERVAL_SECONDS`   |    no    | `5`                 | Seconds between checks.                                            |
| `CHECK_TIMEOUT`      |    no    | `1.5`               | Per-target TCP connect timeout (seconds).                          |
| `FAIL_THRESHOLD`     |    no    | `3`                 | Consecutive failed checks before declaring "down" (anti false-alarm). |
| `TARGETS`            |    no    | `1.1.1.1:443,8.8.8.8:53` | Comma-separated `host:port` TCP probes. Online = any one connects. |
| `STARTUP_PING`       |    no    | `true`              | Send a short "monitor is online" message on startup.              |
| `STATE_FILE`         |    no    | `/data/state.json`  | Where outage state is persisted across restarts.                  |
| `SPEED_TEST`         |    no    | `true`              | Run a download speed test after recovery and send it as a second message. |
| `SPEED_TEST_URL`     |    no    | `https://speed.cloudflare.com/__down?bytes=10000000` | File to download for the measurement. |
| `SPEED_TEST_BYTES`   |    no    | `10000000`          | Hard cap on bytes downloaded (keeps it cheap on metered 4G).       |
| `SPEED_TEST_TIMEOUT` |    no    | `30`                | Overall timeout for the speed test (seconds).                     |
| `SPEED_TEST_SLOW_MBPS` |  no    | `0`                 | Flag the result as "probably the 4G backup" if below this many Mbps. `0` disables the warning. |

With the defaults, an outage has to persist for roughly `FAIL_THRESHOLD ×
INTERVAL_SECONDS` (≈ 15s) before it's counted — tune those down for a twitchier
trigger or up to ignore brief blips.

## Testing it

Once deployed and you've seen the startup ping, unplug your router (or disable
its WAN) for a minute, then plug it back in. Within a few seconds of the
connection returning you should get the recovery message. The container logs
(`docker compose logs -f` or the Coolify log view) show every state change too.

---

## The original PowerShell script

`ConnectionTester.ps1` is unchanged — it polls once a second and appends
`DISCONNECTED` / `RECONNECTED` lines to `connection-log.txt`. Handy on a Windows
box, but it has no Telegram integration; use the Docker monitor above for alerts.

```powershell
.\ConnectionTester.ps1
```
