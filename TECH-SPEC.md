# TECH-SPEC — reachy-11labs-agent

Technical companion to [`README.md`](README.md): component responsibilities,
data flow, integration contracts, config, and open items for this repo.

## 1. Purpose

A citizen talks to Reachy Mini. The ElevenLabs Conversational AI agent
(cloud-hosted STT/LLM/TTS + tool calling) either:

- answers a government-eligibility/requirements question, sourced live
  from official government pages via context.dev, or
- drives a real browser (via PinchTab) to fill out a government form on
  screen, narrating what it's doing as it goes.

This repo is the thin client + knowledge webhook that makes both possible.
It does not implement any session/approval/review layer — whatever the
agent fills in gets filled in live, on screen, as it talks.

## 2. Components

### 2.1 `main.py` — entry point

Loads `.env`, parses CLI args (agent id, API key, PinchTab URL, Reachy
host/port, `--no-motors`, `--no-audio`), then wires together:

- `pinchtab_tools.init_pinchtab` + `register_tools` — client tools
- `reachy_audio.ReachyAudioInterface` — mic/speaker bridge (skipped with
  `--no-audio`, useful for text-only debugging)
- `motion.MotionController` — speech-driven idle/speaking motion (skipped
  with `--no-motors`)
- `elevenlabs.conversational_ai.conversation.Conversation` — the SDK
  session itself

Does **not** import `gov_intel` or run the Flask webhook — that's a
separate process (`server.py`), reachable independently by ElevenLabs'
cloud as a webhook tool.

### 2.2 `pinchtab_tools.py` — form-filling client tools

Wraps a real, separately-run PinchTab instance's HTTP API
(`pinchtab.com`, typically headed Chrome on a LAN PC, default port 9867).
`PinchTabClient` remembers the last `tabId` so subsequent calls don't need
to pass it explicitly. Registered tools: `open_form`, `fill_field`,
`click_element`, `press_key`, `get_page_snapshot`, `get_page_text` — each
wrapped so network errors come back as `{"error": ...}` (LLM-friendly)
rather than raising.

PinchTab's model: navigate → snapshot the interactive accessibility tree
(elements get `ref`s like `e3`) → act on an element by `ref` → re-snapshot,
since refs expire on navigation.

### 2.3 `reachy_audio.py` / `motion.py` — robot I/O

- `ReachyAudioInterface` implements the ElevenLabs SDK's `AudioInterface`:
  robot mic → PCM → WebSocket, WebSocket → PCM → robot speaker, with AGC +
  noise suppression applied to the mic path.
- `MotionController` (`motion.py`) is a Python port of a Go motion model:
  produces head pose / antenna angle / body yaw at 50 Hz from a single
  "speech level" input and streams it to the robot daemon over its own
  WebSocket (`set_full_target`). `main.py` toggles it via
  `set_speaking(True/False)` around agent speech.

### 2.4 `server.py` — government-knowledge webhook

A small Flask app, run as its own process, independent of `main.py`.
Exposes `POST /api/visa-intel`: accepts a loosely-shaped payload (tries
`question`/`query`/`input`, top-level or under `parameters`, to tolerate
whatever shape ElevenLabs sends), calls
`UAEVisaIntelClient.smart_extract(question)`, summarizes the result with
`LLMVoiceSummarizer`, and returns it under several aliased keys
(`result`/`response`/`answer`/`output`) again to tolerate whatever key the
ElevenLabs webhook-tool response mapping expects.

This is configured on the ElevenLabs dashboard as a **webhook tool** (not
a client tool) — ElevenLabs' cloud calls it directly over HTTPS, so it
must be independently reachable from the internet (deployed, or tunneled)
wherever it runs; being on the same LAN as the robot is not sufficient.

### 2.5 `gov_intel/` — government-knowledge client

- `uae_visa_client.py` — `UAEVisaIntelClient` wraps context.dev's
  `POST /v1/web/extract`, restricted to four official UAE sources (`u.ae`,
  `icp.gov.ae`, `gdrfad.gov.ae`, `mofa.gov.ae`). Every extraction passes an
  explicit `{url, instructions, schema}` — the instructions always tell
  the model not to infer/estimate missing fields. Aggressively caches to
  `visa_cache.json` on disk (24h TTL, since visa rules don't change
  hourly) and falls back to any cached data for the same URL if the API
  call fails, rather than fabricating an answer. `smart_extract(query)`
  picks the right source page from free-text (tourist/golden/student/
  work/green/visit-on-arrival) without a caller having to specify a URL.
- `llm_summarizer.py` — `LLMVoiceSummarizer.summarize_for_speech` turns a
  raw extraction into a 2-3 sentence, markdown-free spoken answer. Tries
  Groq (`llama-3.3-70b-versatile`) → OpenAI (`gpt-4o-mini`) → a
  rule-based template fallback, in that order, so it degrades gracefully
  without any LLM key configured.
- `voice_speaker.py` — `ElevenLabsSpeaker`: microphone recording, STT
  (Scribe v2), TTS, all via direct ElevenLabs REST calls. **Not currently
  imported by `main.py` or `server.py`** — see §6.

## 3. Provenance note

`gov_intel/` and `server.py` originated in a separate repo,
[`Shaaha-7/uae-visa-agent`](https://github.com/Shaaha-7/uae-visa-agent), as
a standalone (non-robot) voice pipeline: record mic → context.dev extract
→ LLM summarize → ElevenLabs TTS → play MP3 (see that repo's
`voice_agent_pipeline.py`). They were copied into this repo to plug
government knowledge into the Reachy agent via a webhook tool. The
`uae-visa-agent` repo's own working tree still shows these files as
locally deleted but uncommitted — that deletion should get committed (or
the repo archived) once this integration is confirmed working, to avoid
two copies drifting apart.

## 4. Data flow

**Form filling:**

```
User speech -> STT (ElevenLabs) -> LLM tool call: open_form(url)
  -> pinchtab_tools.open_form -> PinchTab: POST /navigate, GET /snapshot
  -> snapshot returned to LLM context
  -> LLM tool call: fill_field(ref, text) per field, re-snapshotting each time
  -> LLM narrates -> TTS -> robot speaker
```

**Government knowledge:**

```
User speech -> STT (ElevenLabs) -> LLM tool call: webhook(question)
  -> ElevenLabs cloud -> POST server.py:/api/visa-intel
  -> UAEVisaIntelClient.smart_extract -> context.dev /web/extract (or cache)
  -> LLMVoiceSummarizer.summarize_for_speech
  -> response -> LLM speaks it -> TTS -> robot speaker
```

The two flows are independent — nothing from a knowledge answer is
carried into the form-filling tool calls; the LLM itself is the only
thing connecting "what the citizen said they needed" to "what it fills
into the form."

## 5. Integration contracts

### 5.1 ElevenLabs client tools

Defined in `tool_configs/*.json`, registered in
`pinchtab_tools.register_tools`. Names are case-sensitive and must match
exactly between the dashboard config and the code:

| Tool | Params | Returns |
| --- | --- | --- |
| `open_form` | `url` | `{tabId, snapshot}` |
| `fill_field` | `ref`, `text`, optional `tabId` | updated snapshot |
| `click_element` | `ref`, optional `tabId` | updated snapshot |
| `press_key` | `key`, optional `tabId` | updated snapshot |
| `get_page_snapshot` | optional `tabId` | current snapshot |
| `get_page_text` | optional `tabId` | `{text}` |

If `tabId` is omitted, the client tools fall back to the last tab
returned by `open_form` (`PinchTabClient.last_tab_id`).

### 5.2 ElevenLabs webhook tool

`POST /api/visa-intel` on `server.py`. Request/response shapes are
deliberately loose (see §2.4) to absorb whatever ElevenLabs' webhook-tool
payload/response mapping ends up being configured as. Response includes
`source_url` and `raw_data` alongside the spoken-answer text, so the
webhook-tool config can surface the source to the LLM if desired.

### 5.3 PinchTab HTTP API (consumed, not owned by this repo)

| Endpoint | Purpose |
| --- | --- |
| `POST /navigate` | open a URL, returns `tabId` |
| `GET /snapshot?filter=interactive&tabId=` | interactive accessibility tree |
| `POST /action` (`kind: click/fill/press`) | act on an element by `ref` |
| `GET /text?tabId=` | page text content |
| `GET /health` | liveness check |

## 6. Known gaps / cleanup items

- `gov_intel/voice_speaker.py` (`ElevenLabsSpeaker`) is dead code in this
  repo — nothing imports it outside `gov_intel/__init__.py`'s re-export.
  It's a leftover from `uae-visa-agent`'s standalone pipeline. Either wire
  it in for a non-ElevenLabs-SDK fallback path, or drop it.
- No automated tests. `python main.py --no-motors --no-audio` is the
  closest thing to a manual smoke test (skips audio/motion, still needs a
  live ElevenLabs session and PinchTab reachable).
- `.env.example` doesn't list `PINCHTAB_TOKEN`, even though
  `start_pinchtab.sh` generates one and expects it to be set on the robot
  side once PinchTab is bound to `0.0.0.0` on the LAN — worth adding once
  `pinchtab_tools.py` actually sends it (it currently doesn't attach any
  auth header to its requests).
- `server.py` runs with `debug=True` — fine for local/hackathon use, not
  for anything actually exposed to the internet long-term.
- No shared context between a knowledge answer and a later form-fill: if
  the agent already learned the citizen is applying for a "golden visa"
  during the Q&A flow, that isn't passed into `open_form`/`fill_field`
  calls — the LLM has to re-derive it from conversation history alone.

## 7. Configuration reference (`.env`)

| Var | Used by | Purpose |
| --- | --- | --- |
| `ELEVENLABS_API_KEY`, `AGENT_ID` | `main.py` | ElevenLabs Conversational AI agent |
| `PINCHTAB_URL` | `pinchtab_tools.py` | PinchTab instance URL, e.g. `http://pinchtab-pc:9867` |
| `REACHY_HOST`, `REACHY_PORT` | `main.py`, `motion.py` | Reachy Mini daemon address |
| `CONTEXT_API_KEY` / `CONTEXT_DEV_API_KEY` | `gov_intel/uae_visa_client.py` | context.dev API key (note: two names appear across `.env.example` and the client's own `os.environ.get` — confirm which one is actually read before relying on it) |
| `OPENAI_API_KEY` / `GEMINI_API_KEY` / `GROQ_API_KEY` | `gov_intel/llm_summarizer.py` | optional, speech summarization (Groq → OpenAI → rule-based fallback; `GEMINI_API_KEY` is read but not currently used by the summarizer) |
| `PORT` | `server.py` | webhook server port, default `5000` |

## 8. Testing

No automated test suite currently exists for this repo. Manual checks:

- `curl http://pinchtab-pc:9867/health` — PinchTab reachable
- `python server.py` then `curl -X POST localhost:5000/api/visa-intel -d '{"question":"..."}'` — webhook path end to end
- `uv run reachy-agent --no-motors --no-audio` — text-mode agent session for tool-call debugging without robot hardware
