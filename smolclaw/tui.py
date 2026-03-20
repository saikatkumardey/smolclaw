"""Textual TUI for local interactive chat with the SmolClaw agent."""
from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer
from textual.widgets import Input, LoadingIndicator, Static
from textual.worker import Worker, WorkerState
from textual import work

from smolclaw.agent import get_current_model, run as agent_run, reset_session
from smolclaw.tools import TelegramSender
from smolclaw.scheduler import setup_scheduler

# ---------------------------------------------------------------------------
# Tokyo Night palette
# ---------------------------------------------------------------------------
_BG = "#1a1b26"
_PURPLE = "#bb9af7"
_GREEN = "#9ece6a"
_BLUE = "#3d59a1"
_AMBER = "#e0af68"
_RED = "#f7768e"
_DIM = "#565f89"

CHAT_ID = "tui"

# 8-bit dog mascot — 3-line pixel art per mood.
_MASCOT = {
    "idle": (
        " [bold]▄▀▀▀▄[/]\n"
        "▐ [bold {_GREEN}]● ●[/] ▌\n"
        " [bold]▀▄▽▄▀[/]"
    ),
    "thinking": (
        " [bold]▄▀▀▀▄[/]\n"
        "▐ [bold {_AMBER}]◑ ◑[/] ▌\n"
        " [bold]▀▄~▄▀[/]"
    ),
    "happy": (
        " [bold]▄▀▀▀▄[/]\n"
        "▐ [bold {_GREEN}]★ ★[/] ▌\n"
        " [bold]▀▄▽▄▀[/] ♪"
    ),
    "error": (
        " [bold]▄▀▀▀▄[/]\n"
        "▐ [bold {_RED}]; ;[/] ▌\n"
        " [bold]▀▄△▄▀[/]"
    ),
}


class Mascot(Static):
    """Reactive 8-bit dog mascot that changes expression with app state."""

    DEFAULT_CSS = f"""
    Mascot {{
        background: {_BG};
        color: {_PURPLE};
        width: auto;
        height: 3;
        padding: 0 1;
    }}
    """

    def on_mount(self) -> None:
        self.set_mood("idle")

    def set_mood(self, mood: str) -> None:
        self.update(_MASCOT.get(mood, _MASCOT["idle"]))


class StatusBar(Horizontal):
    """Top bar showing model name, connection status, and mascot."""

    DEFAULT_CSS = f"""
    StatusBar {{
        background: {_BG};
        height: 3;
        dock: top;
        align-vertical: middle;
    }}
    """

    def compose(self) -> ComposeResult:
        yield Static(
            f"  SmolClaw · {get_current_model()}  [bold {_GREEN}]● connected[/]",
            classes="status-text",
        )
        yield Mascot()


class MessageBubble(Static):
    """A single chat message rendered as a bubble.

    variant: "user" | "bot" | "cron" | "error" | "system"
    """

    DEFAULT_CSS = f"""
    MessageBubble {{
        margin: 0 1;
        padding: 0 1;
    }}
    MessageBubble.user {{
        background: {_BLUE};
        color: white;
        text-align: right;
        width: auto;
        max-width: 80%;
    }}
    MessageBubble.bot {{
        border: round {_GREEN};
        color: {_GREEN};
        max-width: 80%;
    }}
    MessageBubble.cron {{
        border: round {_AMBER};
        color: {_AMBER};
        text-align: center;
        max-width: 90%;
        offset-x: 5%;
    }}
    MessageBubble.error {{
        border: round {_RED};
        color: {_RED};
        max-width: 80%;
    }}
    MessageBubble.system {{
        color: {_DIM};
        text-align: center;
        text-style: italic;
    }}
    """

    def __init__(self, text: str, variant: str = "bot") -> None:
        super().__init__(text)
        self.add_class(variant)


class UserRow(Horizontal):
    """Right-aligned wrapper for user message bubbles."""

    DEFAULT_CSS = """
    UserRow {
        align-horizontal: right;
        height: auto;
        width: 100%;
    }
    """


class ChatView(ScrollableContainer):
    """Scrollable container holding all message bubbles."""

    # Prevent focus so clicks here don't steal focus from the Input widget.
    # (ScrollableContainer defaults to can_focus=True, which causes space and
    # arrow keys to be consumed by the chat area instead of the input box.)
    _inherit_bindings = False

    DEFAULT_CSS = f"""
    ChatView {{
        background: {_BG};
        height: 1fr;
        padding: 1 0;
    }}
    """


class SmolClawApp(App):
    """Bubble-chat TUI for SmolClaw."""

    CSS = f"""
    Screen {{
        background: {_BG};
        layers: base overlay;
    }}
    .status-text {{
        color: {_PURPLE};
        width: 1fr;
        height: 3;
        content-align-vertical: middle;
    }}
    LoadingIndicator {{
        height: 1;
        background: {_BG};
        color: {_GREEN};
    }}
    Input {{
        background: #24283b;
        border: round #414868;
        color: #c0caf5;
        dock: bottom;
        height: 3;
    }}
    Input:focus {{
        border: round {_GREEN};
    }}
    """

    BINDINGS = [
        Binding("ctrl+r", "reset_session", "Reset session"),
        Binding("ctrl+d", "quit", "Quit"),
        Binding("ctrl+z", "quit", "Quit"),
    ]

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield StatusBar()
        yield ChatView(can_focus=False)
        yield LoadingIndicator()
        yield Input(placeholder="Message SmolClaw...")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_mount(self) -> None:
        self._drain_task = None
        self._scheduler = None
        self._original_send = TelegramSender.send
        # 1. Create queue bound to Textual's event loop
        self._cron_queue: asyncio.Queue[str] = asyncio.Queue()
        # 2. Capture running loop (never use get_event_loop inside async)
        _loop = asyncio.get_running_loop()
        # 3. Patch TelegramSender.send → enqueue to TUI instead of Telegram
        TelegramSender.send = lambda self_s, cid, msg: _loop.call_soon_threadsafe(
            self._cron_queue.put_nowait, msg
        )
        # 4. Start APScheduler (cron jobs will call patched send)
        self._scheduler = setup_scheduler()
        self._scheduler.start()
        # 5. Drain cron queue into chat bubbles
        self._drain_task = asyncio.create_task(self._drain_cron_queue())
        # Hide spinner until agent is running
        self.query_one(LoadingIndicator).display = False
        # Focus input
        self.query_one(Input).focus()

    async def on_unmount(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
        if self._drain_task is not None:
            self._drain_task.cancel()
            await asyncio.gather(self._drain_task, return_exceptions=True)
        if self._original_send is not None:
            TelegramSender.send = self._original_send

    # ------------------------------------------------------------------
    # User input
    # ------------------------------------------------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        msg = event.value.strip()
        if not msg:
            return
        event.input.clear()
        event.input.disabled = True
        self.query_one(LoadingIndicator).display = True
        self.query_one(Mascot).set_mood("thinking")
        await self._append_bubble(msg, "user")
        self._run_agent(msg)

    @work(exclusive=True)
    async def _run_agent(self, msg: str) -> None:
        result = await agent_run(chat_id=CHAT_ID, user_message=msg)
        await self._append_bubble(result, "bot")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "_run_agent":
            return
        state = event.worker.state
        # Only act on terminal states — ignore PENDING and RUNNING.
        if state not in (WorkerState.SUCCESS, WorkerState.ERROR, WorkerState.CANCELLED):
            return
        # Restore UI
        mascot = self.query_one(Mascot)
        self.query_one(LoadingIndicator).display = False
        self.query_one(Input).disabled = False
        self.query_one(Input).focus()
        if state == WorkerState.ERROR:
            mascot.set_mood("error")
            err = str(event.worker.error)
            self.call_after_refresh(self._sync_append_bubble, f"Error: {err}", "error")
        elif state == WorkerState.CANCELLED:
            mascot.set_mood("error")
            self.call_after_refresh(self._sync_append_bubble, "Request cancelled.", "error")
        else:
            mascot.set_mood("happy")

    # ------------------------------------------------------------------
    # Cron drain
    # ------------------------------------------------------------------

    async def _drain_cron_queue(self) -> None:
        try:
            while True:
                msg = await self._cron_queue.get()
                await self._append_bubble(msg, "cron")
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Keyboard actions
    # ------------------------------------------------------------------

    async def action_reset_session(self) -> None:
        await self.query_one(ChatView).remove_children()
        await reset_session(CHAT_ID)
        await self._append_bubble("Session reset.", "system")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # Rows from the bottom within which auto-scroll kicks in.
    _SCROLL_THRESHOLD = 2

    async def _append_bubble(self, text: str, variant: str) -> None:
        chat_view = self.query_one(ChatView)
        widget = UserRow(MessageBubble(text, variant=variant)) if variant == "user" else MessageBubble(text, variant=variant)
        await chat_view.mount(widget)
        self._maybe_scroll(chat_view)

    def _sync_append_bubble(self, text: str, variant: str) -> None:
        """Non-async version for use with call_after_refresh."""
        chat_view = self.query_one(ChatView)
        chat_view.mount(MessageBubble(text, variant=variant))
        self._maybe_scroll(chat_view)

    def _maybe_scroll(self, chat_view: ChatView) -> None:
        if chat_view.scroll_y >= chat_view.max_scroll_y - self._SCROLL_THRESHOLD:
            chat_view.scroll_end(animate=False)
