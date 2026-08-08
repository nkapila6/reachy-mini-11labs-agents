# reachy-11labs-agent — Government Form Voice Assistant

Voice-first assistant that helps a citizen through a government form: talk
to Reachy Mini, ask questions and get answers sourced from official
government sites, then have the robot drive a real browser to fill the
form on screen while you watch.

## Product goal

Help a citizen:

- choose the government service/form they need
- understand requirements and eligibility (sourced live from official
  government pages, never guessed)
- answer the required questions conversationally
- see the real form get filled in on screen as they talk

**The core idea:** Reachy handles the conversation, context.dev handles
government knowledge, and PinchTab executes the real browser form.

## How it works

The ElevenLabs agent runs in the cloud — it handles STT, LLM, TTS, and
tool calling. This repo is a thin client plus a small knowledge webhook:

- Wires the robot's mic and speaker to the ElevenLabs session via a custom
  `AudioInterface`, and drives idle/speaking motion on the robot.
- Registers **client tools** that call PinchTab's HTTP API over the LAN to
  navigate pages, snapshot interactive elements, fill fields, and click
  buttons — the agent sees page snapshots in its conversation context and
  decides the next action.
- Runs a small **Flask webhook server** (`server.py`) that the ElevenLabs
  agent calls (as a webhook tool, configured on the ElevenLabs dashboard)
  to answer government-knowledge questions: it queries context.dev
  restricted to official UAE sources, then summarizes the raw facts into a
  short spoken answer.

The robot talks to ElevenLabs over a WebSocket (audio up/down, client tool
calls dispatched by the SDK). The robot talks to PinchTab over plain HTTP
on the local network. ElevenLabs talks to `server.py`'s webhook over HTTP
wherever that's reachable from their cloud. PinchTab shows a real Chrome
window so the user sees the form being filled.

## Architecture

```
  Reachy Mini (robot)                          ElevenLabs Cloud
  +-------------------+    WebSocket           +-------------------+
  | 11Labs SDK client |<---------------------->| Agent             |
  | + audio bridge     |   audio up/down       | (STT+LLM+TTS      |
  | (mic->PCM, PCM->   |                       |  +tool calling)   |
  |  speaker)          |                       +----+---------+----+
  |                    |   client tools             |         |
  | client tools reg.  |<--- SDK dispatch ----------+         | webhook tool
  | (open_form,        |                                      | call (HTTPS)
  |  fill_field,        |                                      |
  |  click_element,     |                                      v
  |  get_page_snapshot)|                            +----------------------+
  |                    |                            | server.py (Flask)    |
  | motion controller   |                            | POST /api/visa-intel |
  | (speech-driven      |                            +-----------+----------+
  |  pose via WS)       |                                        |
  +----+---------------+                                        v
       | HTTP LAN                                     +--------------------+
       v                                               | gov_intel/          |
  +-------------------+                                | UAEVisaIntelClient  |
  | PinchTab PC        |                                | -> context.dev API  |
  | (headed Chrome)     |                                | (u.ae, icp.gov.ae,  |
  | port 9867 on LAN    |                                |  gdrfad.gov.ae,     |
  | forms visible on    |                                |  mofa.gov.ae only)  |
  | screen              |                                | LLMVoiceSummarizer  |
  +-------------------+                                | -> Groq/OpenAI/     |
                                                        |    rule-based       |
                                                        +--------------------+
```

Two independent request paths, both driven by the ElevenLabs agent's tool
calls:

1. **Form filling** — client tools dispatched to the robot process, which
   calls PinchTab directly over the LAN.
2. **Government knowledge** — a webhook tool call from ElevenLabs' cloud
   straight to `server.py` (wherever it's hosted/tunneled), which calls
   context.dev and summarizes the result for speech.

## Setup

### Prerequisites

- Reachy Mini (Wireless) with daemon running
- A separate PC on the same LAN running PinchTab in headed mode
- An ElevenLabs account with a Conversational AI agent configured (see
  below)
- Python 3.11+
- A context.dev API key (for the government-knowledge webhook)

### Install PinchTab on the form-filling PC

```bash
curl -fsSL https://pinchtab.com/install.sh | bash
```

Then use `./start_pinchtab.sh` to bind it to the LAN, set an auth token,
and start a headed Chrome instance — it prints the `PINCHTAB_URL` and
token to put in the robot's `.env`. (Doing it manually:
`pinchtab server &` then `pinchtab instance start --mode headed`.)

Make sure PinchTab is reachable from the robot's machine:

```bash
curl http://pinchtab-pc:9867/health
```

### Install this project on the robot

```bash
cp .env.example .env
# Fill in: AGENT_ID, ELEVENLABS_API_KEY, PINCHTAB_URL, REACHY_HOST,
# CONTEXT_API_KEY

uv sync
```

### Run the government-knowledge webhook

`server.py` needs to be reachable from ElevenLabs' cloud (e.g. deployed,
or tunneled with something like ngrok during development):

```bash
uv run python server.py   # defaults to :5000, POST /api/visa-intel
```

Point the webhook tool's URL (configured on the ElevenLabs dashboard) at
`https://<wherever-this-is-reachable>/api/visa-intel`.

### Configure the ElevenLabs agent

`./setup_agent.sh` automates this end to end: creates the agent, registers
the six client tools from `tool_configs/`, writes their IDs plus a system
prompt into the agent config, and pushes it — requires
`npm install -g @elevenlabs/cli` and `elevenlabs auth login` first.

Doing it by hand: create client tools on the ElevenLabs dashboard (or via
CLI) matching the tool configs in `tool_configs/`. Each tool is type
"client" with `expects_response: true`. The tool names must match exactly:

- `open_form` - navigate to a URL, returns page snapshot
- `fill_field` - fill an input by ref, returns updated snapshot
- `click_element` - click by ref, returns updated snapshot
- `press_key` - press a keyboard key, returns updated snapshot
- `get_page_snapshot` - get current page interactive elements
- `get_page_text` - extract page text content

Then add the government-knowledge webhook tool separately, pointed at
`server.py`'s `/api/visa-intel` endpoint. Attach all tool IDs to your
agent. Use a high-intelligence LLM (GPT 5.2, Gemini 2.5 Flash, or Claude
Sonnet 4.5) for reliable tool calling.

### Run

```bash
uv run reachy-agent
```

Talk to the robot. Ask it a visa question and it'll answer from official
sources; ask it to fill a form and the agent will narrate what it's doing
while PinchTab fills the form on screen.

### Deploy to the robot

`./deploy.sh` scp's changed files (`main.py`, `reachy_audio.py`,
`pinchtab_tools.py`, `pyproject.toml`, `.env`) to the robot over SSH,
syncs the venv, and prints the run command. Set `ROBOT=user@host` to
override the default target.

## Project structure

```
.
├── main.py              # entry point: wires audio + motion + tools + conversation
├── motion.py             # speech-driven idle/speaking motion, streamed over WS
├── reachy_audio.py      # AudioInterface impl for Reachy Mini mic/speaker
├── pinchtab_tools.py     # client tool functions wrapping PinchTab's HTTP API
├── server.py             # Flask webhook: ElevenLabs -> context.dev government Q&A
├── gov_intel/             # government-knowledge client used by server.py
│   ├── uae_visa_client.py  # context.dev client, scoped to official UAE gov domains
│   ├── llm_summarizer.py   # raw JSON facts -> short spoken answer
│   └── voice_speaker.py    # standalone ElevenLabs STT/TTS helper (not currently wired
│                            # into main.py/server.py — see TECH-SPEC.md)
├── tool_configs/          # ElevenLabs tool definition JSONs (for CLI/dashboard)
├── setup_agent.sh         # creates/configures the ElevenLabs agent + tools
├── start_pinchtab.sh      # configures + starts PinchTab for LAN access
├── deploy.sh               # syncs this project to the robot over SSH
├── pyproject.toml
└── .env.example
```

## Tool call flow

**Filling a form:**

1. User speaks -> robot mic -> audio interface -> WebSocket -> ElevenLabs (STT)
2. LLM decides to call `open_form` with `url: "https://example.com/contact"`
3. SDK invokes the registered `open_form` function on the robot
4. `open_form` calls `POST http://pinchtab-pc:9867/navigate` then `GET /snapshot`
5. PinchTab navigates in headed Chrome, returns interactive elements
6. Tool returns snapshot -> SDK sends it back over WebSocket -> appended to LLM context
7. LLM sees form fields, calls `fill_field` with `ref: "e3"`, `text: "John"`
8. Repeat until the form is filled
9. LLM narrates completion -> TTS -> WebSocket -> audio interface -> robot speaker

**Answering a government-knowledge question:**

1. User asks a visa question -> STT -> LLM decides to call the webhook tool
2. ElevenLabs' cloud calls `POST /api/visa-intel` on `server.py` with the question
3. `server.py` calls `UAEVisaIntelClient.smart_extract`, which picks a
   relevant official source page and asks context.dev to extract from it
   (cached 24h on disk in `gov_intel/visa_cache.json`)
4. `LLMVoiceSummarizer` turns the raw JSON into a 2-3 sentence spoken answer
5. `server.py` returns it -> LLM speaks it -> TTS -> robot speaker

## Notes

- PinchTab refs expire after navigation. Every tool function snapshots again after acting.
- Client tool names are case-sensitive. They must match between the ElevenLabs config and `client_tools.register()` in the code.
- `server.py` must be reachable from ElevenLabs' cloud for the webhook tool to work — it's not enough to just run it on the robot's own network.
- context.dev lookups are restricted to `u.ae`, `icp.gov.ae`, `gdrfad.gov.ae`, and `mofa.gov.ae` — never fabricated or estimated.
- The robot's audio bridge applies DSP config (AGC + noise suppression) to keep the mic clean for STT.
- See [`TECH-SPEC.md`](TECH-SPEC.md) for the full technical breakdown, config reference, and open items.
