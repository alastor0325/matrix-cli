import importlib.util
import json
import os
import shutil
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


# ---------------------------------------------------------------------------
# Bot auto-join during setup
# ---------------------------------------------------------------------------

class TestBotAutoJoin:
    def _base_inputs(self, tmp_path):
        return [str(tmp_path), "", "!room:m.org", "@you:m.org", "", ""]

    def _mock_put(self, event_id="$e:m.org"):
        m = MagicMock(status_code=200)
        m.json.return_value = {"event_id": event_id}
        return m

    def test_join_called_with_room_id(self, tmp_path):
        _load()
        join_resp = MagicMock(status_code=200)
        join_resp.json.return_value = {"room_id": "!room:m.org"}
        with patch("subprocess.call"), patch("subprocess.check_call"):
            with patch.object(matrix_notify, "install_to_path"):
                with patch("builtins.input", side_effect=iter(self._base_inputs(tmp_path))):
                    with patch("getpass.getpass", return_value="tok"):
                        with patch("requests.post", return_value=join_resp) as mock_post:
                            with patch("requests.put", return_value=self._mock_put()):
                                matrix_notify.setup()
        assert mock_post.called
        urls = [c[0][0] for c in mock_post.call_args_list]
        assert any("join" in url for url in urls)
        assert any("room" in url for url in urls)

    def test_join_failure_does_not_block_setup(self, tmp_path):
        _load()
        join_resp = MagicMock(status_code=403)
        join_resp.json.return_value = {"errcode": "M_FORBIDDEN"}
        with patch("subprocess.call"), patch("subprocess.check_call"):
            with patch.object(matrix_notify, "install_to_path"):
                with patch("builtins.input", side_effect=iter(self._base_inputs(tmp_path))):
                    with patch("getpass.getpass", return_value="tok"):
                        with patch("requests.post", return_value=join_resp):
                            with patch("requests.put", return_value=self._mock_put()):
                                matrix_notify.setup()  # should not raise


# ---------------------------------------------------------------------------
# Setup input validation — all steps loop on invalid input
# ---------------------------------------------------------------------------

def _run_setup(inputs, token="tok", tmp_path=None):
    """Helper: run setup() with mocked inputs, returns without error if successful."""
    _load()
    mock_put = MagicMock(status_code=200)
    mock_put.json.return_value = {"event_id": "$e:m.org"}
    mock_post = MagicMock(status_code=200)
    mock_post.json.return_value = {"room_id": "!room:m.org"}
    it = iter(inputs)
    with patch.object(matrix_notify, "_script_path", return_value=Path("/fake/script")):
        with patch("subprocess.call"):
            with patch("subprocess.check_call"):
                with patch.object(matrix_notify, "install_to_path"):
                    with patch("builtins.input", side_effect=it):
                        with patch("getpass.getpass", return_value=token):
                            with patch("requests.put", return_value=mock_put):
                                with patch("requests.post", return_value=mock_post):
                                    matrix_notify.setup()


class TestSetupValidation:
    # Step 0 — config directory
    def test_step0_retries_on_bad_path(self, tmp_path):
        # First input is an unwritable path (mocked to fail), second is valid
        _load()
        config_dir = tmp_path / "cfg"
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"event_id": "$e:m.org"}
        call_count = 0
        original_mkdir = Path.mkdir

        def mkdir_side_effect(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("permission denied")
            return original_mkdir(self, *args, **kwargs)

        mock_post = MagicMock(status_code=200)
        mock_post.json.return_value = {"room_id": "!room:m.org"}
        inputs = iter(["bad/path", str(config_dir), "", "!room:m.org", "@you:m.org", "", ""])
        with patch.object(Path, "mkdir", mkdir_side_effect):
            with patch("subprocess.call"):
                with patch("subprocess.check_call"):
                    with patch.object(matrix_notify, "install_to_path"):
                        with patch("builtins.input", side_effect=inputs):
                            with patch("getpass.getpass", return_value="tok"):
                                with patch("requests.put", return_value=mock_resp):
                                    with patch("requests.post", return_value=mock_post):
                                        matrix_notify.setup()

    # Step 1 — homeserver
    def test_step1_retries_on_invalid_url(self, tmp_path):
        # invalid URL first, then valid
        inputs = [str(tmp_path), "not-a-url", "https://example.org", "!room:m.org", "@you:m.org", "", ""]
        _run_setup(inputs)

    def test_step1_accepts_default(self, tmp_path):
        inputs = [str(tmp_path), "", "!room:m.org", "@you:m.org", "", ""]
        _run_setup(inputs)

    # Step 2 — access token
    def test_step2_strips_ansi_escape_residue(self, tmp_path):
        # Simulate paste with arrow-key escape sequences: ESC stripped by getpass
        # but leaves '[C[C[C' (printable bracket+letter pairs) before the token
        inputs = [str(tmp_path), "", "!room:m.org", "@you:m.org", "", ""]
        _run_setup(inputs, token="[C[C[Cmat_validtoken")
        config_path = tmp_path / "config"
        config = {k: v for k, _, v in (l.partition("=") for l in config_path.read_text().splitlines() if "=" in l)}
        assert config["MATRIX_ACCESS_TOKEN"] == "mat_validtoken"

    def test_step2_strips_full_ansi_sequence(self, tmp_path):
        # ESC+[+C survives intact through getpass in some terminals
        inputs = [str(tmp_path), "", "!room:m.org", "@you:m.org", "", ""]
        _run_setup(inputs, token="\x1b[Cmat_validtoken")
        config_path = tmp_path / "config"
        config = {k: v for k, _, v in (l.partition("=") for l in config_path.read_text().splitlines() if "=" in l)}
        assert config["MATRIX_ACCESS_TOKEN"] == "mat_validtoken"

    def test_step2_retries_on_empty_token(self, tmp_path):
        inputs = [str(tmp_path), "", "!room:m.org", "@you:m.org", "", ""]
        _load()
        mock_put = MagicMock(status_code=200)
        mock_put.json.return_value = {"event_id": "$e:m.org"}
        mock_post = MagicMock(status_code=200)
        mock_post.json.return_value = {"room_id": "!room:m.org"}
        tokens = iter(["", "", "valid-token"])
        with patch("subprocess.call"):
            with patch("subprocess.check_call"):
                with patch.object(matrix_notify, "install_to_path"):
                    with patch("builtins.input", side_effect=iter(inputs)):
                        with patch("getpass.getpass", side_effect=tokens):
                            with patch("requests.put", return_value=mock_put):
                                with patch("requests.post", return_value=mock_post):
                                    matrix_notify.setup()

    # Step 3 — room ID
    def test_step3_retries_on_empty_room_id(self, tmp_path):
        inputs = [str(tmp_path), "", "", "!room:m.org", "@you:m.org", "", ""]
        _run_setup(inputs)

    def test_step3_retries_on_invalid_format(self, tmp_path):
        inputs = [str(tmp_path), "", "notaroomid", "!room:m.org", "@you:m.org", "", ""]
        _run_setup(inputs)

    # Step 4 — user ID
    def test_step4_retries_on_empty_user_id(self, tmp_path):
        inputs = [str(tmp_path), "", "!room:m.org", "", "@you:m.org", "", ""]
        _run_setup(inputs)

    def test_step4_retries_on_missing_at(self, tmp_path):
        inputs = [str(tmp_path), "", "!room:m.org", "you:m.org", "@you:m.org", "", ""]
        _run_setup(inputs)

    def test_step4_retries_on_missing_colon(self, tmp_path):
        inputs = [str(tmp_path), "", "!room:m.org", "@youmorg", "@you:m.org", "", ""]
        _run_setup(inputs)


# ---------------------------------------------------------------------------
# git hooks auto-configuration
# ---------------------------------------------------------------------------

class TestGitHooksSetup:
    def test_git_hooks_path_configured_during_setup(self, tmp_path):
        _load()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()
        fake_script = repo_dir / "matrix-notify"
        fake_script.write_text("")

        # input() call order: config dir, homeserver, room id, user id, test room id, install dir
        # Use tmp_path for config dir to avoid writing to real ~/.matrix-cli/
        inputs = iter([str(tmp_path / "config"), "", "!room:m.org", "@you:m.org", "", ""])
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"event_id": "$e:m.org"}
        mock_post = MagicMock(status_code=200)
        mock_post.json.return_value = {"room_id": "!testroom:m.org"}

        with patch.object(matrix_notify, "_script_path", return_value=fake_script):
            with patch("subprocess.call") as mock_call:
                with patch("subprocess.check_call"):
                    with patch.object(matrix_notify, "install_to_path"):
                        with patch("builtins.input", side_effect=inputs):
                            with patch("getpass.getpass", return_value="tok"):
                                with patch("requests.put", return_value=mock_resp):
                                    with patch("requests.post", return_value=mock_post):
                                        matrix_notify.setup()
        git_calls = [c for c in mock_call.call_args_list
                     if c[0][0][:3] == ["git", "config", "core.hooksPath"]]
        assert len(git_calls) == 1
        assert git_calls[0][0][0] == ["git", "config", "core.hooksPath", "scripts"]


# ---------------------------------------------------------------------------
# Auto-setup when config is missing
# ---------------------------------------------------------------------------

class TestAutoSetup:
    def test_missing_config_triggers_setup(self, tmp_path):
        _load()
        with patch.object(matrix_notify, "CONFIG_PATH", tmp_path / "nonexistent"):
            with patch.object(matrix_notify, "setup") as mock_setup:
                with patch("sys.argv", ["matrix-notify", "log", "hello"]):
                    matrix_notify.main()
                    mock_setup.assert_called_once()

    def test_present_config_does_not_trigger_setup(self, tmp_path):
        _load()
        cfg = tmp_path / "config"
        cfg.write_text(
            "MATRIX_HOMESERVER=https://chat.mozilla.org\n"
            "MATRIX_ACCESS_TOKEN=tok\n"
            "MATRIX_ROOM_ID=!abc:mozilla.org\n"
            "MATRIX_NOTIFY_USER=@you:mozilla.org\n"
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"event_id": "$e:mozilla.org"}
        with patch.object(matrix_notify, "CONFIG_PATH", cfg):
            with patch.object(matrix_notify, "SESSIONS_PATH", tmp_path / "sessions.json"):
                with patch.object(matrix_notify, "setup") as mock_setup:
                    with patch("sys.argv", ["matrix-notify", "log", "hello"]):
                        with patch("requests.put", return_value=mock_resp):
                            matrix_notify.main()
                            mock_setup.assert_not_called()


# ---------------------------------------------------------------------------
# install_to_path — cross-platform PATH installation
# ---------------------------------------------------------------------------

class TestInstallToPath:
    def _fake_script(self, tmp_path):
        s = tmp_path / "matrix-notify"
        s.write_text("#!/usr/bin/env python3\n")
        s.chmod(0o644)
        return s

    def _fake_venv(self, tmp_path):
        venv_dir = tmp_path / ".venv"
        bin_dir = venv_dir / "bin"
        bin_dir.mkdir(parents=True)
        python = bin_dir / "python3"
        python.write_text("#!/bin/sh\nexec python3 \"$@\"\n")
        python.chmod(0o755)
        return venv_dir

    def test_unix_creates_wrapper_in_local_bin(self, tmp_path):
        _load()
        src = self._fake_script(tmp_path)
        bin_dir = tmp_path / "bin"
        venv_dir = self._fake_venv(tmp_path)
        with patch("sys.platform", "linux"):
            with patch.object(matrix_notify, "_script_path", return_value=src):
                with patch.object(matrix_notify, "_setup_venv", return_value=venv_dir):
                    matrix_notify.install_to_path(bin_dir=bin_dir, venv_dir=venv_dir)
        wrapper = bin_dir / "matrix-notify"
        assert wrapper.exists()
        assert not wrapper.is_symlink()
        content = wrapper.read_text()
        assert str(src) in content
        assert "python3" in content

    def test_unix_bin_dir_created_if_missing(self, tmp_path):
        _load()
        src = self._fake_script(tmp_path)
        bin_dir = tmp_path / "newdir" / "bin"
        venv_dir = self._fake_venv(tmp_path)
        assert not bin_dir.exists()
        with patch("sys.platform", "darwin"):
            with patch.object(matrix_notify, "_script_path", return_value=src):
                with patch.object(matrix_notify, "_setup_venv", return_value=venv_dir):
                    matrix_notify.install_to_path(bin_dir=bin_dir, venv_dir=venv_dir)
        assert bin_dir.exists()

    def test_windows_creates_bat_wrapper(self, tmp_path):
        _load()
        bin_dir = tmp_path / "bin"
        script = self._fake_script(tmp_path)
        venv_dir = tmp_path / ".venv"
        (venv_dir / "Scripts").mkdir(parents=True)
        (venv_dir / "Scripts" / "python.exe").write_text("")
        with patch("sys.platform", "win32"):
            with patch.object(matrix_notify, "_setup_venv", return_value=venv_dir):
                matrix_notify.install_to_path(bin_dir=bin_dir, script=script, venv_dir=venv_dir)
        bat = bin_dir / "matrix-notify.bat"
        assert bat.exists()
        assert "python" in bat.read_text().lower()

    def test_returns_bin_dir(self, tmp_path):
        _load()
        src = self._fake_script(tmp_path)
        bin_dir = tmp_path / "bin"
        venv_dir = self._fake_venv(tmp_path)
        with patch("sys.platform", "linux"):
            with patch.object(matrix_notify, "_script_path", return_value=src):
                with patch.object(matrix_notify, "_setup_venv", return_value=venv_dir):
                    result = matrix_notify.install_to_path(bin_dir=bin_dir, venv_dir=venv_dir)
        assert result == bin_dir
