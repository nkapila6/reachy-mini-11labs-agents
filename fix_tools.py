#!/usr/bin/env python3
"""Fix deployed ElevenLabs client tools to match local tool_configs/*.json."""

import glob
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
TOOL_CONFIG_DIR = os.path.join(PROJECT_ROOT, "tool_configs")


def load_env(path: str) -> dict:
    """Parse a simple key=value .env file (no dotenv dependency)."""
    values = {}
    if not os.path.exists(path):
        return values
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def load_local_tool_configs() -> dict:
    """Return mapping of tool name -> local config dict."""
    configs = {}
    pattern = os.path.join(TOOL_CONFIG_DIR, "*.json")
    for path in sorted(glob.glob(pattern)):
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        name = cfg.get("name")
        if not name:
            print(f"Warning: skipping {path}: no 'name' field", file=sys.stderr)
            continue
        configs[name] = cfg
    return configs


def build_parameters_input(parameters: list) -> ObjectJsonSchemaPropertyInput:
    """Convert the local parameters array into an ElevenLabs SDK parameters object."""
    if not parameters:
        return ObjectJsonSchemaPropertyInput(type="object", required=[], properties={})

    properties = {}
    required = []
    for param in parameters:
        param_id = param.get("id")
        if not param_id:
            continue
        properties[param_id] = LiteralJsonSchemaProperty(
            type=param.get("type", "string"),
            description=param.get("description", ""),
        )
        if param.get("required", False):
            required.append(param_id)

    return ObjectJsonSchemaPropertyInput(
        type="object",
        description="Parameters for the client tool",
        required=required,
        properties=properties,
    )


def tool_needs_update(remote_tool, local_cfg: dict) -> bool:
    """Check whether the remote tool already matches the local config."""
    cfg = remote_tool.tool_config

    if cfg.name != local_cfg.get("name"):
        return True
    if cfg.description != local_cfg.get("description"):
        return True
    if cfg.expects_response != local_cfg.get("expects_response"):
        return True

    remote_params = cfg.parameters
    remote_props = getattr(remote_params, "properties", None) or {}
    remote_param_ids = set(remote_props.keys())
    local_param_ids = {
        p.get("id") for p in local_cfg.get("parameters", []) if p.get("id")
    }

    return remote_param_ids != local_param_ids


def update_remote_tool(client, tool_id: str, local_cfg: dict) -> bool:
    """Push the local config to ElevenLabs for one tool ID."""
    parameters = build_parameters_input(local_cfg.get("parameters", []))

    tool_config_payload = {
        "type": local_cfg.get("type", "client"),
        "name": local_cfg["name"],
        "description": local_cfg.get("description", ""),
        "expects_response": local_cfg.get("expects_response", True),
        "parameters": parameters,
    }

    request = ToolRequestModel(tool_config=tool_config_payload)
    client.conversational_ai.tools.update(tool_id=tool_id, request=request)
    return True


def main() -> int:
    env = load_env(ENV_PATH)
    api_key = env.get("ELEVENLABS_API_KEY")
    if not api_key:
        print(f"Error: ELEVENLABS_API_KEY not found in {ENV_PATH}", file=sys.stderr)
        return 1

    client = ElevenLabs(api_key=api_key)
    print("Fetching existing tools from ElevenLabs...")
    try:
        existing = client.conversational_ai.tools.list()
    except Exception as e:
        print(f"Error fetching tools: {e}", file=sys.stderr)
        return 1

    local_configs = load_local_tool_configs()
    print(f"Loaded {len(local_configs)} local tool configs.")

    # Map remote tools by name.
    remote_by_name: dict[str, list] = {}
    for tool in existing.tools:
        name = tool.tool_config.name
        remote_by_name.setdefault(name, []).append(tool)

    print("\nUpdating deployed tools...")
    updated_summary = []

    for name in sorted(local_configs):
        local_cfg = local_configs[name]
        remote_tools = remote_by_name.get(name, [])

        if not remote_tools:
            print(f"  {name}: no remote tool found, skipping")
            updated_summary.append(
                {
                    "name": name,
                    "ids": [],
                    "updated": False,
                    "param_ids": [p.get("id") for p in local_cfg.get("parameters", [])],
                    "note": "no remote tool found",
                }
            )
            continue

        tool_ids = []
        any_updated = False
        for tool in remote_tools:
            tool_id = tool.id
            tool_ids.append(tool_id)
            needs_update = tool_needs_update(tool, local_cfg)
            if not needs_update:
                print(f"  {name} ({tool_id}): already correct, skipping")
                continue

            try:
                update_remote_tool(client, tool_id, local_cfg)
                print(f"  {name} ({tool_id}): updated")
                any_updated = True
            except Exception as e:
                print(f"  {name} ({tool_id}): update failed - {e}")
                updated_summary.append(
                    {
                        "name": name,
                        "ids": [tool_id],
                        "updated": False,
                        "param_ids": [],
                        "note": f"error: {e}",
                    }
                )
                continue

        updated_summary.append(
            {
                "name": name,
                "ids": tool_ids,
                "updated": any_updated,
                "param_ids": [p.get("id") for p in local_cfg.get("parameters", [])],
                "note": ""
                if any_updated
                or not any(tool_needs_update(t, local_cfg) for t in remote_tools)
                else "some duplicates skipped due to errors",
            }
        )

    print("\nSummary:")
    print("-" * 60)
    for item in updated_summary:
        ids_str = ", ".join(item["ids"]) if item["ids"] else "(none)"
        status = "updated" if item["updated"] else "no change"
        param_ids = item["param_ids"] or []
        print(f"{item['name']:20s} IDs: {ids_str}")
        print(f"{'':20s} status: {status}")
        print(f"{'':20s} parameters: {param_ids}")
        if item.get("note"):
            print(f"{'':20s} note: {item['note']}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
