"""
Integration tests — hit the real Matrix room.
Skipped automatically when ~/.matrix-cli/config is absent or has no MATRIX_TEST_ROOM_ID.
"""
import importlib.util
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import requests as _requests


def _fetch_thread_messages(config, room_id, thread_root_id):
    """Return the list of message bodies in a Matrix thread via the relations API."""
    homeserver = config["MATRIX_HOMESERVER"]
    token = config["MATRIX_ACCESS_TOKEN"]
    url = f"{homeserver}/_matrix/client/v1/rooms/{room_id}/relations/{thread_root_id}/m.thread"
    resp = _requests.get(url, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code != 200:
        return []
    return [e["content"].get("body", "") for e in resp.json().get("chunk", [])]

CONFIG_PATH = Path.home() / ".matrix-cli" / "config"

pytestmark = pytest.mark.skipif(
    not CONFIG_PATH.exists(),
    reason="~/.matrix-cli/config not present — skipping integration tests",
)

_script = Path(__file__).parents[2] / "matrix-cli"
_spec = importlib.util.spec_from_loader(
    "matrix_notify",
    importlib.machinery.SourceFileLoader("matrix_notify", str(_script)),
)
matrix_notify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(matrix_notify)


@pytest.fixture(scope="session")
def config():
    return matrix_notify.load_config(str(CONFIG_PATH))


@pytest.fixture(scope="session")
def test_room(config):
    room_id = config.get("MATRIX_TEST_ROOM_ID")
    if not room_id:
        pytest.skip("MATRIX_TEST_ROOM_ID not set in config — run matrix-notify setup to configure a test room")
    return room_id


@pytest.fixture
def sessions_file(tmp_path):
    return str(tmp_path / "sessions.json")


@pytest.fixture
def test_config(config, test_room):
    return {**config, "MATRIX_ROOM_ID": test_room}


class TestLiveAPI:
    def test_send_log_message(self, test_config, sessions_file):
        thread_id = matrix_notify.ensure_thread("integration-test", test_config, sessions_file)
        event_id = matrix_notify.send_message("log", f"integration test log {time.time()}", thread_id, test_config)
        assert event_id.startswith("$")

    def test_send_alert_message(self, test_config, sessions_file):
        thread_id = matrix_notify.ensure_thread("integration-test", test_config, sessions_file)
        event_id = matrix_notify.send_message("alert", f"integration test alert {time.time()}", thread_id, test_config)
        assert event_id.startswith("$")

    def test_send_done_message(self, test_config, sessions_file):
        thread_id = matrix_notify.ensure_thread("integration-test", test_config, sessions_file)
        event_id = matrix_notify.send_message("done", f"integration test done {time.time()}", thread_id, test_config)
        assert event_id.startswith("$")

    def test_two_sessions_create_two_threads(self, test_config, sessions_file):
        thread_a = matrix_notify.ensure_thread("integ-session-a", test_config, sessions_file)
        thread_b = matrix_notify.ensure_thread("integ-session-b", test_config, sessions_file)
        assert thread_a != thread_b

    def test_same_session_reuses_thread(self, test_config, sessions_file):
        thread_1 = matrix_notify.ensure_thread("integ-reuse", test_config, sessions_file)
        thread_2 = matrix_notify.ensure_thread("integ-reuse", test_config, sessions_file)
        assert thread_1 == thread_2


class TestMultiSessionThreadIsolation:
    """Simulate three distinct session contexts and verify three separate Matrix threads."""

    def test_three_sessions_produce_three_threads(self, test_config, sessions_file):
        ts = int(time.time())
        room_id = test_config["MATRIX_ROOM_ID"]

        msg_no_tmux = f"no-tmux-msg-{ts}"
        msg_alpha   = f"tmux-alpha-msg-{ts}"
        msg_beta    = f"tmux-beta-msg-{ts}"

        # Session 1: no tmux ($TMUX unset, tmux unavailable) → hostname-based session
        env_no_tmux = {k: v for k, v in os.environ.items() if k != "TMUX"}
        with patch.dict(os.environ, env_no_tmux, clear=True):
            with patch("subprocess.check_output", side_effect=Exception("no tmux")):
                with patch("socket.gethostname", return_value=f"integ-host-{ts}"):
                    thread_no_tmux = matrix_notify.ensure_thread(
                        f"integ-host-{ts}", test_config, sessions_file
                    )
                    matrix_notify.send_message("log", msg_no_tmux, thread_no_tmux, test_config)

        # Session 2: inside tmux session "integ-tmux-alpha"
        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,1,0"}):
            with patch("subprocess.check_output", return_value=b"integ-tmux-alpha\n"):
                thread_alpha = matrix_notify.ensure_thread(
                    f"integ-tmux-alpha-{ts}", test_config, sessions_file
                )
                matrix_notify.send_message("log", msg_alpha, thread_alpha, test_config)

        # Session 3: inside tmux session "integ-tmux-beta"
        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,2,0"}):
            with patch("subprocess.check_output", return_value=b"integ-tmux-beta\n"):
                thread_beta = matrix_notify.ensure_thread(
                    f"integ-tmux-beta-{ts}", test_config, sessions_file
                )
                matrix_notify.send_message("log", msg_beta, thread_beta, test_config)

        # All three threads must be distinct
        threads = {thread_no_tmux, thread_alpha, thread_beta}
        assert len(threads) == 3, f"Expected 3 distinct threads, got: {threads}"

        # Give Matrix a moment to index the events
        time.sleep(1)

        # Verify each message landed in the correct thread and not in the others
        bodies_no_tmux = _fetch_thread_messages(test_config, room_id, thread_no_tmux)
        bodies_alpha   = _fetch_thread_messages(test_config, room_id, thread_alpha)
        bodies_beta    = _fetch_thread_messages(test_config, room_id, thread_beta)

        assert any(msg_no_tmux in b for b in bodies_no_tmux), \
            f"Expected '{msg_no_tmux}' in no-tmux thread, got: {bodies_no_tmux}"
        assert not any(msg_no_tmux in b for b in bodies_alpha), \
            f"no-tmux message leaked into alpha thread"
        assert not any(msg_no_tmux in b for b in bodies_beta), \
            f"no-tmux message leaked into beta thread"

        assert any(msg_alpha in b for b in bodies_alpha), \
            f"Expected '{msg_alpha}' in alpha thread, got: {bodies_alpha}"
        assert not any(msg_alpha in b for b in bodies_no_tmux), \
            f"alpha message leaked into no-tmux thread"
        assert not any(msg_alpha in b for b in bodies_beta), \
            f"alpha message leaked into beta thread"

        assert any(msg_beta in b for b in bodies_beta), \
            f"Expected '{msg_beta}' in beta thread, got: {bodies_beta}"
        assert not any(msg_beta in b for b in bodies_no_tmux), \
            f"beta message leaked into no-tmux thread"
        assert not any(msg_beta in b for b in bodies_alpha), \
            f"beta message leaked into alpha thread"

    def test_no_tmux_session_records_tmux_target(self, test_config, sessions_file):
        ts = int(time.time())
        session_key = f"integ-host-target-{ts}"

        # Simulate: outside tmux (hostname session key) but tmux server is reachable
        env_no_tmux = {k: v for k, v in os.environ.items() if k != "TMUX"}
        with patch.dict(os.environ, env_no_tmux, clear=True):
            # get_session_name → hostname (no $TMUX)
            # get_tmux_session → "matrix" (tmux server reachable)
            with patch("subprocess.check_output", return_value=b"matrix\n"):
                with patch("socket.gethostname", return_value=session_key):
                    matrix_notify.ensure_thread(session_key, test_config, sessions_file)

        sessions = matrix_notify.load_sessions(sessions_file)
        assert session_key in sessions
        assert sessions[session_key].get("tmux_target") == "matrix"

    def test_tmux_session_has_no_separate_tmux_target(self, test_config, sessions_file):
        ts = int(time.time())
        session_key = f"integ-tmux-same-{ts}"

        # Inside tmux: session name and tmux_target are the same — no need to store separately
        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,1,0"}):
            with patch("subprocess.check_output", return_value=session_key.encode() + b"\n"):
                matrix_notify.ensure_thread(session_key, test_config, sessions_file)

        sessions = matrix_notify.load_sessions(sessions_file)
        assert session_key in sessions
        # tmux_target is stored but equals the session key (redundant but harmless)
        target = sessions[session_key].get("tmux_target")
        assert target is None or target == session_key
