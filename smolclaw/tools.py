"""Minimal tools module — shared Telegram helper + TelegramSender for scheduler use."""
from __future__ import annotations

import os
from pathlib import Path

import httpx

from . import workspace

MAX_TG_MSG = 4000


def _send_telegram(chat_id: str, message: str) -> str:
    """Send a Telegram message. Returns 'Sent. [message_id=N]' or an error string.

    Uses httpx (sync) instead of requests so the function is safe to call via
    asyncio.to_thread() from async callers without pulling in two HTTP stacks.
    """
    try:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chunks = [message[i:i + MAX_TG_MSG] for i in range(0, len(message), MAX_TG_MSG)]
        last_message_id = None
        with httpx.Client(timeout=10) as client:
            for chunk in chunks:
                r = client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"},
                )
                if not r.is_success:
                    # Retry without Markdown if parse fails
                    r = client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": chunk},
                    )
                    if not r.is_success:
                        return f"Failed: {r.text}"
                try:
                    last_message_id = r.json().get("result", {}).get("message_id")
                except Exception:
                    pass
        if last_message_id:
            return f"Sent. [message_id={last_message_id}]"
        return "Sent."
    except Exception as e:
        return f"Error: {e}"


def _edit_telegram(chat_id: str, message_id: int, message: str) -> str:
    """Edit an existing Telegram message. Returns 'Edited.' or an error string."""
    try:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        text = message[:MAX_TG_MSG]
        with httpx.Client(timeout=10) as client:
            r = client.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                    "parse_mode": "Markdown",
                },
            )
            if not r.is_success:
                # Retry without Markdown
                r = client.post(
                    f"https://api.telegram.org/bot{token}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": text,
                    },
                )
                if not r.is_success:
                    return f"Failed: {r.text}"
        return "Edited."
    except Exception as e:
        return f"Error: {e}"


def _send_telegram_file(chat_id: str, file_path: str) -> str:
    """Send a file to a Telegram chat via sendDocument. Returns 'Sent.' or an error string.

    Uses httpx (sync) instead of requests so the function is safe to call via
    asyncio.to_thread() from async callers without pulling in two HTTP stacks.
    """
    try:
        path = Path(file_path).resolve()
        if not str(path).startswith(str(workspace.HOME.resolve())):
            return f"Error: file path {file_path!r} is outside the workspace."
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        with open(path, "rb") as f, httpx.Client(timeout=30) as client:
            r = client.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat_id},
                files={"document": (path.name, f)},
            )
        return "Sent." if r.is_success else f"Failed: {r.text}"
    except FileNotFoundError:
        return f"File not found: {file_path}"
    except Exception as e:
        return f"Error: {e}"


def _send_telegram_voice(chat_id: str, audio_path: str, caption: str = "") -> str:
    """Send a voice message (OGG/Opus) to a Telegram chat. Returns 'Sent.' or an error string."""
    try:
        path = Path(audio_path).resolve()
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        with open(path, "rb") as f, httpx.Client(timeout=30) as client:
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption[:1024]
            r = client.post(
                f"https://api.telegram.org/bot{token}/sendVoice",
                data=data,
                files={"voice": (path.name, f, "audio/ogg")},
            )
        return "Sent." if r.is_success else f"Failed: {r.text}"
    except FileNotFoundError:
        return f"File not found: {audio_path}"
    except Exception as e:
        return f"Error: {e}"


def _text_to_voice(text: str, output_path: str, voice: str = "en-US-AriaNeural") -> str:
    """Convert text to OGG voice file using edge-tts. Returns output path or error."""
    import subprocess
    import tempfile

    mp3_path = None
    try:
        # edge-tts outputs MP3; convert to OGG/Opus for Telegram voice messages
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            mp3_path = tmp.name

        # Use edge-tts CLI (simpler than async API in sync context)
        result = subprocess.run(
            ["edge-tts", "--voice", voice, "--text", text, "--write-media", mp3_path],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return f"TTS failed: {result.stderr[:200]}"

        # Convert MP3 to OGG/Opus using ffmpeg
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", mp3_path, "-c:a", "libopus", "-b:a", "48k", output_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return f"FFmpeg conversion failed: {result.stderr[:200]}"

        return output_path
    except Exception as e:
        return f"Error: {e}"
    finally:
        if mp3_path:
            Path(mp3_path).unlink(missing_ok=True)


def _set_reaction(chat_id: str, message_id: int, emoji: str) -> str:
    """Set a reaction emoji on a Telegram message. Returns 'Done.' or error."""
    try:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        with httpx.Client(timeout=10) as client:
            r = client.post(
                f"https://api.telegram.org/bot{token}/setMessageReaction",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reaction": [{"type": "emoji", "emoji": emoji}],
                },
            )
        return "Done." if r.is_success else f"Failed: {r.text}"
    except Exception as e:
        return f"Error: {e}"


class TelegramSender:
    """Send Telegram messages. Used by scheduler for cron job delivery."""

    def send(self, chat_id: str, message: str) -> str:
        return _send_telegram(chat_id, message)
