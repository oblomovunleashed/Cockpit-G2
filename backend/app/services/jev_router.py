from __future__ import annotations

import re
import time
import httpx

from app.config import Settings
from app.models import ActionType, DocumentChunk, JevDecision, Mind, Verdict
from app.portfolio import PORTFOLIO_BY_TICKER, match_portfolio_position_in_text
from app.services.retrieval import extract_content_tokens


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

# Strictly for offline mock mode (live Jev understands products & companies natively)
_MOCK_PRODUCT_HINTS: dict[str, str] = {
    "signatera": "NTRA",
    "aws": "AMZN",
    "hamr": "STX",
    "mozaic": "STX",
}

_MOCK_FILLER_WORDS = {
    "a", "an", "the", "this", "that", "my", "our", "your", "please", "can", "could",
    "would", "you", "we", "me", "to", "for", "of", "on", "in", "at", "about", "before",
    "after", "next", "last", "latest", "one", "holding", "company", "stock", "earnings",
    "preview", "review", "brief", "check", "results", "quarter", "print", "call",
    "guidance", "margin", "margins", "revenue", "capex", "what", "how", "why", "did",
    "do", "does", "is", "are", "was", "were", "changed", "compared", "with", "vs",
    "versus", "against", "read", "across", "read-across", "peer", "cross", "mean",
    "means", "from", "and",
}


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

    Live mode has zero hardcoded tickers or external company lists:
      - Dynamic watchlist tickers/names come straight from SQLite / Notion.
      - When Jev classifies an utterance as `holding = untracked`, the raw utterance
        passes straight to the System-2 web LLM to resolve company + ticker, and
        `portfolio.csv` stamps the portfolio weight via a 1-line dict lookup.
    Includes a compact self-contained offline fallback when COCKPIT_MOCK_MODE=true.
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
                available_holdings=available_holdings,
                active_ticker=active_ticker,
            )

        holdings_map = available_holdings or {}
        ticker_criteria: dict[str, str] = {}
        tracked_display: list[str] = []
        for t in available_tickers:
            company = holdings_map.get(t.upper(), "")
            label = f"{t.upper()} ({company})" if company else t.upper()
            tracked_display.append(label)
            ticker_criteria[t] = f"The wearer explicitly asked about {label} as the primary target company."

        ticker_criteria["untracked"] = (
            "The wearer explicitly named a primary target company or stock ticker that is NOT in the "
            "indexed watchlist above (whether another portfolio holding or an external company)."
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
            "The wearer explicitly named a peer/source company that is NOT in the indexed watchlist above."
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
                        "earnings_preview": "Pre-earnings setup, preview, or checklist BEFORE a print for ANY company (held or not held).",
                        "brief_holding": "Post-earnings thesis review or earnings summary for ANY company (held or not held).",
                        "follow_up_source": "Ask a specific factual follow-up question about one company's metric, driver, margin, or management comment.",
                        "peer_read_across": "Cross-company read-across comparing one company's filing/results against another holding's thesis.",
                        "draft_next_check": "Explicitly dictate a watch item or question to check at the next earnings call or next quarter.",
                        "clarify": "The utterance is genuinely ambiguous and needs clarification.",
                    },
                },
                "holding": {
                    "type": "choice",
                    "instructions": (
                        "Which PRIMARY target company is this utterance about? Pick an indexed ticker if "
                        "the wearer named that indexed company or one of its flagship products/segments "
                        "(e.g. Signatera -> NTRA, Mozaic/HAMR -> STX, AWS -> AMZN). Pick 'untracked' ONLY "
                        "if the primary target is a different company outside the indexed watchlist. "
                        "Pick 'implicit_active' if no company was named."
                    ),
                    "criteria": ticker_criteria,
                },
                "peer_holding": {
                    "type": "choice",
                    "instructions": (
                        "Only for cross-company peer read-across: which PEER/SOURCE company is being read from? "
                        "Pick an indexed ticker if that peer is in the indexed watchlist, 'untracked' if the peer "
                        "is outside the indexed watchlist, or 'none' if not a peer read-across."
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
        worker_prob = _extract_noul_probability(worker_ans, 0.0)
        enrich_prob = _extract_noul_probability(enrich_ans, 0.0)

        holding: str | None = None
        peer_holding: str | None = None
        requested_company: str | None = None
        tracked_set = {t.upper() for t in available_tickers}

        if mind == Mind.WORK:
            if action == ActionType.PEER_READ_ACROSS:
                if raw_holding.upper() in tracked_set:
                    holding = raw_holding.upper()
                elif raw_holding.lower() == "implicit_active" and active_ticker:
                    holding = active_ticker.upper()

                if raw_peer.upper() in tracked_set:
                    peer_holding = raw_peer.upper()

                if raw_peer.lower() == "untracked" or raw_holding.lower() == "untracked":
                    requested_company = transcript.strip()
                if peer_holding and peer_holding == holding:
                    peer_holding = None
            else:
                if raw_holding.lower() == "untracked":
                    holding = None
                    requested_company = transcript.strip()
                elif raw_holding.upper() in tracked_set:
                    holding = raw_holding.upper()
                elif raw_holding.lower() in ("implicit_active", "none") and active_ticker:
                    holding = active_ticker.upper()

        is_adhoc_company = mind == Mind.WORK and holding is None and requested_company is not None
        no_llm_actions = (
            ActionType.LOG_NOTE,
            ActionType.LOG_TASK,
            ActionType.DRAFT_NEXT_CHECK,
        )
        llm_actions = (
            ActionType.BRIEF_HOLDING,
            ActionType.FOLLOW_UP_SOURCE,
        )
        if action in no_llm_actions:
            needs_worker = False
        elif action == ActionType.EARNINGS_PREVIEW:
            needs_worker = is_adhoc_company
        elif action == ActionType.PEER_READ_ACROSS:
            needs_worker = holding is not None and peer_holding is not None
        else:
            needs_worker = (
                is_adhoc_company
                or action in llm_actions
                or (mind == Mind.WORK and worker_prob >= 0.55)
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
    # Compact offline simulator (COCKPIT_MOCK_MODE=true only)
    # ------------------------------------------------------------------

    @staticmethod
    def _mock_tracked_terms(
        available_tickers: list[str],
        available_holdings: dict[str, str] | None,
    ) -> dict[str, tuple[str, ...]]:
        """Derives searchable terms for indexed holdings directly from SQLite/CSV metadata."""
        holdings_map = available_holdings or {}
        terms: dict[str, tuple[str, ...]] = {}
        for t in available_tickers:
            t_up = t.upper()
            candidates: list[str] = [t_up.lower()]
            company = holdings_map.get(t_up) or (
                PORTFOLIO_BY_TICKER[t_up].company if t_up in PORTFOLIO_BY_TICKER else ""
            )
            if company:
                # Strip weight suffix like "(29.2%)" and legal words to grab the company root
                clean = re.sub(r"\([^)]*\)", " ", company)
                clean = re.sub(r"\.com\b", "", clean, flags=re.IGNORECASE)
                clean = re.sub(
                    r"\b(inc|incorporated|corp|corporation|holdings|plc|ltd|limited|technology|technologies)\b\.?",
                    " ",
                    clean,
                    flags=re.IGNORECASE,
                )
                clean = re.sub(r"[^A-Za-z0-9 ]+", " ", clean)
                clean = re.sub(r"\s+", " ", clean).strip().lower()
                if clean:
                    candidates.append(clean)
                    first_word = clean.split()[0]
                    if len(first_word) >= 4:
                        candidates.append(first_word)
            for hint, hint_ticker in _MOCK_PRODUCT_HINTS.items():
                if hint_ticker == t_up:
                    candidates.append(hint)
            terms[t_up] = tuple(dict.fromkeys(candidates))
        return terms

    @classmethod
    def _mock_find_tracked_in_clause(
        cls,
        clause: str,
        tracked_terms: dict[str, tuple[str, ...]],
    ) -> list[tuple[int, str]]:
        lower = clause.lower()
        hits: list[tuple[int, str]] = []
        for ticker, words in tracked_terms.items():
            starts = [m.start() for w in words for m in re.finditer(rf"\b{re.escape(w)}\b", lower)]
            if starts:
                hits.append((min(starts), ticker))
        hits.sort(key=lambda item: item[0])
        return hits

    @classmethod
    def _mock_resolve_clause(
        cls,
        clause: str,
        available_tickers: list[str],
        tracked_terms: dict[str, tuple[str, ...]],
    ) -> tuple[str | None, str | None]:
        """Returns (tracked_ticker, untracked_company_label) for a single clause in offline mock mode."""
        tracked_hits = cls._mock_find_tracked_in_clause(clause, tracked_terms)
        if tracked_hits:
            return tracked_hits[0][1], None

        excluded = {t.upper() for t in available_tickers}
        pos = match_portfolio_position_in_text(clause, exclude_tickers=excluded)
        if pos is not None:
            return None, pos.display_name

        # Fallback: extract non-filler company phrase (e.g. 'Apple', 'Alphabet', 'TSMC')
        words = [
            w.strip("?.,!'\"")
            for w in clause.split()
            if w.strip("?.,!'\"") and w.strip("?.,!'\" ").lower() not in _MOCK_FILLER_WORDS
        ]
        if 1 <= len(words) <= 3:
            return None, " ".join(words)
        return None, None

    @classmethod
    def _mock_resolve_target(
        cls,
        transcript: str,
        available_tickers: list[str],
        tracked_terms: dict[str, tuple[str, ...]],
    ) -> tuple[str | None, str | None]:
        # Prioritize target clause before any incidental comparison ('compared with X', 'vs X')
        target_m = re.search(
            r"\b(?:preview|review|brief|check)\s+(?:on\s+|for\s+)?([^,.;?!]+?)"
            r"(?:\s+(?:compared\s+(?:with|to)|versus|vs\.?|against)\b|$)",
            transcript,
            flags=re.IGNORECASE,
        )
        if target_m:
            tracked, untracked = cls._mock_resolve_clause(
                target_m.group(1),
                available_tickers,
                tracked_terms,
            )
            if tracked or untracked:
                return tracked, untracked

        # Otherwise check tracked holdings, then CSV portfolio, then '<Company> earnings'
        tracked_hits = cls._mock_find_tracked_in_clause(transcript, tracked_terms)
        if tracked_hits:
            return tracked_hits[0][1], None

        pos = match_portfolio_position_in_text(
            transcript,
            exclude_tickers={t.upper() for t in available_tickers},
        )
        if pos is not None:
            return None, pos.display_name

        m_earn = re.search(
            r"\b([A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*)?)\s+(?:earnings|results|quarter|print)\b",
            transcript,
        )
        if m_earn:
            cleaned = " ".join(
                w for w in m_earn.group(1).split() if w.lower() not in _MOCK_FILLER_WORDS
            )
            if cleaned:
                return None, cleaned
        return None, None

    @classmethod
    def _mock_resolve_peer(
        cls,
        transcript: str,
        available_tickers: list[str],
        tracked_terms: dict[str, tuple[str, ...]],
        active_ticker: str | None,
    ) -> tuple[str | None, str | None, str | None]:
        """Returns (target_ticker, peer_ticker, unsupported_label) for offline mock peer read-across."""
        peer_clause: str | None = None
        target_clause: str | None = None

        m_from_to = re.search(r"\bfrom\s+([^,.;?!]+?)\s+(?:to|for|on)\s+([^,.;?!]+)", transcript, re.I)
        m_to_from = re.search(r"\b(?:to|for)\s+([^,.;?!]+?)\s+from\s+([^,.;?!]+)", transcript, re.I)
        m_mean = re.search(r"([^,.;?!]+?)\s+mean(?:s)?\s+for\s+([^,.;?!]+)", transcript, re.I)

        if m_from_to:
            peer_clause, target_clause = m_from_to.group(1), m_from_to.group(2)
        elif m_to_from:
            target_clause, peer_clause = m_to_from.group(1), m_to_from.group(2)
        elif m_mean:
            peer_clause, target_clause = m_mean.group(1), m_mean.group(2)

        if peer_clause is not None and target_clause is not None:
            p_tracked, p_untracked = cls._mock_resolve_clause(peer_clause, available_tickers, tracked_terms)
            t_tracked, t_untracked = cls._mock_resolve_clause(target_clause, available_tickers, tracked_terms)
            target_t = t_tracked if (t_tracked or t_untracked) else (active_ticker.upper() if active_ticker else None)
            peer_t = p_tracked if p_tracked != target_t else None
            unsupported = p_untracked or t_untracked
            return target_t, peer_t, unsupported

        # No directional preposition: use distinct tracked holdings in order
        hits = cls._mock_find_tracked_in_clause(transcript, tracked_terms)
        tickers_seen = [t for _, t in hits]
        if len(tickers_seen) >= 2:
            return tickers_seen[1], tickers_seen[0], None
        if len(tickers_seen) == 1 and active_ticker and tickers_seen[0] != active_ticker.upper():
            return active_ticker.upper(), tickers_seen[0], None
        return (tickers_seen[0] if tickers_seen else (active_ticker.upper() if active_ticker else None)), None, None

    def _mock_route_utterance(
        self,
        transcript: str,
        *,
        available_tickers: list[str],
        available_holdings: dict[str, str] | None,
        active_ticker: str | None,
    ) -> JevDecision:
        lower = transcript.lower().strip()

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

        tracked_terms = self._mock_tracked_terms(available_tickers, available_holdings)

        if any(kw in lower for kw in ("read-across", "read across", "peer read", "cross-read", "cross read", "mean for")):
            target_t, peer_t, unsupported = self._mock_resolve_peer(
                transcript, available_tickers, tracked_terms, active_ticker
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

        explicit_ticker, untracked_company = self._mock_resolve_target(
            transcript, available_tickers, tracked_terms
        )
        resolved_ticker = (
            None
            if untracked_company
            else (explicit_ticker or (active_ticker.upper() if active_ticker else None))
        )

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

        if any(
            kw in lower
            for kw in (
                "what drove", "asp", "cash flow", "moldx", "reimbursement",
                "why did", "what did management", "how did", "follow up",
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
