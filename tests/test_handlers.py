"""Handler tests — Telegram message handling, chunking, markdown conversion."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# _to_telegram_md
# ---------------------------------------------------------------------------

class TestToTelegramMd:
    def test_bold_conversion(self):
        from smolclaw.handlers import _to_telegram_md
        assert _to_telegram_md("**hello**") == "*hello*"

    def test_heading_conversion(self):
        from smolclaw.handlers import _to_telegram_md
        assert _to_telegram_md("## Heading") == "*Heading*"

    def test_h1_heading(self):
        from smolclaw.handlers import _to_telegram_md
        assert _to_telegram_md("# Title") == "*Title*"

    def test_no_change_plain_text(self):
        from smolclaw.handlers import _to_telegram_md
        text = "Just some plain text"
        assert _to_telegram_md(text) == text

    def test_mixed_bold_and_heading(self):
        from smolclaw.handlers import _to_telegram_md
        result = _to_telegram_md("## Title\n\n**bold** word")
        assert "*Title*" in result
        assert "*bold*" in result


# ---------------------------------------------------------------------------
# _reply_chunked
# ---------------------------------------------------------------------------

class TestReplyChunked:
    @pytest.mark.asyncio
    async def test_short_message_single_chunk(self):
        from smolclaw.handlers import _reply_chunked
        msg = AsyncMock()
        await _reply_chunked(msg, "hello")
        msg.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_long_message_splits_at_max(self):
        from smolclaw.handlers import _reply_chunked
        from smolclaw.tools import MAX_TG_MSG
        msg = AsyncMock()
        text = "a" * (MAX_TG_MSG + 100)
        await _reply_chunked(msg, text)
        assert msg.reply_text.await_count == 2

    @pytest.mark.asyncio
    async def test_exact_boundary(self):
        from smolclaw.handlers import _reply_chunked
        from smolclaw.tools import MAX_TG_MSG
        msg = AsyncMock()
        text = "b" * MAX_TG_MSG
        await _reply_chunked(msg, text)
        assert msg.reply_text.await_count == 1

    @pytest.mark.asyncio
    async def test_markdown_failure_falls_back_to_plain(self):
        from smolclaw.handlers import _reply_chunked
        msg = AsyncMock()
        call_count = 0
        async def _side_effect(text, **kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("parse_mode") == "Markdown":
                raise Exception("Bad markdown")
        msg.reply_text = AsyncMock(side_effect=_side_effect)
        await _reply_chunked(msg, "hello")
        assert call_count == 2  # first try markdown, then plain


# ---------------------------------------------------------------------------
# on_message
# ---------------------------------------------------------------------------

def _make_update(chat_id="123", text="hi"):
    update = MagicMock()
    update.effective_chat.id = int(chat_id)
    update.edited_message = None
    update.message.text = text
    # reply_text returns a placeholder message with edit_text for inline editing
    placeholder = MagicMock()
    placeholder.edit_text = AsyncMock()
    placeholder.message_id = 99
    update.message.reply_text = AsyncMock(return_value=placeholder)
    update.message.message_id = 42
    return update


def _make_context(chat_id="123"):
    ctx = MagicMock()
    ctx.bot.send_chat_action = AsyncMock()
    ctx.bot.send_message = AsyncMock()
    ctx.bot.edit_message_text = AsyncMock()
    ctx.bot.delete_message = AsyncMock()
    return ctx


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from smolclaw.handlers import on_message
        update = _make_update()
        ctx = _make_context()
        with patch("smolclaw.handlers.agent_run", new_callable=AsyncMock, return_value="Reply!"):
            await on_message(update, ctx)
        # First reply_text sends "..." placeholder
        update.message.reply_text.assert_awaited()
        # Final reply edits the placeholder in place
        placeholder = update.message.reply_text.return_value
        placeholder.edit_text.assert_awaited()
        args = placeholder.edit_text.await_args_list[0]
        assert "Reply!" in args[0][0]

    @pytest.mark.asyncio
    async def test_agent_error_sends_fallback(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from smolclaw.handlers import on_message
        update = _make_update()
        ctx = _make_context()
        with patch("smolclaw.handlers.agent_run", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            await on_message(update, ctx)
        update.message.reply_text.assert_awaited()
        # Error edits the placeholder instead of sending a new message
        placeholder = update.message.reply_text.return_value
        placeholder.edit_text.assert_awaited()
        args = placeholder.edit_text.await_args_list[-1]
        assert "wrong" in args[0][0].lower()

    @pytest.mark.asyncio
    async def test_not_allowed_user_ignored(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "999")
        from smolclaw.handlers import on_message
        update = _make_update(chat_id="123")
        ctx = _make_context()
        with patch("smolclaw.handlers.agent_run", new_callable=AsyncMock) as mock_run:
            await on_message(update, ctx)
        mock_run.assert_not_awaited()


# ---------------------------------------------------------------------------
# on_photo / on_document
# ---------------------------------------------------------------------------

class TestOnPhoto:
    @pytest.mark.asyncio
    async def test_downloads_and_passes_to_agent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        import smolclaw.workspace as ws
        monkeypatch.setattr(ws, "UPLOADS_DIR", tmp_path)

        from smolclaw.handlers import on_photo
        update = MagicMock()
        update.effective_chat.id = 123
        update.message.caption = "Look at this"
        photo = MagicMock()
        photo.file_id = "abc"
        photo.file_unique_id = "photo123"
        update.message.photo = [photo]
        update.message.reply_text = AsyncMock()

        ctx = MagicMock()
        ctx.bot.send_chat_action = AsyncMock()
        file_mock = AsyncMock()
        ctx.bot.get_file = AsyncMock(return_value=file_mock)

        with patch("smolclaw.handlers.agent_run", new_callable=AsyncMock, return_value="Saw it"):
            await on_photo(update, ctx)
        file_mock.download_to_drive.assert_awaited_once()
        update.message.reply_text.assert_awaited()


class TestOnDocument:
    @pytest.mark.asyncio
    async def test_downloads_and_passes_to_agent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        import smolclaw.workspace as ws
        monkeypatch.setattr(ws, "UPLOADS_DIR", tmp_path)

        from smolclaw.handlers import on_document
        update = MagicMock()
        update.effective_chat.id = 123
        update.message.caption = "Here's a file"
        doc = MagicMock()
        doc.file_id = "def"
        doc.file_unique_id = "doc456"
        doc.file_name = "report.pdf"
        doc.mime_type = "application/pdf"
        update.message.document = doc
        update.message.reply_text = AsyncMock()

        ctx = MagicMock()
        ctx.bot.send_chat_action = AsyncMock()
        file_mock = AsyncMock()
        ctx.bot.get_file = AsyncMock(return_value=file_mock)

        with patch("smolclaw.handlers.agent_run", new_callable=AsyncMock, return_value="Got it"):
            await on_document(update, ctx)
        file_mock.download_to_drive.assert_awaited_once()
        update.message.reply_text.assert_awaited()


# ---------------------------------------------------------------------------
# Phase 3.1: Error classification
# ---------------------------------------------------------------------------

class TestErrorClassification:
    @pytest.mark.asyncio
    async def test_timeout_error_message(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from smolclaw.handlers import on_message
        update = _make_update()
        ctx = _make_context()
        with patch("smolclaw.handlers.agent_run", new_callable=AsyncMock, side_effect=TimeoutError()):
            await on_message(update, ctx)
        placeholder = update.message.reply_text.return_value
        msg = placeholder.edit_text.await_args_list[-1][0][0]
        assert "timed out" in msg.lower() or "timeout" in msg.lower()

    @pytest.mark.asyncio
    async def test_permission_error_message(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from smolclaw.handlers import on_message
        update = _make_update()
        ctx = _make_context()
        with patch("smolclaw.handlers.agent_run", new_callable=AsyncMock, side_effect=PermissionError("denied")):
            await on_message(update, ctx)
        placeholder = update.message.reply_text.return_value
        msg = placeholder.edit_text.await_args_list[-1][0][0]
        assert "permission" in msg.lower()

    @pytest.mark.asyncio
    async def test_connection_error_message(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from smolclaw.handlers import on_message
        update = _make_update()
        ctx = _make_context()
        with patch("smolclaw.handlers.agent_run", new_callable=AsyncMock, side_effect=ConnectionError("no network")):
            await on_message(update, ctx)
        placeholder = update.message.reply_text.return_value
        msg = placeholder.edit_text.await_args_list[-1][0][0]
        assert "connection" in msg.lower()

    @pytest.mark.asyncio
    async def test_generic_error_message(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from smolclaw.handlers import on_message
        update = _make_update()
        ctx = _make_context()
        with patch("smolclaw.handlers.agent_run", new_callable=AsyncMock, side_effect=RuntimeError("wat")):
            await on_message(update, ctx)
        placeholder = update.message.reply_text.return_value
        msg = placeholder.edit_text.await_args_list[-1][0][0]
        assert "wrong" in msg.lower()
        assert "wat" not in msg  # should not leak internal error


# ---------------------------------------------------------------------------
# on_update — restart behavior
# ---------------------------------------------------------------------------

class TestOnUpdate:
    @pytest.mark.asyncio
    async def test_same_version_no_restart(self, monkeypatch):
        """When remote version == local version, should NOT restart."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from smolclaw.handlers import on_update
        update = _make_update(text="/update")
        ctx = _make_context()

        monkeypatch.setattr("smolclaw.handlers._local_version", lambda: "0.5.0")

        # Mock requests.get response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = 'version = "0.5.0"'

        async def fake_to_thread(fn, *a, **kw):
            return mock_resp  # only call is requests.get — returns same version

        with patch("smolclaw.handlers._check_remote_version", return_value="0.5.0"):
            await on_update(update, ctx)

        # One placeholder sent, then edited with the final status
        placeholder = update.message.reply_text.return_value
        placeholder.edit_text.assert_awaited()
        edits = [call[0][0] for call in placeholder.edit_text.await_args_list]
        assert any("already on latest" in e.lower() for e in edits)

    @pytest.mark.asyncio
    async def test_new_version_uses_clean_exit(self, monkeypatch):
        """When a real update happens, should do a clean process exit, not os.execv."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from smolclaw.handlers import on_update
        update = _make_update(text="/update")
        ctx = _make_context()

        monkeypatch.setattr("smolclaw.handlers._local_version", lambda: "0.4.7")

        mock_install = MagicMock()
        mock_install.returncode = 0
        mock_install.stdout = "Installed"
        mock_install.stderr = ""

        call_count = 0
        async def fake_to_thread(fn, *a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "0.5.0"         # _check_remote_version
            elif call_count == 2:
                return mock_install    # subprocess.run (uv install)
            else:
                return "Updated: 0.4.7 -> 0.5.0"  # _get_update_summary

        import smolclaw.handlers as _h
        with patch.object(_h.asyncio, "to_thread", side_effect=fake_to_thread), \
             patch.object(_h, "save_handover", create=True), \
             patch("os.kill") as mock_kill, \
             patch("os.getpid", return_value=12345), \
             patch("os.execv") as mock_execv:
            await on_update(update, ctx)

        mock_execv.assert_not_called()
        import signal
        mock_kill.assert_called_with(12345, signal.SIGTERM)


# ---------------------------------------------------------------------------
# Reaction handler — message=None safety
# ---------------------------------------------------------------------------

class TestRunAgentNullMessage:
    @pytest.mark.asyncio
    async def test_no_crash_when_message_is_none(self, monkeypatch):
        """_run_agent_and_reply should not crash when message=None (reaction path)."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from smolclaw.handlers import _run_agent_and_reply

        bot = MagicMock()
        bot.send_chat_action = AsyncMock()
        bot.send_message = AsyncMock()

        with patch("smolclaw.handlers.agent_run", new_callable=AsyncMock, return_value="Noted the reaction."):
            # Should not raise AttributeError
            await _run_agent_and_reply(bot, None, "123", "reaction msg", use_placeholder=False)

        # Reply should be sent via bot.send_message since message is None
        bot.send_message.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_crash_on_error_when_message_is_none(self, monkeypatch):
        """Error path should not crash when message=None."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from smolclaw.handlers import _run_agent_and_reply

        bot = MagicMock()
        bot.send_chat_action = AsyncMock()
        bot.send_message = AsyncMock()

        with patch("smolclaw.handlers.agent_run", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            # Should not raise
            await _run_agent_and_reply(bot, None, "123", "reaction msg", use_placeholder=False)


# ---------------------------------------------------------------------------
# on_photo — empty photo array safety
# ---------------------------------------------------------------------------

class TestOnPhotoEmptyArray:
    @pytest.mark.asyncio
    async def test_empty_photo_array_no_crash(self, monkeypatch):
        """on_photo should handle empty photo array gracefully."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from smolclaw.handlers import on_photo

        update = MagicMock()
        update.effective_chat.id = 123
        update.message.photo = []
        update.message.caption = "test"
        update.message.reply_text = AsyncMock()
        ctx = MagicMock()

        # Should not raise IndexError
        await on_photo(update, ctx)
