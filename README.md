# reachy-11labs-agent

Voice-first form-filling assistant for the Reachy Mini robot. The user talks to the robot, the ElevenLabs agent reasons and speaks, and client tools drive pinchtab (headed Chrome on a separate PC) to navigate and fill web forms on screen.

## How it works

The ElevenLabs agent runs in the cloud - it handles STT, LLM, TTS, and tool calling. This project is a thin client that runs on the robot's companion compute:

- Wires the robot's mic and speaker to the ElevenLabs session via a custom `AudioInterface`
- Registers client tools that call pinchtab's HTTP API over the LAN to navigate pages, snapshot interactive elements, fill fields, and click buttons
- The agent sees tool results (page snapshots) in its conversation context and decides the next action

The robot talks to ElevenLabs over a WebSocket (audio up/down, tool calls dispatched by the SDK). The robot talks to pinchtab over plain HTTP on the local network. Pinchtab shows a real Chrome window so the user sees the form being filled.

## Architecture

```
  Reachy Mini (robot)                      ElevenLabs Cloud
  +-------------------+    WebSocket        +------------------+
  | 11Labs SDK client |<------------------>| Agent            |
  | + audio bridge    |   audio up/down    | (STT+LLM+TTS     |
  | (mic->PCM, PCM->  |                   | +tool calling)   |
  |  speaker)         |                   +---+------+-------+
  |                   |   client tools        |      | webhook
  | client tools reg. |--- HTTP LAN -------+ |      |
  | (open_form,       |                    | |      v
  |  fill_field,      |                    | |  +-----------+
  |  click_element,   |                    | |  |context.dev|
  |  get_page_snapshot|                    | |  |etc.       |
  +-------------------+                    | |  +-----------+
                                          |
                     +-------------------+ |
                     | pinchtab PC       |<+
                     | (headed Chrome)   |
                     | port 9867 on LAN  |
                     | forms visible on  |
                     | screen            |
                     +-------------------+
```

## Setup

### Prerequisites

- Reachy Mini (Wireless) with daemon running
- A separate PC on the same LAN running pinchtab in headed mode
- An ElevenLabs account with a Conversational AI agent configured (see below)
- Python 3.11+

### Install pinchtab on the form-filling PC

```bash
curl -fsSL https://pinchtab.com/install.sh | bash
pinchtab server &
pinchtab instance start --mode headed
```

Make sure pinchtab is reachable from the robot's machine:
```bash
curl http://pinchtab-pc:9867/health
```

### Install this project on the robot

```bash
cp .env.example .env
# Fill in: AGENT_ID, ELEVENLABS_API_KEY, PINCHTAB_URL, REACHY_HOST

uv sync
```

### Configure the ElevenLabs agent

Create client tools on the ElevenLabs dashboard (or via CLI) matching the tool configs in `tool_configs/`. Each tool is type "client" with `expects_response: true`. The tool names must match exactly:

- `open_form` - navigate to a URL, returns page snapshot
- `fill_field` - fill an input by ref, returns updated snapshot
- `click_element` - click by ref, returns updated snapshot
- `press_key` - press a keyboard key, returns updated snapshot
- `get_page_snapshot` - get current page interactive elements
- `get_page_text` - extract page text content

Attach the tool IDs to your agent. Set the system prompt to instruct the agent to: open the form, snapshot it, identify fields, fill each field by ref, snapshot to confirm, then submit. Use a high-intelligence LLM (GPT 5.2, Gemini-2.5-Flash, or Claude Sonnet 4.5) for reliable tool calling.

### Run

```bash
uv run reachy-agent
```

Talk to the robot. Ask it to fill a form. The agent will narrate what it's doing while pinchtab fills the form on screen.

## Project structure

```
.
├── main.py              # entry point: wires audio + tools + conversation
├── reachy_audio.py     # AudioInterface impl for Reachy Mini mic/speaker
├── pinchtab_tools.py    # client tool functions wrapping pinchtab HTTP API
├── tool_configs/        # ElevenLabs tool definition JSONs (for CLI/dashboard)
├── pyproject.toml
└── .env.example
```

## Tool call flow

1. User speaks -> robot mic -> audio interface -> WebSocket -> ElevenLabs (STT)
2. LLM decides to call `open_form` with `url: "https://example.com/contact"`
3. SDK invokes the registered `open_form` function on the robot
4. `open_form` calls `POST http://pinchtab-pc:9867/navigate` then `GET /snapshot`
5. pinchtab navigates in headed Chrome, returns interactive elements
6. Tool returns snapshot -> SDK sends it back over WebSocket -> appended to LLM context
7. LLM sees form fields, calls `fill_field` with `ref: "e3"`, `text: "John"`
8. Repeat until form is submitted
9. LLM narrates completion -> TTS -> WebSocket -> audio interface -> robot speaker

## Notes

- pinchtab refs expire after navigation. Every tool function snapshots again after acting.
- Client tool names are case-sensitive. They must match between the ElevenLabs config and `client_tools.register()` in the code.
- For webhook tools (context.dev etc.), configure them directly on the ElevenLabs dashboard. No local code needed - ElevenLabs calls them from their cloud.
- The robot's audio bridge applies DSP config (AGC + noise suppression) to keep the mic clean for STT.
