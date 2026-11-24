from __future__ import annotations

import re
import time
import httpx

from app.config import Settings
from app.models import ActionType, DocumentChunk, JevDecision, Mind, Verdict
from app.services.retrieval import extract_content_tokens


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


class JevRouter:
    """
    System-1 decision router backed by TypeSafe Jev on OpenRouter
    (POST https://openrouter.ai/api/alpha/decisions).

    Decides fast, typed routing on every utterance:
      - Fast path ("logged, logged"): Personal task or Interests note -> NO LLM worker.
      - Deep path ("llm worker"): Work earnings brief or source follow-up -> wakes Work worker.
    Includes a deterministic local fallback when COCKPIT_MOCK_MODE=true or no API key is set.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def route_utterance(
        self,
        transcript: str,
        *,
        available_tickers: list[str],
        active_ticker: str | None = None,
    ) -> JevDecision:
        if not self.settings.live_openrouter_enabled:
            return self._mock_route_utterance(
                transcript,
                available_tickers=available_tickers,
                active_ticker=active_ticker,
            )

        ticker_criteria = {t: f"The user is referring to {t} earnings or research." for t in available_tickers}
        ticker_criteria["none"] = "No specific holding is referenced."

        state_lines = [
            f"Utterance: {transcript}",
            f"Currently displayed holding on glasses: {active_ticker or 'none'}",
            f"Tracked portfolio tickers: {', '.join(available_tickers)}",
        ]
        payload = {
            "model": self.settings.openrouter_jev_model,
            "state": "\n".join(state_lines),
            "questions": {
                "mind": {
                    "type": "choice",
                    "instructions": "Which Cockpit mind owns this utterance?",
                    "criteria": {
                        "work": "Investing, earnings, portfolio holdings, revenue, margins, filings, or research checks.",
                        "personal": "Personal errands, gifts, reminders, errands, family, appointments, or chores.",
                        "interests": "Podcasts, books, articles, ideas, films, or general thoughts worth noting.",
                    },
                },
                "action": {
                    "type": "choice",
                    "instructions": "Which Cockpit skill or action should handle this utterance?",
                    "criteria": {
                        "log_note": "Save a short personal or interests reflection, podcast/book reaction, or general thought without calling an LLM.",
                        "log_task": "Save a short personal reminder, errand, gift, or to-do task without calling an LLM.",
                        "earnings_preview": "Pre-earnings setup or checklist BEFORE a print: show what the wearer is watching, saved question, and prior guidance baseline (e.g. 'preview NVIDIA earnings', 'what am I watching on TSMC?'). Does NOT need an LLM.",
                        "brief_holding": "Post-earnings thesis review for a single holding: open or refresh the earnings delta card (e.g. 'review NVIDIA earnings', 'what changed?'), NOT a pre-earnings preview or cross-company read-across.",
                        "follow_up_source": "Ask a specific factual follow-up question about one company's metric, driver, margin recovery, management comment, or outlook.",
                        "peer_read_across": "Cross-company read-across comparing one company's filing/results against another holding's thesis (e.g. 'read-across from TSMC to NVIDIA', 'what does Microsoft capex mean for NVIDIA?').",
                        "draft_next_check": "Explicitly dictate a watch item or question to check at the next earnings call or next quarter.",
                        "clarify": "The utterance is genuinely ambiguous and needs a quick tap clarification.",
                    },
                },
                "holding": {
                    "type": "choice",
                    "instructions": (
                        "Which primary/target portfolio ticker is this utterance about? "
                        "For peer read-across (e.g. 'from TSMC to NVIDIA'), pick the TARGET ticker (NVIDIA)."
                    ),
                    "criteria": ticker_criteria,
                },
                "peer_holding": {
                    "type": "choice",
                    "instructions": (
                        "Only for cross-company peer read-across: which PEER/SOURCE ticker is being read from "
                        "(e.g. in 'read-across from TSMC to NVIDIA', pick TSM)? Otherwise pick 'none'."
                    ),
                    "criteria": ticker_criteria,
                },
                "needs_llm_worker": {
                    "type": "noul",
                    "instructions": (
                        "Does this request require running the System-2 LLM synthesis worker (true for thesis review, "
                        "source follow-up, or peer read-across), or can deterministic code handle it with zero LLM "
                        "(false for fast logs, drafts, and pre-earnings previews)?"
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

        mind = _parse_enum(Mind, mind_ans.get("choice"), Mind.INTERESTS)
        action = _parse_enum(ActionType, action_ans.get("choice"), ActionType.LOG_NOTE)
        raw_holding = str(holding_ans.get("choice", "none")).strip()
        holding = None if raw_holding.lower() == "none" else raw_holding.upper()
        raw_peer = str(peer_ans.get("choice", "none")).strip()
        peer_holding = None if raw_peer.lower() == "none" else raw_peer.upper()

        if mind != Mind.WORK:
            holding = None
            peer_holding = None
        else:
            if holding is None and active_ticker:
                holding = active_ticker.upper()
            if action != ActionType.PEER_READ_ACROSS:
                peer_holding = None
            elif peer_holding == holding:
                # If user asked "read-across from TSMC" while viewing NVDA, disambiguate target vs peer
                if active_ticker and active_ticker.upper() != holding:
                    peer_holding = holding
                    holding = active_ticker.upper()
                else:
                    peer_holding = None

        worker_prob = _extract_noul_probability(worker_ans, 0.0)
        no_llm_actions = (
            ActionType.LOG_NOTE,
            ActionType.LOG_TASK,
            ActionType.DRAFT_NEXT_CHECK,
            ActionType.EARNINGS_PREVIEW,
        )
        llm_actions = (
            ActionType.BRIEF_HOLDING,
            ActionType.FOLLOW_UP_SOURCE,
            ActionType.PEER_READ_ACROSS,
        )
        needs_worker = (
            action in llm_actions
            or (mind == Mind.WORK and worker_prob >= 0.55 and action not in no_llm_actions)
        )
        confidence = float(action_ans.get("confidence", mind_ans.get("confidence", 0.9)))
        cost_part = f", ${float(cost):.6f}" if isinstance(cost, (int, float)) else ""

        return JevDecision(
            mind=mind,
            action=action,
            holding=holding,
            peer_holding=peer_holding,
            needs_llm_worker=needs_worker,
            confidence=min(max(confidence, 0.0), 1.0),
            mode="openrouter",
            reasoning=f"Jev ({resolved_model}, {latency_ms}ms, worker_p={worker_prob:.2f}{cost_part})",
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
    # Deterministic local fallback (mock mode / offline tests)
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_explicit_tickers(lower: str, available_tickers: list[str]) -> list[str]:
        """Returns explicitly mentioned tickers ordered by first appearance in the utterance."""
        aliases = {
            "NVDA": ("nvda", "nvidia"),
            "MSFT": ("msft", "microsoft", "azure"),
            "TSM": ("tsm", "tsmc"),
        }
        hits: list[tuple[int, str]] = []
        for ticker in available_tickers:
            names = aliases.get(ticker.upper(), (ticker.lower(),))
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
                confidence=0.95,
                mode="mock",
                reasoning="Fast-path interests note detected (no LLM worker).",
            )

        explicit_tickers = self._detect_explicit_tickers(lower, available_tickers)
        explicit_ticker = explicit_tickers[0] if explicit_tickers else None
        resolved_ticker = explicit_ticker or active_ticker or (available_tickers[0] if available_tickers else None)

        # Peer read-across ("read-across from TSMC to NVIDIA", "what does Microsoft mean for NVIDIA")
        if any(kw in lower for kw in ("read-across", "read across", "peer read", "cross-read", "cross read", "mean for")):
            if len(explicit_tickers) >= 2:
                peer_t, target_t = explicit_tickers[0], explicit_tickers[1]
            elif len(explicit_tickers) == 1 and active_ticker and explicit_tickers[0] != active_ticker.upper():
                peer_t, target_t = explicit_tickers[0], active_ticker.upper()
            else:
                peer_t = explicit_ticker
                target_t = active_ticker.upper() if active_ticker else (available_tickers[0] if available_tickers else None)
            return JevDecision(
                mind=Mind.WORK,
                action=ActionType.PEER_READ_ACROSS,
                holding=target_t,
                peer_holding=peer_t,
                needs_llm_worker=True,
                confidence=0.95,
                mode="mock",
                reasoning=f"Work peer_read_across skill ({peer_t} -> {target_t}) routes to System-2 synthesis.",
            )

        # Pre-earnings preview (Tier 0 deterministic — NO LLM worker!)
        if any(kw in lower for kw in ("preview", "before earnings", "pre-call", "pre call", "what am i watching", "baseline")):
            return JevDecision(
                mind=Mind.WORK,
                action=ActionType.EARNINGS_PREVIEW,
                holding=resolved_ticker,
                needs_llm_worker=False,
                confidence=0.95,
                mode="mock",
                reasoning="Deterministic earnings_preview skill (zero LLM).",
            )

        # Next-quarter check dictation ("At the next call, check whether those price increases are on schedule")
        if any(kw in lower for kw in ("next call", "next quarter", "check whether", "track whether", "save check", "watch item")):
            return JevDecision(
                mind=Mind.WORK,
                action=ActionType.DRAFT_NEXT_CHECK,
                holding=resolved_ticker,
                needs_llm_worker=False,
                confidence=0.94,
                mode="mock",
                reasoning="Work next-quarter check staged for 1-tap journal confirmation.",
            )

        # Deep source follow-up ("What did management expect to bring margins back up?")
        if any(kw in lower for kw in ("bring margins", "margins back", "price increase", "why did", "what did management", "how did", "follow up")):
            return JevDecision(
                mind=Mind.WORK,
                action=ActionType.FOLLOW_UP_SOURCE,
                holding=resolved_ticker,
                needs_llm_worker=True,
                confidence=0.93,
                mode="mock",
                reasoning="Work follow-up question requires source retrieval + worker.",
            )

        # Earnings / holdings review ("Can we review NVIDIA earnings?", "What changed?")
        # Only explicit ticker mentions or work keywords trigger Work — display context alone does not.
        if any(kw in lower for kw in ("earning", "review", "what changed", "holding", "guidance", "margin", "revenue")) or explicit_ticker is not None:
            return JevDecision(
                mind=Mind.WORK,
                action=ActionType.BRIEF_HOLDING,
                holding=resolved_ticker,
                needs_llm_worker=True,
                confidence=0.95,
                mode="mock",
                reasoning="Work earnings review routes to LLM worker.",
            )

        return JevDecision(
            mind=Mind.INTERESTS,
            action=ActionType.LOG_NOTE,
            holding=None,
            needs_llm_worker=False,
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

            # Emphasize topic-specific discriminators for the NVDA demo
            bonus = 0.0
            if "revenue" in q_tokens and "revenue" in chunk.topic.lower():
                bonus += 4.0
            if ("back" in q_tokens or "recover" in q_tokens or "price" in q_tokens) and "recovery" in chunk.topic.lower():
                bonus += 4.0
            if ("trough" in q_tokens or "deeper" in q_tokens) and "trough" in chunk.topic.lower():
                bonus += 4.0
            score = overlap + bonus
            if score > best_score:
                best_score = score
                best = chunk

        if best is None or best_score <= 0.0:
            return None
        return best, best.verdict, 0.94
