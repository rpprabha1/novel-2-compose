"""Local LLM backend (Ollama) for AGENT/HYBRID stages.

This module is deliberately dumb: it sends a system+user message pair to a
local Ollama model and returns raw text. Deciding what to send (the prompt)
and what to do with the response (parse, validate, route) is each stage's
job - this stays generic across 02/06/07/09 (CLAUDE.md: agents are judgment,
everything mechanical around them is code).
"""

from __future__ import annotations

from dataclasses import dataclass

import requests


class AgentBackendError(RuntimeError):
    """Raised on any network/HTTP/empty-response failure. Callers must turn this
    into a FAILED StageResponse - never silently retry with a guessed input or
    fall back to a default output (CLAUDE.md: agents never assume)."""


@dataclass
class AgentCallResult:
    raw_text: str
    model: str


def call_ollama(
    system_prompt: str,
    user_message: str,
    model: str,
    host: str = "http://localhost:11434",
    timeout_s: int = 120,
    json_mode: bool = True,
    options: dict | None = None,
) -> AgentCallResult:
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
    }
    if json_mode:
        payload["format"] = "json"
    if options:
        payload["options"] = options

    try:
        resp = requests.post(f"{host}/api/chat", json=payload, timeout=timeout_s)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise AgentBackendError(f"Ollama call to {host} failed: {exc}") from exc

    data = resp.json()
    content = data.get("message", {}).get("content", "")
    if not content:
        raise AgentBackendError(f"Ollama returned an empty response: {data}")
    return AgentCallResult(raw_text=content, model=model)
