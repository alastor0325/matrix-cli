import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, mock_open, patch

import pytest

# Load the script as a module despite having no .py extension
_script = Path(__file__).parents[2] / "matrix-notify"
_spec = importlib.util.spec_from_loader(
    "matrix_notify",
    importlib.machinery.SourceFileLoader("matrix_notify", str(_script)),
)
matrix_notify = importlib.util.module_from_spec(_spec)


def _load():
    _spec.loader.exec_module(matrix_notify)


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_valid_config(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(
            "MATRIX_HOMESERVER=https://chat.mozilla.org\n"
            "MATRIX_ACCESS_TOKEN=tok\n"
            "MATRIX_ROOM_ID=!abc:mozilla.org\n"
            "MATRIX_NOTIFY_USER=@alwu:mozilla.org\n"
        )
        _load()
        result = matrix_notify.load_config(str(cfg))
        assert result["MATRIX_HOMESERVER"] == "https://chat.mozilla.org"
        assert result["MATRIX_ACCESS_TOKEN"] == "tok"
        assert result["MATRIX_ROOM_ID"] == "!abc:mozilla.org"
        assert result["MATRIX_NOTIFY_USER"] == "@alwu:mozilla.org"

    def test_missing_file_raises(self, tmp_path):
        _load()
        with pytest.raises(SystemExit):
            matrix_notify.load_config(str(tmp_path / "nonexistent"))

    def test_missing_key_raises(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("MATRIX_HOMESERVER=https://chat.mozilla.org\n")
        _load()
        with pytest.raises(SystemExit):
            matrix_notify.load_config(str(cfg))


# ---------------------------------------------------------------------------
# get_session_name
# ---------------------------------------------------------------------------

class TestGetSessionName:
    def test_inside_tmux(self):
        _load()
        with patch("subprocess.check_output", return_value=b"bug-1234\n"):
            assert matrix_notify.get_session_name() == "bug-1234"

    def test_outside_tmux_fallback(self):
        _load()
        with patch("subprocess.check_output", side_effect=Exception("no tmux")):
            with patch("socket.gethostname", return_value="mymac"):
                with patch("os.getpid", return_value=42):
                    result = matrix_notify.get_session_name()
                    assert result == "mymac-42"


# ---------------------------------------------------------------------------
# load_sessions / save_sessions
# ---------------------------------------------------------------------------

class TestSessions:
    def test_load_empty_when_missing(self, tmp_path):
        _load()
        result = matrix_notify.load_sessions(str(tmp_path / "sessions.json"))
        assert result == {}

    def test_save_and_reload(self, tmp_path):
        _load()
        path = str(tmp_path / "sessions.json")
        data = {"bug-1234": {"thread_id": "$abc:mozilla.org", "started": "2026-04-01T10:00:00"}}
        matrix_notify.save_sessions(path, data)
        assert matrix_notify.load_sessions(path) == data


# ---------------------------------------------------------------------------
# Thread isolation — two sessions produce two separate ensure_thread calls
# ---------------------------------------------------------------------------

class TestThreadIsolation:
    def test_two_sessions_create_two_roots(self, tmp_path):
        _load()
        sessions_path = str(tmp_path / "sessions.json")
        config = {
            "MATRIX_HOMESERVER": "https://chat.mozilla.org",
            "MATRIX_ACCESS_TOKEN": "tok",
            "MATRIX_ROOM_ID": "!abc:mozilla.org",
            "MATRIX_NOTIFY_USER": "@alwu:mozilla.org",
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"event_id": "$root:mozilla.org"}

        with patch("requests.put", return_value=mock_resp) as mock_put:
            matrix_notify.ensure_thread("bug-1234", config, sessions_path)
            matrix_notify.ensure_thread("bug-5678", config, sessions_path)
            assert mock_put.call_count == 2

        sessions = matrix_notify.load_sessions(sessions_path)
        assert "bug-1234" in sessions
        assert "bug-5678" in sessions
        assert sessions["bug-1234"]["thread_id"] != sessions["bug-5678"]["thread_id"] or \
               sessions["bug-1234"]["thread_id"] == "$root:mozilla.org"

    def test_same_session_reuses_thread(self, tmp_path):
        _load()
        sessions_path = str(tmp_path / "sessions.json")
        config = {
            "MATRIX_HOMESERVER": "https://chat.mozilla.org",
            "MATRIX_ACCESS_TOKEN": "tok",
            "MATRIX_ROOM_ID": "!abc:mozilla.org",
            "MATRIX_NOTIFY_USER": "@alwu:mozilla.org",
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"event_id": "$root:mozilla.org"}

        with patch("requests.put", return_value=mock_resp) as mock_put:
            matrix_notify.ensure_thread("bug-1234", config, sessions_path)
            matrix_notify.ensure_thread("bug-1234", config, sessions_path)
            # root message created only once
            assert mock_put.call_count == 1


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

class TestMessageFormatting:
    def _make_config(self):
        return {
            "MATRIX_HOMESERVER": "https://chat.mozilla.org",
            "MATRIX_ACCESS_TOKEN": "tok",
            "MATRIX_ROOM_ID": "!abc:mozilla.org",
            "MATRIX_NOTIFY_USER": "@alwu:mozilla.org",
        }

    def _capture_body(self, mock_put):
        return mock_put.call_args[1]["json"]

    def test_log_plain_text(self, tmp_path):
        _load()
        config = self._make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"event_id": "$e:mozilla.org"}

        with patch("requests.put", return_value=mock_resp) as mock_put:
            matrix_notify.send_message("log", "hello", "$root:mozilla.org", config)
            body = self._capture_body(mock_put)
            assert "[log]" in body["body"]
            assert "formatted_body" not in body or "<b>" not in body.get("formatted_body", "")

    def test_alert_has_bold_and_mention(self, tmp_path):
        _load()
        config = self._make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"event_id": "$e:mozilla.org"}

        with patch("requests.put", return_value=mock_resp) as mock_put:
            matrix_notify.send_message("alert", "needs approval", "$root:mozilla.org", config)
            body = self._capture_body(mock_put)
            assert "formatted_body" in body
            assert "<b>" in body["formatted_body"]
            assert "matrix.to" in body["formatted_body"]
            assert "@alwu:mozilla.org" in body["formatted_body"]

    def test_done_has_bold(self, tmp_path):
        _load()
        config = self._make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"event_id": "$e:mozilla.org"}

        with patch("requests.put", return_value=mock_resp) as mock_put:
            matrix_notify.send_message("done", "bug landed", "$root:mozilla.org", config)
            body = self._capture_body(mock_put)
            assert "formatted_body" in body
            assert "<b>" in body["formatted_body"]
            assert "[done]" in body["body"]


# ---------------------------------------------------------------------------
# Matrix API — URL, headers, body shape, retry on 429, error exit
# ---------------------------------------------------------------------------

class TestMatrixAPI:
    def _make_config(self):
        return {
            "MATRIX_HOMESERVER": "https://chat.mozilla.org",
            "MATRIX_ACCESS_TOKEN": "tok",
            "MATRIX_ROOM_ID": "!abc:mozilla.org",
            "MATRIX_NOTIFY_USER": "@alwu:mozilla.org",
        }

    def test_correct_url_and_auth_header(self):
        _load()
        config = self._make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"event_id": "$e:mozilla.org"}

        with patch("requests.put", return_value=mock_resp) as mock_put:
            matrix_notify.send_message("log", "hi", "$root:mozilla.org", config)
            url = mock_put.call_args[0][0]
            headers = mock_put.call_args[1]["headers"]
            assert "https://chat.mozilla.org/_matrix/client/v3/rooms/" in url
            assert "send/m.room.message/" in url
            assert headers["Authorization"] == "Bearer tok"

    def test_threaded_reply_has_relates_to(self):
        _load()
        config = self._make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"event_id": "$e:mozilla.org"}

        with patch("requests.put", return_value=mock_resp) as mock_put:
            matrix_notify.send_message("log", "hi", "$root:mozilla.org", config)
            body = mock_put.call_args[1]["json"]
            assert body["m.relates_to"]["rel_type"] == "m.thread"
            assert body["m.relates_to"]["event_id"] == "$root:mozilla.org"

    def test_retry_on_429(self):
        _load()
        config = self._make_config()
        rate_limit = MagicMock()
        rate_limit.status_code = 429
        ok = MagicMock()
        ok.status_code = 200
        ok.json.return_value = {"event_id": "$e:mozilla.org"}

        with patch("requests.put", side_effect=[rate_limit, rate_limit, ok]) as mock_put:
            with patch("time.sleep"):
                matrix_notify.send_message("log", "hi", "$root:mozilla.org", config)
                assert mock_put.call_count == 3

    def test_error_exits_on_4xx(self):
        _load()
        config = self._make_config()
        err = MagicMock()
        err.status_code = 403
        err.text = "Forbidden"

        with patch("requests.put", return_value=err):
            with pytest.raises(SystemExit):
                matrix_notify.send_message("log", "hi", "$root:mozilla.org", config)
