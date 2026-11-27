from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings
from app.db import CockpitDB
from app.models import (
    ActionType,
    ConfirmWriteRequest,
    ConfirmWriteResponse,
    DeckStateResponse,
    DraftCheck,
    HoldingRecord,
    HudCard,
    JevDecision,
    JournalEntry,
    Mind,
    PendingWrite,
    StageCheckRequest,
    UndoWriteResponse,
    UpdatePendingWriteRequest,
    UpdateQuestionRequest,
    UtteranceRequest,
    UtteranceResponse,
)
from app.portfolio import (
    PORTFOLIO_BY_TICKER,
    PORTFOLIO_POSITIONS,
    format_weight_badge,
    lookup_portfolio_position,
)
from app.seed import seed_if_empty
from app.services.jev_router import JevRouter, heuristic_needs_enrichment
from app.services.job_runner import AsyncJobQueue, BackgroundJobRunner
from app.services.llm_worker import LLMWorker
from app.services.notion_sync import NotionSync
from app.services.openrouter_stt import OpenRouterSTT
from app.services.retrieval import DocumentRetriever, extract_content_tokens
from app.services.work_skills import render_earnings_preview


NO_MATCH_FINDING = "No matching passage in indexed filings for this question."


def _clip(text: str, limit: int) -> str:
    clean = " ".join(text.strip().split())
    if len(clean) <= limit:
        return clean
    return clean[: max(1, limit - 3)].rstrip() + "..."


def _default_kind_for_mind(mind: Mind) -> str:
    if mind == Mind.PERSONAL:
        return "task"
    if mind == Mind.WORK:
        return "check"
    return "note"


def format_holding_hud(holding: HoldingRecord, *, index: int = 1, total: int = 3) -> HudCard:
    verdict_part = f" · {holding.verdict.value}" if holding.verdict is not None else ""
    header = _clip(f"WORK · {holding.ticker} {index}/{total}{verdict_part}", 60)

    if holding.source_citation:
        raw_body = f"{holding.latest_finding}\n[{holding.source_citation}]"
    else:
        raw_body = holding.latest_finding or NO_MATCH_FINDING
    body = _clip(raw_body, 240)

    if holding.suggested_next_check:
        footer = "Hold: speak · Tap: review check · Swipe: next"
    else:
        footer = "Hold: ask or dictate check · Swipe: next"
    return HudCard(header=header, body=body, footer=footer)


def format_confirm_hud(pending: PendingWrite, *, low_confidence: bool = False) -> HudCard:
    mind_label = pending.mind.value.upper()
    kind_label = pending.kind.upper()
    ticker_part = f" · {pending.ticker}" if pending.ticker else ""
    q_mark = "?" if low_confidence else ""
    header = _clip(f"{mind_label}{ticker_part} · CONFIRM {kind_label}{q_mark}", 60)
    body = _clip(pending.content, 240)
    footer = "Tap: save · Swipe: switch mind · 2x: cancel"
    return HudCard(header=header, body=body, footer=footer)


def format_saved_hud(
    entry: JournalEntry,
    *,
    synced: bool,
    enrichment_queued: bool,
) -> HudCard:
    dest = "Notion + Journal" if synced else "Journal"
    if enrichment_queued:
        header = _clip(f"{entry.mind.value.upper()} · SAVED · ENRICHING", 60)
        body = _clip(f"OK: {entry.content}\nSaved to {dest}. Enriching context...", 240)
    else:
        header = _clip(f"{entry.mind.value.upper()} · SAVED", 60)
        body = _clip(f"OK: {entry.content}\nSaved to {dest}.", 240)
    footer = "Tap: done · Swipe: undo · Hold: speak"
    return HudCard(header=header, body=body, footer=footer)


@dataclass
class AppRuntime:
    settings: Settings
    db: CockpitDB
    stt: OpenRouterSTT
    jev: JevRouter
    retriever: DocumentRetriever
    worker: LLMWorker
    notion: NotionSync
    jobs: BackgroundJobRunner
    enrichment_jobs: AsyncJobQueue[int]


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings()
    db = CockpitDB(cfg.cockpit_db_path)
    seed_if_empty(db)

    stt = OpenRouterSTT(cfg)
    jev = JevRouter(cfg)
    retriever = DocumentRetriever(db)
    worker = LLMWorker(cfg)
    notion = NotionSync(cfg)

    async def refresh_holding_from_notion(ticker: str) -> HoldingRecord | None:
        holding = db.get_holding(ticker)
        if not holding or not notion.holdings_enabled:
            return holding
        snapshots = await notion.pull_holdings()
        snap = snapshots.get(holding.ticker.upper())
        if not snap:
            return holding

        updates: dict[str, object] = {}
        if snap.notion_page_id and snap.notion_page_id != holding.notion_page_id:
            updates["notion_page_id"] = snap.notion_page_id
        if snap.saved_question and snap.saved_question != holding.saved_question:
            updates["saved_question"] = snap.saved_question
        if snap.next_check != holding.next_check:
            updates["next_check"] = snap.next_check
        if updates:
            return db.upsert_holding(holding.model_copy(update=updates))
        return holding

    async def recompute_holding(
        ticker: str,
        *,
        pull_from_notion: bool = True,
        push_saved_question: bool = False,
    ) -> HoldingRecord:
        holding = db.get_holding(ticker)
        if not holding:
            raise HTTPException(status_code=404, detail=f"Unknown holding: {ticker}")

        if notion.holdings_enabled:
            if pull_from_notion:
                refreshed = await refresh_holding_from_notion(holding.ticker)
                if refreshed is not None:
                    holding = refreshed
            elif not holding.notion_page_id:
                snapshots = await notion.pull_holdings()
                snap = snapshots.get(holding.ticker.upper())
                if snap and snap.notion_page_id:
                    holding = (
                        db.update_holding_notion_page_id(holding.ticker, snap.notion_page_id)
                        or holding.model_copy(update={"notion_page_id": snap.notion_page_id})
                    )

        candidates = retriever.retrieve(holding.ticker, holding.saved_question, limit=4)
        match = await jev.select_best_chunk(holding.saved_question, candidates) if candidates else None

        if match is None:
            updated = db.update_holding_finding(
                holding.ticker,
                verdict=None,
                latest_finding=NO_MATCH_FINDING,
                source_citation="",
                suggested_next_check="",
            )
        else:
            chunk, verdict, _ = match
            finding, suggested_check = await worker.synthesize_card(
                ticker=holding.ticker,
                question=holding.saved_question,
                chunk=chunk,
                verdict=verdict,
            )
            updated = db.update_holding_finding(
                holding.ticker,
                verdict=verdict,
                latest_finding=finding,
                source_citation=chunk.page_ref,
                suggested_next_check=suggested_check,
            )

        assert updated is not None
        if notion.holdings_enabled:
            page_id = await notion.sync_holding_update(
                updated,
                include_finding=True,
                include_saved_question=push_saved_question,
                include_next_check=False,
            )
            if page_id and page_id != updated.notion_page_id:
                updated = (
                    db.update_holding_notion_page_id(updated.ticker, page_id)
                    or updated.model_copy(update={"notion_page_id": page_id})
                )
        return updated

    async def _job_handler(ticker: str) -> None:
        await recompute_holding(ticker, pull_from_notion=True, push_saved_question=False)

    async def _enrich_entry_handler(entry_id: int) -> None:
        entry = db.get_journal_entry(entry_id)
        if entry is None:
            return
        result = await worker.enrich_journal_entry(entry)
        # Re-read in case the user triggered Undo while enrichment was in flight
        latest = db.get_journal_entry(entry_id)
        if latest is None:
            return
        updated = db.update_journal_enrichment(
            entry_id,
            status=result.status,
            enriched_summary=result.enriched_summary,
            enriched_url=result.enriched_url,
            due_date=result.due_date,
        )
        if updated and updated.notion_page_id and notion.enabled:
            await notion.update_journal_enrichment(
                updated.notion_page_id,
                status=result.status,
                enriched_summary=result.enriched_summary,
                enriched_url=result.enriched_url,
                due_date=result.due_date,
            )

    jobs = BackgroundJobRunner(_job_handler)
    enrichment_jobs: AsyncJobQueue[int] = AsyncJobQueue(
        _enrich_entry_handler,
        name="cockpit-enrichment-runner",
    )
    runtime = AppRuntime(
        settings=cfg,
        db=db,
        stt=stt,
        jev=jev,
        retriever=retriever,
        worker=worker,
        notion=notion,
        jobs=jobs,
        enrichment_jobs=enrichment_jobs,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        runtime.jobs.start()
        runtime.enrichment_jobs.start()
        try:
            yield
        finally:
            await runtime.enrichment_jobs.stop()
            await runtime.jobs.stop()

    app = FastAPI(
        title="Cockpit-G2 Backend",
        version="0.2.0",
        description="System-1 Jev router + Work LLM worker + Post-log enrichment + Notion sync for Even G2.",
        lifespan=lifespan,
    )
    app.state.runtime = runtime

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def handle_utterance(transcript: str, active_ticker: str | None) -> UtteranceResponse:
        holdings = db.list_holdings()
        tickers = [h.ticker for h in holdings]
        holdings_map = {h.ticker.upper(): h.company for h in holdings}
        decision: JevDecision = await jev.route_utterance(
            transcript,
            available_tickers=tickers,
            available_holdings=holdings_map,
            active_ticker=active_ticker,
        )

        # --------------------------------------------------------------
        # FAST PATH ("logged, logged") — ZERO BLOCKING LLM ON GLASSES
        # (Optional async enrichment wakes AFTER wearer taps Confirm)
        # --------------------------------------------------------------
        if (
            decision.action in (ActionType.LOG_NOTE, ActionType.LOG_TASK, ActionType.CLARIFY)
            and not decision.needs_llm_worker
        ):
            kind = "task" if decision.action == ActionType.LOG_TASK else _default_kind_for_mind(decision.mind)
            pending = PendingWrite(
                id=uuid.uuid4().hex[:12],
                mind=decision.mind,
                kind=kind,  # type: ignore[arg-type]
                content=transcript.strip(),
                ticker=None,
                source_citation=None,
                updates_next_check=False,
                needs_enrichment=decision.needs_enrichment,
            )
            db.save_pending_write(pending)
            low_conf = decision.confidence < 0.72 or decision.action == ActionType.CLARIFY
            return UtteranceResponse(
                transcript=transcript,
                decision=decision,
                hud=format_confirm_hud(pending, low_confidence=low_conf),
                holding=None,
                pending_write=pending,
                suggested_check=None,
            )

        # --------------------------------------------------------------
        # WORK: STAGE SPOKEN NEXT-QUARTER CHECK FOR 1-TAP CONFIRM
        # --------------------------------------------------------------
        if decision.action == ActionType.DRAFT_NEXT_CHECK:
            if decision.holding is None and decision.requested_company:
                pos = lookup_portfolio_position(decision.requested_company)
                ticker: str | None = pos.ticker if pos else None
                holding = None
            else:
                raw_ticker = decision.holding or active_ticker
                ticker = raw_ticker.upper() if raw_ticker else None
                holding = db.get_holding(ticker) if ticker else None
            pending = PendingWrite(
                id=uuid.uuid4().hex[:12],
                mind=Mind.WORK,
                kind="check",
                content=transcript.strip(),
                ticker=ticker,
                source_citation=holding.source_citation if holding else None,
                updates_next_check=holding is not None,
                needs_enrichment=False,
            )
            db.save_pending_write(pending)
            return UtteranceResponse(
                transcript=transcript,
                decision=decision,
                hud=format_confirm_hud(pending, low_confidence=decision.confidence < 0.72),
                holding=holding,
                pending_write=pending,
                suggested_check=None,
            )

        # --------------------------------------------------------------
        # GENERAL LIVE QUESTION / RESEARCH (WORK, PERSONAL, INTERESTS)
        # --------------------------------------------------------------
        if decision.action == ActionType.ANSWER_QUESTION:
            answer, source_label, saveable_note, _verified_url = await worker.answer_general_question(
                question=transcript,
                mind=decision.mind,
            )
            mind_label = decision.mind.value.upper()
            hud = HudCard(
                header=_clip(f"{mind_label} · ANSWER · WEB", 60),
                body=_clip(f"{answer}\n[Web: {source_label}]", 240),
                footer="Tap: save note · Hold: ask · 2x: home",
            )
            draft = DraftCheck(
                content=saveable_note,
                ticker="",
                source_citation=f"Web: {source_label}",
                mind=decision.mind,
                kind="note",
            )
            return UtteranceResponse(
                transcript=transcript,
                decision=decision,
                hud=hud,
                holding=None,
                pending_write=None,
                suggested_check=draft,
            )

        # --------------------------------------------------------------
        # WORK SKILL 1: EARNINGS PREVIEW (HELD OR NOT HELD!)
        # - Held watchlist ticker -> Tier 0 deterministic (ZERO LLM)
        # - Any non-watchlist company -> Live web-grounded preview
        # --------------------------------------------------------------
        if decision.action == ActionType.EARNINGS_PREVIEW:
            if decision.holding is None and decision.requested_company:
                pre_weight = None if cfg.live_openrouter_enabled else format_weight_badge(decision.requested_company)
                resolved_ticker, finding, source_label, draft = await worker.synthesize_adhoc_earnings(
                    company_query=decision.requested_company,
                    mode="preview",
                    utterance=transcript,
                    portfolio_weight_label=pre_weight,
                )
                pos = PORTFOLIO_BY_TICKER.get(resolved_ticker.upper())
                wt_label = pos.weight_label if pos else None
                ticker_badge = f"{resolved_ticker} ({wt_label})" if wt_label else resolved_ticker
                hud = HudCard(
                    header=_clip(f"PREVIEW · {ticker_badge} · WEB", 60),
                    body=_clip(f"{finding}\n[Web: {source_label}]", 240),
                    footer="Tap: save watchpoint · Swipe: watchlist",
                )
                return UtteranceResponse(
                    transcript=transcript,
                    decision=decision,
                    hud=hud,
                    holding=None,
                    pending_write=None,
                    suggested_check=draft,
                )

            raw_ticker = decision.holding or active_ticker
            if not raw_ticker:
                hud = HudCard(
                    header="WORK · WHICH COMPANY?",
                    body="Name any company to preview (e.g. Natera, Seagate, Insmed, Alphabet) or swipe into your watchlist.",
                    footer="Hold: ask with company · Swipe: watchlist",
                )
                return UtteranceResponse(
                    transcript=transcript,
                    decision=decision,
                    hud=hud,
                    holding=None,
                    pending_write=None,
                    suggested_check=None,
                )

            ticker = raw_ticker.upper()
            holding = await refresh_holding_from_notion(ticker) or db.get_holding(ticker)
            if holding is None:
                raise HTTPException(status_code=404, detail=f"Holding not found: {ticker}")
            return UtteranceResponse(
                transcript=transcript,
                decision=decision,
                hud=render_earnings_preview(holding),
                holding=holding,
                pending_write=None,
                suggested_check=None,
            )

        # --------------------------------------------------------------
        # WORK SKILL 2 (TIER 1 + TIER 2): PEER READ-ACROSS (e.g. AMZN -> STX)
        # --------------------------------------------------------------
        if decision.action == ActionType.PEER_READ_ACROSS:
            raw_target = decision.holding
            if not raw_target and decision.requested_company is None:
                raw_target = active_ticker

            if not raw_target:
                indexed_list = ", ".join(tickers)
                unsupported_label = decision.requested_company or "That company"
                if "(" in unsupported_label and unsupported_label.endswith(")"):
                    unsupported_label = unsupported_label.split("(")[-1].rstrip(")")
                elif len(unsupported_label) > 24 or len(unsupported_label.split()) > 3:
                    unsupported_label = "That company"
                hud = HudCard(
                    header="WORK · PEER READ · CLARIFY",
                    body=_clip(
                        f"{unsupported_label} isn't in indexed peer filings ({indexed_list}). "
                        f"Ask for an indexed pair or preview it on web.",
                        240,
                    ),
                    footer="Hold: e.g. 'What does Amazon capex mean for Seagate?'",
                )
                return UtteranceResponse(
                    transcript=transcript,
                    decision=decision,
                    hud=hud,
                    holding=None,
                    pending_write=None,
                    suggested_check=None,
                )

            target_ticker = raw_target.upper()
            target_holding = await refresh_holding_from_notion(target_ticker) or db.get_holding(target_ticker)
            if target_holding is None:
                raise HTTPException(status_code=404, detail=f"Holding not found: {target_ticker}")

            peer_ticker = (decision.peer_holding or "").upper()
            if not peer_ticker or peer_ticker == target_ticker or db.get_holding(peer_ticker) is None:
                peers = ", ".join(t for t in tickers if t != target_ticker)
                if decision.requested_company:
                    peer_label = decision.requested_company
                    if "(" in peer_label and peer_label.endswith(")"):
                        peer_label = peer_label.split("(")[-1].rstrip(")")
                    elif len(peer_label) > 24 or len(peer_label.split()) > 3:
                        peer_label = "That peer"
                    body = (
                        f"{peer_label} isn't in indexed peer filings for {target_ticker} "
                        f"(indexed peers: {peers}). Ask for an indexed peer or preview it on web."
                    )
                else:
                    body = f"Specify a peer to read across to {target_ticker} (available: {peers})."
                hud = HudCard(
                    header=_clip(f"WORK · PEER READ · {target_ticker}", 60),
                    body=_clip(body, 240),
                    footer="Hold: e.g. 'What does Amazon capex mean for Seagate?'",
                )
                return UtteranceResponse(
                    transcript=transcript,
                    decision=decision,
                    hud=hud,
                    holding=target_holding,
                    pending_write=None,
                    suggested_check=None,
                )

            peer_filler = {
                "read", "across", "peer", "cross", "mean", "means", "impact",
                "implication", "implications", "result", "results", "quarter",
                "earning", "earnings", "holding", "thesis", "tell", "check",
            }
            specific_tokens = [
                tok
                for tok in extract_content_tokens(transcript, ticker=peer_ticker)
                if tok not in peer_filler and tok != target_ticker.lower()
            ]

            if specific_tokens:
                peer_query = " ".join(specific_tokens)
                peer_candidates = retriever.retrieve(peer_ticker, peer_query, limit=4)
            else:
                peer_query = target_holding.saved_question
                peer_candidates = db.list_chunks_for_ticker(peer_ticker)[:4]

            if not peer_candidates:
                hud = HudCard(
                    header=_clip(f"WORK · {peer_ticker} -> {target_ticker} · NO MATCH", 60),
                    body=_clip(NO_MATCH_FINDING, 240),
                    footer="Hold: ask again or dictate check",
                )
                return UtteranceResponse(
                    transcript=transcript,
                    decision=decision,
                    hud=hud,
                    holding=target_holding,
                    pending_write=None,
                    suggested_check=None,
                )

            if cfg.live_openrouter_enabled:
                peer_question = (
                    f"Which {peer_ticker} passage provides the strongest read-across for "
                    f"{target_holding.ticker}'s thesis ({target_holding.saved_question}) "
                    f"given the wearer's request: {transcript}"
                )
                match = await jev.select_best_chunk(peer_question, peer_candidates)
                if match is None:
                    hud = HudCard(
                        header=_clip(f"WORK · {peer_ticker} -> {target_ticker} · NO MATCH", 60),
                        body=_clip(NO_MATCH_FINDING, 240),
                        footer="Hold: ask again or dictate check",
                    )
                    return UtteranceResponse(
                        transcript=transcript,
                        decision=decision,
                        hud=hud,
                        holding=target_holding,
                        pending_write=None,
                        suggested_check=None,
                    )
                peer_chunk = match[0]
            else:
                if specific_tokens:
                    ranked = await jev.select_best_chunk(peer_query, peer_candidates)
                    if ranked is None:
                        hud = HudCard(
                            header=_clip(f"WORK · {peer_ticker} -> {target_ticker} · NO MATCH", 60),
                            body=_clip(NO_MATCH_FINDING, 240),
                            footer="Hold: ask again or dictate check",
                        )
                        return UtteranceResponse(
                            transcript=transcript,
                            decision=decision,
                            hud=hud,
                            holding=target_holding,
                            pending_write=None,
                            suggested_check=None,
                        )
                    peer_chunk = ranked[0]
                else:
                    peer_chunk = peer_candidates[-1]

            read_across_text, draft = await worker.synthesize_peer_read_across(
                target=target_holding,
                peer_ticker=peer_ticker,
                peer_chunk=peer_chunk,
                utterance=transcript,
            )
            hud = HudCard(
                header=_clip(f"PEER · {peer_ticker} → {target_ticker}", 60),
                body=_clip(f"{read_across_text}\n[{peer_chunk.page_ref}]", 240),
                footer="Tap: review check · Hold: dictate",
            )
            return UtteranceResponse(
                transcript=transcript,
                decision=decision,
                hud=hud,
                holding=target_holding,
                pending_write=None,
                suggested_check=draft,
            )

        # --------------------------------------------------------------
        # WORK SKILL 3: AD-HOC WEB EARNINGS REVIEW / FOLLOW-UP (PORTFOLIO OR EXTERNAL)
        # --------------------------------------------------------------
        if decision.holding is None and decision.requested_company:
            mode = "followup" if decision.action == ActionType.FOLLOW_UP_SOURCE else "review"
            pre_weight = None if cfg.live_openrouter_enabled else format_weight_badge(decision.requested_company)
            resolved_ticker, finding, source_label, draft = await worker.synthesize_adhoc_earnings(
                company_query=decision.requested_company,
                mode=mode,
                utterance=transcript,
                portfolio_weight_label=pre_weight,
            )
            pos = PORTFOLIO_BY_TICKER.get(resolved_ticker.upper())
            wt_label = pos.weight_label if pos else None
            ticker_badge = f"{resolved_ticker} ({wt_label})" if wt_label else resolved_ticker
            hud = HudCard(
                header=_clip(f"WORK · {ticker_badge} · WEB", 60),
                body=_clip(f"{finding}\n[Web: {source_label}]", 240),
                footer="Tap: save watchpoint · Swipe: watchlist",
            )
            return UtteranceResponse(
                transcript=transcript,
                decision=decision,
                hud=hud,
                holding=None,
                pending_write=None,
                suggested_check=draft,
            )

        # --------------------------------------------------------------
        # DEEP PATH ("LLM WORKER") — TRACKED WORK EARNINGS BRIEF OR FOLLOW-UP
        # --------------------------------------------------------------
        raw_ticker = decision.holding or active_ticker
        if not raw_ticker:
            hud = HudCard(
                header="WORK · WHICH COMPANY?",
                body="Name any company (e.g. Natera, Seagate, Insmed, Alphabet) or swipe into your watchlist.",
                footer="Hold: ask with company · Swipe: watchlist",
            )
            return UtteranceResponse(
                transcript=transcript,
                decision=decision,
                hud=hud,
                holding=None,
                pending_write=None,
                suggested_check=None,
            )

        ticker = raw_ticker.upper()
        holding = db.get_holding(ticker)
        if holding is None:
            raise HTTPException(status_code=404, detail=f"Holding not found: {ticker}")

        if decision.action == ActionType.FOLLOW_UP_SOURCE:
            candidates = retriever.retrieve(ticker, transcript, limit=4)
            match = await jev.select_best_chunk(transcript, candidates) if candidates else None
            if match is None:
                hud = HudCard(
                    header=_clip(f"WORK · {ticker} · NO MATCH", 60),
                    body=_clip(NO_MATCH_FINDING, 240),
                    footer="Hold: ask again or dictate check",
                )
                return UtteranceResponse(
                    transcript=transcript,
                    decision=decision,
                    hud=hud,
                    holding=holding,
                    pending_write=None,
                    suggested_check=None,
                )

            chunk, verdict, _ = match
            finding, suggested_check = await worker.synthesize_card(
                ticker=ticker,
                question=transcript,
                chunk=chunk,
                verdict=verdict,
            )
            draft = DraftCheck(
                content=suggested_check.strip(),
                ticker=ticker,
                source_citation=chunk.page_ref,
            )
            hud = HudCard(
                header=_clip(f"WORK · {ticker} · SOURCE", 60),
                body=_clip(f"{finding}\n[{chunk.page_ref}]", 240),
                footer="Tap: review next check · Hold: dictate",
            )
            return UtteranceResponse(
                transcript=transcript,
                decision=decision,
                hud=hud,
                holding=holding,
                pending_write=None,
                suggested_check=draft,
            )

        # Default Work path: pull any newer Notion question first, then surface sourced card
        updated = await recompute_holding(ticker, pull_from_notion=True, push_saved_question=False)
        idx = next((i + 1 for i, h in enumerate(holdings) if h.ticker == updated.ticker), 1)
        return UtteranceResponse(
            transcript=transcript,
            decision=decision,
            hud=format_holding_hud(updated, index=idx, total=max(len(holdings), 1)),
            holding=updated,
            pending_write=None,
            suggested_check=None,
        )

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "mock_mode": not cfg.live_openrouter_enabled,
            "notion_enabled": notion.enabled,
        }

    @app.get("/api/state", response_model=DeckStateResponse)
    async def get_state() -> DeckStateResponse:
        return DeckStateResponse(
            holdings=db.list_holdings(),
            recent_journal=db.list_journal_entries(limit=15),
            pending_jobs=jobs.pending_count + enrichment_jobs.pending_count,
            mock_mode=not cfg.live_openrouter_enabled,
            notion_enabled=notion.enabled,
        )

    @app.get("/api/holdings", response_model=list[HoldingRecord])
    async def list_holdings() -> list[HoldingRecord]:
        return db.list_holdings()

    @app.get("/api/portfolio")
    async def list_full_portfolio() -> dict[str, object]:
        return {
            "total_positions": len(PORTFOLIO_POSITIONS),
            "total_weight_pct": round(sum(p.weight_pct for p in PORTFOLIO_POSITIONS), 2),
            "positions": [
                {
                    "ticker": p.ticker,
                    "company": p.company,
                    "weight_pct": p.weight_pct,
                }
                for p in PORTFOLIO_POSITIONS
            ],
        }

    @app.patch("/api/holdings/{ticker}/question")
    async def update_holding_question(ticker: str, body: UpdateQuestionRequest) -> dict[str, object]:
        updated = db.update_holding_question(ticker, body.saved_question.strip())
        if not updated:
            raise HTTPException(status_code=404, detail=f"Unknown holding: {ticker}")
        recomputed = await recompute_holding(
            ticker,
            pull_from_notion=False,
            push_saved_question=True,
        )
        holdings = db.list_holdings()
        idx = next((i + 1 for i, h in enumerate(holdings) if h.ticker == recomputed.ticker), 1)
        return {
            "holding": recomputed,
            "hud": format_holding_hud(recomputed, index=idx, total=max(len(holdings), 1)),
        }

    @app.post("/api/stage-check")
    async def stage_explicit_check(body: StageCheckRequest) -> dict[str, object]:
        """
        Stages an explicit proposed check or note (for a tracked holding, an ad-hoc company
        like GOOGL, or a general Personal/Interests web answer) and returns the CONFIRM card
        showing exact wording.
        """
        clean_ticker = body.ticker.strip().upper()[:12] or None
        holding = db.get_holding(clean_ticker) if clean_ticker else None
        updates_next_check = (
            body.mind == Mind.WORK
            and body.kind == "check"
            and holding is not None
        )
        pending = PendingWrite(
            id=uuid.uuid4().hex[:12],
            mind=body.mind,
            kind=body.kind,
            content=body.content.strip(),
            ticker=(holding.ticker if holding else clean_ticker) if body.mind == Mind.WORK else None,
            source_citation=body.source_citation or (holding.source_citation if holding else None),
            updates_next_check=updates_next_check,
            needs_enrichment=False,
        )
        db.save_pending_write(pending)
        return {"pending_write": pending, "hud": format_confirm_hud(pending)}

    @app.post("/api/holdings/{ticker}/stage-suggested-check")
    async def stage_suggested_check(ticker: str) -> dict[str, object]:
        """Stages the holding's AI-drafted next check for explicit on-glasses review."""
        holding = db.get_holding(ticker)
        if not holding:
            raise HTTPException(status_code=404, detail=f"Unknown holding: {ticker}")
        draft = holding.suggested_next_check.strip()
        if not draft:
            raise HTTPException(
                status_code=409,
                detail=f"No suggested check available for {holding.ticker} until a matching passage is found.",
            )
        pending = PendingWrite(
            id=uuid.uuid4().hex[:12],
            mind=Mind.WORK,
            kind="check",
            content=draft,
            ticker=holding.ticker,
            source_citation=holding.source_citation or None,
            updates_next_check=True,
            needs_enrichment=False,
        )
        db.save_pending_write(pending)
        return {"pending_write": pending, "hud": format_confirm_hud(pending)}

    @app.patch("/api/pending/{pending_id}")
    async def reclassify_pending_write(
        pending_id: str,
        body: UpdatePendingWriteRequest,
    ) -> dict[str, object]:
        """
        Reclassifies a staged pending write in place (e.g. fixing a Jev misclassification
        between Interests Note, Personal Task, and Work Check) without re-speaking.
        """
        existing = db.get_pending_write(pending_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Pending write not found: {pending_id}")

        new_kind = body.kind or _default_kind_for_mind(body.mind)
        new_ticker: str | None = None
        updates_next_check = False
        if body.mind == Mind.WORK:
            raw_t = (body.ticker or existing.ticker or "").strip().upper()
            if raw_t:
                new_ticker = raw_t
                updates_next_check = new_kind == "check" and (db.get_holding(raw_t) is not None)

        updated = db.update_pending_write(
            pending_id,
            mind=body.mind,
            kind=new_kind,
            ticker=new_ticker,
            updates_next_check=updates_next_check,
            needs_enrichment=(
                False
                if body.mind == Mind.WORK
                else (existing.needs_enrichment or heuristic_needs_enrichment(existing.content, body.mind))
            ),
        )
        if not updated:
            raise HTTPException(status_code=404, detail=f"Pending write not found: {pending_id}")
        return {"pending_write": updated, "hud": format_confirm_hud(updated)}

    @app.post("/api/notion/pull")
    async def pull_notion_questions() -> dict[str, object]:
        """Pulls edited saved questions from Notion and recomputes changed holdings."""
        snapshots = await notion.pull_holdings()
        updated_tickers: list[str] = []
        for ticker, snap in snapshots.items():
            existing = db.get_holding(ticker)
            if not existing:
                continue
            question_changed = bool(snap.saved_question and snap.saved_question != existing.saved_question)
            updates: dict[str, object] = {"notion_page_id": snap.notion_page_id}
            if snap.saved_question:
                updates["saved_question"] = snap.saved_question
            if snap.next_check != existing.next_check:
                updates["next_check"] = snap.next_check
            db.upsert_holding(existing.model_copy(update=updates))
            if question_changed:
                await recompute_holding(ticker, pull_from_notion=False, push_saved_question=False)
                updated_tickers.append(ticker)
        return {"pulled_count": len(snapshots), "recomputed": updated_tickers}

    @app.post("/api/text", response_model=UtteranceResponse)
    async def process_text_utterance(body: UtteranceRequest) -> UtteranceResponse:
        return await handle_utterance(body.transcript.strip(), body.active_ticker)

    @app.post("/api/voice", response_model=UtteranceResponse)
    async def process_voice_utterance(
        audio: UploadFile = File(...),
        active_ticker: str | None = Form(default=None),
        fallback_transcript: str | None = Form(default=None),
    ) -> UtteranceResponse:
        wav_bytes = await audio.read()
        try:
            transcript = await stt.transcribe_wav(wav_bytes, fallback_transcript=fallback_transcript)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return await handle_utterance(transcript, active_ticker)

    @app.post("/api/confirm", response_model=ConfirmWriteResponse)
    async def confirm_write(body: ConfirmWriteRequest) -> ConfirmWriteResponse:
        # Atomically claim the pending write and persist locally BEFORE awaiting Notion
        # so overlapping confirmations cannot create duplicate journal or Notion entries.
        claimed = db.claim_and_commit_pending_write(
            body.pending_id,
            override_text=body.override_text,
        )
        if claimed is None:
            raise HTTPException(status_code=404, detail=f"Pending write not found: {body.pending_id}")

        pending, entry, updated_holding = claimed

        synced = False
        notion_page_id = await notion.push_journal_entry(entry)
        if notion_page_id:
            synced = True
            db.update_journal_notion_page_id(entry.id, notion_page_id)
            entry = entry.model_copy(update={"notion_page_id": notion_page_id})

        if updated_holding and notion.holdings_enabled:
            if not updated_holding.notion_page_id:
                snapshots = await notion.pull_holdings()
                snap = snapshots.get(updated_holding.ticker.upper())
                if snap and snap.notion_page_id:
                    updated_holding = (
                        db.update_holding_notion_page_id(updated_holding.ticker, snap.notion_page_id)
                        or updated_holding.model_copy(update={"notion_page_id": snap.notion_page_id})
                    )

            holding_page_id = await notion.sync_holding_update(
                updated_holding,
                include_finding=False,
                include_saved_question=False,
                include_next_check=True,
            )
            if holding_page_id and holding_page_id != updated_holding.notion_page_id:
                updated_holding = (
                    db.update_holding_notion_page_id(updated_holding.ticker, holding_page_id)
                    or updated_holding.model_copy(update={"notion_page_id": holding_page_id})
                )

        # Enqueue post-confirm enrichment AFTER notion_page_id is persisted so the worker can PATCH in place
        enrichment_queued = pending.needs_enrichment and pending.mind != Mind.WORK
        if enrichment_queued:
            enrichment_jobs.enqueue(entry.id)

        hud = format_saved_hud(
            entry,
            synced=synced,
            enrichment_queued=enrichment_queued,
        )
        return ConfirmWriteResponse(
            entry=entry,
            holding=updated_holding,
            synced_to_notion=synced,
            enrichment_queued=enrichment_queued,
            hud=hud,
        )

    @app.post("/api/journal/{entry_id}/undo", response_model=UndoWriteResponse)
    async def undo_journal_entry(entry_id: int) -> UndoWriteResponse:
        undone = db.undo_journal_entry(entry_id)
        if undone is None:
            raise HTTPException(status_code=404, detail=f"Journal entry not found: {entry_id}")

        deleted_entry, restored_holding = undone
        archived = False
        if deleted_entry.notion_page_id and notion.enabled:
            archived = await notion.archive_page(deleted_entry.notion_page_id)

        if restored_holding and notion.holdings_enabled and restored_holding.notion_page_id:
            await notion.sync_holding_update(
                restored_holding,
                include_finding=False,
                include_saved_question=False,
                include_next_check=True,
            )

        notion_note = " Removed from Notion + Journal." if archived else " Removed from Journal."
        hud = HudCard(
            header=_clip(f"{deleted_entry.mind.value.upper()} · UNDONE", 60),
            body=_clip(f"Undid: {deleted_entry.content}\n{notion_note.strip()}", 240),
            footer="Tap: deck · Hold: speak again",
        )
        return UndoWriteResponse(
            undone_id=deleted_entry.id,
            archived_in_notion=archived,
            holding=restored_holding,
            hud=hud,
        )

    @app.post("/api/cancel/{pending_id}")
    async def cancel_pending_write(pending_id: str) -> dict[str, object]:
        deleted = db.delete_pending_write(pending_id)
        return {"cancelled": deleted}

    @app.get("/api/journal/{entry_id}", response_model=JournalEntry)
    async def get_journal_entry(entry_id: int) -> JournalEntry:
        entry = db.get_journal_entry(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Journal entry not found: {entry_id}")
        return entry

    @app.get("/api/journal", response_model=list[JournalEntry])
    async def list_journal(
        mind: Mind | None = Query(default=None),
        limit: int = Query(default=25, ge=1, le=100),
    ) -> list[JournalEntry]:
        return db.list_journal_entries(mind=mind, limit=limit)

    return app


app = create_app()
