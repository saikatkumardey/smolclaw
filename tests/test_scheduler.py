"""Tests for scheduler.py — cron job timeout and delivery."""
from __future__ import annotations

import os
import threading
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

        def fake_run_job(job_id, prompt, deliver_to, timeout=None):
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


