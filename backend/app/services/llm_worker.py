from __future__ import annotations

import json
import httpx

from app.config import Settings
from app.models import DocumentChunk, Verdict


class LLMWorker:
    """
    System-2 synthesis worker (called ONLY when Jev routes to Work retrieval/synthesis).
    Condenses a verified issuer passage into <=200 chars for the 576x288 G2 display
    and proposes a 1-tap next-quarter check.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def synthesize_card(
        self,
        *,
        ticker: str,
        question: str,
        chunk: DocumentChunk,
        verdict: Verdict,
    ) -> tuple[str, str]:
        """Returns (concise_finding_card, suggested_next_check)."""
        if not self.settings.live_openrouter_enabled:
            return chunk.summary_card, chunk.suggested_next_check

        url = f"{self.settings.openrouter_base_url.rstrip('/')}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "X-OpenRouter-Title": "Cockpit-G2",
        }
        system_prompt = (
            "You format single-glance findings for Even Realities G2 monochrome smart glasses (576x288). "
            "No emojis, no markdown, no financial advice. Return strict JSON with keys "
            "'finding' (<= 175 chars, includes exact figure, comparison, and zero fluff) and "
            "'suggested_next_check' (<= 85 chars, actionable question for next earnings call). "
            "Use ONLY facts present in the provided source passage."
        )
        user_prompt = (
            f"Ticker: {ticker}\n"
            f"Wearer question: {question}\n"
            f"Verdict: {verdict.value}\n"
            f"Source ({chunk.page_ref}): {chunk.excerpt}"
        )
        payload = {
            "model": self.settings.openrouter_llm_model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            finding = str(parsed.get("finding", "")).strip() or chunk.summary_card
            next_check = str(parsed.get("suggested_next_check", "")).strip() or chunk.suggested_next_check
            return finding[:190], next_check[:95]
        except Exception:
            # Never break the HUD on formatting hiccups; fall back to verified sourced card.
            return chunk.summary_card, chunk.suggested_next_check
