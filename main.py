#!/usr/bin/env python3
"""Entry point for the Reachy + ElevenLabs + PinchTab agent.

Wires the robot's mic/speaker to an ElevenLabs Conversational AI agent via
the SDK, and registers client tools that control pinchtab (headed Chrome)
for form-filling. The agent runs entirely on ElevenLabs' cloud - this process
just handles audio I/O and tool execution.
"""

import logging
import os
import signal
import sys

from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation, ClientTools

from pinchtab_tools import init_pinchtab, register_tools
from reachy_audio import ReachyAudioInterface

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s: %(message)s",
)
logger = logging.getLogger("reachy-agent")


def main():
    api_key = os.getenv("ELEVENLABS_API_KEY")
    agent_id = os.getenv("AGENT_ID")
    pinchtab_url = os.getenv("PINCHTAB_URL", "http://localhost:9867")
    reachy_host = os.getenv("REACHY_HOST", "localhost")

    if not agent_id:
        logger.error("AGENT_ID is required (set it in .env or environment)")
        sys.exit(1)

    # --- pinchtab client tools ---
    init_pinchtab(pinchtab_url)
    client_tools = ClientTools()
    register_tools(client_tools)

    # --- robot audio interface ---
    audio_interface = ReachyAudioInterface(robot_host=reachy_host)

    # --- ElevenLabs client + conversation ---
    elevenlabs = ElevenLabs(api_key=api_key) if api_key else ElevenLabs()

    conversation = Conversation(
        elevenlabs,
        agent_id,
        requires_auth=bool(api_key),
        audio_interface=audio_interface,
        client_tools=client_tools,
        callback_agent_response=lambda response: logger.info("Agent: %s", response),
        callback_user_transcript=lambda transcript: logger.info("User: %s", transcript),
    )

    # --- start + signal handling ---
    logger.info("starting session with agent %s...", agent_id)
    conversation.start_session()

    def shutdown(sig, frame):
        logger.info("shutting down...")
        conversation.end_session()

    signal.signal(signal.SIGINT, shutdown)

    conversation_id = conversation.wait_for_session_end()
    logger.info("session ended (id=%s)", conversation_id)


if __name__ == "__main__":
    main()
