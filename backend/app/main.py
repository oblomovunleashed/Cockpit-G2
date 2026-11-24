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
    HoldingRecord,
    HudCard,
    JevDecision,
    JournalEntry,
    Mind,
    PendingWrite,
    UpdateQuestionRequest,
    UtteranceRequest,
    UtteranceResponse,
)
from app.seed import seed_if_empty
from app.services.jev_router import JevRouter
from app.services.job_runner import BackgroundJobRunner
from app.services.llm_worker import LLMWorker
from app.services.notion_sync import NotionSync
from app.services.openrouter_stt import OpenRouterSTT
from app.services.retrieval import DocumentRetriever


def _clip(text: str, limit: int) -> str:
    clean = " ".join(text.strip().split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def format_holding_hud(holding: HoldingRecord, *, index: int = 1, total: int = 3) -> HudCard:
    header = _clip(f"WORK · {holding.ticker} {index}/{total} · {holding.verdict.value}", 60)
    body = _clip(f"{holding.latest_finding}\n[{holding.source_citation}]", 240)
    footer = "Hold: speak · Tap: save check · Swipe: next"
    return HudCard(header=header, body=body, footer=footer)


def format_confirm_hud(pending: PendingWrite) -> HudCard:
    mind_label = pending.mind.value.upper()
    kind_label = pending.kind.upper()
    ticker_part = f" · {pending.ticker}" if pending.ticker else ""
    header = _clip(f"{mind_label}{ticker_part} · CONFIRM {kind_label}", 60)
    body = _clip(pending.content, 240)
    footer = "Tap: save to journal · Swipe: cancel"
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


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings()
    db = CockpitDB(cfg.cockpit_db_path)
    seed_if_empty(db)

    stt = OpenRouterSTT(cfg)
    jev = JevRouter(cfg)
    retriever = DocumentRetriever(db)
    worker = LLMWorker(cfg)
    notion = NotionSync(cfg)

    async def recompute_holding(ticker: str) -> HoldingRecord:
        holding = db.get_holding(ticker)
        if not holding:
            raise HTTPException(status_code=404, detail=f"Unknown holding: {ticker}")
        candidates = retriever.retrieve(holding.ticker, holding.saved_question, limit=4)
        if not candidates:
            return holding
        chunk, verdict, _ = await jev.select_best_chunk(holding.saved_question, candidates)
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
            page_id = await notion.sync_holding_update(updated)
            if page_id and page_id != updated.notion_page_id:
                updated = (
                    db.update_holding_notion_page_id(updated.ticker, page_id)
                    or updated.model_copy(update={"notion_page_id": page_id})
                )
        return updated

    async def _job_handler(ticker: str) -> None:
        await recompute_holding(ticker)

    jobs = BackgroundJobRunner(_job_handler)
    runtime = AppRuntime(
        settings=cfg,
        db=db,
        stt=stt,
        jev=jev,
        retriever=retriever,
        worker=worker,
        notion=notion,
        jobs=jobs,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        runtime.jobs.start()
        try:
            yield
        finally:
            await runtime.jobs.stop()

    app = FastAPI(
        title="Cockpit-G2 Backend",
        version="0.1.0",
        description="System-1 Jev router + Work LLM worker + Notion sync for Even G2 smart glasses.",
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
        decision: JevDecision = await jev.route_utterance(
            transcript,
            available_tickers=tickers,
            active_ticker=active_ticker,
        )

        # --------------------------------------------------------------
        # FAST PATH ("logged, logged") — ZERO LLM CALLS
        # --------------------------------------------------------------
        if decision.action in (ActionType.LOG_NOTE, ActionType.LOG_TASK) and not decision.needs_llm_worker:
            kind = "task" if decision.action == ActionType.LOG_TASK else "note"
            pending = PendingWrite(
                id=uuid.uuid4().hex[:12],
                mind=decision.mind,
                kind=kind,  # type: ignore[arg-type]
                content=transcript.strip(),
                ticker=None,
                source_citation=None,
                updates_next_check=False,
            )
            db.save_pending_write(pending)
            return UtteranceResponse(
                transcript=transcript,
                decision=decision,
                hud=format_confirm_hud(pending),
                holding=None,
                pending_write=pending,
            )

        # --------------------------------------------------------------
        # WORK: STAGE SPOKEN NEXT-QUARTER CHECK FOR 1-TAP CONFIRM
        # --------------------------------------------------------------
        if decision.action == ActionType.DRAFT_NEXT_CHECK:
            ticker = (decision.holding or active_ticker or (tickers[0] if tickers else "NVDA")).upper()
            holding = db.get_holding(ticker)
            pending = PendingWrite(
                id=uuid.uuid4().hex[:12],
                mind=Mind.WORK,
                kind="check",
                content=transcript.strip(),
                ticker=ticker,
                source_citation=holding.source_citation if holding else None,
                updates_next_check=True,
            )
            db.save_pending_write(pending)
            return UtteranceResponse(
                transcript=transcript,
                decision=decision,
                hud=format_confirm_hud(pending),
                holding=holding,
                pending_write=pending,
            )

        # --------------------------------------------------------------
        # DEEP PATH ("LLM WORKER") — WORK EARNINGS BRIEF OR FOLLOW-UP
        # --------------------------------------------------------------
        ticker = (decision.holding or active_ticker or (tickers[0] if tickers else "NVDA")).upper()
        holding = db.get_holding(ticker)
        if holding is None:
            raise HTTPException(status_code=404, detail=f"Holding not found: {ticker}")

        if decision.action == ActionType.FOLLOW_UP_SOURCE:
            candidates = retriever.retrieve(ticker, transcript, limit=4)
            chunk, verdict, _ = await jev.select_best_chunk(transcript, candidates)
            finding, suggested_check = await worker.synthesize_card(
                ticker=ticker,
                question=transcript,
                chunk=chunk,
                verdict=verdict,
            )
            # Stage the AI-drafted next-quarter check for instant 1-tap confirmation
            pending = PendingWrite(
                id=uuid.uuid4().hex[:12],
                mind=Mind.WORK,
                kind="check",
                content=suggested_check.strip(),
                ticker=ticker,
                source_citation=chunk.page_ref,
                updates_next_check=True,
            )
            db.save_pending_write(pending)
            hud = HudCard(
                header=_clip(f"WORK · {ticker} · SOURCE", 60),
                body=_clip(f"{finding}\n[{chunk.page_ref}]", 240),
                footer="Tap: save next check · Hold: dictate",
            )
            return UtteranceResponse(
                transcript=transcript,
                decision=decision,
                hud=hud,
                holding=holding,
                pending_write=pending,
            )

        # Default Work path: recompute or surface sourced card for saved question
        updated = await recompute_holding(ticker)
        idx = next((i + 1 for i, h in enumerate(holdings) if h.ticker == updated.ticker), 1)
        return UtteranceResponse(
            transcript=transcript,
            decision=decision,
            hud=format_holding_hud(updated, index=idx, total=max(len(holdings), 1)),
            holding=updated,
            pending_write=None,
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
            pending_jobs=jobs.pending_count,
            mock_mode=not cfg.live_openrouter_enabled,
            notion_enabled=notion.enabled,
        )

    @app.get("/api/holdings", response_model=list[HoldingRecord])
    async def list_holdings() -> list[HoldingRecord]:
        return db.list_holdings()

    @app.patch("/api/holdings/{ticker}/question")
    async def update_holding_question(ticker: str, body: UpdateQuestionRequest) -> dict[str, object]:
        updated = db.update_holding_question(ticker, body.saved_question.strip())
        if not updated:
            raise HTTPException(status_code=404, detail=f"Unknown holding: {ticker}")
        recomputed = await recompute_holding(ticker)
        holdings = db.list_holdings()
        idx = next((i + 1 for i, h in enumerate(holdings) if h.ticker == recomputed.ticker), 1)
        return {
            "holding": recomputed,
            "hud": format_holding_hud(recomputed, index=idx, total=max(len(holdings), 1)),
        }

    @app.post("/api/holdings/{ticker}/stage-suggested-check")
    async def stage_suggested_check(ticker: str) -> dict[str, object]:
        """Zero-dictation 1-tap path: stages the holding's AI-drafted next check."""
        holding = db.get_holding(ticker)
        if not holding:
            raise HTTPException(status_code=404, detail=f"Unknown holding: {ticker}")
        draft = (holding.suggested_next_check or f"Review {holding.ticker} next earnings call").strip()
        pending = PendingWrite(
            id=uuid.uuid4().hex[:12],
            mind=Mind.WORK,
            kind="check",
            content=draft,
            ticker=holding.ticker,
            source_citation=holding.source_citation,
            updates_next_check=True,
        )
        db.save_pending_write(pending)
        return {"pending_write": pending, "hud": format_confirm_hud(pending)}

    @app.post("/api/notion/pull")
    async def pull_notion_questions() -> dict[str, object]:
        """Pulls edited saved questions from Notion and recomputes changed holdings."""
        pulled = await notion.pull_saved_questions()
        updated_tickers: list[str] = []
        for ticker, (question, page_id) in pulled.items():
            existing = db.get_holding(ticker)
            if existing and existing.saved_question != question:
                db.upsert_holding(
                    existing.model_copy(update={"saved_question": question, "notion_page_id": page_id})
                )
                await recompute_holding(ticker)
                updated_tickers.append(ticker)
        return {"pulled_count": len(pulled), "recomputed": updated_tickers}

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
        transcript = await stt.transcribe_wav(wav_bytes, fallback_transcript=fallback_transcript)
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
            holding_page_id = await notion.sync_holding_update(updated_holding)
            if holding_page_id and holding_page_id != updated_holding.notion_page_id:
                updated_holding = (
                    db.update_holding_notion_page_id(updated_holding.ticker, holding_page_id)
                    or updated_holding.model_copy(update={"notion_page_id": holding_page_id})
                )

        dest = "Notion + Journal" if synced else "Journal"
        label = f"{pending.mind.value.upper()} · SAVED"
        hud = HudCard(
            header=_clip(label, 60),
            body=_clip(f"✓ {entry.content}\nSaved to {dest}.", 240),
            footer="Hold: speak · Swipe: holdings",
        )
        return ConfirmWriteResponse(
            entry=entry,
            holding=updated_holding,
            synced_to_notion=synced,
            hud=hud,
        )

    @app.post("/api/cancel/{pending_id}")
    async def cancel_pending_write(pending_id: str) -> dict[str, object]:
        deleted = db.delete_pending_write(pending_id)
        return {"cancelled": deleted}

    @app.get("/api/journal", response_model=list[JournalEntry])
    async def list_journal(
        mind: Mind | None = Query(default=None),
        limit: int = Query(default=25, ge=1, le=100),
    ) -> list[JournalEntry]:
        return db.list_journal_entries(mind=mind, limit=limit)

    return app


app = create_app()
