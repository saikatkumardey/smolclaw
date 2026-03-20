"""Tests for scheduler.py — cron job timeout and delivery."""
from __future__ import annotations

import os
import threading
import time
from unittest.mock import patch

from smolclaw import scheduler as _sched


class TestRunJobTimeout:
    """Cron jobs must not block forever — verify timeout behavior."""

    @patch.object(_sched, "_telegram")
    @patch("smolclaw.agent.run")
    def test_timeout_abandons_hung_thread(self, mock_run, mock_tg):
        """A cron job that exceeds the timeout notifies the user."""
        hang_event = threading.Event()

        async def _hang(*a, **kw):
            hang_event.wait(10)
            return "should never arrive"

        mock_run.side_effect = _hang

        with patch.object(_sched, "_CRON_TIMEOUT_SECONDS", 0.5):
            _sched._run_job("test-hang", "prompt", deliver_to="123")

        # Timeout errors are now reported to the user
        mock_tg.send.assert_called_once()
        assert "timed out" in mock_tg.send.call_args.kwargs["message"]
        hang_event.set()  # release the thread

    @patch.object(_sched, "_telegram")
    @patch("smolclaw.agent.run")
    def test_successful_job_delivers(self, mock_run, mock_tg):
        """A job that completes within timeout delivers its result."""

        async def _quick(*a, **kw):
            return "hello world"

        mock_run.side_effect = _quick
        _sched._run_job("test-ok", "prompt", deliver_to="123")
        mock_tg.send.assert_called_once_with(chat_id="123", message="hello world")

    @patch.object(_sched, "_telegram")
    @patch("smolclaw.agent.run")
    def test_heartbeat_ok_suppresses_delivery(self, mock_run, mock_tg):
        """Heartbeat jobs returning HEARTBEAT_OK should not send a message."""

        async def _heartbeat(*a, **kw):
            return "All good. HEARTBEAT_OK"

        mock_run.side_effect = _heartbeat
        _sched._run_job("test-hb", "check health", deliver_to="123", heartbeat=True)
        mock_tg.send.assert_not_called()

    @patch.object(_sched, "_telegram")
    @patch("smolclaw.agent.run")
    def test_exception_notifies_user(self, mock_run, mock_tg):
        """A job that raises an exception should notify the user."""

        async def _boom(*a, **kw):
            raise RuntimeError("boom")

        mock_run.side_effect = _boom
        _sched._run_job("test-err", "prompt", deliver_to="123")
        mock_tg.send.assert_called_once()
        assert "failed" in mock_tg.send.call_args.kwargs["message"]
        assert "boom" in mock_tg.send.call_args.kwargs["message"]


class TestSubconsciousTimeout:
    """Subconscious jobs get a longer timeout than regular cron jobs."""

    def test_subconscious_timeout_is_longer_than_default(self):
        assert _sched._SUBCONSCIOUS_TIMEOUT_SECONDS > _sched._CRON_TIMEOUT_SECONDS

    @patch.object(_sched, "_telegram")
    @patch("smolclaw.agent.run")
    def test_subconscious_passes_longer_timeout(self, mock_run, mock_tg):
        """_run_subconscious uses _SUBCONSCIOUS_TIMEOUT_SECONDS, not the default."""
        call_log = []

        def fake_run_job(job_id, prompt, deliver_to, heartbeat=False, timeout=None):
            call_log.append({"job_id": job_id, "timeout": timeout})

        with patch.object(_sched, "_run_job", side_effect=fake_run_job), \
             patch("smolclaw.subconscious.load_threads", return_value=[]), \
             patch("smolclaw.workspace.read", return_value=""), \
             patch("smolclaw.workspace.HOME") as mock_home, \
             patch("smolclaw.config.Config.load") as mock_cfg:
            mock_cfg.return_value.get = lambda k, default=None: True if k == "subconscious_enabled" else default
            mock_home.__truediv__ = lambda self, x: mock_home
            mock_home.exists.return_value = False
            _sched._run_subconscious()

        assert len(call_log) == 1
        assert call_log[0]["timeout"] == _sched._SUBCONSCIOUS_TIMEOUT_SECONDS


class TestVersionCheckSkipped:
    """Cron jobs skip the SDK version check subprocess."""

    def test_skip_version_check_env_set(self):
        assert os.environ.get("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK") == "1"


def _patch_heartbeat_workspace(tmp_path, monkeypatch):
    """Set up workspace paths for heartbeat tests."""
    import smolclaw.workspace as ws
    monkeypatch.setattr(ws, "HOME", tmp_path)
    monkeypatch.setattr(ws, "MEMORY", tmp_path / "MEMORY.md")
    monkeypatch.setattr(ws, "USER", tmp_path / "USER.md")
    monkeypatch.setattr(ws, "SUBCONSCIOUS", tmp_path / "subconscious.yaml")
    (tmp_path / "sessions").mkdir(exist_ok=True)


class TestHeartbeatSkipsWhenQuiet:
    """Heartbeat should not invoke the model when nothing has changed."""

    def test_skips_when_no_files_changed(self, tmp_path, monkeypatch):
        """No watched files modified since last beat → no model call."""
        _patch_heartbeat_workspace(tmp_path, monkeypatch)
        # Create files with old mtimes
        (tmp_path / "MEMORY.md").write_text("nothing here")
        (tmp_path / "USER.md").write_text("tz: UTC")

        # Pretend last heartbeat was recent (after the files were written)
        monkeypatch.setattr(_sched, "_last_heartbeat_mtime", time.time() + 1)

        run_job_calls = []
        with patch.object(_sched, "_run_job", side_effect=lambda *_a, **_kw: run_job_calls.append(1)), \
             patch("smolclaw.auth.default_chat_id", return_value="123"):
            _sched._run_heartbeat()

        assert len(run_job_calls) == 0

    def test_runs_when_memory_changed(self, tmp_path, monkeypatch):
        """MEMORY.md modified after last beat → model is invoked."""
        _patch_heartbeat_workspace(tmp_path, monkeypatch)
        (tmp_path / "MEMORY.md").write_text("task completed!")
        (tmp_path / "USER.md").write_text("tz: UTC")

        # Last heartbeat was before the files
        monkeypatch.setattr(_sched, "_last_heartbeat_mtime", 0)

        run_job_calls = []
        with patch.object(_sched, "_run_job", side_effect=lambda *_a, **_kw: run_job_calls.append(1)), \
             patch("smolclaw.auth.default_chat_id", return_value="123"):
            _sched._run_heartbeat()

        assert len(run_job_calls) == 1

    def test_runs_on_first_beat(self, tmp_path, monkeypatch):
        """First heartbeat ever (mtime=0) always invokes model."""
        _patch_heartbeat_workspace(tmp_path, monkeypatch)
        (tmp_path / "MEMORY.md").write_text("some memory")

        monkeypatch.setattr(_sched, "_last_heartbeat_mtime", 0)

        run_job_calls = []
        with patch.object(_sched, "_run_job", side_effect=lambda *_a, **_kw: run_job_calls.append(1)), \
             patch("smolclaw.auth.default_chat_id", return_value="123"):
            _sched._run_heartbeat()

        assert len(run_job_calls) == 1

    def test_runs_when_session_log_changed(self, tmp_path, monkeypatch):
        """New session log activity since last beat → model is invoked."""
        _patch_heartbeat_workspace(tmp_path, monkeypatch)

        # Last heartbeat was 1 second ago
        monkeypatch.setattr(_sched, "_last_heartbeat_mtime", time.time() - 1)

        # Create a fresh session log (mtime = now, after last heartbeat)
        (tmp_path / "sessions" / "chat123.jsonl").write_text('{"role":"user"}\n')

        run_job_calls = []
        with patch.object(_sched, "_run_job", side_effect=lambda *_a, **_kw: run_job_calls.append(1)), \
             patch("smolclaw.auth.default_chat_id", return_value="123"):
            _sched._run_heartbeat()

        assert len(run_job_calls) == 1

    def test_updates_mtime_after_run(self, tmp_path, monkeypatch):
        """_last_heartbeat_mtime advances after a successful heartbeat."""
        _patch_heartbeat_workspace(tmp_path, monkeypatch)
        (tmp_path / "MEMORY.md").write_text("changed")
        monkeypatch.setattr(_sched, "_last_heartbeat_mtime", 0)

        before = time.time()
        with patch.object(_sched, "_run_job"), \
             patch("smolclaw.auth.default_chat_id", return_value="123"):
            _sched._run_heartbeat()

        assert _sched._last_heartbeat_mtime >= before


class TestHeartbeatCronYamlSkipped:
    """Old heartbeat entries in crons.yaml should be ignored — it's built-in now."""

    def test_setup_scheduler_skips_heartbeat_from_crons(self, tmp_path, monkeypatch):
        import smolclaw.workspace as ws
        monkeypatch.setattr(ws, "CRONS", tmp_path / "crons.yaml")
        monkeypatch.setattr(ws, "HOME", tmp_path)
        (tmp_path / "crons.yaml").write_text(
            'jobs:\n'
            '  - id: heartbeat\n'
            '    cron: "*/30 * * * *"\n'
            '    heartbeat: true\n'
            '    prompt: "old heartbeat prompt"\n'
            '    deliver_to: "123"\n'
        )

        with patch("smolclaw.config.Config.load") as mock_cfg:
            mock_cfg.return_value.get = lambda k, default=None: default
            sched = _sched.setup_scheduler()

        # The built-in _heartbeat job should exist
        assert sched.get_job("_heartbeat") is not None
        # The old crons.yaml heartbeat should NOT be scheduled
        assert sched.get_job("heartbeat") is None
