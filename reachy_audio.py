#!/usr/bin/env python3
"""Reachy audio backend for ElevenLabs Conversational AI.

Implements the SDK's AudioInterface so the ElevenLabs conversation can use
the Reachy Mini robot's microphone and speaker directly.
"""

import logging
import threading
import time
from queue import Empty, Queue

import numpy as np
from elevenlabs.conversational_ai.conversation import AudioInterface

logger = logging.getLogger(__name__)

# DSP tuning used by the Reachy Mini audio pipeline. AGC + noise suppression
# keeps the robot mic sounding clean for the conversation model.
AUDIO_STARTUP_CONFIG = (
    ("PP_AGCMAXGAIN", (10.0,)),
    ("PP_MIN_NS", (0.8,)),
    ("PP_MIN_NN", (0.8,)),
    ("PP_GAMMA_E", (0.5,)),
    ("PP_GAMMA_ETAIL", (0.5,)),
    ("PP_NLATTENONOFF", (0,)),
    ("PP_MGSCALE", (4.0, 1.0, 1.0)),
)

# The Reachy speaker is quiet; boost incoming agent audio while staying in
# the [-1, 1] float range expected by push_audio_sample.
OUTPUT_VOLUME_BOOST = 5.0


class ReachyAudioInterface(AudioInterface):
    """Thread-backed AudioInterface for Reachy Mini hardware."""

    def __init__(self, robot_host="localhost"):
        self.robot_host = robot_host
        self.robot = None
        self.input_callback = None
        self.input_sample_rate = 16000

        self._mic_thread = None
        self._output_thread = None
        self._output_queue = Queue()
        self._stop_event = threading.Event()

    def start(self, input_callback):
        from reachy_mini import ReachyMini

        # Start the daemon's hardware backend and wake the robot.
        # Same as the old Go pipeline's EnsureReady().
        self._ensure_daemon_ready()

        # Auto-detect matches the old audio_bridge.py - tries localhost
        # first, falls back to reachy-mini.local. More reliable than
        # forcing a specific host.
        logger.info("connecting to Reachy (auto-detect)...")
        self.robot = ReachyMini()

        logger.info("waking up robot...")
        try:
            self.robot.enable_motors()
            self.robot.wake_up()
        except Exception as e:
            logger.warning("wake_up failed: %s", e)

        logger.info("starting media pipelines...")
        self.robot.media.start_recording()
        self.robot.media.start_playing()
        time.sleep(1)

        self._apply_audio_config()
        self.input_callback = input_callback

        try:
            self.input_sample_rate = self.robot.media.get_input_audio_samplerate()
            logger.info("input sample rate: %d Hz", self.input_sample_rate)
        except Exception:
            self.input_sample_rate = 16000
            logger.info(
                "could not get sample rate, assuming %d Hz", self.input_sample_rate
            )

        self._stop_event.clear()
        self._mic_thread = threading.Thread(
            target=self._mic_loop, name="reachy-mic", daemon=True
        )
        self._output_thread = threading.Thread(
            target=self._output_loop, name="reachy-output", daemon=True
        )
        self._mic_thread.start()
        self._output_thread.start()

    def stop(self):
        logger.info("stopping Reachy audio interface...")
        self._stop_event.set()

        if self._mic_thread is not None:
            self._mic_thread.join(timeout=2)

        # Signal the output worker to exit and drain any pending audio.
        self._output_queue.put(None)
        if self._output_thread is not None:
            self._output_thread.join(timeout=2)

        if self.robot is not None:
            try:
                self.robot.media.stop_recording()
            except Exception as e:
                logger.warning("stop_recording failed: %s", e)
            try:
                self.robot.media.stop_playing()
            except Exception as e:
                logger.warning("stop_playing failed: %s", e)

    def output(self, audio: bytes):
        # The SDK calls this from its websocket thread; hand off to the output
        # worker so we never block the conversation's receive loop.
        self._output_queue.put(audio)

    def interrupt(self):
        # Drop any buffered agent audio and flush the hardware player.
        while not self._output_queue.empty():
            try:
                self._output_queue.get_nowait()
            except Empty:
                break

        if self.robot is not None:
            try:
                self.robot.media.audio.clear_player()
            except Exception as e:
                logger.warning("clear_player failed: %s", e)

    def _ensure_daemon_ready(self):
        """Start the daemon's hardware backend and wake the robot."""
        import urllib.request

        base = f"http://{self.robot_host}:8000"
        logger.info("starting daemon backend at %s...", base)
        try:
            urllib.request.urlopen(f"{base}/api/daemon/start?wake_up=true", timeout=5)
        except Exception as e:
            logger.warning("daemon start request failed: %s (continuing...)", e)

        # Poll until the backend is up (same as the old Go EnsureReady).
        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                resp = urllib.request.urlopen(f"{base}/api/daemon/status", timeout=5)
                body = resp.read().decode()
                if '"backend_status":null' not in body:
                    logger.info("daemon backend is up")
                    time.sleep(2)
                    return
            except Exception:
                pass
            time.sleep(1)
        logger.warning("daemon backend did not come up within 25s")

    def _apply_audio_config(self):
        audio = getattr(getattr(self.robot, "media", None), "audio", None)
        if audio is None:
            logger.warning("robot.media.audio not available")
            return

        apply_config = getattr(audio, "apply_audio_config", None)
        if not callable(apply_config):
            logger.warning("audio.apply_audio_config not available")
            return

        try:
            ok = apply_config(
                AUDIO_STARTUP_CONFIG, verify=True, write_settle_seconds=0.1
            )
            if ok:
                logger.info("audio DSP config applied")
        except Exception as e:
            logger.warning("audio DSP config error: %s", e)

    def _mic_loop(self):
        while not self._stop_event.is_set():
            try:
                frame = self.robot.media.get_audio_sample()
            except Exception:
                time.sleep(0.01)
                continue

            if frame is None or frame.size == 0:
                time.sleep(0.001)
                continue

            # Reachy sometimes returns multi-channel or float frames. Normalize
            # to mono int16 PCM, which is what the ElevenLabs SDK expects.
            if frame.ndim > 1:
                frame = frame.mean(axis=1)
            if frame.dtype == np.float32 or frame.dtype == np.float64:
                frame = (frame * 32767.0).clip(-32768, 32767).astype(np.int16)
            elif frame.dtype != np.int16:
                frame = frame.astype(np.int16)

            if self.input_callback is not None:
                self.input_callback(frame.tobytes())

            # Yield so other threads can run; the SDK will chunk as needed.
            time.sleep(0)

    def _output_loop(self):
        while True:
            audio = self._output_queue.get()
            if audio is None:
                break
            self._play_pcm(audio)

    def _play_pcm(self, audio: bytes):
        try:
            pcm = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
            pcm = np.clip(pcm * OUTPUT_VOLUME_BOOST, -1.0, 1.0)
            self.robot.media.push_audio_sample(pcm)
        except Exception as e:
            logger.warning("playback failed: %s", e)
