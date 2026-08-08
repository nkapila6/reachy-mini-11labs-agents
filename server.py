# server.py
"""
Flask Webhook Server for ElevenLabs Conversational AI Tool Integration.

This server acts as a Webhook Tool for ElevenLabs Conversational AI Agents.
When a user talks to an ElevenLabs voice agent, the agent invokes this HTTP webhook
to extract live, official government visa information via context.dev.

Endpoint:
  POST /api/visa-intel
  Payload:  {"question": "How do I apply for tourist visa as an Indian?"}
  Response: {"response": "Official answer summary...", "source_url": "https://u.ae/..."}
"""

import os
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

from gov_intel import UAEVisaIntelClient, ContextDevError, LLMVoiceSummarizer

app = Flask(__name__)
client = UAEVisaIntelClient()
llm_summarizer = LLMVoiceSummarizer()


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "online",
        "service": "UAE Visa Intel Webhook for ElevenLabs Conversational AI",
        "endpoint": "/api/visa-intel"
    })


@app.route("/api/visa-intel", methods=["POST", "GET"])
def visa_intel_webhook():
    """
    Webhook endpoint for ElevenLabs Conversational AI Agents.
    Handles any payload shape sent by ElevenLabs.
    """
    data = request.get_json(silent=True) or {}
    params = data.get("parameters", {}) if isinstance(data.get("parameters"), dict) else {}

    question = (
        data.get("question") or
        data.get("query") or
        data.get("input") or
        params.get("question") or
        params.get("query") or
        params.get("input")
    )

    if not question:
        question = "What are the general UAE visa types and requirements?"

    print(f"\n[ELEVENLABS WEBHOOK] Received question: \"{question}\"")

    try:
        intel_result = client.smart_extract(question)
        extracted_data = intel_result.get("data", {})
        source_url = intel_result.get("url", "")

        # LLM Voice Summarization
        conversational_answer = llm_summarizer.summarize_for_speech(question, extracted_data)

        response_payload = {
            "result": conversational_answer,
            "response": conversational_answer,
            "answer": conversational_answer,
            "output": conversational_answer,
            "source_url": source_url,
            "raw_data": extracted_data
        }
        print(f"[ELEVENLABS WEBHOOK] Responding to ElevenLabs: \"{conversational_answer}\"")
        return jsonify(response_payload), 200

    except ContextDevError as e:
        print(f"[ELEVENLABS WEBHOOK ERROR] {e}")
        return jsonify({
            "error": f"Live government fetch failed: {e}",
            "response": "Could not fetch live government data at the moment. Please check u.ae or ICP directly."
        }), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n============================================================")
    print(f" Starting ElevenLabs Webhook Tool Server on port {port}")
    print(f"   Webhook Endpoint: http://localhost:{port}/api/visa-intel")
    print(f"============================================================\n")
    app.run(host="0.0.0.0", port=port, debug=True)
