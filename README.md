# matrix-notify

A CLI tool that sends structured notifications to a Matrix private room, organized into per-session threads. Useful for any automated workflow that needs to log progress and ping for approvals.

## How it works

Each named session gets its own Matrix thread. Multiple concurrent sessions (`bug-1234`, `bug-5678`) produce separate threads in the room, keeping their output visually isolated in Element.

The session name is taken from the current tmux session if available, or falls back to `hostname-PID`. You can also pass any identifier as the session name — tmux is not required.

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
pip install -r requirements.txt
ln -s "$PWD/matrix-notify" ~/.local/bin/matrix-notify
chmod +x matrix-notify
```

Run any `matrix-notify` command — if no config is found, setup starts automatically:

```bash
matrix-notify
```

Or invoke it explicitly:

```bash
matrix-notify setup
```

This walks you through six steps (homeserver URL, bot access token, your Matrix user ID, notification room, optional test room, and install location) and writes credentials to `~/.matrix-cli/config` — outside the repo, never committed. The homeserver URL defaults to `https://mozilla.modular.im` if you press Enter. All inputs are validated and setup loops until a valid value is entered — it will not exit on a blank or malformed answer. The access token input is hidden while you type. For your Matrix user ID, setup prompts repeatedly until a valid `@username:homeserver` value is entered.

Setup prompts for the notification room name, defaulting to "matrix-notify" — press Enter to accept or type a different name. It then creates a private Matrix room with that name and invites your Matrix user ID to it. You must accept the invite in Element before pressing Enter to continue — once you do, the bot sends a confirmation message to the room so you can verify it is working.

After the notification room is set up, setup prompts for an optional test room ID used by integration tests. Press Enter to have a private room named "matrix-notify tests" created automatically, or paste an existing `!localpart:server` room ID. If auto-creation succeeds, `MATRIX_TEST_ROOM_ID` is written to the config file alongside the other credentials. If you skip this step or creation fails, the test room entry is omitted and integration tests that require it will be skipped.

### Getting the credentials

| What | Where to find it |
|------|-----------------|
| Homeserver URL | Element → Settings → Help & About → Homeserver |
| Bot access token | Log into Element as the bot → Settings → Help & About → Access Token |
| Your Matrix user ID | Element → Settings → Account (shown as `@username:homeserver`) |

### Room setup (one-time)

Setup creates the notification room automatically — no manual room creation required. When prompted, accept the invite in Element and press Enter.

## Usage

```bash
matrix-notify log   "CI analysis done: 3 failures need fixing"
matrix-notify alert "Approval needed: D123456 waiting for r+"
matrix-notify done  "Bug 1000000 fully landed"
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

A pre-commit hook runs the unit suite automatically on every commit. Integration tests run if `~/.matrix-cli/config` is present. The hook is enabled automatically when you run `matrix-notify setup` (it sets `core.hooksPath` to the repo's `scripts/` directory).

See [CLAUDE.md](CLAUDE.md) for TDD rules.
