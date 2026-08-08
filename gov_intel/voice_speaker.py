# gov_intel/voice_speaker.py
"""
ElevenLabs Voice Integration for UAE Visa Agent.
- Text-to-Speech (TTS): Converts structured visa answers into spoken audio (MP3).
- Speech-to-Text (STT): Transcribes user voice recordings into text via Scribe v2.
- Microphone Recording: Records audio from the user's microphone.
"""

import os
import wave
import requests
from typing import Optional


# Default ElevenLabs settings
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel (default clear voice)
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"


class ElevenLabsError(Exception):
    pass


class ElevenLabsSpeaker:
    """
    Full voice client for ElevenLabs:
    - record_microphone(): Records audio from the user's mic.
    - speech_to_text(): Transcribes audio file to text via Scribe v2.
    - generate_speech(): Converts text to spoken audio (MP3) via TTS.
    - speak_visa_answer(): End-to-end structured answer to voice.
    """

    def __init__(self, api_key: Optional[str] = None, voice_id: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
        self.voice_id = voice_id or os.environ.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE_ID

    # ─────────────────────────────────────────────────────────────
    # 1. MICROPHONE RECORDING
    # ─────────────────────────────────────────────────────────────

    def record_microphone(
        self,
        duration_seconds: int = 8,
        output_path: str = "user_voice_input.wav",
        sample_rate: int = 16000
    ) -> str:
        """
        Records audio from the default microphone for the given duration.
        Saves as a WAV file and returns the absolute path.
        """
        try:
            import sounddevice as sd
            import numpy as np
        except ImportError:
            raise ElevenLabsError(
                "sounddevice/numpy not installed. Run: pip install sounddevice numpy"
            )

        print(f"\n  [MIC] Recording for {duration_seconds} seconds... SPEAK NOW!")
        audio_data = sd.rec(
            int(duration_seconds * sample_rate),
            samplerate=sample_rate,
            channels=1,
            dtype="int16"
        )
        sd.wait()  # Block until recording is done
        print("  [MIC] Recording complete.")

        abs_path = os.path.abspath(output_path)
        with wave.open(abs_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit = 2 bytes
            wf.setframerate(sample_rate)
            wf.writeframes(audio_data.tobytes())

        print(f"  [MIC] Audio saved to: {abs_path}")
        return abs_path

    # ─────────────────────────────────────────────────────────────
    # 2. SPEECH-TO-TEXT (ElevenLabs Scribe v2)
    # ─────────────────────────────────────────────────────────────

    def speech_to_text(
        self,
        audio_path: str,
        model_id: str = "scribe_v2",
        language_code: str = "eng"
    ) -> str:
        """
        Sends an audio file to ElevenLabs Speech-to-Text API (Scribe v2)
        and returns the transcribed text.
        """
        if not self.api_key:
            raise ElevenLabsError(
                "ELEVENLABS_API_KEY is not set. Add it to your .env file."
            )

        headers = {
            "xi-api-key": self.api_key,
        }
        data = {
            "model_id": model_id,
            "language_code": language_code,
        }

        abs_path = os.path.abspath(audio_path)
        with open(abs_path, "rb") as audio_file:
            files = {
                "file": (os.path.basename(abs_path), audio_file, "audio/wav"),
            }
            try:
                response = requests.post(
                    ELEVENLABS_STT_URL,
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=30
                )
                response.raise_for_status()
                result = response.json()
                transcript = result.get("text", "").strip()
                print(f"  [STT] Transcribed text: \"{transcript}\"")
                return transcript

            except requests.exceptions.HTTPError as e:
                raise ElevenLabsError(
                    f"ElevenLabs STT HTTP Error: {e} | Response: {response.text}"
                )
            except requests.exceptions.RequestException as e:
                raise ElevenLabsError(f"ElevenLabs STT Request Failed: {e}")

    # ─────────────────────────────────────────────────────────────
    # 3. TEXT-TO-SPEECH (ElevenLabs TTS)
    # ─────────────────────────────────────────────────────────────

    def generate_speech(
        self,
        text: str,
        output_path: str = "response_speech.mp3",
        model_id: str = "eleven_multilingual_v2"
    ) -> str:
        """
        Sends text to ElevenLabs TTS API and saves the resulting MP3 audio file.

        :param text: Plain text string to convert to speech.
        :param output_path: Destination filename for the saved .mp3 audio file.
        :param model_id: ElevenLabs model identifier.
        :return: Absolute path of the generated .mp3 file.
        """
        if not self.api_key:
            raise ElevenLabsError(
                "ELEVENLABS_API_KEY is not set. Please add ELEVENLABS_API_KEY=your_key to your .env file."
            )

        url = f"{ELEVENLABS_TTS_URL}/{self.voice_id}"
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg"
        }
        payload = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()

            abs_path = os.path.abspath(output_path)
            with open(abs_path, "wb") as f:
                f.write(response.content)

            print(f"  [TTS] Audio answer saved to: {abs_path}")
            return abs_path

        except requests.exceptions.HTTPError as e:
            raise ElevenLabsError(f"ElevenLabs TTS HTTP Error: {e} | Response: {response.text}")
        except requests.exceptions.RequestException as e:
            raise ElevenLabsError(f"ElevenLabs TTS Request Failed: {e}")

    # ─────────────────────────────────────────────────────────────
    # 4. STRUCTURED ANSWER -> SPEECH
    # ─────────────────────────────────────────────────────────────

    def speak_visa_answer(self, question: str, response_data: dict, output_path: str = "visa_answer.mp3") -> str:
        """
        Takes a question and response dictionary from UAEVisaIntelClient,
        creates a clean natural text summary, and generates an MP3 via ElevenLabs.
        """
        data = response_data.get("data", {})
        summary_lines = [f"Here is the official information regarding: {question}."]

        if "direct_answer" in data and data["direct_answer"]:
            summary_lines.append(f"{data['direct_answer']}")

        if "visa_type" in data and data["visa_type"]:
            summary_lines.append(f"Visa type: {data['visa_type']}.")

        if "validity_period" in data and data["validity_period"]:
            summary_lines.append(f"Validity period is {data['validity_period']}.")

        if "eligibility_categories" in data and data["eligibility_categories"]:
            summary_lines.append("Key eligible categories include:")
            for cat in data["eligibility_categories"][:3]:  # Top categories for concise speech
                summary_lines.append(f"{cat.get('category_name')}: {cat.get('eligibility_requirements')}.")

        if "eligibility_criteria" in data and data["eligibility_criteria"]:
            summary_lines.append("Eligibility criteria:")
            for item in data["eligibility_criteria"][:3]:
                summary_lines.append(f"{item}.")

        if "application_steps" in data and data["application_steps"]:
            summary_lines.append("Application steps:")
            for step in data["application_steps"][:3]:
                summary_lines.append(f"{step}.")

        if "processing_time" in data and data["processing_time"]:
            summary_lines.append(f"Processing time: {data['processing_time']}.")

        if "official_note" in data and data["official_note"]:
            summary_lines.append(f"Note: {data['official_note']}")

        full_speech_text = " ".join(summary_lines)
        return self.generate_speech(full_speech_text, output_path=output_path)

