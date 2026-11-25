from __future__ import annotations

from dataclasses import dataclass
import re
import time
import httpx

from app.config import Settings
from app.models import ActionType, DocumentChunk, JevDecision, Mind, Verdict
from app.portfolio import find_portfolio_mentions_in_text
from app.services.retrieval import extract_content_tokens


TRACKED_ALIASES: dict[str, tuple[str, ...]] = {
    "NTRA": ("ntra", "natera", "signatera"),
    "STX": ("stx", "seagate", "seagate technology", "mozaic", "hamr"),
    "AMZN": ("amzn", "amazon", "aws"),
    "NVDA": ("nvda", "nvidia"),
    "MSFT": ("msft", "microsoft", "azure"),
    "TSM": ("tsm", "tsmc", "taiwan semiconductor"),
}

# Common non-watchlist public/private companies so explicit mentions never silently hijack active_ticker
KNOWN_UNTRACKED_COMPANIES: dict[str, str] = {
    "alphabet": "Alphabet (GOOGL)",
    "google": "Alphabet (GOOGL)",
    "googl": "Alphabet (GOOGL)",
    "goog": "Alphabet (GOOGL)",
    "nvidia": "NVIDIA (NVDA)",
    "nvda": "NVIDIA (NVDA)",
    "microsoft": "Microsoft (MSFT)",
    "msft": "Microsoft (MSFT)",
    "apple": "Apple (AAPL)",
    "aapl": "Apple (AAPL)",
    "meta": "Meta Platforms (META)",
    "facebook": "Meta Platforms (META)",
    "amd": "AMD",
    "broadcom": "Broadcom (AVGO)",
    "avgo": "Broadcom (AVGO)",
    "netflix": "Netflix (NFLX)",
    "nflx": "Netflix (NFLX)",
    "asml": "ASML",
    "intel": "Intel (INTC)",
    "intc": "Intel (INTC)",
    "oracle": "Oracle (ORCL)",
    "orcl": "Oracle (ORCL)",
    "salesforce": "Salesforce (CRM)",
    "uber": "Uber (UBER)",
    "spotify": "Spotify (SPOT)",
    "novo nordisk": "Novo Nordisk (NVO)",
    "jpmorgan": "JPMorgan Chase (JPM)",
    "berkshire": "Berkshire Hathaway (BRK.B)",
    "costco": "Costco (COST)",
    "walmart": "Walmart (WMT)",
    "disney": "Disney (DIS)",
    "sony": "Sony (SONY)",
    "samsung": "Samsung Electronics",
    "alibaba": "Alibaba (BABA)",
    "arm": "Arm Holdings (ARM)",
    "micron": "Micron (MU)",
    "qualcomm": "Qualcomm (QCOM)",
    "qcom": "Qualcomm (QCOM)",
    "snowflake": "Snowflake (SNOW)",
    "datadog": "Datadog (DDOG)",
    "cloudflare": "Cloudflare (NET)",
    "shopify": "Shopify (SHOP)",
    "stripe": "Stripe",
}

_STOP_COMPANY_WORDS = {
    "a", "an", "the", "this", "that", "these", "those", "my", "our", "your", "their",
    "please", "can", "could", "would", "you", "we", "me", "us", "to", "for", "of",
    "on", "in", "at", "about", "before", "after", "next", "last", "latest", "upcoming",
    "recent", "current", "one", "holding", "company", "stock", "name", "earnings",
    "preview", "review", "brief", "check", "update", "results", "quarter", "print",
    "call", "guidance", "margin", "margins", "revenue", "capex", "numbers", "report",
    "what", "how", "why", "did", "do", "does", "is", "are", "was", "were", "changed",
    "compared", "with", "vs", "versus", "against", "read", "across", "read-across",
    "peer", "cross", "cross-read", "mean", "means", "impact", "impacts", "implication",
    "implications", "from", "and",
}


@dataclass(frozen=True)
class _CompanyMention:
    start: int
    end: int
    tracked_ticker: str | None
    display_name: str
    weight_pct: float = 0.0

_RELATIVE_DATE_RE = re.compile(
    r"\b(tomorrow|tonight|today|yesterday|this weekend|next week|next month|"
    r"(?:this|next)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"in\s+\d+\s+(?:day|days|week|weeks))\b",
    re.IGNORECASE,
)

_ENRICH_MEDIA_RE = re.compile(
    r"\b(podcast|episode|latest|book|article|essay|newsletter|interview|documentary|movie|film|paper|talk)\b",
    re.IGNORECASE,
)


def _extract_noul_probability(answer: dict | None, default: float) -> float:
    """OpenRouter Decisions API returns `probability` for noul questions (legacy: `noul`)."""
    if not isinstance(answer, dict):
        return default
    raw = answer.get("probability")
    if raw is None:
        raw = answer.get("noul")
    if raw is None:
        return default
    try:
        return min(max(float(raw), 0.0), 1.0)
    except (TypeError, ValueError):
        return default


def _parse_enum(enum_cls, raw: object, default):
    if raw is None:
        return default
    val = str(raw).strip()
    for member in enum_cls:
        if member.value.lower() == val.lower():
            return member
    return default


def heuristic_needs_enrichment(transcript: str, mind: Mind) -> bool:
    """Deterministic fallback trigger for post-confirm background enrichment."""
    lower = transcript.lower().strip()
    if mind == Mind.PERSONAL:
        return bool(_RELATIVE_DATE_RE.search(lower))
    if mind == Mind.INTERESTS:
        return bool(_ENRICH_MEDIA_RE.search(lower))
    return False


class JevRouter:
    """
    System-1 decision router backed by TypeSafe Jev on OpenRouter
    (POST https://openrouter.ai/api/alpha/decisions).

    Decides fast, typed routing on every utterance:
      - Fast path ("logged, logged"): Personal task or Interests note -> NO blocking LLM on glasses
        (optional async post-confirm enrichment when Jev flags `needs_enrichment`).
      - Deep path ("llm worker"): Work thesis review, follow-up, peer read-across, or web-grounded
        earnings preview/review for any other portfolio holding or external company.
    Includes a deterministic local fallback when COCKPIT_MOCK_MODE=true or no API key is set.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def route_utterance(
        self,
        transcript: str,
        *,
        available_tickers: list[str],
        available_holdings: dict[str, str] | None = None,
        active_ticker: str | None = None,
    ) -> JevDecision:
        if not self.settings.live_openrouter_enabled:
            return self._mock_route_utterance(
                transcript,
                available_tickers=available_tickers,
                active_ticker=active_ticker,
            )

        holdings_map = available_holdings or {}
        ticker_criteria: dict[str, str] = {}
        tracked_display: list[str] = []
        for t in available_tickers:
            company = holdings_map.get(t.upper(), "")
            label = f"{t.upper()} ({company})" if company else t.upper()
            tracked_display.append(label)
            ticker_criteria[t] = f"The wearer explicitly referred to {label} earnings or research."

        ticker_criteria["untracked"] = (
            "The wearer explicitly named a company or stock ticker that is NOT in the indexed watchlist "
            "(for example Insmed/INSM, Fox/FOXA, Sea Limited/SE, Alphabet/GOOGL, Apple/AAPL, TSMC, etc.)."
        )
        ticker_criteria["implicit_active"] = (
            "The wearer did NOT name any company and is referring implicitly to the currently displayed holding "
            "(for example 'what changed?', 'preview this one', 'what drove ASP and cash flow?')."
        )
        ticker_criteria["none"] = "Not about any company or stock."

        peer_criteria = {
            t: f"The peer/source company being read from is {holdings_map.get(t.upper(), t.upper())}."
            for t in available_tickers
        }
        peer_criteria["untracked"] = (
            "The wearer explicitly named a peer/source company that is NOT in the indexed watchlist "
            "(for example TSMC, Western Digital, Apple, Insmed)."
        )
        peer_criteria["none"] = "No peer company or not a peer read-across."

        state_lines = [
            f"Utterance: {transcript}",
            f"Currently displayed holding on glasses: {active_ticker or 'none'}",
            f"Indexed core watchlist holdings: {', '.join(tracked_display) if tracked_display else 'none'}",
        ]
        payload = {
            "model": self.settings.openrouter_jev_model,
            "state": "\n".join(state_lines),
            "questions": {
                "mind": {
                    "type": "choice",
                    "instructions": "Which Cockpit mind owns this utterance?",
                    "criteria": {
                        "work": "Investing, earnings, companies, stocks, revenue, margins, filings, or research checks.",
                        "personal": "Personal errands, gifts, reminders, family, appointments, or chores.",
                        "interests": "Podcasts, books, articles, ideas, films, or general thoughts worth noting.",
                    },
                },
                "action": {
                    "type": "choice",
                    "instructions": "Which Cockpit skill or action should handle this utterance?",
                    "criteria": {
                        "log_note": "Save a short personal or interests reflection, podcast/book reaction, or general thought without blocking on an LLM.",
                        "log_task": "Save a short personal reminder, errand, gift, or to-do task without blocking on an LLM.",
                        "earnings_preview": "Pre-earnings setup, preview, or checklist BEFORE a print for ANY company (held or not held), e.g. 'preview Natera earnings', 'preview Insmed earnings', 'preview Alphabet earnings'.",
                        "brief_holding": "Post-earnings thesis review or earnings summary for ANY company (held or not held), e.g. 'review Natera earnings', 'review Seagate earnings', 'what changed?'.",
                        "follow_up_source": "Ask a specific factual follow-up question about one company's metric, driver, ASP, cash flow, margin, or management comment.",
                        "peer_read_across": "Cross-company read-across comparing one company's filing/results against another holding's thesis (e.g. 'what does Amazon capex mean for Seagate?', 'read-across from Amazon to Seagate').",
                        "draft_next_check": "Explicitly dictate a watch item or question to check at the next earnings call or next quarter.",
                        "clarify": "The utterance is genuinely ambiguous and needs clarification.",
                    },
                },
                "holding": {
                    "type": "choice",
                    "instructions": (
                        "Which target company is this utterance about? Pick an indexed ticker ONLY if the wearer "
                        "explicitly named that indexed company. Pick 'untracked' if the wearer named a DIFFERENT "
                        "company (e.g. Insmed, Fox, Sea Limited, Alphabet, Apple). Pick 'implicit_active' ONLY if no company was named."
                    ),
                    "criteria": ticker_criteria,
                },
                "peer_holding": {
                    "type": "choice",
                    "instructions": (
                        "Only for cross-company peer read-across: which PEER/SOURCE ticker is being read from "
                        "(e.g. in 'what does Amazon capex mean for Seagate?', pick AMZN)? Otherwise pick 'none'."
                    ),
                    "criteria": peer_criteria,
                },
                "needs_llm_worker": {
                    "type": "noul",
                    "instructions": (
                        "Does this request need the System-2 LLM worker on screen right now (true for thesis review, "
                        "source follow-up, peer read-across, or preview/review of a non-indexed company; false for "
                        "fast logs, dictated checks, and indexed-holding pre-earnings previews)?"
                    ),
                },
                "needs_enrichment": {
                    "type": "noul",
                    "instructions": (
                        "If this is a note or task being logged, does it reference a podcast, episode, book, "
                        "article, film, or relative date ('latest', 'next Friday', 'tomorrow') that a background "
                        "worker should resolve after saving? False for plain standalone reminders like 'buy milk'."
                    ),
                },
            },
        }

        response_data, latency_ms = await self._call_jev(payload)
        answers = response_data.get("answers", {})
        resolved_model = str(response_data.get("model") or self.settings.openrouter_jev_model)
        cost = response_data.get("usage", {}).get("cost")

        mind_ans = answers.get("mind", {})
        action_ans = answers.get("action", {})
        holding_ans = answers.get("holding", {})
        peer_ans = answers.get("peer_holding", {})
        worker_ans = answers.get("needs_llm_worker", {})
        enrich_ans = answers.get("needs_enrichment", {})

        mind = _parse_enum(Mind, mind_ans.get("choice"), Mind.INTERESTS)
        action = _parse_enum(ActionType, action_ans.get("choice"), ActionType.LOG_NOTE)
        raw_holding = str(holding_ans.get("choice", "none")).strip()
        raw_peer = str(peer_ans.get("choice", "none")).strip()
        peer_holding = None if raw_peer.lower() == "none" else raw_peer.upper()

        worker_prob = _extract_noul_probability(worker_ans, 0.0)
        enrich_prob = _extract_noul_probability(enrich_ans, 0.0)

        holding: str | None = None
        requested_company: str | None = None

        if mind != Mind.WORK:
            holding = None
            peer_holding = None
            requested_company = None
        elif action == ActionType.PEER_READ_ACROSS:
            parsed_target, parsed_peer, unsupported = self._resolve_peer_and_target(
                transcript,
                available_tickers=available_tickers,
                active_ticker=active_ticker,
            )
            if unsupported is not None or raw_peer.lower() == "untracked":
                holding = parsed_target
                peer_holding = parsed_peer if raw_peer.lower() != "untracked" else None
                requested_company = unsupported or self.extract_requested_company(transcript, available_tickers)
            else:
                if parsed_target is not None or parsed_peer is not None:
                    holding = parsed_target
                    peer_holding = parsed_peer
                else:
                    if raw_holding.lower() in ("implicit_active", "none", "untracked"):
                        holding = active_ticker.upper() if active_ticker else None
                    else:
                        holding = raw_holding.upper()
                    if raw_peer.lower() in ("none", "untracked"):
                        peer_holding = None
                    if peer_holding and peer_holding == holding:
                        peer_holding = None
        else:
            peer_holding = None
            target_mention = self._resolve_target_mention(transcript, available_tickers)

            if target_mention is not None:
                if target_mention.tracked_ticker is not None:
                    holding = target_mention.tracked_ticker
                    requested_company = None
                else:
                    holding = None
                    requested_company = target_mention.display_name
            elif raw_holding.lower() == "untracked":
                holding = None
                requested_company = self._fallback_company_phrase(transcript)
            elif raw_holding.lower() in ("implicit_active", "none"):
                if active_ticker:
                    holding = active_ticker.upper()
            else:
                holding = raw_holding.upper()

        is_adhoc_company = mind == Mind.WORK and holding is None and requested_company is not None
        no_llm_actions = (
            ActionType.LOG_NOTE,
            ActionType.LOG_TASK,
            ActionType.DRAFT_NEXT_CHECK,
        )
        llm_actions = (
            ActionType.BRIEF_HOLDING,
            ActionType.FOLLOW_UP_SOURCE,
            ActionType.PEER_READ_ACROSS,
        )
        if action == ActionType.EARNINGS_PREVIEW:
            needs_worker = is_adhoc_company
        else:
            needs_worker = (
                is_adhoc_company
                or action in llm_actions
                or (mind == Mind.WORK and worker_prob >= 0.55 and action not in no_llm_actions)
            )

        is_fast_log = action in (ActionType.LOG_NOTE, ActionType.LOG_TASK)
        needs_enrichment = is_fast_log and (
            enrich_prob >= 0.55 or heuristic_needs_enrichment(transcript, mind)
        )

        confidence = float(action_ans.get("confidence", mind_ans.get("confidence", 0.9)))
        cost_part = f", ${float(cost):.6f}" if isinstance(cost, (int, float)) else ""
        enrich_part = f", enrich_p={enrich_prob:.2f}" if is_fast_log else ""

        return JevDecision(
            mind=mind,
            action=action,
            holding=holding,
            peer_holding=peer_holding,
            requested_company=requested_company,
            needs_llm_worker=needs_worker,
            needs_enrichment=needs_enrichment,
            confidence=min(max(confidence, 0.0), 1.0),
            mode="openrouter",
            reasoning=f"Jev ({resolved_model}, {latency_ms}ms, worker_p={worker_prob:.2f}{enrich_part}{cost_part})",
        )

    async def select_best_chunk(
        self,
        question: str,
        candidates: list[DocumentChunk],
    ) -> tuple[DocumentChunk, Verdict, float] | None:
        """
        Use Jev to pick the single candidate passage that directly answers the saved question.
        Returns None when no candidate passage supports the question.
        """
        if not candidates:
            return None

        if not self.settings.live_openrouter_enabled:
            return self._mock_select_chunk(question, candidates)

        passages_block = "\n".join(
            f"- chunk_{c.id} [{c.page_ref}] {c.headline}: {c.excerpt}"
            for c in candidates
        )
        criteria = {
            f"chunk_{c.id}": f"Passage chunk_{c.id} ({c.headline}) directly answers the wearer's question."
            for c in candidates
        }
        criteria["none"] = "None of the candidate passages actually answer the wearer's question."

        payload = {
            "model": self.settings.openrouter_jev_model,
            "state": (
                f"Wearer's question: {question}\n\n"
                f"Candidate source passages:\n{passages_block}"
            ),
            "questions": {
                "is_answered": {
                    "type": "noul",
                    "instructions": (
                        "Does at least one of the candidate source passages listed in state "
                        "directly answer the wearer's question?"
                    ),
                },
                "best_chunk": {
                    "type": "choice",
                    "instructions": (
                        "Select the single chunk ID from state that most directly answers the "
                        "wearer's question, or 'none' if no passage answers it."
                    ),
                    "criteria": criteria,
                },
                "verdict": {
                    "type": "choice",
                    "instructions": "For the matching passage in state, how does the result compare to prior expectations?",
                    "criteria": {
                        "HELD": "In line with or already flagged by prior guidance.",
                        "AHEAD": "Clearly beat prior guidance or expectations.",
                        "WATCH": "Explains a driver or risk that requires monitoring next quarter.",
                    },
                },
            },
        }
        response_data, _latency_ms = await self._call_jev(payload)
        answers = response_data.get("answers", {})
        is_answered_p = _extract_noul_probability(answers.get("is_answered"), 1.0)
        chosen_key = str(answers.get("best_chunk", {}).get("choice", "none")).strip()
        confidence = float(answers.get("best_chunk", {}).get("confidence", 0.0))

        if chosen_key.lower() == "none" or is_answered_p < 0.5:
            return None

        chosen = next((c for c in candidates if f"chunk_{c.id}" == chosen_key), None)
        if chosen is None:
            return None
        verdict = _parse_enum(Verdict, answers.get("verdict", {}).get("choice"), chosen.verdict)
        return chosen, verdict, confidence

    async def _call_jev(self, payload: dict) -> tuple[dict, int]:
        url = f"{self.settings.openrouter_base_url.rstrip('/')}/alpha/decisions"
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "X-OpenRouter-Title": "Cockpit-G2",
        }
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.is_error:
                raise RuntimeError(
                    f"OpenRouter Jev API error {resp.status_code}: {resp.text[:300]}"
                )
            data = resp.json()
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return data, elapsed_ms

    # ------------------------------------------------------------------
    # Entity detection helpers & deterministic local fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_explicit_tickers(lower: str, available_tickers: list[str]) -> list[str]:
        """Returns explicitly mentioned indexed watchlist tickers ordered by first appearance."""
        hits: list[tuple[int, str]] = []
        for ticker in available_tickers:
            names = TRACKED_ALIASES.get(ticker.upper(), (ticker.lower(),))
            positions = [
                m.start()
                for name in names
                for m in re.finditer(rf"\b{re.escape(name)}\b", lower)
            ]
            if positions:
                hits.append((min(positions), ticker.upper()))
        hits.sort(key=lambda item: item[0])
        return [ticker for _, ticker in hits]

    @classmethod
    def _find_company_mentions(
        cls,
        transcript: str,
        available_tickers: list[str],
    ) -> list[_CompanyMention]:
        """
        Finds all non-overlapping company mentions across the indexed watchlist,
        the full 61-position portfolio, common external companies, and earnings-clause
        regex patterns, ordered by text position with longest/most-specific span winning.
        """
        lower = transcript.lower()
        excluded = {t.upper() for t in available_tickers}
        tracked_names: set[str] = set()
        raw: list[_CompanyMention] = []

        # 1. Indexed watchlist tickers and aliases
        for ticker in available_tickers:
            t_up = ticker.upper()
            names = set(TRACKED_ALIASES.get(t_up, ())) | {t_up.lower()}
            tracked_names.update(names)
            for name in names:
                for m in re.finditer(rf"\b{re.escape(name)}\b", lower):
                    raw.append(
                        _CompanyMention(
                            start=m.start(),
                            end=m.end(),
                            tracked_ticker=t_up,
                            display_name=t_up,
                            weight_pct=100.0,
                        )
                    )

        # 2. Remaining holdings in the 61-position portfolio (longest-span-first)
        for start, end, pos in find_portfolio_mentions_in_text(transcript, exclude_tickers=excluded):
            raw.append(
                _CompanyMention(
                    start=start,
                    end=end,
                    tracked_ticker=None,
                    display_name=pos.display_name,
                    weight_pct=pos.weight_pct,
                )
            )

        # 3. Known external public/private companies
        for alias, display in KNOWN_UNTRACKED_COMPANIES.items():
            if alias in tracked_names:
                continue
            for m in re.finditer(rf"\b{re.escape(alias)}\b", lower):
                raw.append(
                    _CompanyMention(
                        start=m.start(),
                        end=m.end(),
                        tracked_ticker=None,
                        display_name=display,
                        weight_pct=0.0,
                    )
                )

        # Deduplicate known dictionary spans first (earliest start, then longest span)
        raw.sort(
            key=lambda item: (
                item.start,
                -(item.end - item.start),
                0 if item.tracked_ticker is not None else 1,
                -item.weight_pct,
            )
        )
        accepted: list[_CompanyMention] = []
        for mention in raw:
            if any(mention.start < acc.end and mention.end > acc.start for acc in accepted):
                continue
            accepted.append(mention)

        # 4. Pattern extraction for unlisted companies (e.g. 'Preview Rivian earnings')
        patterns = (
            r"\b(?:preview|review|brief|check)\s+(?:on\s+|for\s+)?([A-Za-z0-9&.\- ]{2,36}?)\s+(?:before\s+)?(?:earnings|results|quarter|print)\b",
            r"\b([A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*)?)\s+(?:earnings|results|quarter|print)\b",
            r"\b(?:preview|review)\s+earnings\s+(?:for|of|on)\s+([A-Za-z0-9&.\- ]{2,36})",
        )
        for pat in patterns:
            flags = re.IGNORECASE if pat.startswith(r"\b(?:preview") else 0
            for m in re.finditer(pat, transcript, flags=flags):
                span_start, span_end = m.start(1), m.end(1)
                if any(span_start < acc.end and span_end > acc.start for acc in accepted):
                    continue
                candidate = re.split(
                    r"\s+(?:compared\s+(?:with|to)|versus|vs\.?|against)\b",
                    m.group(1),
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )[0].strip(" ?.,!'\"")
                words = [w for w in candidate.split() if w.lower() not in _STOP_COMPANY_WORDS]
                if not words:
                    continue
                cleaned = " ".join(words)
                if cleaned.lower() in tracked_names or any(w.lower() in tracked_names for w in words):
                    continue
                accepted.append(
                    _CompanyMention(
                        start=span_start,
                        end=span_end,
                        tracked_ticker=None,
                        display_name=cleaned,
                        weight_pct=0.0,
                    )
                )

        accepted.sort(key=lambda item: item.start)
        return accepted

    @classmethod
    def _resolve_target_mention(
        cls,
        transcript: str,
        available_tickers: list[str],
    ) -> _CompanyMention | None:
        """
        Resolves the primary requested company before incidental mentions
        (e.g. 'Preview Apple earnings compared with Insmed' -> Apple, not Insmed).
        """
        mentions = cls._find_company_mentions(transcript, available_tickers)
        if not mentions:
            return None

        target_match = re.search(
            r"\b(?:preview|review|brief|check)\s+(?:on\s+|for\s+)?([^,.;?!]+?)(?:\s+(?:compared\s+(?:with|to)|versus|vs\.?|against)\b|$)",
            transcript,
            flags=re.IGNORECASE,
        )
        if target_match:
            c_start, c_end = target_match.start(1), target_match.end(1)
            in_target = [m for m in mentions if m.start >= c_start and m.start < c_end]
            if in_target:
                return in_target[0]

        return mentions[0]

    @classmethod
    def _extract_clause_entity(
        cls,
        clause: str,
        available_tickers: list[str],
    ) -> _CompanyMention | None:
        mentions = cls._find_company_mentions(clause, available_tickers)
        if mentions:
            return mentions[0]
        words = [
            w.strip("?.,!'\"")
            for w in clause.split()
            if w.strip("?.,!'\"") and w.strip("?.,!'\" ").lower() not in _STOP_COMPANY_WORDS
        ]
        if 1 <= len(words) <= 3:
            cleaned = " ".join(words)
            return _CompanyMention(
                start=0,
                end=len(cleaned),
                tracked_ticker=None,
                display_name=cleaned,
                weight_pct=0.0,
            )
        return None

    @classmethod
    def _resolve_peer_and_target(
        cls,
        transcript: str,
        *,
        available_tickers: list[str],
        active_ticker: str | None,
    ) -> tuple[str | None, str | None, str | None]:
        """
        Resolves (target_ticker, peer_ticker, unsupported_company) for peer read-across queries.
        Preserves explicit peer/target roles ('from TSMC to Seagate' -> target=STX, peer=None,
        unsupported=TSM) so an unsupported peer never swaps the target into the peer slot.
        """
        peer_mention: _CompanyMention | None = None
        target_mention: _CompanyMention | None = None
        matched_direction = False

        # 1. 'from <peer> to/for/on <target>'
        m_from_to = re.search(
            r"\bfrom\s+([^,.;?!]+?)\s+(?:to|for|on)\s+([^,.;?!]+)",
            transcript,
            flags=re.IGNORECASE,
        )
        # 2. 'to/for <target> from <peer>'
        m_to_from = re.search(
            r"\b(?:to|for)\s+([^,.;?!]+?)\s+from\s+([^,.;?!]+)",
            transcript,
            flags=re.IGNORECASE,
        )
        # 3. '<peer> mean(s) for <target>'
        m_mean_for = re.search(
            r"([^,.;?!]+?)\s+mean(?:s)?\s+for\s+([^,.;?!]+)",
            transcript,
            flags=re.IGNORECASE,
        )

        if m_from_to:
            matched_direction = True
            peer_mention = cls._extract_clause_entity(m_from_to.group(1), available_tickers)
            target_mention = cls._extract_clause_entity(m_from_to.group(2), available_tickers)
        elif m_to_from:
            matched_direction = True
            target_mention = cls._extract_clause_entity(m_to_from.group(1), available_tickers)
            peer_mention = cls._extract_clause_entity(m_to_from.group(2), available_tickers)
        elif m_mean_for:
            matched_direction = True
            peer_mention = cls._extract_clause_entity(m_mean_for.group(1), available_tickers)
            target_mention = cls._extract_clause_entity(m_mean_for.group(2), available_tickers)

        if not matched_direction:
            raw_mentions = cls._find_company_mentions(transcript, available_tickers)
            mentions: list[_CompanyMention] = []
            seen_entities: set[str] = set()
            for m in raw_mentions:
                entity_key = m.tracked_ticker or m.display_name
                if entity_key in seen_entities:
                    continue
                seen_entities.add(entity_key)
                mentions.append(m)

            if len(mentions) >= 2:
                peer_mention, target_mention = mentions[0], mentions[1]
            elif len(mentions) == 1:
                only = mentions[0]
                if active_ticker and (only.tracked_ticker is None or only.tracked_ticker != active_ticker.upper()):
                    peer_mention = only
                    target_mention = None
                else:
                    target_mention = only
                    peer_mention = None

        if target_mention is not None:
            target_ticker = target_mention.tracked_ticker
        else:
            target_ticker = active_ticker.upper() if active_ticker else None

        peer_ticker = peer_mention.tracked_ticker if peer_mention is not None else None

        unsupported: str | None = None
        if peer_mention is not None and peer_mention.tracked_ticker is None:
            unsupported = peer_mention.display_name
        elif target_mention is not None and target_mention.tracked_ticker is None:
            unsupported = target_mention.display_name

        if peer_ticker and target_ticker and peer_ticker == target_ticker:
            peer_ticker = None

        return target_ticker, peer_ticker, unsupported

    @classmethod
    def extract_requested_company(cls, transcript: str, available_tickers: list[str]) -> str | None:
        """
        Detects an explicitly named primary company outside the indexed watchlist,
        resolving target-clause priority and specificity before incidental mentions.
        """
        target = cls._resolve_target_mention(transcript, available_tickers)
        if target is not None and target.tracked_ticker is None:
            return target.display_name
        for mention in cls._find_company_mentions(transcript, available_tickers):
            if mention.tracked_ticker is None:
                return mention.display_name
        return None

    @staticmethod
    def _fallback_company_phrase(transcript: str) -> str:
        words = [
            w.strip("?.,!'\"")
            for w in transcript.split()
            if w.strip("?.,!'\"") and w.strip("?.,!'\" ").lower() not in _STOP_COMPANY_WORDS
        ]
        return " ".join(words[:4]) or transcript.strip()[:40]

    @classmethod
    def _detect_explicit_ticker(cls, lower: str, available_tickers: list[str]) -> str | None:
        found = cls._detect_explicit_tickers(lower, available_tickers)
        return found[0] if found else None

    def _mock_route_utterance(
        self,
        transcript: str,
        *,
        available_tickers: list[str],
        active_ticker: str | None,
    ) -> JevDecision:
        lower = transcript.lower().strip()

        # Check fast-path Personal tasks first ("remember to buy a birthday present")
        if any(kw in lower for kw in ("remember to", "remind me", "buy ", "birthday", "present", "groceries", "call mom")):
            return JevDecision(
                mind=Mind.PERSONAL,
                action=ActionType.LOG_TASK,
                holding=None,
                needs_llm_worker=False,
                needs_enrichment=heuristic_needs_enrichment(transcript, Mind.PERSONAL),
                confidence=0.96,
                mode="mock",
                reasoning="Fast-path personal task detected (no LLM worker).",
            )

        # Check fast-path Interests notes ("this podcast was great")
        if any(kw in lower for kw in ("podcast", "book ", "article", "film", "movie", "episode", "great listen")):
            return JevDecision(
                mind=Mind.INTERESTS,
                action=ActionType.LOG_NOTE,
                holding=None,
                needs_llm_worker=False,
                needs_enrichment=heuristic_needs_enrichment(transcript, Mind.INTERESTS),
                confidence=0.95,
                mode="mock",
                reasoning="Fast-path interests note detected (no LLM worker).",
            )

        # Peer read-across ("what does Amazon capex mean for Seagate?", "read-across from Amazon to Seagate")
        if any(kw in lower for kw in ("read-across", "read across", "peer read", "cross-read", "cross read", "mean for")):
            target_t, peer_t, unsupported = self._resolve_peer_and_target(
                transcript,
                available_tickers=available_tickers,
                active_ticker=active_ticker,
            )
            can_synthesize = target_t is not None and peer_t is not None
            return JevDecision(
                mind=Mind.WORK,
                action=ActionType.PEER_READ_ACROSS,
                holding=target_t,
                peer_holding=peer_t,
                requested_company=unsupported,
                needs_llm_worker=can_synthesize,
                confidence=0.95,
                mode="mock",
                reasoning=(
                    f"Work peer_read_across skill ({peer_t} -> {target_t}) routes to System-2 synthesis."
                    if can_synthesize
                    else f"Peer read-across needs clarification (unsupported={unsupported or 'missing'}, target={target_t})."
                ),
            )

        target_mention = self._resolve_target_mention(transcript, available_tickers)
        if target_mention is not None:
            if target_mention.tracked_ticker is not None:
                explicit_ticker: str | None = target_mention.tracked_ticker
                untracked_company: str | None = None
            else:
                explicit_ticker = None
                untracked_company = target_mention.display_name
        else:
            explicit_ticker = None
            untracked_company = None

        resolved_ticker = (
            None
            if untracked_company
            else (explicit_ticker or (active_ticker.upper() if active_ticker else None))
        )

        # Pre-earnings preview (Tier 0 zero-LLM for indexed holdings; web worker for all other names)
        if any(kw in lower for kw in ("preview", "before earnings", "pre-call", "pre call", "what am i watching", "baseline")):
            is_untracked = untracked_company is not None
            return JevDecision(
                mind=Mind.WORK,
                action=ActionType.EARNINGS_PREVIEW,
                holding=resolved_ticker,
                requested_company=untracked_company,
                needs_llm_worker=is_untracked,
                confidence=0.95,
                mode="mock",
                reasoning=(
                    f"Ad-hoc web earnings_preview for {untracked_company}."
                    if is_untracked
                    else "Deterministic earnings_preview skill (zero LLM)."
                ),
            )

        # Next-quarter check dictation
        if any(kw in lower for kw in ("next call", "next quarter", "check whether", "track whether", "save check", "watch item")):
            return JevDecision(
                mind=Mind.WORK,
                action=ActionType.DRAFT_NEXT_CHECK,
                holding=resolved_ticker,
                requested_company=untracked_company,
                needs_llm_worker=False,
                confidence=0.94,
                mode="mock",
                reasoning="Work next-quarter check staged for 1-tap journal confirmation.",
            )

        # Deep source follow-up ("What drove Signatera ASP and cash flow?", "What did management say...")
        if any(
            kw in lower
            for kw in (
                "what drove",
                "asp",
                "cash flow",
                "moldx",
                "reimbursement",
                "why did",
                "what did management",
                "how did",
                "follow up",
            )
        ):
            return JevDecision(
                mind=Mind.WORK,
                action=ActionType.FOLLOW_UP_SOURCE,
                holding=resolved_ticker,
                requested_company=untracked_company,
                needs_llm_worker=True,
                confidence=0.93,
                mode="mock",
                reasoning="Work follow-up question requires source retrieval + worker.",
            )

        # Earnings / holdings review
        if (
            any(kw in lower for kw in ("earning", "review", "what changed", "holding", "guidance", "margin", "revenue"))
            or explicit_ticker is not None
            or untracked_company is not None
        ):
            return JevDecision(
                mind=Mind.WORK,
                action=ActionType.BRIEF_HOLDING,
                holding=resolved_ticker,
                requested_company=untracked_company,
                needs_llm_worker=True,
                confidence=0.95,
                mode="mock",
                reasoning=(
                    f"Ad-hoc web earnings review for {untracked_company}."
                    if untracked_company
                    else "Work earnings review routes to LLM worker."
                ),
            )

        return JevDecision(
            mind=Mind.INTERESTS,
            action=ActionType.LOG_NOTE,
            holding=None,
            needs_llm_worker=False,
            needs_enrichment=heuristic_needs_enrichment(transcript, Mind.INTERESTS),
            confidence=0.72,
            mode="mock",
            reasoning="Default fast-path note capture.",
        )

    @staticmethod
    def _mock_select_chunk(
        question: str,
        candidates: list[DocumentChunk],
    ) -> tuple[DocumentChunk, Verdict, float] | None:
        ticker = candidates[0].ticker if candidates else None
        q_tokens = set(extract_content_tokens(question, ticker=ticker))
        if not q_tokens:
            return None

        best: DocumentChunk | None = None
        best_score = 0.0
        for chunk in candidates:
            haystack = f"{chunk.topic} {chunk.headline} {chunk.summary_card} {chunk.excerpt}".lower()
            c_tokens = set(extract_content_tokens(haystack, ticker=chunk.ticker))
            overlap = len(q_tokens & c_tokens)
            if overlap == 0:
                continue

            bonus = 0.0
            topic_l = chunk.topic.lower()
            if ("volume" in q_tokens or "oncology" in q_tokens or "guidance" in q_tokens) and "volume" in topic_l:
                bonus += 4.0
            if ("asp" in q_tokens or "cash" in q_tokens or "moldx" in q_tokens or "reimbursement" in q_tokens) and "asp" in topic_l:
                bonus += 4.0
            if ("true" in q_tokens or "ups" in q_tokens or "underlying" in q_tokens or "cogs" in q_tokens) and "true-ups" in topic_l:
                bonus += 4.0
            if ("hamr" in q_tokens or "mozaic" in q_tokens or "azure" in q_tokens or "exabytes" in q_tokens) and "mozaic" in topic_l:
                bonus += 4.0
            if ("capex" in q_tokens or "storage" in q_tokens or "infrastructure" in q_tokens) and "capex" in topic_l:
                bonus += 4.0
            if ("aws" in q_tokens or "growth" in q_tokens or "operating" in q_tokens) and "operating" in topic_l:
                bonus += 3.0
            score = overlap + bonus
            if score > best_score:
                best_score = score
                best = chunk

        if best is None or best_score <= 0.0:
            return None
        return best, best.verdict, 0.94
