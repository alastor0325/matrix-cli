"""
Integration tests — hit the real Matrix room.
Skipped automatically when ~/.matrix-cli/config is absent.
"""
import importlib.util
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

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


@pytest.fixture
def config():
    return matrix_notify.load_config(str(CONFIG_PATH))


@pytest.fixture
def sessions_file(tmp_path):
    return str(tmp_path / "sessions.json")


class TestLiveAPI:
    def test_send_log_message(self, config, sessions_file):
        thread_id = matrix_notify.ensure_thread("integration-test", config, sessions_file)
        resp_event_id = matrix_notify.send_message(
            "log", f"integration test log {time.time()}", thread_id, config
        )
        assert resp_event_id.startswith("$")

    def test_send_alert_message(self, config, sessions_file):
        thread_id = matrix_notify.ensure_thread("integration-test", config, sessions_file)
        resp_event_id = matrix_notify.send_message(
            "alert", f"integration test alert {time.time()}", thread_id, config
        )
        assert resp_event_id.startswith("$")

    def test_send_done_message(self, config, sessions_file):
        thread_id = matrix_notify.ensure_thread("integration-test", config, sessions_file)
        resp_event_id = matrix_notify.send_message(
            "done", f"integration test done {time.time()}", thread_id, config
        )
        assert resp_event_id.startswith("$")

    def test_two_sessions_create_two_threads(self, config, sessions_file):
        thread_a = matrix_notify.ensure_thread("integ-session-a", config, sessions_file)
        thread_b = matrix_notify.ensure_thread("integ-session-b", config, sessions_file)
        assert thread_a != thread_b

    def test_same_session_reuses_thread(self, config, sessions_file):
        thread_1 = matrix_notify.ensure_thread("integ-reuse", config, sessions_file)
        thread_2 = matrix_notify.ensure_thread("integ-reuse", config, sessions_file)
        assert thread_1 == thread_2
