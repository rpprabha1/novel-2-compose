# shared/agents/

Local LLM backend for AGENT/HYBRID stages (02_beat_extraction, 06_fallback_generation, 07_editorial_direction, 09_audio_production). Backend and model choice are configured in `config/agents.yaml`, not hardcoded (CLAUDE.md §9: no magic numbers/values outside `config/`).

**Backend: Ollama**, running locally. `default_model: llama3.2:3b` was chosen to fit the actual development machine (NVIDIA MX230, 2GB VRAM; ~8GB system RAM) — it's the largest model that runs comfortably there. This is a config value, not a hardcoded assumption: swap it in `config/agents.yaml` (globally or per-stage) with no code change as better hardware or models become available.

## Interface

```python
call_ollama(system_prompt: str, user_message: str, model: str, host: str, timeout_s: int, json_mode: bool, options: dict) -> AgentCallResult
```

Sends a system+user message pair, returns raw text (JSON-formatted when `json_mode=True`, using Ollama's structured-output mode). Raises `AgentBackendError` on any network/HTTP/empty-response failure — a calling stage must turn that into a `FAILED` `StageResponse`, never silently retry with a guessed input or fall back to a default output.

Each stage owns: rendering its `AGENT_PROMPT.md` into the actual system prompt, building the user message from its inputs, parsing/validating the returned JSON against its output schema, and deciding `NEEDS_INPUT`/`FALLBACK_ROUTED` routing. This module only knows how to talk to Ollama — no stage-specific logic lives here.

## Config loading

```python
load_agent_config(repo_root: Path) -> dict   # parses config/agents.yaml
resolve_model(agent_config: dict, stage_name: str) -> str   # per-stage override, else default_model
```
