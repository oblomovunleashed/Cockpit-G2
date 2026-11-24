from __future__ import annotations

import re
import httpx

from app.config import Settings
from app.models import ActionType, DocumentChunk, JevDecision, Mind, Verdict


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
                    "instructions": "What action should Cockpit take?",
                    "criteria": {
                        "log_note": "Save a short personal/interests reflection or takeaway without calling an LLM.",
                        "log_task": "Save a short reminder or to-do task without calling an LLM.",
                        "brief_holding": "Open or review an earnings summary card for a portfolio holding.",
                        "follow_up_source": "Ask a factual follow-up question against an earnings release or call transcript.",
                        "draft_next_check": "Record a watch item / question to check at the next earnings call.",
                        "clarify": "The utterance is genuinely ambiguous and needs a quick tap clarification.",
                    },
                },
                "holding": {
                    "type": "choice",
                    "instructions": "Which portfolio ticker is this utterance about?",
                    "criteria": ticker_criteria,
                },
                "needs_llm_worker": {
                    "type": "noul",
                    "instructions": (
                        "Does this request require retrieving issuer documents and running "
                        "the Work LLM worker (true), or can it be logged immediately without an LLM (false)?"
                    ),
                },
            },
        }

        answers = await self._call_jev(payload)
        mind_ans = answers.get("mind", {})
        action_ans = answers.get("action", {})
        holding_ans = answers.get("holding", {})
        worker_ans = answers.get("needs_llm_worker", {})

        mind = Mind(mind_ans.get("choice", Mind.WORK.value))
        action = ActionType(action_ans.get("choice", ActionType.LOG_NOTE.value))
        raw_holding = holding_ans.get("choice", "none")
        holding = None if raw_holding == "none" else raw_holding
        if holding is None and mind == Mind.WORK and active_ticker:
            holding = active_ticker

        worker_prob = float(worker_ans.get("noul", 0.0))
        needs_worker = (
            action in (ActionType.BRIEF_HOLDING, ActionType.FOLLOW_UP_SOURCE)
            or worker_prob >= 0.55
        )
        confidence = float(action_ans.get("confidence", mind_ans.get("confidence", 0.9)))

        return JevDecision(
            mind=mind,
            action=action,
            holding=holding,
            needs_llm_worker=needs_worker,
            confidence=min(max(confidence, 0.0), 1.0),
            mode="openrouter",
            reasoning=f"Jev OpenRouter ({self.settings.openrouter_jev_model}), worker_p={worker_prob:.2f}",
        )

    async def select_best_chunk(
        self,
        question: str,
        candidates: list[DocumentChunk],
    ) -> tuple[DocumentChunk, Verdict, float]:
        """Use Jev to pick the single candidate passage that best answers the saved question."""
        if not candidates:
            raise ValueError("Cannot select from empty candidate list.")
        if len(candidates) == 1:
            return candidates[0], candidates[0].verdict, 1.0

        if not self.settings.live_openrouter_enabled:
            return self._mock_select_chunk(question, candidates)

        criteria = {
            f"chunk_{c.id}": f"[{c.page_ref}] {c.headline}: {c.excerpt}"
            for c in candidates
        }
        payload = {
            "model": self.settings.openrouter_jev_model,
            "state": f"Wearer's question: {question}",
            "questions": {
                "best_chunk": {
                    "type": "choice",
                    "instructions": "Select the single sourced passage that most directly answers the wearer's question.",
                    "criteria": criteria,
                },
                "verdict": {
                    "type": "choice",
                    "instructions": "How does this finding compare against prior expectations?",
                    "criteria": {
                        "HELD": "In line with or already flagged by prior guidance.",
                        "AHEAD": "Clearly beat prior guidance or expectations.",
                        "WATCH": "Requires monitoring or follow-up next quarter.",
                    },
                },
            },
        }
        answers = await self._call_jev(payload)
        chosen_key = answers.get("best_chunk", {}).get("choice", f"chunk_{candidates[0].id}")
        confidence = float(answers.get("best_chunk", {}).get("confidence", 0.9))
        verdict_str = answers.get("verdict", {}).get("choice", candidates[0].verdict.value)

        chosen = next((c for c in candidates if f"chunk_{c.id}" == chosen_key), candidates[0])
        return chosen, Verdict(verdict_str), confidence

    async def _call_jev(self, payload: dict) -> dict:
        url = f"{self.settings.openrouter_base_url.rstrip('/')}/alpha/decisions"
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "X-OpenRouter-Title": "Cockpit-G2",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data.get("answers", {})

    # ------------------------------------------------------------------
    # Deterministic local fallback (mock mode / offline tests)
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_ticker(lower: str, available_tickers: list[str], active_ticker: str | None) -> str | None:
        aliases = {
            "NVDA": ("nvda", "nvidia"),
            "MSFT": ("msft", "microsoft", "azure"),
            "TSM": ("tsm", "tsmc"),
        }
        for ticker in available_tickers:
            names = aliases.get(ticker.upper(), (ticker.lower(),))
            if any(re.search(rf"\b{re.escape(name)}\b", lower) for name in names):
                return ticker.upper()
        return active_ticker

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

        ticker = self._detect_ticker(lower, available_tickers, active_ticker)

        # Next-quarter check dictation ("At the next call, check whether those price increases are on schedule")
        if any(kw in lower for kw in ("next call", "next quarter", "check whether", "track whether", "save check", "watch item")):
            return JevDecision(
                mind=Mind.WORK,
                action=ActionType.DRAFT_NEXT_CHECK,
                holding=ticker or (available_tickers[0] if available_tickers else None),
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
                holding=ticker or (available_tickers[0] if available_tickers else None),
                needs_llm_worker=True,
                confidence=0.93,
                mode="mock",
                reasoning="Work follow-up question requires source retrieval + worker.",
            )

        # Earnings / holdings review ("Can we review NVIDIA earnings?", "What changed?")
        if any(kw in lower for kw in ("earning", "review", "what changed", "holding", "guidance", "margin", "revenue")) or ticker:
            return JevDecision(
                mind=Mind.WORK,
                action=ActionType.BRIEF_HOLDING,
                holding=ticker or (available_tickers[0] if available_tickers else None),
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
    def _mock_select_chunk(question: str, candidates: list[DocumentChunk]) -> tuple[DocumentChunk, Verdict, float]:
        q_tokens = set(re.findall(r"[a-z0-9]+", question.lower()))
        best = candidates[0]
        best_score = -1.0
        for chunk in candidates:
            haystack = f"{chunk.topic} {chunk.headline} {chunk.summary_card} {chunk.excerpt}".lower()
            c_tokens = set(re.findall(r"[a-z0-9]+", haystack))
            overlap = len(q_tokens & c_tokens)
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
        return best, best.verdict, 0.94
