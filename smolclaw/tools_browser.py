from __future__ import annotations

from claude_agent_sdk import tool


def _text(t: str) -> dict:
    return {"content": [{"type": "text", "text": t}]}


async def _browser_call(method: str, *args) -> dict:
    from .browser import BrowserManager
    try:
        result = await getattr(BrowserManager.get(), method)(*args)
        return _text(str(result) if isinstance(result, str) else f"OK: {result}")
    except Exception as e:
        return _text(f"Browser error: {e}")


@tool(
    "browse",
    "Navigate to a URL in a headless browser. Renders JavaScript. "
    "Returns page title and visible text content. Use for JS-heavy pages that WebFetch can't read. "
    "Creates a persistent browser session per chat_id — subsequent browser_* calls reuse it.",
    {"chat_id": str, "url": str},
)
async def browse(args: dict) -> dict:
    from .browser import BrowserManager

    try:
        result = await BrowserManager.get().navigate(str(args["chat_id"]), str(args["url"]))
        return _text(f"Title: {result['title']}\nURL: {result['url']}\n\n{result['text']}")
    except Exception as e:
        return _text(f"Browser error: {e}")


@tool(
    "browser_click",
    "Click an element on the current browser page by CSS selector.",
    {"chat_id": str, "selector": str},
)
async def browser_click(args: dict) -> dict:
    return await _browser_call("click", str(args["chat_id"]), str(args["selector"]))


@tool(
    "browser_type",
    "Type text into a form field by CSS selector. Clears existing content first.",
    {"chat_id": str, "selector": str, "text": str},
)
async def browser_type(args: dict) -> dict:
    return await _browser_call("type_text", str(args["chat_id"]), str(args["selector"]), str(args["text"]))


@tool(
    "browser_screenshot",
    "Take a screenshot of the current browser page. Returns the file path. "
    "Use Read tool to view the image or telegram_send_file to send it.",
    {"chat_id": str},
)
async def browser_screenshot(args: dict) -> dict:
    from .browser import BrowserManager
    try:
        path = await BrowserManager.get().screenshot(str(args["chat_id"]))
        return _text(f"Screenshot saved: {path}")
    except Exception as e:
        return _text(f"Browser error: {e}")


@tool(
    "browser_eval",
    "Execute JavaScript on the current browser page and return the result.",
    {"chat_id": str, "javascript": str},
)
async def browser_eval(args: dict) -> dict:
    return await _browser_call("evaluate", str(args["chat_id"]), str(args["javascript"]))
