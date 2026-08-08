#!/usr/bin/env python3
"""Entry point for the Reachy + ElevenLabs + PinchTab agent.

Wires the robot's mic/speaker to an ElevenLabs Conversational AI agent via
the SDK, and registers client tools that control pinchtab (headed Chrome)
for form-filling. The agent runs entirely on ElevenLabs' cloud - this process
just handles audio I/O, tool execution, and motor control.
"""

import argparse
import logging
import os
import signal
import sys

from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation, ClientTools

from pinchtab_tools import init_pinchtab, register_tools
from reachy_audio import ReachyAudioInterface

logger = logging.getLogger("reachy-agent")


def load_env():
    """Load .env file into os.environ if it exists."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    if key and key not in os.environ:
                        os.environ[key] = value


def main():
    load_env()

    parser = argparse.ArgumentParser(description="Reachy + ElevenLabs + PinchTab agent")
    parser.add_argument(
        "--agent-id",
        default=os.getenv("AGENT_ID"),
        help="ElevenLabs agent ID (env: AGENT_ID)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("ELEVENLABS_API_KEY"),
        help="ElevenLabs API key (env: ELEVENLABS_API_KEY)",
    )
    parser.add_argument(
        "--pinchtab-url",
        default=os.getenv("PINCHTAB_URL", "http://localhost:9867"),
        help="PinchTab HTTP URL (env: PINCHTAB_URL)",
    )
    parser.add_argument(
        "--reachy-host",
        default=os.getenv("REACHY_HOST", "localhost"),
        help="Reachy Mini daemon host (env: REACHY_HOST)",
    )
    parser.add_argument(
        "--reachy-port",
        type=int,
        default=int(os.getenv("REACHY_PORT", "8000")),
        help="Reachy Mini daemon port (env: REACHY_PORT)",
    )
    parser.add_argument(
        "--no-motors", action="store_true", default=False, help="Disable motor control"
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        default=False,
        help="Disable audio (text-only mode for testing)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

    if not args.agent_id:
        logger.error("AGENT_ID is required (use --agent-id or set AGENT_ID in .env)")
        sys.exit(1)

    # Print config (mask API key)
    logger.info("config:")
    logger.info("  agent_id: %s", args.agent_id)
    logger.info("  api_key: %s", "***" if args.api_key else "(none - public agent)")
    logger.info("  pinchtab_url: %s", args.pinchtab_url)
    logger.info("  reachy_host: %s:%d", args.reachy_host, args.reachy_port)
    logger.info("  motors: %s", "off" if args.no_motors else "on")
    logger.info("  audio: %s", "off" if args.no_audio else "on")

    # --- pinchtab client tools ---
    init_pinchtab(args.pinchtab_url)
    client_tools = ClientTools()
    register_tools(client_tools)

    # --- robot audio interface ---
    audio_interface = None
    if not args.no_audio:
        audio_interface = ReachyAudioInterface(robot_host=args.reachy_host)

    # --- motion controller ---
    motion = None
    if not args.no_motors:
        from motion import MotionController

        motion = MotionController(robot_host=args.reachy_host, port=args.reachy_port)
        motion.start()
        motion.set_speaking(False)
        logger.info("motion controller started")

    # --- ElevenLabs client + conversation ---
    elevenlabs = ElevenLabs(api_key=args.api_key) if args.api_key else ElevenLabs()

    # Build callback dict - include motion speaking toggle if motion is enabled
    callback_agent_response = lambda response: logger.info("Agent: %s", response)
    callback_user_transcript = lambda transcript: logger.info("User: %s", transcript)

    conversation = Conversation(
        elevenlabs,
        args.agent_id,
        requires_auth=bool(args.api_key),
        audio_interface=audio_interface,
        client_tools=client_tools,
        callback_agent_response=callback_agent_response,
        callback_user_transcript=callback_user_transcript,
    )

    # --- start + signal handling ---
    logger.info("starting session with agent %s...", args.agent_id)
    conversation.start_session()

    def shutdown(sig, frame):
        logger.info("shutting down...")
        conversation.end_session()
        if motion:
            motion.stop()

    signal.signal(signal.SIGINT, shutdown)

    conversation_id = conversation.wait_for_session_end()
    logger.info("session ended (id=%s)", conversation_id)

    if motion:
        motion.stop()


if __name__ == "__main__":
    main()
