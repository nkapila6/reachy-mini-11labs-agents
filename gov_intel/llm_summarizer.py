# gov_intel/llm_summarizer.py
"""
LLM Voice Summarizer:
Grounds and transforms raw context.dev JSON extractions into warm, natural,
concise 2-3 sentence speech scripts tailored for real-time ElevenLabs voice audio.
"""

import os
import json
import requests
from typing import Optional


class LLMVoiceSummarizer:
    """
    LLM Summarizer module supporting OpenAI, Gemini, or Groq API (with smart template fallback).
    Refines raw extracted government data into human-like conversational answers.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.openai_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.gemini_key = os.environ.get("GEMINI_API_KEY")
        self.groq_key = os.environ.get("GROQ_API_KEY")

    def summarize_for_speech(self, user_question: str, raw_extracted_data: dict) -> str:
        """
        Takes the user's question and raw context.dev JSON, and synthesizes a concise,
        warm, conversational speech response (max 60-80 words).
        """
        system_prompt = (
            "You are a friendly, expert UAE Visa & Government Assistant speaking to a user over a live voice call. "
            "Your task is to take the user's question and the raw verified government facts provided, "
            "and create a warm, natural, 2-to-3 sentence spoken answer. "
            "Rules:\n"
            "1. Be concise, direct, and conversational (optimized for voice speech).\n"
            "2. Strictly use the provided government facts — do not invent or estimate rules.\n"
            "3. Do not include markdown formatting, bullet points, or complex symbols since this will be read aloud."
        )

        user_content = (
            f"User Question: {user_question}\n\n"
            f"Extracted Government Facts (from context.dev):\n"
            f"{json.dumps(raw_extracted_data, indent=2)}"
        )

        # 1. Try Groq API if available
        if self.groq_key:
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.groq_key}", "Content-Type": "application/json"},
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content}
                        ],
                        "max_tokens": 150
                    },
                    timeout=10
                )
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"].strip()
            except Exception as e:
                print(f"  [LLM] Groq API call failed: {e}")

        # 2. Try OpenAI API if available
        if self.openai_key:
            try:
                resp = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.openai_key}", "Content-Type": "application/json"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content}
                        ],
                        "max_tokens": 150
                    },
                    timeout=10
                )
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"].strip()
            except Exception as e:
                print(f"  [LLM] OpenAI API call failed: {e}")

        # 3. Smart Conversational Rule-Based Fallback
        return self._smart_fallback_summary(user_question, raw_extracted_data)

    def _smart_fallback_summary(self, question: str, data: dict) -> str:
        """
        Creates a clean, conversational fallback speech response if no LLM API key is set.
        """
        direct_ans = data.get("direct_answer")
        eligibility = data.get("eligibility_criteria") or []
        steps = data.get("application_steps") or []
        visa_type = data.get("visa_type", "visa")

        sentences = []

        if direct_ans:
            sentences.append(direct_ans)
        else:
            sentences.append(f"Here is what official UAE government portals state regarding {question}.")

        if eligibility:
            top_criteria = "; ".join(eligibility[:2])
            sentences.append(f"Key requirements include: {top_criteria}.")

        if steps:
            sentences.append(f"To apply, you can submit your application through {steps[0]}.")

        return " ".join(sentences)
