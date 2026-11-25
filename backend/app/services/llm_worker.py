from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import re
from typing import Literal
import httpx

from app.config import Settings
from app.models import (
    DocumentChunk,
    DraftCheck,
    EnrichmentStatus,
    HoldingRecord,
    JournalEntry,
    Mind,
    Verdict,
)
from app.services.work_skills import build_grounded_peer_read_across


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_MD_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_TICKER_IN_PARENS_RE = re.compile(r"\(([A-Z]{1,6}(?:\.[A-Z])?)\)")
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def strip_markdown(text: str) -> str:
    """Strips markdown links/formatting for Even G2 fixed-font monochrome rendering."""
    cleaned = _MD_LINK_RE.sub(r"\1", text)
    cleaned = _MD_BOLD_RE.sub(r"\1", cleaned)
    cleaned = re.sub(r"\s*\(\s*\)", "", cleaned)
    return " ".join(cleaned.strip().split())


def resolve_relative_date(text: str, today: date) -> str | None:
    """Deterministic calendar resolution for common relative dates (returns YYYY-MM-DD)."""
    lower = text.lower()
    if re.search(r"\btoday\b|\btonight\b", lower):
        return today.isoformat()
    if re.search(r"\btomorrow\b", lower):
        return (today + timedelta(days=1)).isoformat()
    if re.search(r"\byesterday\b", lower):
        return (today - timedelta(days=1)).isoformat()

    m_in = re.search(r"\bin\s+(\d+)\s+(day|days|week|weeks)\b", lower)
    if m_in:
        qty = int(m_in.group(1))
        unit = m_in.group(2)
        delta_days = qty * 7 if unit.startswith("week") else qty
        return (today + timedelta(days=delta_days)).isoformat()

    m_wd = re.search(
        r"\b(this|next)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        lower,
    )
    if m_wd:
        modifier = m_wd.group(1)
        target_wd = _WEEKDAYS[m_wd.group(2)]
        days_ahead = (target_wd - today.weekday()) % 7
        if modifier == "next":
            # "next Friday" means Friday of next week (at least 7 days if still ahead this week, or next week's Friday)
            days_until_next_monday = (7 - today.weekday()) % 7 or 7
            next_week_start = today + timedelta(days=days_until_next_monday)
            return (next_week_start + timedelta(days=target_wd)).isoformat()
        if days_ahead == 0:
            days_ahead = 7
        return (today + timedelta(days=days_ahead)).isoformat()

    if re.search(r"\bthis weekend\b", lower):
        days_to_sat = (5 - today.weekday()) % 7
        return (today + timedelta(days=days_to_sat)).isoformat()
    if re.search(r"\bnext week\b", lower):
        days_until_next_monday = (7 - today.weekday()) % 7 or 7
        return (today + timedelta(days=days_until_next_monday)).isoformat()
    return None


def _derive_short_ticker(company_query: str) -> str:
    m = _TICKER_IN_PARENS_RE.search(company_query)
    if m:
        return m.group(1)
    letters = re.sub(r"[^A-Za-z]", "", company_query.split()[0] if company_query.strip() else "")
    return (letters[:6] or "EQUITY").upper()


@dataclass(frozen=True)
class EnrichmentResult:
    status: EnrichmentStatus
    enriched_summary: str
    enriched_url: str = ""
    due_date: str | None = None


class LLMWorker:
    """
    System-2 synthesis & enrichment worker powered by OpenRouter:
      - Tracked Work cards (thesis_review, follow_up_source, peer_read_across) from indexed filings.
      - Any-company web-grounded earnings preview/review/follow-up (held OR not held!).
      - Async post-confirm journal enrichment (date-aware + web-grounded episode/entity resolution).
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
            "Plain text only: no emojis, no markdown, no links, no financial advice. Return strict JSON with keys "
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
        parsed, _urls = await self._call_json_llm(system_prompt, user_prompt)
        if not parsed:
            return chunk.summary_card, chunk.suggested_next_check
        finding = strip_markdown(str(parsed.get("finding", ""))) or chunk.summary_card
        next_check = strip_markdown(str(parsed.get("suggested_next_check", ""))) or chunk.suggested_next_check
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
        fallback_finding, fallback_draft = build_grounded_peer_read_across(
            target=target,
            peer_ticker=peer_ticker,
            peer_chunk=peer_chunk,
        )
        if not self.settings.live_openrouter_enabled:
            return fallback_finding, fallback_draft

        system_prompt = (
            "You write concise peer read-across cards for Even Realities G2 smart glasses (576x288). "
            "Plain text only: no emojis, no markdown, no links, no investment advice. Connect ONLY the verified facts in "
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
        parsed, _urls = await self._call_json_llm(system_prompt, user_prompt)
        if not parsed:
            return fallback_finding, fallback_draft

        finding = strip_markdown(str(parsed.get("read_across", ""))) or fallback_finding
        check = strip_markdown(str(parsed.get("suggested_next_check", ""))) or fallback_draft.content
        return (
            finding[:190],
            DraftCheck(
                content=check[:115],
                ticker=target.ticker.upper(),
                source_citation=f"Peer read: {peer_chunk.page_ref}",
            ),
        )

    async def synthesize_adhoc_earnings(
        self,
        *,
        company_query: str,
        mode: Literal["preview", "review", "followup"],
        utterance: str,
        today_date: date | None = None,
    ) -> tuple[str, str, str, DraftCheck]:
        """
        Web-grounded earnings preview / review / follow-up for ANY company (held or not held).
        Returns (ticker, concise_finding, source_label, suggested_draft_check).
        """
        today = today_date or datetime.now(timezone.utc).date()
        short_ticker = _derive_short_ticker(company_query)

        if not self.settings.live_openrouter_enabled:
            action_label = "preview" if mode == "preview" else "review"
            finding = (
                f"{company_query} earnings {action_label}: live OpenRouter web search is off (mock mode). "
                f"Enable live OpenRouter to fetch current {today.year} watchpoints."
            )
            source_label = "Offline mock"
            draft = DraftCheck(
                content=f"Check {short_ticker} latest earnings guidance and key drivers",
                ticker=short_ticker,
                source_citation=f"Web {action_label}: {company_query}",
            )
            return short_ticker, finding[:190], source_label, draft

        mode_instruction = {
            "preview": (
                "Provide a crisp pre-earnings preview for the upcoming quarter: expected print window if known, "
                "prior-quarter baseline figures, and top 2-3 thesis watchpoints investors care about."
            ),
            "review": (
                "Provide a crisp earnings review of the most recently reported quarter as of today: "
                "headline revenue/segment beats or misses, margin/capex surprise, and management guidance."
            ),
            "followup": (
                "Directly answer the wearer's specific factual question about this company using the latest "
                "reported earnings release, transcript, or investor update."
            ),
        }[mode]

        system_prompt = (
            f"Today is {today.isoformat()} ({today.strftime('%A')}). "
            "You format single-glance equity research cards for Even Realities G2 monochrome smart glasses (576x288). "
            f"{mode_instruction} "
            "Plain text ONLY: no emojis, no markdown, no [text](url) links inside text fields, no investment advice. "
            "If web results do not contain a figure, do NOT fabricate it. Return strict JSON with keys: "
            "'ticker' (uppercase symbol, e.g. GOOGL), "
            "'company' (short name), "
            "'finding' (<= 175 chars, dense with concrete numbers/watchpoints), "
            "'source_label' (<= 40 chars, e.g. 'Alphabet Q3 2026 IR' or 'Reuters · Nov 2026'), "
            "'suggested_next_check' (<= 95 chars, specific watch item for the next call)."
        )
        user_prompt = (
            f"Company requested: {company_query}\n"
            f"Skill mode: {mode}\n"
            f"Wearer utterance: {utterance}"
        )

        parsed, citation_urls = await self._call_json_llm(
            system_prompt,
            user_prompt,
            use_web=True,
            max_web_results=3,
        )
        if not parsed:
            finding = f"Could not retrieve live web earnings data for {company_query} right now."
            source_label = "Web lookup unavailable"
            draft = DraftCheck(
                content=f"Follow up on {short_ticker} earnings watchpoints",
                ticker=short_ticker,
                source_citation="Web lookup unavailable",
            )
            return short_ticker, finding, source_label, draft

        resolved_ticker = strip_markdown(str(parsed.get("ticker", ""))).upper() or short_ticker
        resolved_ticker = re.sub(r"[^A-Z0-9.\-]", "", resolved_ticker)[:8] or short_ticker
        finding = strip_markdown(str(parsed.get("finding", "")))
        if not finding:
            finding = f"No verified web earnings summary returned for {company_query}."
        source_label = strip_markdown(str(parsed.get("source_label", ""))) or "Live Web"
        next_check = strip_markdown(str(parsed.get("suggested_next_check", ""))) or (
            f"Review {resolved_ticker} next earnings guidance and key drivers"
        )
        citation = f"Web: {source_label}"
        if citation_urls and len(citation) < 55:
            citation = f"Web: {source_label}"

        draft = DraftCheck(
            content=next_check[:115],
            ticker=resolved_ticker,
            source_citation=citation[:80],
        )
        return resolved_ticker, finding[:190], source_label[:45], draft

    async def enrich_journal_entry(
        self,
        entry: JournalEntry,
        *,
        today_date: date | None = None,
    ) -> EnrichmentResult:
        """
        Post-confirm background enrichment worker:
          - Resolves relative dates ('next Friday', 'tomorrow') with exact calendar math.
          - Resolves named podcasts/books/articles ('latest Acquired podcast') via date-aware web search.
          - Strict anti-hallucination: never guesses an unnamed show/title; marks `needs_detail` instead.
        """
        today = today_date or datetime.now(timezone.utc).date()
        local_due = resolve_relative_date(entry.content, today)

        # Pure calendar task in Personal mind -> fast, deterministic, zero hallucination
        if entry.mind == Mind.PERSONAL and local_due and not re.search(
            r"\b(podcast|book|article|film|movie|restaurant|flight|hotel)\b",
            entry.content,
            re.IGNORECASE,
        ):
            return EnrichmentResult(
                status="enriched",
                enriched_summary=f"Scheduled target date: {local_due} (resolved from {today.isoformat()}).",
                enriched_url="",
                due_date=local_due,
            )

        if not self.settings.live_openrouter_enabled:
            return self._mock_enrich_entry(entry, today, local_due)

        system_prompt = (
            f"Today is {today.isoformat()} ({today.strftime('%A')}). "
            "You are a background enrichment worker for a personal voice journal called Cockpit. "
            "Your job is to enrich a confirmed voice note/task with factual, date-aware context "
            "(for example: resolving the actual latest episode & guest of a named podcast as of today, "
            "identifying the author/topic of a named book/article, or resolving a relative calendar date). "
            "CRITICAL HONESTY RULE: If the user did NOT name the specific show, book, article, or entity "
            "(for example: 'I enjoy the latest, and that's the best podcast' without naming WHICH podcast), "
            "DO NOT guess or invent a show or episode! Set 'resolved': false and 'needs_detail': true, "
            "and set 'enriched_summary' to a short polite prompt saying what detail is missing. "
            "Plain text ONLY: no markdown links [a](b), no emojis. Return strict JSON with keys: "
            "'resolved' (boolean), 'needs_detail' (boolean), 'enriched_summary' (<= 200 chars), "
            "'url' (canonical https URL or empty string), 'due_date' (YYYY-MM-DD or null)."
        )
        user_prompt = (
            f"Mind: {entry.mind.value}\n"
            f"Kind: {entry.kind}\n"
            f"Logged on: {today.isoformat()} ({today.strftime('%A')})\n"
            f"Pre-computed relative date (if any): {local_due or 'none'}\n"
            f"Wearer transcript: {entry.content}"
        )

        parsed, citation_urls = await self._call_json_llm(
            system_prompt,
            user_prompt,
            use_web=True,
            max_web_results=3,
        )
        if not parsed:
            if local_due:
                return EnrichmentResult(
                    status="enriched",
                    enriched_summary=f"Resolved date: {local_due}.",
                    due_date=local_due,
                )
            return EnrichmentResult(
                status="failed",
                enriched_summary="Background enrichment unavailable right now.",
            )

        resolved = bool(parsed.get("resolved", False))
        needs_detail = bool(parsed.get("needs_detail", False))
        summary = strip_markdown(str(parsed.get("enriched_summary", "")))
        raw_url = str(parsed.get("url", "") or "").strip()
        if not raw_url.startswith(("http://", "https://")):
            raw_url = citation_urls[0] if (resolved and citation_urls) else ""

        raw_due = parsed.get("due_date")
        due_date = local_due
        if isinstance(raw_due, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_due.strip()):
            due_date = raw_due.strip()

        if needs_detail or not resolved:
            return EnrichmentResult(
                status="needs_detail",
                enriched_summary=(
                    summary
                    or "More detail needed (e.g. name the show, title, or person) to resolve context."
                )[:220],
                enriched_url="",
                due_date=due_date,
            )

        return EnrichmentResult(
            status="enriched",
            enriched_summary=(summary or "Enriched context saved.")[:220],
            enriched_url=raw_url[:500],
            due_date=due_date,
        )

    @staticmethod
    def _mock_enrich_entry(entry: JournalEntry, today: date, local_due: str | None) -> EnrichmentResult:
        lower = entry.content.lower()
        if local_due:
            return EnrichmentResult(
                status="enriched",
                enriched_summary=f"Target date resolved to {local_due} (from {today.isoformat()}).",
                due_date=local_due,
            )
        # Check for named shows in offline tests vs unnamed vague podcast notes
        named_shows = {
            "acquired": ("Acquired — Seinfeld (Nov 2026 business deep dive)", "https://www.acquired.fm/episodes/seinfeld"),
            "dwarkesh": ("Dwarkesh Podcast — latest Nov 2026 episode", "https://www.dwarkesh.com"),
            "all-in": ("All-In Podcast — latest Nov 2026 episode", "https://www.allinpodcast.co"),
            "invest like the best": ("Invest Like the Best — latest Nov 2026 episode", "https://www.joincolossus.com"),
        }
        for key, (summary, url) in named_shows.items():
            if key in lower:
                return EnrichmentResult(
                    status="enriched",
                    enriched_summary=summary,
                    enriched_url=url,
                )
        if "podcast" in lower or "episode" in lower or "latest" in lower:
            return EnrichmentResult(
                status="needs_detail",
                enriched_summary="Podcast name not specified — mention the show name to resolve the latest episode.",
            )
        return EnrichmentResult(
            status="enriched",
            enriched_summary=f"Logged context indexed on {today.isoformat()}.",
        )

    async def _call_json_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        use_web: bool = False,
        max_web_results: int = 3,
    ) -> tuple[dict | None, list[str]]:
        url = f"{self.settings.openrouter_base_url.rstrip('/')}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "X-OpenRouter-Title": "Cockpit-G2",
        }
        payload: dict = {
            "model": self.settings.openrouter_llm_model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if use_web:
            payload["plugins"] = [{"id": "web", "max_results": max_web_results}]
            payload["reasoning"] = {"effort": "low"}

        timeout = 30.0 if use_web else 20.0
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.is_error and use_web and "reasoning" in payload:
                    # Fallback cleanly if upstream provider rejects reasoning effort param
                    fallback_payload = dict(payload)
                    fallback_payload.pop("reasoning", None)
                    resp = await client.post(url, headers=headers, json=fallback_payload)
                resp.raise_for_status()
                body = resp.json()
                message = body["choices"][0]["message"]
                content = message["content"]
                annotations = message.get("annotations") or []
                urls: list[str] = []
                for ann in annotations:
                    if isinstance(ann, dict):
                        url_cit = ann.get("url_citation")
                        if isinstance(url_cit, dict) and url_cit.get("url"):
                            urls.append(str(url_cit["url"]))
            parsed = json.loads(content)
            return (parsed if isinstance(parsed, dict) else None), urls
        except Exception:
            return None, []
