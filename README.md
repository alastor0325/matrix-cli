# matrix-cli

A CLI tool that sends and receives Matrix notifications for automated workflows, organized into per-session threads. Useful for any process that needs to log progress, ping for approvals, and accept replies from your phone or Element.

## How it works

Each named session gets its own Matrix thread. Multiple concurrent sessions (`bug-1234`, `bug-5678`) produce separate threads in the room, keeping their output visually isolated in Element.

The session name is taken from the current tmux session if available, or falls back to the hostname.

```
my-bot  [session-name] started 2026-04-01 10:00
  └─ [log]   CI analysis done: 3 failures need fixing
  └─ [alert] @you Approval needed: D100000 waiting for r+
  └─ [done]  Bug 1000000 fully landed
```

## Setup

**Prerequisites:** Python 3, `pip`

```bash
git clone https://github.com/alastor0325/matrix-cli
cd matrix-cli
```

Run setup interactively:

```bash
python3 matrix-cli setup
```

Or run any subcommand — if no config is found, setup starts automatically.

This walks you through six steps (homeserver URL, bot access token, your Matrix user ID, notification room, optional test room, and install location) and writes credentials to `~/.matrix-cli/config` — outside the repo, never committed. The homeserver URL defaults to `https://mozilla.modular.im`. All inputs are validated and setup loops until a valid value is entered. The access token input is hidden while you type.

Setup creates a private Matrix room, invites your Matrix user ID, and installs `matrix-cli` (and a `matrix-notify` backwards-compatibility shim) to `~/.local/bin/` by default.

### Getting the credentials

| What | Where to find it |
|------|-----------------|
| Homeserver URL | Element → Settings → Help & About → Homeserver |
| Bot access token | Log into Element as the bot → Settings → Help & About → Access Token |
| Your Matrix user ID | Element → Settings → Account (shown as `@username:homeserver`) |

## Usage

### Sending notifications

```bash
matrix-cli notify log   "CI analysis done: 3 failures need fixing"
matrix-cli notify alert "Approval needed: D123456 waiting for r+"
matrix-cli notify done  "Bug 1000000 fully landed"
```

- `log` — plain text progress update
- `alert` — bold + @mention to trigger an Element notification
- `done` — bold, marks a task complete

Session name is auto-detected from the current tmux session name if available, or falls back to the hostname.

### Listening for replies

`matrix-cli listen` polls your Matrix room for thread replies from your own user ID and forwards them as input to the corresponding tmux session:

```bash
matrix-cli listen           # foreground, Ctrl-C to stop
matrix-cli listen --daemon  # background daemon, PID saved to /tmp/matrix-listen.pid
```

When you reply to a thread in Element (e.g. from your phone), `listen` detects the reply, looks up which tmux session owns that thread, and submits your message to that session via `tmux send-keys`. This lets you interact with running Claude agents remotely.

The listener filters to messages from your own `MATRIX_NOTIFY_USER` ID — replies from other users in the room are ignored.

**Sync state** is saved to `~/.matrix-cli/sync-token` so the daemon resumes from where it left off after a restart, without replaying history.

**Auto-start:** the `matrix-cli listen --daemon` is started automatically by the firefox-manager watcher on every `/manager` init, and is kept alive by the healthcheck cron.

### Backwards compatibility

The `matrix-notify` shim is installed alongside `matrix-cli` and delegates to `matrix-cli notify`, so existing scripts using `matrix-notify log/alert/done` continue to work unchanged.

## Development

```bash
pytest tests/unit/        # fast, no credentials needed
pytest tests/integration/ # requires ~/.matrix-cli/config
pytest --cov              # coverage report
```

A pre-commit hook runs the unit suite automatically on every commit. Integration tests run if `~/.matrix-cli/config` is present. The hook is enabled automatically during `matrix-cli setup` (it sets `core.hooksPath` to the repo's `scripts/` directory).

See [CLAUDE.md](CLAUDE.md) for TDD rules.
