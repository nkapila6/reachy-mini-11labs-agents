"""Register the web_search client tool on ElevenLabs and attach it to the agent.

Run once after adding web_search to tool_configs/:
    .venv/bin/python3 add_web_search_tool.py
"""

import json
import os
import sys

from elevenlabs import ElevenLabs
from elevenlabs.types.literal_json_schema_property import LiteralJsonSchemaProperty
from elevenlabs.types.object_json_schema_property_input import (
    ObjectJsonSchemaPropertyInput,
)
from elevenlabs.types.tool_request_model import ToolRequestModel

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")
TOOL_CONFIG_PATH = os.path.join(PROJECT_ROOT, "tool_configs", "web_search.json")


def load_env(path):
    if not os.path.exists(path):
        return {}
    values = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def main():
    env = load_env(ENV_PATH)
    api_key = env.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("ERROR: ELEVENLABS_API_KEY not found in .env", file=sys.stderr)
        return 1

    agent_id = env.get("AGENT_ID")
    if not agent_id:
        print("ERROR: AGENT_ID not found in .env", file=sys.stderr)
        return 1

    client = ElevenLabs(api_key=api_key)

    # Load the local tool config
    with open(TOOL_CONFIG_PATH) as f:
        tool_config = json.load(f)

    # Build the parameters object for the SDK
    params = tool_config.get("parameters", [])
    properties = {}
    required = []
    for p in params:
        properties[p["id"]] = LiteralJsonSchemaProperty(
            type=p.get("type", "string"),
            description=p.get("description", ""),
        )
        if p.get("required"):
            required.append(p["id"])

    parameters_input = ObjectJsonSchemaPropertyInput(
        type="object",
        description="Parameters for the client tool",
        required=required,
        properties=properties,
    )

    tool_config_payload = {
        "type": tool_config.get("type", "client"),
        "name": tool_config["name"],
        "description": tool_config.get("description", ""),
        "expects_response": tool_config.get("expects_response", True),
        "parameters": parameters_input,
    }

    # Check if web_search already exists in the agent's tools
    existing_tools = client.conversational_ai.tools.list()
    web_search_ids = []
    for t in existing_tools.tools:
        if t.tool_config.name == "web_search":
            web_search_ids.append(t.id)

    if web_search_ids:
        tool_id = web_search_ids[0]
        print(f"web_search tool already exists: {web_search_ids}")
        print(f"updating tool {tool_id} with latest config...")
        request = ToolRequestModel(tool_config=tool_config_payload)
        client.conversational_ai.tools.update(tool_id=tool_id, request=request)
    else:
        print("creating web_search tool...")
        request = ToolRequestModel(tool_config=tool_config_payload)
        created = client.conversational_ai.tools.create(request=request)
        tool_id = created.id
        print(f"created web_search tool: {tool_id}")

    # Attach to the agent if not already attached
    agent = client.conversational_ai.agents.get(agent_id)
    existing_tool_ids = agent.conversation_config.agent.prompt.tool_ids or []

    if tool_id not in existing_tool_ids:
        updated_tool_ids = existing_tool_ids + [tool_id]
        print(f"attaching web_search to agent {agent_id}...")
        client.conversational_ai.agents.update(
            agent_id=agent_id,
            conversation_config={
                "agent": {"prompt": {"tool_ids": updated_tool_ids}},
            },
        )
        print(f"agent tool_ids: {updated_tool_ids}")
    else:
        print(f"web_search already attached to agent (tool_id={tool_id})")

    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
