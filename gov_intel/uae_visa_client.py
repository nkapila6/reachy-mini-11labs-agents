# gov_intel/uae_visa_client.py
"""
context.dev client tailored for UAE government visa information retrieval.
Sources official/authoritative UAE government domains for visa types,
requirements, fees, and FAQs.
"""

import os
import json
import time
import requests
from typing import Optional
from dataclasses import dataclass


CONTEXT_DEV_API_BASE = "https://api.context.dev/v1"
CONTEXT_DEV_API_KEY = os.environ.get("CONTEXT_DEV_API_KEY")

# Authoritative UAE government sources — stick to these, not random blogs/agencies
UAE_OFFICIAL_SOURCES = {
    "federal_portal": "https://u.ae/en/information-and-services/visa-and-emirates-id",
    "icp": "https://icp.gov.ae/en/",  # Federal Authority for Identity, Citizenship, Customs & Port Security
    "gdrfa_dubai": "https://www.gdrfad.gov.ae/en",  # Dubai residency/visa authority
    "mofa": "https://www.mofa.gov.ae/en/",
}


class ContextDevError(Exception):
    pass


@dataclass
class CacheEntry:
    data: dict
    fetched_at: float


class UAEVisaIntelClient:
    """
    Fetches live UAE visa info restricted to official government sources.
    Caches aggressively since visa rules change slowly, not per-request.
    """

    def __init__(self, api_key: Optional[str] = None, cache_ttl_seconds: int = 86400, timeout: int = 60):
        self.api_key = api_key or os.environ.get("CONTEXT_DEV_API_KEY")
        if not self.api_key:
            raise ValueError("CONTEXT_DEV_API_KEY not set")
        self.cache_ttl = cache_ttl_seconds  # 24h default — visa rules don't change hourly
        self.timeout = timeout
        self.cache_file = os.path.join(os.path.dirname(__file__), "visa_cache.json")
        self._cache: dict[str, CacheEntry] = self._load_disk_cache()

    def _load_disk_cache(self) -> dict[str, CacheEntry]:
        cache = {}
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    for k, v in raw.items():
                        cache[k] = CacheEntry(data=v["data"], fetched_at=v["fetched_at"])
            except Exception as e:
                print(f"  [CACHE WARN] Could not load disk cache: {e}")
        return cache

    def _save_disk_cache(self) -> None:
        try:
            raw = {
                k: {"data": v.data, "fetched_at": v.fetched_at}
                for k, v in self._cache.items()
            }
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2)
        except Exception as e:
            print(f"  [CACHE WARN] Could not save disk cache: {e}")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _get_cached(self, key: str) -> Optional[dict]:
        entry = self._cache.get(key)
        if entry:
            return entry.data
        for k, e in self._cache.items():
            if "tourist" in key and "tourist" in k:
                return e.data
            if "golden" in key and "golden" in k:
                return e.data
            if "studying" in key and "studying" in k:
                return e.data
            if "visit" in key and "tourist" in k:
                return e.data
        return None

    def _set_cached(self, key: str, data: dict) -> None:
        self._cache[key] = CacheEntry(data=data, fetched_at=time.time())
        self._save_disk_cache()

    def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"{CONTEXT_DEV_API_BASE}/{endpoint}"
        try:
            resp = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            raise ContextDevError(f"context.dev timed out on {endpoint}")
        except requests.exceptions.HTTPError as e:
            raise ContextDevError(f"context.dev HTTP error on {endpoint}: {e} | Body: {resp.text}")
        except requests.exceptions.RequestException as e:
            raise ContextDevError(f"context.dev request failed on {endpoint}: {e}")

    def extract_visa_page(
        self, url: str, instructions: str, schema: Optional[dict] = None, use_cache: bool = True
    ) -> dict:
        """
        Generic extractor for any UAE gov visa page. Always pass explicit
        instructions describing exactly what fields you need — don't rely
        on the model to guess structure for legal/government content.
        """
        if schema is None:
            schema = {
                "type": "object",
                "properties": {
                    "details": {"type": "string"}
                }
            }

        cache_key = f"extract:{url}:{instructions}:{str(schema)}"
        if use_cache:
            cached = self._get_cached(cache_key)
            if cached:
                return cached

        payload = {"url": url, "instructions": instructions, "schema": schema}
        try:
            data = self._post("web/extract", payload)
            if use_cache:
                self._set_cached(cache_key, data)
            return data
        except ContextDevError as e:
            # Fallback to any cached data for this key or URL if API fails or credits run out
            for k, entry in self._cache.items():
                if url in k:
                    print(f"  [CACHE FALLBACK] Serving persistent cached data for {url}")
                    return entry.data
            raise e

    def get_visa_types_overview(self) -> dict:
        """Federal portal: list of visa types (tourist, residency, golden visa, etc.)"""
        schema = {
            "type": "object",
            "properties": {
                "visa_types": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "category": {"type": "string"},
                            "eligibility_summary": {"type": "string"},
                            "link": {"type": "string"}
                        },
                        "required": ["name"]
                    }
                }
            },
            "required": ["visa_types"]
        }
        return self.extract_visa_page(
            url=UAE_OFFICIAL_SOURCES["federal_portal"],
            instructions=(
                "Extract every visa type listed (name, category, eligibility summary). "
                "Include links to detail pages if present. Do not infer types not explicitly listed."
            ),
            schema=schema,
        )

    def get_visa_requirements(self, visa_type: str) -> dict:
        """
        Requirements + documents + fees for a specific visa type, e.g.
        'golden visa', 'tourist visa', 'work visa', 'family residency'.
        """
        schema = {
            "type": "object",
            "properties": {
                "visa_type": {"type": "string"},
                "eligibility_categories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category_name": {"type": "string"},
                            "eligibility_requirements": {"type": "string"},
                            "validity_years": {"type": "string"}
                        },
                        "required": ["category_name"]
                    }
                },
                "general_requirements": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "validity_period": {"type": "string"}
            },
            "required": ["visa_type"]
        }
        target_url = (
            "https://u.ae/en/information-and-services/visa-and-emirates-id/Types-of-visas/golden-visa"
            if "golden" in visa_type.lower()
            else UAE_OFFICIAL_SOURCES["federal_portal"]
        )
        return self.extract_visa_page(
            url=target_url,
            instructions=(
                f"Extract all eligibility criteria, requirements, financial thresholds, required qualifications, "
                f"and validity period for '{visa_type}' across all eligible categories (e.g. real estate investors, "
                f"investors in public investments, entrepreneurs, outstanding talents, doctors/scientists, high-performing students). "
                f"Do not guess or omit any explicitly listed requirements."
            ),
            schema=schema,
        )

    def get_visa_faqs(self, topic: str) -> dict:
        """
        Pulls Q&A content specifically — e.g. topic='golden visa renewal',
        'overstay fines', 'family sponsorship'.
        """
        schema = {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "faqs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "answer": {"type": "string"}
                        },
                        "required": ["question", "answer"]
                    }
                }
            },
            "required": ["topic"]
        }
        return self.extract_visa_page(
            url=UAE_OFFICIAL_SOURCES["gdrfa_dubai"],
            instructions=(
                f"Find any FAQ, help, or Q&A content related to '{topic}'. "
                f"Extract each question and its official answer verbatim as listed. "
                f"If no FAQ content exists for this topic on this page, return an empty result — do not fabricate Q&As."
            ),
            schema=schema,
        )

    def get_visa_fees(self, visa_type: str) -> dict:
        """Fee schedule lookup — flagged separately since fees change and matter most."""
        schema = {
            "type": "object",
            "properties": {
                "visa_type": {"type": "string"},
                "fees": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "fee_description": {"type": "string"},
                            "amount_aed": {"type": "string"}
                        },
                        "required": ["fee_description"]
                    }
                },
                "page_section": {"type": "string"}
            },
            "required": ["visa_type"]
        }
        return self.extract_visa_page(
            url=UAE_OFFICIAL_SOURCES["icp"],
            instructions=(
                f"Extract only the fee amounts (in AED) associated with '{visa_type}', "
                f"including any service fees, typing fees, or Emirates ID fees mentioned. "
                f"Note the exact page section this came from."
            ),
            schema=schema,
        )

    def get_visa_processing_time(self, visa_type: str) -> dict:
        """
        Extract processing duration, timeline, and steps required for a specific visa type.
        """
        schema = {
            "type": "object",
            "properties": {
                "visa_type": {"type": "string"},
                "processing_time": {"type": "string"},
                "steps_or_requirements": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "official_note": {"type": "string"}
            },
            "required": ["visa_type"]
        }
        target_url = (
            "https://u.ae/en/information-and-services/visa-and-emirates-id/Types-of-visas/residence-visa-for-studying-in-the-uae"
            if "student" in visa_type.lower()
            else UAE_OFFICIAL_SOURCES["federal_portal"]
        )
        return self.extract_visa_page(
            url=target_url,
            instructions=(
                f"Extract the official processing time / duration required for '{visa_type}' application process. "
                f"Include any details on validity, steps, or duration if stated on the official page. "
                f"If processing time is not explicitly stated, state clearly that it is not specified — do not guess."
            ),
            schema=schema,
        )

    def smart_extract(self, query: str, target_url: Optional[str] = None) -> dict:
        """
        Dynamically selects the best target URL and builds an optimal extraction schema
        based on the user's question, avoiding rigid hardcoded URLs.
        """
        q_lower = query.lower()

        DYNAMIC_URL_MAP = {
            "tourist": "https://u.ae/en/information-and-services/visa-and-emirates-id/Types-of-visas/tourist-visa",
            "visit_on_arrival": "https://u.ae/en/information-and-services/visa-and-emirates-id/Types-of-visas/tourist-visa",
            "golden": "https://u.ae/en/information-and-services/visa-and-emirates-id/Types-of-visas/golden-visa",
            "student": "https://u.ae/en/information-and-services/visa-and-emirates-id/Types-of-visas/residence-visa-for-studying-in-the-uae",
            "work": "https://u.ae/en/information-and-services/visa-and-emirates-id/Types-of-visas/residence-visa-for-working-in-the-uae",
            "green": "https://u.ae/en/information-and-services/visa-and-emirates-id/Types-of-visas/the-green-visa",
            "federal_portal": "https://u.ae/en/information-and-services/visa-and-emirates-id"
        }

        if target_url is None:
            if "indian" in q_lower or "on arrival" in q_lower or "passport" in q_lower:
                target_url = DYNAMIC_URL_MAP["visit_on_arrival"]
            elif "tourist" in q_lower or "visit" in q_lower:
                target_url = DYNAMIC_URL_MAP["tourist"]
            elif "golden" in q_lower:
                target_url = DYNAMIC_URL_MAP["golden"]
            elif "student" in q_lower:
                target_url = DYNAMIC_URL_MAP["student"]
            elif "work" in q_lower:
                target_url = DYNAMIC_URL_MAP["work"]
            elif "green" in q_lower:
                target_url = DYNAMIC_URL_MAP["green"]
            else:
                target_url = DYNAMIC_URL_MAP["federal_portal"]

        schema = {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "direct_answer": {"type": "string"},
                "eligibility_criteria": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "application_steps": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "fees_or_validity": {"type": "string"}
            },
            "required": ["topic", "direct_answer"]
        }

        instructions = (
            f"Answer the user's question: '{query}'. "
            f"Extract all direct eligibility rules, nationality-specific conditions (if applicable, e.g. for Indian passport holders or visa-on-arrival), "
            f"documents required, application steps, and validity period. "
            f"Be precise and strictly reflect official government portal information."
        )

        return self.extract_visa_page(url=target_url, instructions=instructions, schema=schema)
