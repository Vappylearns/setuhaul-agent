"""Thin LLM adapter: OpenAI SDK pointed at OpenRouter (OpenAI-compatible API).

One small file means LiteLLM or Bedrock can be swapped in later without touching the agent.
The key is read from the environment ONLY. Nothing here ever prints it.
"""
from __future__ import annotations

import json
import os

from . import config


def get_client():
    from openai import OpenAI  # imported lazily so offline mode doesn't need it configured

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("LLM_MODE=live needs OPENROUTER_API_KEY in the environment (.env)")
    return OpenAI(base_url=config.OPENROUTER_BASE_URL, api_key=key, timeout=30, max_retries=2)


def complete(client, messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False):
    """One chat completion at temperature 0. Returns the assistant message object."""
    kwargs = {"model": config.openrouter_model(), "messages": messages, "temperature": 0}
    if tools:
        kwargs["tools"] = tools
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message


def message_to_dict(msg) -> dict:
    """Convert an SDK assistant message (with tool calls) back into a plain dict for the next request."""
    d = {"role": "assistant", "content": msg.content or ""}
    if getattr(msg, "tool_calls", None):
        d["tool_calls"] = [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                           for tc in msg.tool_calls]
    return d


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])
