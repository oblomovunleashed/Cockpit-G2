from __future__ import annotations

import json
import httpx

from app.config import Settings
from app.models import DocumentChunk, DraftCheck, HoldingRecord, Verdict
from app.services.work_skills import build_mock_peer_read_across


class LLMWorker:
    """
    System-2 synthesis worker (called ONLY when Jev routes to a Work skill that
    genuinely needs cross-text synthesis: novel thesis_review, source_followup,
    or cross-holding peer_read_across).
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

        system_prompt = (
            "You format single-glance findings for Even Realities G2 monochrome smart glasses (576x288). "
            "No emojis, no markdown, no financial advice. Return strict JSON with keys "
            "'finding' (<= 175 chars, includes exact figure, comparison, and zero fluff) and "
            "'suggested_next_check' (<= 90 chars, actionable question for next earnings call). "
            "Use ONLY facts inside <source_excerpt>."
        )
        user_prompt = (
            f"Ticker: {ticker}\n"
            f"Wearer question: {question}\n"
            f"Verdict: {verdict.value}\n"
            f"Source citation: {chunk.page_ref}\n"
            f"<source_excerpt>\n{chunk.excerpt}\n</source_excerpt>"
        )
        parsed = await self._call_json_llm(system_prompt, user_prompt)
        if not parsed:
            return chunk.summary_card, chunk.suggested_next_check
        finding = str(parsed.get("finding", "")).strip() or chunk.summary_card
        next_check = str(parsed.get("suggested_next_check", "")).strip() or chunk.suggested_next_check
        return finding[:190], next_check[:110]

    async def synthesize_peer_read_across(
        self,
        *,
        target: HoldingRecord,
        peer_ticker: str,
        peer_chunk: DocumentChunk,
        utterance: str,
    ) -> tuple[str, DraftCheck]:
        """
        Cross-company synthesis skill: connects a verified peer filing passage
        (e.g. TSMC CoWoS / Microsoft capex) to the target holding's saved thesis question.
        """
        fallback_finding, fallback_draft = build_mock_peer_read_across(
            target=target,
            peer_ticker=peer_ticker,
            peer_chunk=peer_chunk,
        )
        if not self.settings.live_openrouter_enabled:
            return fallback_finding, fallback_draft

        system_prompt = (
            "You write concise peer read-across cards for Even Realities G2 smart glasses (576x288). "
            "No emojis, no markdown, no investment advice. Connect ONLY the verified facts in "
            "<peer_excerpt> to the target holding's saved thesis question. Return strict JSON with keys "
            "'read_across' (<= 175 chars: peer figure + crisp implication for target) and "
            "'suggested_next_check' (<= 95 chars: concrete check to verify on the target's next earnings call)."
        )
        user_prompt = (
            f"Target holding: {target.ticker} ({target.company})\n"
            f"Target saved thesis question: {target.saved_question}\n"
            f"Wearer request: {utterance}\n"
            f"Peer company: {peer_ticker}\n"
            f"Peer source: {peer_chunk.page_ref}\n"
            f"<peer_excerpt>\n{peer_chunk.excerpt}\n</peer_excerpt>"
        )
        parsed = await self._call_json_llm(system_prompt, user_prompt)
        if not parsed:
            return fallback_finding, fallback_draft

        finding = str(parsed.get("read_across", "")).strip() or fallback_finding
        check = str(parsed.get("suggested_next_check", "")).strip() or fallback_draft.content
        return (
            finding[:190],
            DraftCheck(
                content=check[:115],
                ticker=target.ticker.upper(),
                source_citation=f"Peer read: {peer_chunk.page_ref}",
            ),
        )

    async def _call_json_llm(self, system_prompt: str, user_prompt: str) -> dict | None:
        url = f"{self.settings.openrouter_base_url.rstrip('/')}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "X-OpenRouter-Title": "Cockpit-G2",
        }
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
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
