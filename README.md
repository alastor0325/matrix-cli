# matrix-notify

A CLI tool that sends structured notifications to a Matrix private room, organized into per-session threads. Useful for any automated workflow that needs to log progress and ping for approvals.

## How it works

Each tmux session gets its own Matrix thread. Multiple concurrent sessions (`bug-1234`, `bug-5678`) produce separate threads in the room, keeping their output visually isolated in Element.

```
my-bot  [session-name] started 2026-04-01 10:00
  └─ [log]   CI analysis done: 3 Tier 1 failures need fixing
  └─ [alert] @you Approval needed: D123456 waiting for r+
  └─ [done]  Bug 1962876 fully landed
```

## Setup

**Prerequisites:** Python 3, `pip`

```bash
git clone https://github.com/alastor0325/matrix-cli
cd matrix-cli
pip install -r requirements.txt
ln -s "$PWD/matrix-notify" ~/.local/bin/matrix-notify
chmod +x matrix-notify
```

Then run the interactive setup wizard:

```bash
matrix-notify setup
```

This walks you through four steps (homeserver, bot access token, room ID, your Matrix user ID) and writes credentials to `~/.matrix-cli/config` — outside the repo, never committed.

### Getting the credentials

| What | Where to find it |
|------|-----------------|
| Bot access token | Log into Element as the bot → Settings → Help & About → Access Token |
| Room ID | Open the private room → Room Settings → Advanced → Internal room ID (starts with `!`) |

### Room setup (one-time)

1. Log into Element as your primary account
2. Create a private room and invite the bot account
3. Copy the room ID from Room Settings → Advanced
4. Use it in `matrix-notify setup`

## Usage

```bash
matrix-notify log   "CI analysis done: 3 failures need fixing"
matrix-notify alert "Approval needed: D123456 waiting for r+"
matrix-notify done  "Bug 1962876 fully landed"
```

- `log` — plain text progress update
- `alert` — bold + @mention to trigger an Element notification
- `done` — bold, marks a task complete

Session name is auto-detected from the current tmux session name if available, or falls back to `hostname-PID`. tmux is not required.

## Development

```bash
pytest tests/unit/        # fast, no credentials needed
pytest tests/integration/ # requires ~/.matrix-cli/config
pytest --cov              # coverage report
pytest tests/unit/ --watch  # watch mode during dev
```

A pre-commit hook runs the unit suite automatically on every commit. Integration tests run if `~/.matrix-cli/config` is present.

See [CLAUDE.md](CLAUDE.md) for TDD rules.
