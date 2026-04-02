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
_script = Path(__file__).parents[2] / "matrix-cli"
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
    def test_inside_tmux_uses_session_name(self):
        _load()
        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,1234,0"}):
            with patch("subprocess.check_output", return_value=b"bug-1234\n"):
                assert matrix_notify.get_session_name() == "bug-1234"

    def test_no_tmux_env_uses_hostname_even_if_tmux_running(self):
        # Without $TMUX (e.g. Claude Code), hostname is used — tmux is not queried
        _load()
        env = {k: v for k, v in os.environ.items() if k != "TMUX"}
        with patch.dict(os.environ, env, clear=True):
            with patch("subprocess.check_output") as mock_sub:
                with patch("socket.gethostname", return_value="my-host"):
                    assert matrix_notify.get_session_name() == "my-host"
                    mock_sub.assert_not_called()

    def test_tmux_unavailable_falls_back_to_hostname(self):
        _load()
        with patch("subprocess.check_output", side_effect=Exception("tmux not found")):
            with patch("socket.gethostname", return_value="mymac"):
                assert matrix_notify.get_session_name() == "mymac"

    def test_tmux_empty_output_falls_back_to_hostname(self):
        _load()
        with patch("subprocess.check_output", return_value=b"\n"):
            with patch("socket.gethostname", return_value="fallback-host"):
                assert matrix_notify.get_session_name() == "fallback-host"


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
# get_tmux_session
# ---------------------------------------------------------------------------

class TestGetTmuxSession:
    def test_returns_session_name_when_tmux_running(self):
        _load()
        with patch("subprocess.check_output", return_value=b"matrix\n"):
            assert matrix_notify.get_tmux_session() == "matrix"

    def test_returns_none_when_tmux_unavailable(self):
        _load()
        with patch("subprocess.check_output", side_effect=Exception("no tmux")):
            assert matrix_notify.get_tmux_session() is None

    def test_returns_none_on_empty_output(self):
        _load()
        with patch("subprocess.check_output", return_value=b"\n"):
            assert matrix_notify.get_tmux_session() is None

    def test_works_regardless_of_tmux_env_var(self):
        _load()
        env = {k: v for k, v in os.environ.items() if k != "TMUX"}
        with patch.dict(os.environ, env, clear=True):
            with patch("subprocess.check_output", return_value=b"matrix\n"):
                assert matrix_notify.get_tmux_session() == "matrix"


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

    def test_stores_tmux_target_when_tmux_running(self, tmp_path):
        _load()
        sessions_path = str(tmp_path / "sessions.json")
        config = {
            "MATRIX_HOMESERVER": "https://chat.mozilla.org",
            "MATRIX_ACCESS_TOKEN": "tok",
            "MATRIX_ROOM_ID": "!abc:mozilla.org",
            "MATRIX_NOTIFY_USER": "@alwu:mozilla.org",
        }
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"event_id": "$root:mozilla.org"}
        with patch("requests.put", return_value=mock_resp):
            with patch("subprocess.check_output", return_value=b"matrix\n"):
                matrix_notify.ensure_thread("my-host", config, sessions_path)
        sessions = matrix_notify.load_sessions(sessions_path)
        assert sessions["my-host"]["tmux_target"] == "matrix"

    def test_no_tmux_target_when_tmux_unavailable(self, tmp_path):
        _load()
        sessions_path = str(tmp_path / "sessions.json")
        config = {
            "MATRIX_HOMESERVER": "https://chat.mozilla.org",
            "MATRIX_ACCESS_TOKEN": "tok",
            "MATRIX_ROOM_ID": "!abc:mozilla.org",
            "MATRIX_NOTIFY_USER": "@alwu:mozilla.org",
        }
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"event_id": "$root:mozilla.org"}
        with patch("requests.put", return_value=mock_resp):
            with patch("subprocess.check_output", side_effect=Exception("no tmux")):
                matrix_notify.ensure_thread("my-host", config, sessions_path)
        sessions = matrix_notify.load_sessions(sessions_path)
        assert "tmux_target" not in sessions["my-host"]


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
            assert body["m.mentions"] == {"user_ids": ["@alwu:mozilla.org"]}

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
# Bot creates room and invites user during setup
# ---------------------------------------------------------------------------

class TestBotRoomSetup:
    def _base_inputs(self, tmp_path):
        # config_dir, homeserver(default), user_id, accept_invite, test_room(auto), install_dir(default)
        return [str(tmp_path), "", "@you:m.org", "", "", "", ""]

    def _mock_put(self, event_id="$e:m.org"):
        m = MagicMock(status_code=200)
        m.json.return_value = {"event_id": event_id}
        return m

    def _mock_post(self, room_id="!room:m.org"):
        m = MagicMock(status_code=200)
        m.json.return_value = {"room_id": room_id}
        return m

    def test_room_created_via_api(self, tmp_path):
        _load()
        with patch("subprocess.call"), patch("subprocess.check_call"):
            with patch.object(matrix_notify, "install_to_path"):
                with patch("builtins.input", side_effect=iter(self._base_inputs(tmp_path))):
                    with patch("getpass.getpass", return_value="tok"):
                        with patch("requests.post", return_value=self._mock_post()) as mock_post:
                            with patch("requests.put", return_value=self._mock_put()):
                                matrix_notify.setup()
        urls = [c[0][0] for c in mock_post.call_args_list]
        assert any("createRoom" in url for url in urls)

    def test_user_invited_after_room_creation(self, tmp_path):
        _load()
        with patch("subprocess.call"), patch("subprocess.check_call"):
            with patch.object(matrix_notify, "install_to_path"):
                with patch("builtins.input", side_effect=iter(self._base_inputs(tmp_path))):
                    with patch("getpass.getpass", return_value="tok"):
                        with patch("requests.post", return_value=self._mock_post()) as mock_post:
                            with patch("requests.put", return_value=self._mock_put()):
                                matrix_notify.setup()
        bodies = [c[1].get("json", {}) for c in mock_post.call_args_list]
        assert any(b.get("user_id") == "@you:m.org" for b in bodies)

    def test_room_id_saved_to_config(self, tmp_path):
        _load()
        with patch("subprocess.call"), patch("subprocess.check_call"):
            with patch.object(matrix_notify, "install_to_path"):
                with patch("builtins.input", side_effect=iter(self._base_inputs(tmp_path))):
                    with patch("getpass.getpass", return_value="tok"):
                        with patch("requests.post", return_value=self._mock_post("!created:m.org")):
                            with patch("requests.put", return_value=self._mock_put()):
                                matrix_notify.setup()
        config = {k: v for k, _, v in (l.partition("=") for l in (tmp_path / "config").read_text().splitlines() if "=" in l)}
        assert config["MATRIX_ROOM_ID"] == "!created:m.org"


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
        _load()
        config_dir = tmp_path / "cfg"
        mock_put = MagicMock(status_code=200)
        mock_put.json.return_value = {"event_id": "$e:m.org"}
        mock_post = MagicMock(status_code=200)
        mock_post.json.return_value = {"room_id": "!room:m.org"}
        call_count = 0
        original_mkdir = Path.mkdir

        def mkdir_side_effect(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("permission denied")
            return original_mkdir(self, *args, **kwargs)

        inputs = iter(["bad/path", str(config_dir), "", "@you:m.org", "", "", "", ""])
        with patch.object(Path, "mkdir", mkdir_side_effect):
            with patch("subprocess.call"):
                with patch("subprocess.check_call"):
                    with patch.object(matrix_notify, "install_to_path"):
                        with patch("builtins.input", side_effect=inputs):
                            with patch("getpass.getpass", return_value="tok"):
                                with patch("requests.put", return_value=mock_put):
                                    with patch("requests.post", return_value=mock_post):
                                        matrix_notify.setup()

    # Step 1 — homeserver
    def test_step1_retries_on_invalid_url(self, tmp_path):
        inputs = [str(tmp_path), "not-a-url", "https://example.org", "@you:m.org", "", "", "", ""]
        _run_setup(inputs)

    def test_step1_accepts_default(self, tmp_path):
        inputs = [str(tmp_path), "", "@you:m.org", "", "", "", ""]
        _run_setup(inputs)

    # Step 2 — access token
    def test_step2_strips_ansi_escape_residue(self, tmp_path):
        inputs = [str(tmp_path), "", "@you:m.org", "", "", "", ""]
        _run_setup(inputs, token="[C[C[Cmat_validtoken")
        config = {k: v for k, _, v in (l.partition("=") for l in (tmp_path / "config").read_text().splitlines() if "=" in l)}
        assert config["MATRIX_ACCESS_TOKEN"] == "mat_validtoken"

    def test_step2_strips_full_ansi_sequence(self, tmp_path):
        inputs = [str(tmp_path), "", "@you:m.org", "", "", "", ""]
        _run_setup(inputs, token="\x1b[Cmat_validtoken")
        config = {k: v for k, _, v in (l.partition("=") for l in (tmp_path / "config").read_text().splitlines() if "=" in l)}
        assert config["MATRIX_ACCESS_TOKEN"] == "mat_validtoken"

    def test_step2_retries_on_empty_token(self, tmp_path):
        inputs = [str(tmp_path), "", "@you:m.org", "", "", "", ""]
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

    # Step 3 — user ID
    def test_step3_retries_on_empty_user_id(self, tmp_path):
        inputs = [str(tmp_path), "", "", "@you:m.org", "", "", "", ""]
        _run_setup(inputs)

    def test_step3_retries_on_missing_at(self, tmp_path):
        inputs = [str(tmp_path), "", "you:m.org", "@you:m.org", "", "", "", ""]
        _run_setup(inputs)

    def test_step3_retries_on_missing_colon(self, tmp_path):
        inputs = [str(tmp_path), "", "@youmorg", "@you:m.org", "", "", "", ""]
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

        # input() call order: config dir, homeserver, user id, accept invite, test room id, install dir
        # Use tmp_path for config dir to avoid writing to real ~/.matrix-cli/
        inputs = iter([str(tmp_path / "config"), "", "@you:m.org", "", "", "", ""])
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"event_id": "$e:m.org"}
        mock_post = MagicMock(status_code=200)
        mock_post.json.return_value = {"room_id": "!room:m.org"}

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
        s = tmp_path / "matrix-cli"
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
        wrapper = bin_dir / "matrix-cli"
        assert wrapper.exists()
        assert not wrapper.is_symlink()
        content = wrapper.read_text()
        assert str(src) in content
        assert "python3" in content
        shim = bin_dir / "matrix-notify"
        assert shim.exists()
        assert str(src) in shim.read_text()

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
        bat = bin_dir / "matrix-cli.bat"
        assert bat.exists()
        assert "python" in bat.read_text().lower()
        shim = bin_dir / "matrix-notify.bat"
        assert shim.exists()

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


# ---------------------------------------------------------------------------
# _process_sync_events
# ---------------------------------------------------------------------------

class TestProcessSyncEvents:
    def _sessions(self, tmp_path):
        path = str(tmp_path / "sessions.json")
        _load()
        matrix_notify.save_sessions(path, {
            "bug-1234": {"thread_id": "$root1:mozilla.org", "started": "2026-04-01T10:00:00"},
        })
        return path

    def test_returns_empty_when_no_events(self, tmp_path):
        _load()
        sessions_path = self._sessions(tmp_path)
        result = matrix_notify._process_sync_events({}, "@me:mozilla.org", sessions_path)
        assert result == []

    def test_returns_event_for_matching_thread(self, tmp_path):
        _load()
        sessions_path = self._sessions(tmp_path)
        data = {"rooms": {"join": {"!r:m.org": {"timeline": {"events": [{
            "type": "m.room.message",
            "sender": "@me:mozilla.org",
            "content": {
                "body": "hello",
                "m.relates_to": {"rel_type": "m.thread", "event_id": "$root1:mozilla.org"},
            },
        }]}}}}}
        result = matrix_notify._process_sync_events(data, "@me:mozilla.org", sessions_path)
        assert result == [("bug-1234", "hello")]

    def test_ignores_event_from_other_sender(self, tmp_path):
        _load()
        sessions_path = self._sessions(tmp_path)
        data = {"rooms": {"join": {"!r:m.org": {"timeline": {"events": [{
            "type": "m.room.message",
            "sender": "@other:mozilla.org",
            "content": {
                "body": "hello",
                "m.relates_to": {"rel_type": "m.thread", "event_id": "$root1:mozilla.org"},
            },
        }]}}}}}
        result = matrix_notify._process_sync_events(data, "@me:mozilla.org", sessions_path)
        assert result == []

    def test_ignores_non_thread_reply(self, tmp_path):
        _load()
        sessions_path = self._sessions(tmp_path)
        data = {"rooms": {"join": {"!r:m.org": {"timeline": {"events": [{
            "type": "m.room.message",
            "sender": "@me:mozilla.org",
            "content": {
                "body": "plain message",
                "m.relates_to": {"rel_type": "m.reference", "event_id": "$root1:mozilla.org"},
            },
        }]}}}}}
        result = matrix_notify._process_sync_events(data, "@me:mozilla.org", sessions_path)
        assert result == []

    def test_ignores_event_for_unknown_thread(self, tmp_path):
        _load()
        sessions_path = self._sessions(tmp_path)
        data = {"rooms": {"join": {"!r:m.org": {"timeline": {"events": [{
            "type": "m.room.message",
            "sender": "@me:mozilla.org",
            "content": {
                "body": "hello",
                "m.relates_to": {"rel_type": "m.thread", "event_id": "$unknown:mozilla.org"},
            },
        }]}}}}}
        result = matrix_notify._process_sync_events(data, "@me:mozilla.org", sessions_path)
        assert result == []

    def test_ignores_empty_body(self, tmp_path):
        _load()
        sessions_path = self._sessions(tmp_path)
        data = {"rooms": {"join": {"!r:m.org": {"timeline": {"events": [{
            "type": "m.room.message",
            "sender": "@me:mozilla.org",
            "content": {
                "body": "   ",
                "m.relates_to": {"rel_type": "m.thread", "event_id": "$root1:mozilla.org"},
            },
        }]}}}}}
        result = matrix_notify._process_sync_events(data, "@me:mozilla.org", sessions_path)
        assert result == []


# ---------------------------------------------------------------------------
# _forward_to_tmux
# ---------------------------------------------------------------------------

class TestForwardToTmux:
    def test_prefixes_with_matrix_tag(self):
        _load()
        with patch("subprocess.run") as mock_run:
            matrix_notify._forward_to_tmux("bug-1234", "hello world")
            sent_text = mock_run.call_args[0][0][4]
            assert sent_text == "[matrix] hello world"

    def test_calls_tmux_send_keys_with_enter(self):
        _load()
        with patch("subprocess.run") as mock_run:
            matrix_notify._forward_to_tmux("bug-1234", "hello world")
            args = mock_run.call_args[0][0]
            assert args[0] == "tmux"
            assert args[1] == "send-keys"
            assert args[-1] == "Enter"
            assert args[2] == "-t"
            assert args[3] == "bug-1234"

    def test_swallows_exception_on_failure(self):
        _load()
        with patch("subprocess.run", side_effect=Exception("tmux not found")):
            matrix_notify._forward_to_tmux("bug-1234", "hello")  # should not raise

    def test_uses_tmux_target_from_sessions_when_present(self, tmp_path):
        _load()
        with patch.object(matrix_notify, "SESSIONS_PATH", tmp_path / "sessions.json"):
            matrix_notify.save_sessions(str(tmp_path / "sessions.json"), {
                "my-host": {
                    "thread_id": "$t:m.org",
                    "started": "2026-04-01T10:00:00",
                    "tmux_target": "matrix",
                }
            })
            with patch("subprocess.run") as mock_run:
                matrix_notify._forward_to_tmux("my-host", "hello")
                target = mock_run.call_args[0][0][3]
                assert target == "matrix"

    def test_falls_back_to_session_name_when_no_tmux_target(self, tmp_path):
        _load()
        with patch.object(matrix_notify, "SESSIONS_PATH", tmp_path / "sessions.json"):
            matrix_notify.save_sessions(str(tmp_path / "sessions.json"), {
                "matrix": {"thread_id": "$t:m.org", "started": "2026-04-01T10:00:00"}
            })
            with patch("subprocess.run") as mock_run:
                matrix_notify._forward_to_tmux("matrix", "hello")
                target = mock_run.call_args[0][0][3]
                assert target == "matrix"


# ---------------------------------------------------------------------------
# cmd_forward
# ---------------------------------------------------------------------------

class TestCmdForward:
    def test_prints_matrix_prefixed_text(self, capsys):
        _load()
        matrix_notify.cmd_forward("do something useful")
        out = capsys.readouterr().out
        assert out.strip() == "[matrix] do something useful"

    def test_preserves_original_text_in_output(self, capsys):
        _load()
        matrix_notify.cmd_forward("bug 1234 status?")
        out = capsys.readouterr().out
        assert "bug 1234 status?" in out


# ---------------------------------------------------------------------------
# _get_initial_since
# ---------------------------------------------------------------------------

class TestGetInitialSince:
    def test_returns_next_batch_on_success(self):
        _load()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"next_batch": "s123_456"}
        with patch("requests.get", return_value=mock_resp):
            result = matrix_notify._get_initial_since(
                "https://chat.mozilla.org", "!room:mozilla.org", "tok"
            )
        assert result == "s123_456"

    def test_returns_none_on_error(self):
        _load()
        mock_resp = MagicMock(status_code=401)
        with patch("requests.get", return_value=mock_resp):
            result = matrix_notify._get_initial_since(
                "https://chat.mozilla.org", "!room:mozilla.org", "tok"
            )
        assert result is None


# ---------------------------------------------------------------------------
# cmd_listen --daemon: single-instance guard
# ---------------------------------------------------------------------------

class TestCmdListenDaemonSingleInstance:
    def _make_config(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(
            "MATRIX_HOMESERVER=https://chat.mozilla.org\n"
            "MATRIX_ACCESS_TOKEN=tok\n"
            "MATRIX_ROOM_ID=!abc:mozilla.org\n"
            "MATRIX_NOTIFY_USER=@alwu:mozilla.org\n"
        )
        return str(cfg)

    def test_exits_immediately_when_daemon_already_running(self, tmp_path, capsys):
        _load()
        pid_file = tmp_path / "matrix-listen.pid"
        pid_file.write_text("99999")

        def fake_kill(pid, sig):
            if pid == 99999 and sig == 0:
                return  # process is alive

        config_path = self._make_config(tmp_path)
        sync_token_path = str(tmp_path / "sync_token")

        with patch.object(matrix_notify, "LISTEN_PID_FILE", pid_file):
            with patch("os.kill", side_effect=fake_kill):
                matrix_notify.cmd_listen(
                    daemon=True,
                    config_path=config_path,
                    sync_token_path=sync_token_path,
                )

        out = capsys.readouterr().out
        assert "already running" in out.lower()
        assert pid_file.read_text() == "99999"

    def test_proceeds_when_pid_file_missing(self, tmp_path):
        _load()
        pid_file = tmp_path / "matrix-listen.pid"
        assert not pid_file.exists()

        config_path = self._make_config(tmp_path)
        sync_token_path = str(tmp_path / "sync_token")

        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"next_batch": "s1", "rooms": {}}

        call_count = 0

        def fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise KeyboardInterrupt
            return mock_resp

        with patch.object(matrix_notify, "LISTEN_PID_FILE", pid_file):
            with patch("requests.get", side_effect=fake_get):
                try:
                    matrix_notify.cmd_listen(
                        daemon=True,
                        config_path=config_path,
                        sync_token_path=sync_token_path,
                    )
                except KeyboardInterrupt:
                    pass

        assert pid_file.exists()
        assert pid_file.read_text() == str(os.getpid())

    def test_proceeds_when_pid_is_stale(self, tmp_path):
        _load()
        pid_file = tmp_path / "matrix-listen.pid"
        pid_file.write_text("99999")

        def fake_kill(pid, sig):
            raise ProcessLookupError("no such process")

        config_path = self._make_config(tmp_path)
        sync_token_path = str(tmp_path / "sync_token")

        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"next_batch": "s1", "rooms": {}}

        call_count = 0

        def fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise KeyboardInterrupt
            return mock_resp

        with patch.object(matrix_notify, "LISTEN_PID_FILE", pid_file):
            with patch("os.kill", side_effect=fake_kill):
                with patch("requests.get", side_effect=fake_get):
                    try:
                        matrix_notify.cmd_listen(
                            daemon=True,
                            config_path=config_path,
                            sync_token_path=sync_token_path,
                        )
                    except KeyboardInterrupt:
                        pass

        assert pid_file.read_text() == str(os.getpid())


# ---------------------------------------------------------------------------
# cmd_listen — daemon PID and token initialisation
# ---------------------------------------------------------------------------

class TestCmdListen:
    def _make_config(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(
            "MATRIX_HOMESERVER=https://chat.mozilla.org\n"
            "MATRIX_ACCESS_TOKEN=tok\n"
            "MATRIX_ROOM_ID=!abc:mozilla.org\n"
            "MATRIX_NOTIFY_USER=@me:mozilla.org\n"
        )
        return str(cfg)

    def test_daemon_writes_pid_file(self, tmp_path):
        _load()
        config_path = self._make_config(tmp_path)
        pid_file = tmp_path / "listen.pid"
        sync_token_path = tmp_path / "sync-token"

        call_count = [0]
        def fake_get(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # initial since
                m = MagicMock(status_code=200)
                m.json.return_value = {"next_batch": "s1"}
                return m
            raise KeyboardInterrupt

        with patch.object(matrix_notify, "LISTEN_PID_FILE", pid_file):
            with patch("requests.get", side_effect=fake_get):
                with patch("time.sleep"):
                    try:
                        matrix_notify.cmd_listen(
                            daemon=True,
                            config_path=config_path,
                            sync_token_path=str(sync_token_path),
                        )
                    except (KeyboardInterrupt, Exception):
                        pass
        assert pid_file.exists()
        assert int(pid_file.read_text()) == os.getpid()

    def test_initializes_since_from_existing_token(self, tmp_path):
        _load()
        config_path = self._make_config(tmp_path)
        sync_token_path = tmp_path / "sync-token"
        sync_token_path.write_text("s_existing_token")

        get_calls = []
        def fake_get(url, **kwargs):
            get_calls.append(kwargs.get("params", {}))
            raise KeyboardInterrupt

        with patch("requests.get", side_effect=fake_get):
            with patch("time.sleep"):
                try:
                    matrix_notify.cmd_listen(
                        daemon=False,
                        config_path=config_path,
                        sync_token_path=str(sync_token_path),
                    )
                except (KeyboardInterrupt, Exception):
                    pass
        assert get_calls
        assert get_calls[0].get("since") == "s_existing_token"


# ---------------------------------------------------------------------------
# _send_handshake
# ---------------------------------------------------------------------------

class TestSendHandshake:
    def _make_config(self):
        return {
            "MATRIX_HOMESERVER": "https://chat.mozilla.org",
            "MATRIX_ACCESS_TOKEN": "tok",
            "MATRIX_ROOM_ID": "!abc:mozilla.org",
            "MATRIX_NOTIFY_USER": "@me:mozilla.org",
        }

    def test_sends_log_message_with_received_prefix(self, tmp_path):
        _load()
        config = self._make_config()
        sessions_path = str(tmp_path / "sessions.json")
        with patch.object(matrix_notify, "ensure_thread", return_value="$t:m.org") as mock_et:
            with patch.object(matrix_notify, "send_message") as mock_sm:
                matrix_notify._send_handshake("bug-1234", "hello", config, sessions_path)
                mock_et.assert_called_once_with("bug-1234", config, sessions_path)
                mock_sm.assert_called_once_with("log", "Received: hello", "$t:m.org", config)

    def test_swallows_exception(self, tmp_path):
        _load()
        config = self._make_config()
        sessions_path = str(tmp_path / "sessions.json")
        with patch.object(matrix_notify, "ensure_thread", side_effect=Exception("boom")):
            matrix_notify._send_handshake("bug-1234", "hello", config, sessions_path)


# ---------------------------------------------------------------------------
# cmd_handle_forward
# ---------------------------------------------------------------------------

class TestCmdHandleForward:
    def _make_config_file(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(
            "MATRIX_HOMESERVER=https://chat.mozilla.org\n"
            "MATRIX_ACCESS_TOKEN=tok\n"
            "MATRIX_ROOM_ID=!abc:mozilla.org\n"
            "MATRIX_NOTIFY_USER=@me:mozilla.org\n"
        )
        return cfg

    def test_sends_handshake_and_prints_matrix_prefix(self, tmp_path, capsys):
        _load()
        cfg = self._make_config_file(tmp_path)
        with patch.object(matrix_notify, "CONFIG_PATH", cfg):
            with patch.object(matrix_notify, "get_session_name", return_value="bug-1234"):
                with patch.object(matrix_notify, "_send_handshake"):
                    matrix_notify.cmd_handle_forward("do something")
        out = capsys.readouterr().out
        assert "[matrix] do something" in out

    def test_handshake_called_with_session_name(self, tmp_path):
        _load()
        cfg = self._make_config_file(tmp_path)
        with patch.object(matrix_notify, "CONFIG_PATH", cfg):
            with patch.object(matrix_notify, "get_session_name", return_value="my-session"):
                with patch.object(matrix_notify, "_send_handshake") as mock_hs:
                    matrix_notify.cmd_handle_forward("test message")
                    assert mock_hs.call_args[0][0] == "my-session"
                    assert mock_hs.call_args[0][1] == "test message"


# ---------------------------------------------------------------------------
# cmd_notify
# ---------------------------------------------------------------------------

class TestCmdNotify:
    def _make_config_file(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(
            "MATRIX_HOMESERVER=https://chat.mozilla.org\n"
            "MATRIX_ACCESS_TOKEN=tok\n"
            "MATRIX_ROOM_ID=!abc:mozilla.org\n"
            "MATRIX_NOTIFY_USER=@me:mozilla.org\n"
        )
        return cfg

    def test_invalid_type_exits(self, tmp_path):
        _load()
        cfg = self._make_config_file(tmp_path)
        with patch.object(matrix_notify, "CONFIG_PATH", cfg):
            with pytest.raises(SystemExit):
                matrix_notify.cmd_notify("bad-type", "hello")

    def test_valid_types_accepted(self, tmp_path):
        _load()
        cfg = self._make_config_file(tmp_path)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"event_id": "$e:m.org"}
        for msg_type in ("log", "alert", "done"):
            sessions_path = str(tmp_path / f"sessions-{msg_type}.json")
            with patch.object(matrix_notify, "CONFIG_PATH", cfg):
                with patch.object(matrix_notify, "SESSIONS_PATH", tmp_path / f"sessions-{msg_type}.json"):
                    with patch.object(matrix_notify, "get_session_name", return_value="my-session"):
                        with patch("requests.put", return_value=mock_resp):
                            matrix_notify.cmd_notify(msg_type, "hello")

    def test_uses_hostname_as_session_when_tmux_unavailable(self, tmp_path):
        _load()
        cfg = self._make_config_file(tmp_path)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"event_id": "$e:m.org"}
        with patch.object(matrix_notify, "CONFIG_PATH", cfg):
            with patch.object(matrix_notify, "SESSIONS_PATH", tmp_path / "sessions.json"):
                with patch("subprocess.check_output", side_effect=Exception("no tmux")):
                    with patch("socket.gethostname", return_value="my-host"):
                        with patch("requests.put", return_value=mock_resp):
                            matrix_notify.cmd_notify("log", "hello")
        sessions = matrix_notify.load_sessions(str(tmp_path / "sessions.json"))
        assert "my-host" in sessions

    def test_uses_tmux_session_name_when_inside_tmux(self, tmp_path):
        _load()
        cfg = self._make_config_file(tmp_path)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"event_id": "$e:m.org"}
        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,1234,0"}):
            with patch.object(matrix_notify, "CONFIG_PATH", cfg):
                with patch.object(matrix_notify, "SESSIONS_PATH", tmp_path / "sessions.json"):
                    with patch("subprocess.check_output", return_value=b"matrix\n"):
                        with patch("requests.put", return_value=mock_resp):
                            matrix_notify.cmd_notify("log", "hello")
        sessions = matrix_notify.load_sessions(str(tmp_path / "sessions.json"))
        assert "matrix" in sessions

    def test_message_posted_to_existing_thread(self, tmp_path):
        _load()
        cfg = self._make_config_file(tmp_path)
        sessions_path = tmp_path / "sessions.json"
        matrix_notify.save_sessions(str(sessions_path), {
            "my-session": {"thread_id": "$existing:m.org", "started": "2026-04-01T10:00:00"}
        })
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"event_id": "$reply:m.org"}
        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,1234,0"}):
            with patch.object(matrix_notify, "CONFIG_PATH", cfg):
                with patch.object(matrix_notify, "SESSIONS_PATH", sessions_path):
                    with patch("subprocess.check_output", return_value=b"my-session\n"):
                        with patch("requests.put", return_value=mock_resp) as mock_put:
                            matrix_notify.cmd_notify("log", "hello")
        body = mock_put.call_args[1]["json"]
        assert body["m.relates_to"]["event_id"] == "$existing:m.org"
        assert mock_put.call_count == 1

    def test_new_thread_created_for_unknown_session(self, tmp_path):
        _load()
        cfg = self._make_config_file(tmp_path)
        sessions_path = tmp_path / "sessions.json"
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"event_id": "$new-root:m.org"}
        env = {k: v for k, v in os.environ.items() if k != "TMUX"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(matrix_notify, "CONFIG_PATH", cfg):
                with patch.object(matrix_notify, "SESSIONS_PATH", sessions_path):
                    with patch("subprocess.check_output", side_effect=Exception("no tmux")):
                        with patch("socket.gethostname", return_value="new-host"):
                            with patch("requests.put", return_value=mock_resp) as mock_put:
                                matrix_notify.cmd_notify("log", "hello")
        assert mock_put.call_count == 2
        sessions = matrix_notify.load_sessions(str(sessions_path))
        assert "new-host" in sessions


# ---------------------------------------------------------------------------
# main() dispatch
# ---------------------------------------------------------------------------

class TestMainDispatch:
    def _make_config(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(
            "MATRIX_HOMESERVER=https://chat.mozilla.org\n"
            "MATRIX_ACCESS_TOKEN=tok\n"
            "MATRIX_ROOM_ID=!abc:mozilla.org\n"
            "MATRIX_NOTIFY_USER=@me:mozilla.org\n"
        )
        return cfg

    def test_notify_subcommand_dispatched(self, tmp_path):
        _load()
        cfg = self._make_config(tmp_path)
        with patch.object(matrix_notify, "CONFIG_PATH", cfg):
            with patch.object(matrix_notify, "cmd_notify") as mock_cn:
                with patch("sys.argv", ["matrix-cli", "notify", "log", "hello"]):
                    matrix_notify.main()
                    mock_cn.assert_called_once_with("log", "hello")

    def test_forward_subcommand_dispatched(self, tmp_path):
        _load()
        cfg = self._make_config(tmp_path)
        with patch.object(matrix_notify, "CONFIG_PATH", cfg):
            with patch.object(matrix_notify, "cmd_forward") as mock_cf:
                with patch("sys.argv", ["matrix-cli", "forward", "hello"]):
                    matrix_notify.main()
                    mock_cf.assert_called_once()

    def test_listen_subcommand_dispatched(self, tmp_path):
        _load()
        cfg = self._make_config(tmp_path)
        with patch.object(matrix_notify, "CONFIG_PATH", cfg):
            with patch.object(matrix_notify, "cmd_listen") as mock_cl:
                with patch("sys.argv", ["matrix-cli", "listen"]):
                    matrix_notify.main()
                    mock_cl.assert_called_once_with(daemon=False)

    def test_listen_daemon_flag_dispatched(self, tmp_path):
        _load()
        cfg = self._make_config(tmp_path)
        with patch.object(matrix_notify, "CONFIG_PATH", cfg):
            with patch.object(matrix_notify, "cmd_listen") as mock_cl:
                with patch("sys.argv", ["matrix-cli", "listen", "--daemon"]):
                    matrix_notify.main()
                    mock_cl.assert_called_once_with(daemon=True)

    def test_handle_forward_dispatched(self, tmp_path):
        _load()
        cfg = self._make_config(tmp_path)
        with patch.object(matrix_notify, "CONFIG_PATH", cfg):
            with patch.object(matrix_notify, "cmd_handle_forward") as mock_hf:
                with patch("sys.argv", ["matrix-cli", "handle-forward", "hello"]):
                    matrix_notify.main()
                    mock_hf.assert_called_once()

    def test_invalid_subcommand_exits(self, tmp_path):
        _load()
        cfg = self._make_config(tmp_path)
        with patch.object(matrix_notify, "CONFIG_PATH", cfg):
            with patch("sys.argv", ["matrix-cli", "invalid"]):
                with pytest.raises(SystemExit):
                    matrix_notify.main()

    def test_notify_missing_args_exits(self, tmp_path):
        _load()
        cfg = self._make_config(tmp_path)
        with patch.object(matrix_notify, "CONFIG_PATH", cfg):
            with patch("sys.argv", ["matrix-cli", "notify"]):
                with pytest.raises(SystemExit):
                    matrix_notify.main()

    def test_forward_missing_args_exits(self, tmp_path):
        _load()
        cfg = self._make_config(tmp_path)
        with patch.object(matrix_notify, "CONFIG_PATH", cfg):
            with patch("sys.argv", ["matrix-cli", "forward"]):
                with pytest.raises(SystemExit):
                    matrix_notify.main()


# ---------------------------------------------------------------------------
# cmd_listen — error recovery
# ---------------------------------------------------------------------------

class TestCmdListenErrorRecovery:
    def _make_config(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(
            "MATRIX_HOMESERVER=https://chat.mozilla.org\n"
            "MATRIX_ACCESS_TOKEN=tok\n"
            "MATRIX_ROOM_ID=!abc:mozilla.org\n"
            "MATRIX_NOTIFY_USER=@me:mozilla.org\n"
        )
        return str(cfg)

    def test_continues_after_401_response(self, tmp_path):
        _load()
        config_path = self._make_config(tmp_path)
        sync_token_path = str(tmp_path / "sync-token")

        call_count = [0]
        def fake_get(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                m = MagicMock(status_code=401)
                return m
            raise KeyboardInterrupt

        with patch("requests.get", side_effect=fake_get):
            with patch("time.sleep"):
                try:
                    matrix_notify.cmd_listen(
                        daemon=False,
                        config_path=config_path,
                        sync_token_path=sync_token_path,
                    )
                except (KeyboardInterrupt, Exception):
                    pass
        assert call_count[0] >= 2

    def test_continues_after_network_exception(self, tmp_path):
        _load()
        config_path = self._make_config(tmp_path)
        sync_token_path = tmp_path / "sync-token"
        sync_token_path.write_text("s_existing")

        call_count = [0]
        def fake_get(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise requests.exceptions.ConnectionError("network down")
            raise KeyboardInterrupt

        with patch("requests.get", side_effect=fake_get):
            with patch("time.sleep"):
                try:
                    matrix_notify.cmd_listen(
                        daemon=False,
                        config_path=config_path,
                        sync_token_path=str(sync_token_path),
                    )
                except (KeyboardInterrupt, Exception):
                    pass
        assert call_count[0] >= 2

    def test_continues_after_process_sync_exception(self, tmp_path):
        _load()
        config_path = self._make_config(tmp_path)
        sync_token_path = str(tmp_path / "sync-token")

        call_count = [0]
        def fake_get(*args, **kwargs):
            call_count[0] += 1
            m = MagicMock(status_code=200)
            m.json.return_value = {"next_batch": f"s{call_count[0]}"}
            return m

        sync_call_count = [0]
        def fake_process_sync(*args, **kwargs):
            sync_call_count[0] += 1
            if sync_call_count[0] <= 2:
                raise Exception("parse error")
            raise KeyboardInterrupt

        with patch("requests.get", side_effect=fake_get):
            with patch.object(matrix_notify, "_process_sync_events", side_effect=fake_process_sync):
                with patch("time.sleep"):
                    try:
                        matrix_notify.cmd_listen(
                            daemon=False,
                            config_path=config_path,
                            sync_token_path=sync_token_path,
                        )
                    except (KeyboardInterrupt, Exception):
                        pass
        assert sync_call_count[0] >= 2
