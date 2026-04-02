# matrix-cli

A CLI tool that sends and receives Matrix notifications for automated workflows. Each session gets its own thread in a Matrix room, keeping output from concurrent processes visually isolated in Element.

```
my-bot  [session-name] started 2026-04-01 10:00
  └─ [log]   CI analysis done: 3 failures need fixing
  └─ [alert] @you Approval needed: D123456 waiting for r+
  └─ [done]  Bug 1000000 fully landed
```

## Setup

1. **Install**
   ```bash
   curl -fsSL https://raw.githubusercontent.com/alastor0325/matrix-cli/main/install.sh | sh
   ```
   This downloads `matrix-cli` to `~/.local/bin/`, installs the `matrix-notify` shim, and ensures `requests` is available.

2. **Prepare your credentials** — you'll need these during the wizard:

   | What | How to get it |
   |------|--------------|
   | Homeserver URL | Element → Settings → Help & About → Homeserver (defaults to `https://mozilla.modular.im`) |
   | Bot access token | Create a new Matrix account for the bot, log in as it in Element → Settings → Help & About → Access Token |
   | Your Matrix user ID | Your personal account → Element → Settings → Account (shown as `@username:homeserver`) |

3. **Run setup**
   ```bash
   matrix-cli
   ```
   The wizard walks through 7 steps: config directory, homeserver URL, bot access token, your Matrix user ID, notification room (paste an existing room ID or press Enter to auto-create one), optional test room, and install location. Credentials are written to `~/.matrix-cli/config` — outside the repo, never committed.

4. **Done** — setup runs automatically again if the config is ever missing.

## Usage

### Sending notifications

```bash
matrix-cli notify log   "CI analysis done: 3 failures need fixing"
matrix-cli notify alert "Approval needed: D123456 waiting for r+"
matrix-cli notify done  "Bug 1000000 fully landed"
```

| Type | Appearance | When to use |
|------|-----------|-------------|
| `log` | plain text | progress updates |
| `alert` | bold + @mention | action required |
| `done` | bold | task complete |

**Session name** — the thread the message is posted to — is determined automatically:
- Inside a tmux pane (`$TMUX` is set): uses the tmux session name
- Outside tmux (script, cron job, or process that strips `$TMUX`): uses the hostname

The thread is created automatically on first use.

### Listening for replies

```bash
matrix-cli listen           # foreground, Ctrl-C to stop
matrix-cli listen --daemon  # background, PID saved to /tmp/matrix-listen.pid
```

When you reply to a thread in Element (e.g. from your phone), `listen` detects the reply and submits the message — prefixed with `[matrix]` — to the correct tmux session via `tmux send-keys`.

The delivery target is determined at thread-creation time. When a process outside tmux (no `$TMUX`) creates a thread, the active tmux session name is recorded as `tmux_target` in `~/.matrix-cli/sessions.json`. Replies to that thread are then forwarded to the right tmux pane even though the originating process had no tmux context.

Other notes:
- Only replies from your own `MATRIX_NOTIFY_USER` are forwarded — replies from others in the room are ignored.
- Sync state is saved to `~/.matrix-cli/sync-token` so the daemon resumes from where it left off after a restart.
- If `--daemon` is used while a daemon is already running, the command prints `already running (pid N)` and exits.

### Forwarding messages

These commands are for shell scripts and hooks that receive a Matrix reply and need to pass it to another process:

```bash
# Print [matrix]-prefixed text to stdout (no Matrix API call)
matrix-cli forward "some message"

# Send "Received: ..." handshake to the thread, then print [matrix]-prefixed text
matrix-cli handle-forward "some message"
```

Use `handle-forward` when the message originates from a Matrix reply — it acknowledges receipt in the thread so the sender sees confirmation in Element before the message is processed.

### Backwards compatibility

The `matrix-notify` shim is installed alongside `matrix-cli` and delegates to `matrix-cli notify`, so existing scripts using `matrix-notify log/alert/done` continue to work unchanged.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest tests/unit/        # fast, no credentials needed
.venv/bin/pytest tests/integration/ # requires ~/.matrix-cli/config with MATRIX_TEST_ROOM_ID
.venv/bin/pytest --cov              # coverage report
```

A pre-commit hook runs the unit suite automatically on every commit, enabled during `matrix-cli setup` via `core.hooksPath`.

See [CLAUDE.md](CLAUDE.md) for TDD rules.
