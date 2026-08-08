#!/usr/bin/env python3
"""Entry point for the Reachy + ElevenLabs + PinchTab agent.

Wires the robot's mic/speaker to an ElevenLabs Conversational AI agent via
the SDK, and registers client tools that control pinchtab (headed Chrome)
for form-filling. The agent runs entirely on ElevenLabs' cloud - this process
just handles audio I/O, tool execution, and motor control.
"""

import argparse
import json
import logging
import os
import signal
import sys
import threading

from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation, ClientTools

from pinchtab_tools import init_pinchtab, register_tools
from context_tools import init_context, register_context_tools
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
        "--pinchtab-token",
        default=os.getenv("PINCHTAB_TOKEN"),
        help="PinchTab auth token (env: PINCHTAB_TOKEN)",
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
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG, format="%(asctime)s %(name)s: %(message)s"
        )
    else:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(name)s: %(message)s"
        )
    # Show httpx requests so we can see the signed URL fetch + WS connect.
    logging.getLogger("httpx").setLevel(logging.INFO)
    # Keep reachy_mini SDK logs quiet except for warnings.
    logging.getLogger("reachy_mini").setLevel(logging.WARNING)

    if not args.agent_id:
        logger.error("AGENT_ID is required (use --agent-id or set AGENT_ID in .env)")
        sys.exit(1)

    # Print config (mask API key)
    logger.info("config:")
    logger.info("  agent_id: %s", args.agent_id)
    logger.info("  api_key: %s", "***" if args.api_key else "(none - public agent)")
    logger.info("  pinchtab_url: %s", args.pinchtab_url)
    logger.info("  pinchtab_token: %s", "***" if args.pinchtab_token else "(none)")
    logger.info("  reachy_host: %s:%d", args.reachy_host, args.reachy_port)
    logger.info("  motors: %s", "off" if args.no_motors else "on")
    logger.info("  audio: %s", "off" if args.no_audio else "on")

    # --- pinchtab client tools ---
    init_pinchtab(args.pinchtab_url, args.pinchtab_token)
    client_tools = ClientTools()
    register_tools(client_tools)

    # --- context.dev client tools ---
    context_key = os.getenv("CONTEXT_API_KEY")
    init_context(context_key)
    register_context_tools(client_tools)

    # --- motion controller ---
    # Created before audio so we can pass the speaking callback.
    motion = None
    if not args.no_motors:
        from motion import MotionController

        motion = MotionController(robot_host=args.reachy_host, port=args.reachy_port)
        motion.start()
        logger.info("motion controller started")

    # --- robot audio interface ---
    # Start audio BEFORE the conversation so the first message isn't lost.
    # The SDK calls audio_interface.start() in its own thread, but the daemon
    # + ReachyMini + media pipeline takes ~10s. Starting it here means the
    # mic/speaker are ready when the session connects.
    #
    # Wire the motion speaking callback: when TTS audio plays, the robot
    # ramps up to speaking motion (0.7), when it stops, ramps to idle (0.15).
    # Same levels as the Go agent.
    audio_interface = None
    if not args.no_audio:
        on_speaking = motion.set_speaking if motion else None
        audio_interface = ReachyAudioInterface(
            robot_host=args.reachy_host,
            on_speaking_change=on_speaking,
        )
        logger.info("pre-starting audio interface (daemon + media)...")
        audio_interface._pre_start()
        logger.info("audio interface ready")

    # --- ElevenLabs client + conversation ---
    elevenlabs = ElevenLabs(api_key=args.api_key) if args.api_key else ElevenLabs()

    def callback_agent_response(response):
        logger.info("Agent: %s", response)

    def callback_user_transcript(transcript):
        logger.info("User: %s", transcript)

    def callback_agent_response_correction(original, corrected):
        logger.info("Agent corrected: '%s' -> '%s'", original, corrected)

    conversation = Conversation(
        elevenlabs,
        args.agent_id,
        requires_auth=bool(args.api_key),
        audio_interface=audio_interface,
        client_tools=client_tools,
        callback_agent_response=callback_agent_response,
        callback_user_transcript=callback_user_transcript,
        callback_agent_response_correction=callback_agent_response_correction,
    )

    # DEBUG: log raw client_tool_call WebSocket messages to see why parameters
    # such as `url` are not reaching the registered tool functions.
    _orig_handle_message = conversation._handle_message

    def _handle_message_with_logging(message, ws=None):
        if message.get("type") == "client_tool_call":
            logger.info("RAW WS client_tool_call: %s", json.dumps(message))
            logger.info(
                "RAW WS client_tool_call payload: %s",
                json.dumps(message.get("client_tool_call", message)),
            )
        return _orig_handle_message(message, ws)

    conversation._handle_message = _handle_message_with_logging

    # --- start + signal handling ---
    logger.info("starting session with agent %s...", args.agent_id)
    logger.info("ready -- talk to the robot!")
    conversation.start_session()

    def shutdown(sig, frame):
        logger.info("shutting down...")
        conversation.end_session()
        if motion:
            motion.stop()
        os._exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    conversation_id = conversation.wait_for_session_end()
    logger.info("session ended (id=%s)", conversation_id)

    if motion:
        motion.stop()


if __name__ == "__main__":
    main()
