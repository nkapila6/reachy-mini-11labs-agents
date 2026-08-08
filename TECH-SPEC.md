# TECH-SPEC — reachy-11labs-agent

Component responsibilities, data flow, integration contracts, config, and open items.

## 1. Purpose

Voice assistant on the Reachy Mini robot. The heavy work (STT, LLM, TTS, tool routing) runs in the ElevenLabs cloud. The robot runs a thin Python client that wires its mic and speaker to the ElevenLabs Conversational AI agent, executes client tools locally, and fans them out to two backends:

- context.dev for live web search (`web_search`).
- PinchTab for browser control (open/fill/click/press/snapshot/text tools) on a headed Chrome instance running on a separate PC on the LAN.

## 2. Components

### `main.py` — entry point and wiring

Loads `.env`, parses CLI args, then connects the pieces:

- `pinchtab_tools.init_pinchtab` + `register_tools` for browser tools.
- `context_tools.init_context` + `register_context_tools` for web search.
- `motion.MotionController` for speech-driven head/antenna/body motion (disabled with `--no-motors`).
- `reachy_audio.ReachyAudioInterface` for mic/speaker I/O (disabled with `--no-audio`).
- `elevenlabs.conversational_ai.conversation.Conversation` to start the session.

Also patches `conversation._handle_message` to log raw `client_tool_call` WebSocket frames for debugging tool parameter issues.

CLI flags: `--agent-id`, `--api-key`, `--pinchtab-url`, `--pinchtab-token`, `--reachy-host`, `--reachy-port`, `--no-motors`, `--no-audio`, `--debug`.

### `pinchtab_tools.py` — PinchTab client tools

`PinchTabClient` wraps the PinchTab HTTP API:

- Polls `GET /instances` for up to 15 seconds at startup and pins the first running headed instance (`X-Instance-Id` header) so the user can see the browser. Falls back to the default instance if none is found.
- Reuses the last `tabId` across calls.

Registered tools:

- `open_form` — `POST /navigate` then `GET /snapshot`, returns `{tabId, snapshot}`.
- `fill_field` — `POST /action` with `kind: fill`, returns updated snapshot.
- `click_element` — `POST /action` with `kind: click`, returns updated snapshot.
- `press_key` — `POST /action` with `kind: press`, returns updated snapshot.
- `get_page_snapshot` — `GET /snapshot?filter=interactive`.
- `get_page_text` — `GET /text`, returns `{text}`.

Network errors are caught and returned as `{"error": "..."}` so the LLM sees them instead of a tool crash.

### `context_tools.py` — web search tool

Registered tool: `web_search`.

Calls `POST https://api.context.dev/v1/web/search` directly from the robot with:

- `query`
- `numResults` (clamped 10..100)
- `markdownOptions: { enabled: true, useMainContentOnly: true }`

Trims each result's markdown to 2000 characters and returns title, url, description, relevance, the trimmed content, and `credits_remaining`.

### `reachy_audio.py` — mic/speaker bridge

`ReachyAudioInterface` implements the ElevenLabs SDK's `AudioInterface`:

- Starts the Reachy daemon backend, connects to `ReachyMini(automatic_body_yaw=False)`, wakes the robot, and starts recording/playback.
- Applies DSP config (AGC, noise suppression) via `apply_audio_config`.
- Reads mic frames in a background thread, normalizes to mono int16 PCM, and passes them to the SDK input callback.
- Receives TTS audio from the SDK, boosts volume by 5x, clips to [-1, 1], and pushes to the speaker in another thread.
- Calls `on_speaking_change(True/False)` when TTS playback starts/stops, so `motion.py` ramps between speaking and idle motion.

Pre-starts the audio pipeline before the conversation starts so the first agent message isn't lost.

### `motion.py` — speech-driven motion

Python port of a Go motion model. `MotionController` connects to `ws://{REACHY_HOST}:{REACHY_PORT}/ws/sdk` and streams poses at 50 Hz.

`MotionModel` produces continuous head pose (SE3 matrix), antenna angles, and body yaw driven by a speech level signal. `set_speaking(True)` raises the level to 0.7 (active head sway, antenna perk). `set_speaking(False)` drops it to 0.15 (gentle idle sway). Messages sent as `{"type": "set_full_target", "head", "antennas", "body_yaw"}`.

On shutdown it sends `{"type": "goto_sleep"}` and closes the WebSocket.

### `setup_agent.sh` — ElevenLabs agent and tool setup

Idempotent. Looks up the agent and tools in `agents.json` and `tools.json`; reuses existing ones instead of creating duplicates. If the agent doesn't exist, creates it from the `voice-only` template.

For each of the seven tools, backs up the config (the CLI overwrites it with a default template), calls `elevenlabs tools add`, restores the config, then pushes the real parameters via the ElevenLabs Python API so `url`, `ref`, `text`, `query`, etc. are set correctly.

Finally injects the tool IDs, prompt, LLM (`gemini-2.5-flash`), temperature, first message, TTS/ASR config, and conversation settings into the agent config and pushes it.

### `deploy.sh` — robot deploy

Copies changed files to the Reachy Mini by comparing SHA256 hashes. Prompts for `.env` values if `.env` is missing or `--env` is passed. Stops a running `main.py` before overwriting files, then syncs the Python venv with `uv sync` on the robot.

### `start_pinchtab.sh` — PinchTab startup

Run on the PinchTab PC, not the robot. Sets `server.bind` to `0.0.0.0` and `security.allowedDomains` once, then starts the PinchTab server with a runtime token via `PINCHTAB_TOKEN`. The headed default instance is expected to be configured via `instanceDefaults.mode headed` in PinchTab config.

## 3. Data flow

### Web search

```
User speech -> ElevenLabs STT -> LLM calls web_search
  -> robot calls context.dev POST /web/search
  -> trimmed results returned to LLM context
  -> LLM answers -> TTS -> robot speaker + motion ramp
```

### Form filling

```
User speech -> ElevenLabs STT -> LLM calls open_form(url)
  -> robot calls PinchTab POST /navigate + GET /snapshot
  -> snapshot returned to LLM context
  -> LLM calls fill_field(ref, text) / click_element(ref) / press_key(key)
  -> robot calls PinchTab POST /action + GET /snapshot
  -> LLM narrates progress -> TTS -> robot speaker
```

Both flows share the same conversation context; the LLM can move from search to opening a result and then interacting with it.

## 4. Integration contracts

### ElevenLabs client tools

| Tool | Parameters | Returns |
| --- | --- | --- |
| `open_form` | `url` | `{"tabId", "snapshot"}` |
| `fill_field` | `ref`, `text`, optional `tabId` | updated snapshot |
| `click_element` | `ref`, optional `tabId` | updated snapshot |
| `press_key` | `key`, optional `tabId` | updated snapshot |
| `get_page_snapshot` | optional `tabId` | snapshot |
| `get_page_text` | optional `tabId` | `{"text"}` |
| `web_search` | `query`, optional `numResults` | trimmed results array + `credits_remaining` |

Names and parameter ids must match exactly between `tool_configs/*.json` and the Python registrations.

### context.dev API

`POST https://api.context.dev/v1/web/search`

Request body:

```json
{
  "query": "...",
  "numResults": 10,
  "markdownOptions": {
    "enabled": true,
    "useMainContentOnly": true
  }
}
```

Response contains `query`, `results[]`, and `key_metadata.credits_remaining`. Each result has `url`, `title`, `description`, `relevance`, and `markdown.markdown`.

### PinchTab HTTP API

| Endpoint | Purpose |
| --- | --- |
| `GET /instances` | list Chrome instances; used to pin a headed instance |
| `GET /health` | liveness check |
| `POST /navigate` | open URL, optionally reuse `tabId`, returns new `tabId` |
| `GET /snapshot?filter=interactive&tabId=...` | interactive accessibility tree with element refs |
| `POST /action` | perform `kind: click`, `kind: fill`, or `kind: press` on a ref |
| `GET /text?tabId=...` | extract page text content |

All authenticated calls send `Authorization: Bearer {PINCHTAB_TOKEN}`. Pinned calls add `X-Instance-Id: {instance_id}`.

## 5. Configuration reference (`.env`)

| Variable | Used by | Purpose |
| --- | --- | --- |
| `ELEVENLABS_API_KEY` | `main.py` | Optional. Set for non-public agents. |
| `AGENT_ID` | `main.py` | ElevenLabs Conversational AI agent ID. Required. |
| `PINCHTAB_URL` | `main.py`, `pinchtab_tools.py` | PinchTab base URL, e.g. `http://pinchtab-pc:9867`. |
| `PINCHTAB_TOKEN` | `pinchtab_tools.py` | Bearer token for PinchTab API calls. |
| `CONTEXT_API_KEY` | `context_tools.py` | context.dev API key for `web_search`. |
| `REACHY_HOST` | `main.py`, `reachy_audio.py`, `motion.py` | Reachy Mini daemon host. |
| `REACHY_PORT` | `main.py`, `motion.py` | Reachy Mini daemon port (default 8000). |

## 6. Known gaps / cleanup items

- No automated tests.
- `web_search` trims page markdown to 2000 characters per result. Hard-coded in `context_tools.py`; make configurable if longer content is needed.
- PinchTab instance pinning retries for 15 seconds then silently falls back to the default instance. If the headed browser takes longer to start, tool calls will run headless.
- Raw WebSocket debug logging for `client_tool_call` messages is patched into `conversation._handle_message` in `main.py`. Remove once tool parameter delivery is stable.
- Earlier `setup_agent.sh` runs may have left duplicate tool IDs on the ElevenLabs side. The first matching set in `tools.json` is reused and attached to the agent; duplicates are harmless.

## 7. Testing

Manual checks:

- `curl {PINCHTAB_URL}/health` from the robot to confirm PinchTab is reachable.
- `curl -H "Authorization: Bearer {CONTEXT_API_KEY}" -X POST https://api.context.dev/v1/web/search -d '{"query":"..."}'` to confirm context.dev access.
- `uv run reachy-agent --no-motors --no-audio` for text-mode tool-call debugging without robot hardware.
