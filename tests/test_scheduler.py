"""Tests for scheduler.py — cron job timeout and delivery."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from smolclaw import scheduler as _sched


class TestRunJobTimeout:
    """Cron jobs must not block forever — verify timeout behavior."""

    @patch.object(_sched, "_telegram")
    @patch("smolclaw.agent.run")
    def test_timeout_abandons_hung_thread(self, mock_run, mock_tg):
        """A cron job that exceeds the timeout is abandoned without delivering."""
        hang_event = threading.Event()

        async def _hang(*a, **kw):
            hang_event.wait(10)
            return "should never arrive"

        mock_run.side_effect = _hang

        with patch.object(_sched, "_CRON_TIMEOUT_SECONDS", 0.5):
            _sched._run_job("test-hang", "prompt", deliver_to="123")

        mock_tg.send.assert_not_called()
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
    def test_exception_does_not_deliver(self, mock_run, mock_tg):
        """A job that raises an exception should not deliver."""

        async def _boom(*a, **kw):
            raise RuntimeError("boom")

        mock_run.side_effect = _boom
        _sched._run_job("test-err", "prompt", deliver_to="123")
        mock_tg.send.assert_not_called()
