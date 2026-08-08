#!/bin/bash
# setup_agent.sh -- create the ElevenLabs agent, add client tools, attach them, push
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

echo "=== creating agent: $AGENT_NAME ==="
# agents add uploads to the platform by default (no --skip-upload in CLI v0.5.x).
OUTPUT=$(elevenlabs agents add "$AGENT_NAME" --template voice-only 2>&1)
echo "$OUTPUT"
echo ""

# Try to find the agent config file that was just created.
AGENT_CONFIG=$(ls -t agent_configs/*.json 2>/dev/null | head -1)
if [ -z "$AGENT_CONFIG" ]; then
	echo "ERROR: no agent config found in agent_configs/"
	exit 1
fi
echo "=== agent config: $AGENT_CONFIG ==="
echo ""

echo "=== adding client tools ==="
TOOL_IDS=()
TOOLS=("open_form" "fill_field" "click_element" "press_key" "get_page_snapshot" "get_page_text")

for tool in "${TOOLS[@]}"; do
	echo "  adding $tool..."
	OUTPUT=$(elevenlabs tools add "$tool" --type client --config-path "./tool_configs/${tool}.json" 2>&1)
	# The tool ID is typically a string like "tool_abc123..." - extract it.
	TOOL_ID=$(echo "$OUTPUT" | grep -oE 'tool_[a-zA-Z0-9]+' | head -1)
	if [ -z "$TOOL_ID" ]; then
		# Fallback: try to find any hash-like ID in the output
		TOOL_ID=$(echo "$OUTPUT" | grep -oE '"id"\s*:\s*"[^"]+"' | head -1 | grep -oE '[a-zA-Z0-9]{20,}')
	fi
	if [ -z "$TOOL_ID" ]; then
		echo "  ERROR: could not extract tool ID from output:"
		echo "  $OUTPUT"
		exit 1
	fi
	echo "  -> $TOOL_ID"
	TOOL_IDS+=("$TOOL_ID")
done
echo ""

echo "=== writing tool_ids into agent config ==="
if [ ! -f "$AGENT_CONFIG" ]; then
	echo "  agent config not found at $AGENT_CONFIG"
	echo "  available configs:"
	ls agent_configs/ 2>/dev/null || echo "  (none)"
	exit 1
fi

# Build the tool_ids JSON array string.
TOOL_IDS_JSON=$(printf '"%s",' "${TOOL_IDS[@]}")
TOOL_IDS_JSON="[${TOOL_IDS_JSON%,}]"

# Use python to merge tool_ids into the agent config, and set the system prompt + LLM.
python3 -c "
import json, sys

with open('$AGENT_CONFIG', 'r') as f:
    config = json.load(f)

config.setdefault('conversation_config', {}).setdefault('agent', {}).setdefault('prompt', {})
config['conversation_config']['agent']['prompt']['tool_ids'] = $TOOL_IDS_JSON
config['conversation_config']['agent']['prompt']['llm'] = 'gemini-2.5-flash'
config['conversation_config']['agent']['prompt']['temperature'] = 0.0
config['conversation_config']['agent']['prompt']['prompt'] = '''You are a voice-driven form-filling assistant operating on the Reachy Mini robot. The user speaks to you and you fill web forms on their behalf using the available client tools.

CRITICAL: When calling open_form, you MUST always pass the url parameter. Never call open_form without a url. If the user says a domain like "google.com" or "example.com", construct the full URL as "https://google.com" or "https://example.com" and pass it as the url parameter. If the user does not specify a URL, ask them for one before calling open_form.

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

config['conversation_config']['agent']['first_message'] = 'Hi! I can help you fill web forms. Just tell me the URL and what information to put in, and I will fill it in for you.'

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

echo "=== pushing agent to ElevenLabs ==="
# agents push --agent takes an agent ID. Pull it from the config file's "id" field
# if present, otherwise push all agents.
AGENT_ID=$(python3 -c "import json; print(json.load(open('$AGENT_CONFIG')).get('id', ''))" 2>/dev/null || echo "")
if [ -n "$AGENT_ID" ]; then
	elevenlabs agents push --agent "$AGENT_ID"
else
	elevenlabs agents push
fi
echo ""

echo "=== agent is live ==="
echo "Get the agent ID from:"
echo "  elevenlabs agents list"
echo ""
echo "Then put it in .env:"
echo "  AGENT_ID=agent_xxxxxxxxxxxxx"
