#!/bin/bash
# setup_agent.sh -- create or update the ElevenLabs agent and client tools.
#
# Idempotent: if the agent and tools already exist (tracked in agents.json and
# tools.json), reuses them instead of creating duplicates. Safe to run multiple
# times.
#
# Prerequisites:
#   npm install -g @elevenlabs/cli
#   elevenlabs auth login
#
# Usage:
#   ./setup_agent.sh
#   AGENT_NAME="My Custom Agent" ./setup_agent.sh

set -euo pipefail

AGENT_NAME="${AGENT_NAME:-Reachy Form Filler}"

# --- check if the agent already exists (via agents.json) ---
# agents.json is the CLI's source of truth for which agents we've created.
# It maps config file paths to agent IDs. We check it by name match against
# the config file's "name" field.
AGENT_CONFIG=""
AGENT_ID=""

if [ -f "agents.json" ]; then
	MATCH=$(python3 -c "
import json, os, sys
with open('agents.json') as f:
    agents = json.load(f).get('agents', [])
for a in agents:
    cfg = a.get('config', '')
    if not cfg or not os.path.exists(cfg):
        continue
    try:
        with open(cfg) as cf:
            name = json.load(cf).get('name', '')
    except Exception:
        continue
    if name == '$AGENT_NAME':
        print(f\"{cfg}|{a['id']}\")
        break
" 2>/dev/null || echo "")
	if [ -n "$MATCH" ]; then
		AGENT_CONFIG="${MATCH%%|*}"
		AGENT_ID="${MATCH##*|}"
	fi
fi

if [ -n "$AGENT_ID" ]; then
	echo "=== agent already exists: $AGENT_NAME ($AGENT_ID) ==="
	echo "  config: $AGENT_CONFIG"
	echo "  will update config and push."
	echo ""
else
	echo "=== creating agent: $AGENT_NAME ==="
	OUTPUT=$(elevenlabs agents add "$AGENT_NAME" --template voice-only 2>&1)
	echo "$OUTPUT"
	echo ""

	# Find the agent config file (most recently created).
	AGENT_CONFIG=$(ls -t agent_configs/*.json 2>/dev/null | head -1)
	if [ -z "$AGENT_CONFIG" ]; then
		echo "ERROR: no agent config found in agent_configs/"
		exit 1
	fi

	# Get the agent ID from agents.json (the CLI writes it there on creation).
	AGENT_ID=$(python3 -c "
import json
with open('agents.json') as f:
    agents = json.load(f).get('agents', [])
for a in agents:
    if a.get('config') == '$AGENT_CONFIG':
        print(a['id'])
        break
" 2>/dev/null || echo "")
	if [ -z "$AGENT_ID" ]; then
		echo "ERROR: could not find agent ID for $AGENT_CONFIG in agents.json"
		echo "  agents.json contents:"
		cat agents.json
		exit 1
	fi
	echo "=== agent config: $AGENT_CONFIG (id: $AGENT_ID) ==="
	echo ""
fi

# --- tools: check tools.json for existing tools by name ---
# tools.json tracks all tools we've created. We match by the tool name
# appearing in the config path (e.g. ./tool_configs/open_form.json -> open_form).
TOOLS=("open_form" "fill_field" "click_element" "press_key" "get_page_snapshot" "get_page_text" "web_search")

echo "=== registering client tools ==="
TOOL_IDS=()

get_existing_tool_id() {
	local name="$1"
	if [ ! -f "tools.json" ]; then
		echo ""
		return
	fi
	python3 -c "
import json
with open('tools.json') as f:
    data = json.load(f)
for t in data.get('tools', []):
    cfg_path = t.get('config', '')
    if '${name}.json' in cfg_path:
        print(t['id'])
        break
" 2>/dev/null || echo ""
}

for tool in "${TOOLS[@]}"; do
	TOOL_CONFIG_FILE="./tool_configs/${tool}.json"
	if [ ! -f "$TOOL_CONFIG_FILE" ]; then
		echo "  ERROR: tool config not found: $TOOL_CONFIG_FILE"
		exit 1
	fi

	EXISTING_TOOL_ID=$(get_existing_tool_id "$tool")

	if [ -n "$EXISTING_TOOL_ID" ]; then
		echo "  $tool: already exists ($EXISTING_TOOL_ID), reusing"
		TOOL_IDS+=("$EXISTING_TOOL_ID")
	else
		# Back up the config - the CLI overwrites it with a default template.
		cp "$TOOL_CONFIG_FILE" "${TOOL_CONFIG_FILE}.bak"
		echo "  adding $tool..."
		OUTPUT=$(elevenlabs tools add "$tool" --type client --config-path "$TOOL_CONFIG_FILE" 2>&1)
		# Restore our real config immediately.
		mv "${TOOL_CONFIG_FILE}.bak" "$TOOL_CONFIG_FILE"
		TOOL_ID=$(echo "$OUTPUT" | grep -oE 'tool_[a-zA-Z0-9]+' | head -1)
		if [ -z "$TOOL_ID" ]; then
			TOOL_ID=$(echo "$OUTPUT" | grep -oE '"id"\s*:\s*"[^"]+"' | head -1 | grep -oE '[a-zA-Z0-9]{20,}')
		fi
		if [ -z "$TOOL_ID" ]; then
			echo "  ERROR: could not extract tool ID from output:"
			echo "  $OUTPUT"
			exit 1
		fi
		echo "  -> $TOOL_ID"
		TOOL_IDS+=("$TOOL_ID")
		# CLI creates a default tool with no parameters. Push the real config
		# via the API so parameters (url, ref, text, etc.) are set correctly.
		echo "  pushing real config for $tool..."
		python3 -c "
import json, os, sys
from elevenlabs import ElevenLabs
from elevenlabs.types.literal_json_schema_property import LiteralJsonSchemaProperty
from elevenlabs.types.object_json_schema_property_input import ObjectJsonSchemaPropertyInput
from elevenlabs.types.tool_request_model import ToolRequestModel

with open('$TOOL_CONFIG_FILE') as f:
    cfg = json.load(f)

props = {}
required = []
for p in cfg.get('parameters', []):
    props[p['id']] = LiteralJsonSchemaProperty(
        type=p.get('type', 'string'),
        description=p.get('description', ''),
    )
    if p.get('required'):
        required.append(p['id'])

params = ObjectJsonSchemaPropertyInput(
    type='object',
    description='Parameters for the client tool',
    required=required,
    properties=props,
)

payload = {
    'type': cfg.get('type', 'client'),
    'name': cfg['name'],
    'description': cfg.get('description', ''),
    'expects_response': cfg.get('expects_response', True),
    'parameters': params,
}

client = ElevenLabs()
client.conversational_ai.tools.update(tool_id='$TOOL_ID', request=ToolRequestModel(tool_config=payload))
print(f'  $tool config pushed: params={required}')
" 2>&1 || echo "  WARNING: failed to push $tool config via API"
	fi
done
echo ""

# --- write tool_ids into agent config and set prompt/LLM ---
echo "=== configuring agent ==="

TOOL_IDS_JSON=$(printf '"%s",' "${TOOL_IDS[@]}")
TOOL_IDS_JSON="[${TOOL_IDS_JSON%,}]"

python3 -c "
import json, sys

with open('$AGENT_CONFIG', 'r') as f:
    config = json.load(f)

config.setdefault('conversation_config', {}).setdefault('agent', {}).setdefault('prompt', {})
config['conversation_config']['agent']['prompt']['tool_ids'] = $TOOL_IDS_JSON
config['conversation_config']['agent']['prompt']['llm'] = 'gemini-2.5-flash'
config['conversation_config']['agent']['prompt']['temperature'] = 0.0
config['conversation_config']['agent']['prompt']['prompt'] = '''You are a voice-driven assistant operating on the Reachy Mini robot. You do two things: search the web for live information, and fill web forms on the user's behalf using the available client tools.

WEB SEARCH:
When the user asks a question that needs live or factual information you don't know (current events, official requirements, product details, latest news), call web_search with a clear query. Read the returned results and answer concisely. If a result looks useful to show the user, call open_form with that URL.

FORM FILLING:
CRITICAL: When calling open_form, you MUST always pass the url parameter. Never call open_form without a url. If the user says a domain like \"google.com\" or \"example.com\", construct the full URL as \"https://google.com\" or \"https://example.com\" and pass it as the url parameter. If the user does not specify a URL, ask them for one before calling open_form.

When the user asks you to fill a form:
1. Call open_form with the full URL (always include https://). The url parameter is REQUIRED.
2. Examine the returned snapshot to identify form fields by their refs (e.g. e5, e12).
3. Ask the user for any information you need (name, email, phone, etc.).
4. Call fill_field for each field using the ref and the value.
5. Call get_page_snapshot to verify the form is correctly filled.
6. Call click_element on the submit button when ready.
7. Narrate what you are doing throughout. Tell the user what you are filling in and what happened after submission.

Always snapshot after any action that might change the page. Refs from old snapshots are invalid after navigation or page changes.

If the user asks to read the page, use get_page_text. If they ask to see what is on screen, use get_page_snapshot.'''

config['conversation_config']['agent']['first_message'] = 'Hi! I can help you fill web forms or search the web for information. Just tell me what you need.'

# Ensure TTS and ASR are configured for voice.
config['conversation_config'].setdefault('tts', {})
config['conversation_config']['tts']['model_id'] = 'eleven_turbo_v2'
config['conversation_config']['tts']['agent_output_audio_format'] = 'pcm_16000'

config['conversation_config'].setdefault('asr', {})
config['conversation_config']['asr']['provider'] = 'scribe_realtime'
config['conversation_config']['asr']['quality'] = 'high'
config['conversation_config']['asr']['user_input_audio_format'] = 'pcm_16000'

config['conversation_config'].setdefault('conversation', {})
config['conversation_config']['conversation']['text_only'] = False
config['conversation_config']['conversation']['max_duration_seconds'] = 600
config['conversation_config']['conversation']['client_events'] = ['audio', 'interruption']

with open('$AGENT_CONFIG', 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')
"
echo "  written to $AGENT_CONFIG"
echo ""

# --- push agent to ElevenLabs ---
echo "=== pushing agent to ElevenLabs ==="
elevenlabs agents push --agent "$AGENT_ID" --no-ui
echo ""

echo "=== agent is live ==="
echo "  Agent ID: $AGENT_ID"
echo "  Config: $AGENT_CONFIG"
echo ""
echo "Put this in .env:"
echo "  AGENT_ID=$AGENT_ID"
