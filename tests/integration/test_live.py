"""
Integration tests — hit the real Matrix room.
Skipped automatically when ~/.matrix-cli/config is absent.
"""
import importlib.util
import time
from pathlib import Path

import pytest
import requests as _requests

CONFIG_PATH = Path.home() / ".matrix-cli" / "config"

pytestmark = pytest.mark.skipif(
    not CONFIG_PATH.exists(),
    reason="~/.matrix-cli/config not present — skipping integration tests",
)

_script = Path(__file__).parents[2] / "matrix-notify"
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
    """Create a private room for this test session and delete it afterwards."""
    homeserver = config["MATRIX_HOMESERVER"]
    token = config["MATRIX_ACCESS_TOKEN"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    resp = _requests.post(
        f"{homeserver}/_matrix/client/v3/createRoom",
        headers=headers,
        json={"name": "matrix-notify test", "preset": "private_chat", "visibility": "private"},
    )
    assert resp.status_code == 200, f"Failed to create test room: {resp.text}"
    room_id = resp.json()["room_id"]

    yield room_id

    _requests.post(
        f"{homeserver}/_matrix/client/v3/rooms/{_requests.utils.quote(room_id, safe='')}/leave",
        headers=headers,
    )


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
