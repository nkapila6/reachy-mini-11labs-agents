"""Client tools that search the web via context.dev.

Registered with the ElevenLabs SDK's ClientTools alongside the pinchtab tools.
The LLM calls web_search when it needs live or factual information that isn't
in its training data. context.dev returns titles, URLs, snippets, and full
page markdown in a single call.
"""

import json
import logging
import os

import requests

from elevenlabs.conversational_ai.conversation import ClientTools

logger = logging.getLogger(__name__)

CONTEXT_DEV_BASE = "https://api.context.dev/v1"

# Module-level key, set by init_context.
_api_key: str | None = None


def init_context(api_key: str | None = None):
    """Set the context.dev API key. Called from main.py at startup."""
    global _api_key
    _api_key = api_key or os.environ.get("CONTEXT_API_KEY")
    if not _api_key:
        logger.warning("CONTEXT_API_KEY not set; web_search tool will return an error")
    else:
        logger.info("context.dev web_search ready")


def _wrap_errors(func):
    """Decorator that turns network errors into LLM-friendly error strings."""

    def wrapper(*args, **kwargs):
        try:
            logger.info("tool call: %s(%s)", func.__name__, args[0] if args else {})
            result = func(*args, **kwargs)
            if isinstance(result, str):
                return result
            return json.dumps(result)
        except requests.RequestException as e:
            logger.warning("tool error in %s: %s", func.__name__, e)
            return json.dumps({"error": str(e)})

    return wrapper


@_wrap_errors
def web_search(params: dict) -> dict:
    query = params.get("query")
    if not query:
        return {"error": "missing required parameter 'query'"}

    if not _api_key:
        return {"error": "CONTEXT_API_KEY not set on the robot"}

    num_results = params.get("numResults")
    try:
        num_results = int(num_results) if num_results else 10
    except (TypeError, ValueError):
        num_results = 10
    # context.dev requires 10-100
    num_results = max(10, min(100, num_results))

    payload = {
        "query": query,
        "numResults": num_results,
        # Scrape each result to markdown so the agent gets real content,
        # not just snippets. Strips nav/footer noise via main content only.
        "markdownOptions": {
            "enabled": True,
            "useMainContentOnly": True,
        },
    }

    headers = {
        "Authorization": f"Bearer {_api_key}",
        "Content-Type": "application/json",
    }

    resp = requests.post(
        f"{CONTEXT_DEV_BASE}/web/search",
        json=payload,
        headers=headers,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    # Trim the response so the agent doesn't get flooded with huge markdown.
    # Keep title, url, description, relevance, and the first 2000 chars of
    # markdown content per result. Enough for the agent to reason about.
    trimmed = []
    for r in data.get("results", []):
        md = r.get("markdown", {})
        content = md.get("markdown") if isinstance(md, dict) else None
        if content and len(content) > 2000:
            content = content[:2000] + "\n\n... (truncated)"
        trimmed.append(
            {
                "title": r.get("title"),
                "url": r.get("url"),
                "description": r.get("description"),
                "relevance": r.get("relevance"),
                "content": content,
            }
        )

    return {
        "query": data.get("query", query),
        "results": trimmed,
        "credits_remaining": data.get("key_metadata", {}).get("credits_remaining"),
    }


def register_context_tools(client_tools: ClientTools):
    """Register context.dev tools with an ElevenLabs ClientTools instance."""
    client_tools.register("web_search", web_search)
