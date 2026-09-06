# AGENTS.md

Guidance for AI agents (and humans) working in this repository.

## What this project is

A small internet-connection monitor that runs on the network you want to watch
and sends Telegram alerts. When the connection drops and recovers it sends a
combined "was down / now back up" message, optionally runs a ping/download/upload
speed test on recovery (to flag when you've come back on a slow 4G backup), and
listens for on-demand Telegram commands.

- `monitor.py` — the monitor. This is the main program.
- `ConnectionTester.ps1` — the original Windows PowerShell logger (no Telegram).
- `Dockerfile`, `docker-compose.yaml` — containerised deploy (built for Coolify).
- `.env.example` — every configurable environment variable, with defaults.
- `README.md` — user-facing docs.

## Ground rules

- **Standard library only.** `monitor.py` must not require any third-party
  Python packages — it runs from a plain `python:3.12-alpine` image with no
  `pip install`. Do not add dependencies; solve it with the stdlib.
- **Fail gracefully.** The monitor runs unattended on a flaky connection. Any
  network call (Telegram, speed test, getUpdates) must catch its errors, log
  them, and keep the main loop alive — never crash the process.
- **Config via environment variables.** Add new settings through the `env()` /
  `env_bool()` helpers with a sensible default. Mirror every new variable in
  **all three** of `.env.example`, `docker-compose.yaml`, and the README config
  table so a deploy needs no manual Coolify changes.
- **Keep it deployable without touching Coolify.** Defaults in code and the
  hardcoded values in `docker-compose.yaml` should match, so a redeploy works
  out of the box.
- **Only act on commands from `TELEGRAM_CHAT_ID`.** Ignore messages from any
  other chat.
- **Test before pushing.** At minimum `python3 -m py_compile monitor.py`, plus a
  quick offline unit check of any parsing/measurement logic you changed (mock
  the network — the build sandbox cannot reach Telegram or Cloudflare).
- **Attribution.** Follow the commit/PR attribution the session specifies. Do
  not put a model identifier in commits, PR text, or code.

## Workflow

- **Commit and push automatically.** After making a change, commit it with a
  clear message and push it — do not stop to ask the maintainer for permission
  to commit or push. Changes should land on `main` (the default branch) so a
  redeploy picks them up.
- Keep commit messages descriptive: what changed and why.
- Validate before pushing (see "Test before pushing" above); an automatic push
  is not an excuse to skip the quick checks.

## Logging mistakes

- When an AI agent makes a mistake while working on this repo, log it to
  **`mistakes.md`** so the same mistake isn't repeated and future agents can
  learn from it. Record the **cause** (what went wrong and why), the **fix**
  (what was done to correct it), and the **solution** (how to avoid it next
  time). Add a new dated entry per mistake; keep the newest at the top.
