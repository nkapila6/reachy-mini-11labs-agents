"""Client tools that drive pinchtab (headed Chrome) via its HTTP API.

These functions are registered with the ElevenLabs SDK's ClientTools. The LLM
calls them by name to navigate pages, fill forms, click elements, and inspect
the page state through pinchtab's accessibility snapshot.
"""

import requests

from elevenlabs.conversational_ai.conversation import ClientTools


class PinchTabClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.last_tab_id = None

    def navigate(self, url: str, tab_id: str = None) -> dict:
        """Navigate to a URL and remember the returned tabId."""
        body = {"url": url}
        if tab_id:
            body["tabId"] = tab_id
        resp = self.session.post(f"{self.base_url}/navigate", json=body)
        resp.raise_for_status()
        data = resp.json()
        if "tabId" in data:
            self.last_tab_id = data["tabId"]
        return data

    def snapshot(self, tab_id: str = None) -> dict:
        """Get the interactive accessibility tree."""
        params = {"filter": "interactive"}
        if tab_id:
            params["tabId"] = tab_id
        resp = self.session.get(f"{self.base_url}/snapshot", params=params)
        resp.raise_for_status()
        return resp.json()

    def click(self, ref: str, tab_id: str = None) -> dict:
        """Click an element by ref."""
        body = {"kind": "click", "ref": ref}
        if tab_id:
            body["tabId"] = tab_id
        resp = self.session.post(f"{self.base_url}/action", json=body)
        resp.raise_for_status()
        return resp.json()

    def fill(self, ref: str, text: str, tab_id: str = None) -> dict:
        """Fill an input field by ref."""
        body = {"kind": "fill", "ref": ref, "text": text}
        if tab_id:
            body["tabId"] = tab_id
        resp = self.session.post(f"{self.base_url}/action", json=body)
        resp.raise_for_status()
        return resp.json()

    def press_key(self, key: str, tab_id: str = None) -> dict:
        """Press a keyboard key."""
        body = {"kind": "press", "key": key}
        if tab_id:
            body["tabId"] = tab_id
        resp = self.session.post(f"{self.base_url}/action", json=body)
        resp.raise_for_status()
        return resp.json()

    def get_text(self, tab_id: str = None) -> str:
        """Extract page text content."""
        params = {}
        if tab_id:
            params["tabId"] = tab_id
        resp = self.session.get(f"{self.base_url}/text", params=params)
        resp.raise_for_status()
        return resp.text


pinchtab: PinchTabClient | None = None


def init_pinchtab(base_url: str):
    """Create the module-level pinchtab client."""
    global pinchtab
    pinchtab = PinchTabClient(base_url)


def _active_tab_id(override: str | None = None) -> str | None:
    return override or (pinchtab.last_tab_id if pinchtab else None)


def _wrap_errors(func):
    """Decorator that turns network errors into LLM-friendly error results."""

    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except requests.RequestException as e:
            return {"error": str(e)}

    return wrapper


@_wrap_errors
def open_form(params: dict) -> dict:
    url = params.get("url")
    if not url:
        return {"error": "missing required parameter 'url'"}

    result = pinchtab.navigate(url)
    tab_id = result.get("tabId") or pinchtab.last_tab_id
    snapshot = pinchtab.snapshot(tab_id)
    return {"tabId": tab_id, "snapshot": snapshot}


@_wrap_errors
def fill_field(params: dict) -> dict:
    ref = params.get("ref")
    text = params.get("text")
    tab_id = _active_tab_id(params.get("tabId"))
    if not ref or text is None:
        return {"error": "missing required parameters 'ref' and 'text'"}

    pinchtab.fill(ref, text, tab_id)
    return pinchtab.snapshot(tab_id)


@_wrap_errors
def click_element(params: dict) -> dict:
    ref = params.get("ref")
    tab_id = _active_tab_id(params.get("tabId"))
    if not ref:
        return {"error": "missing required parameter 'ref'"}

    pinchtab.click(ref, tab_id)
    return pinchtab.snapshot(tab_id)


@_wrap_errors
def press_key(params: dict) -> dict:
    key = params.get("key")
    tab_id = _active_tab_id(params.get("tabId"))
    if not key:
        return {"error": "missing required parameter 'key'"}

    pinchtab.press_key(key, tab_id)
    return pinchtab.snapshot(tab_id)


@_wrap_errors
def get_page_snapshot(params: dict) -> dict:
    tab_id = _active_tab_id(params.get("tabId"))
    return pinchtab.snapshot(tab_id)


@_wrap_errors
def get_page_text(params: dict) -> dict:
    tab_id = _active_tab_id(params.get("tabId"))
    return {"text": pinchtab.get_text(tab_id)}


def register_tools(client_tools: ClientTools):
    """Register all pinchtab tools with an ElevenLabs ClientTools instance."""
    client_tools.register("open_form", open_form)
    client_tools.register("fill_field", fill_field)
    client_tools.register("click_element", click_element)
    client_tools.register("press_key", press_key)
    client_tools.register("get_page_snapshot", get_page_snapshot)
    client_tools.register("get_page_text", get_page_text)
