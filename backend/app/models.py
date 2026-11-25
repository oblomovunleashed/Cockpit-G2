from __future__ import annotations

from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field


class Mind(str, Enum):
    WORK = "work"
    PERSONAL = "personal"
    INTERESTS = "interests"


class ActionType(str, Enum):
    LOG_NOTE = "log_note"
    LOG_TASK = "log_task"
    EARNINGS_PREVIEW = "earnings_preview"
    BRIEF_HOLDING = "brief_holding"
    FOLLOW_UP_SOURCE = "follow_up_source"
    PEER_READ_ACROSS = "peer_read_across"
    DRAFT_NEXT_CHECK = "draft_next_check"
    CLARIFY = "clarify"


class Verdict(str, Enum):
    HELD = "HELD"
    AHEAD = "AHEAD"
    WATCH = "WATCH"
    PENDING = "PENDING"


EnrichmentStatus = Literal["none", "pending", "enriched", "needs_detail", "failed"]


class HudCard(BaseModel):
    """Exact 3-container payload rendered onto the Even G2 576x288 lens."""

    header: str = Field(..., max_length=64)
    body: str = Field(..., max_length=260)
    footer: str = Field(..., max_length=64)


class JevDecision(BaseModel):
    mind: Mind
    action: ActionType
    holding: str | None = None
    peer_holding: str | None = None
    requested_company: str | None = None
    needs_llm_worker: bool = False
    needs_enrichment: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    mode: Literal["openrouter", "mock"] = "mock"
    reasoning: str | None = None


class DocumentChunk(BaseModel):
    id: int
    ticker: str
    doc_title: str
    doc_date: str
    page_ref: str
    topic: str
    headline: str
    summary_card: str
    verdict: Verdict
    suggested_next_check: str
    excerpt: str
    source_url: str = ""


class HoldingRecord(BaseModel):
    ticker: str
    company: str
    saved_question: str
    verdict: Verdict | None = None
    latest_finding: str = ""
    source_citation: str = ""
    suggested_next_check: str = ""
    next_check: str = ""
    notion_page_id: str | None = None
    updated_at: str = ""


class JournalEntry(BaseModel):
    id: int
    mind: Mind
    kind: Literal["note", "task", "check"]
    content: str
    ticker: str | None = None
    source_citation: str | None = None
    notion_page_id: str | None = None
    enrichment_status: EnrichmentStatus = "none"
    enriched_summary: str = ""
    enriched_url: str = ""
    due_date: str | None = None
    created_at: str


class PendingWrite(BaseModel):
    id: str
    mind: Mind
    kind: Literal["note", "task", "check"]
    content: str
    ticker: str | None = None
    source_citation: str | None = None
    updates_next_check: bool = False
    needs_enrichment: bool = False


class UpdatePendingWriteRequest(BaseModel):
    mind: Mind
    kind: Literal["note", "task", "check"] | None = None
    ticker: str | None = None


class DraftCheck(BaseModel):
    """Proposed next-quarter check surfaced for explicit review before confirmation."""

    content: str
    ticker: str
    source_citation: str | None = None


class StageCheckRequest(BaseModel):
    ticker: str
    content: str = Field(..., min_length=1, max_length=1000)
    source_citation: str | None = None


class UtteranceRequest(BaseModel):
    transcript: str = Field(..., min_length=1, max_length=1000)
    active_ticker: str | None = None


class UtteranceResponse(BaseModel):
    transcript: str
    decision: JevDecision
    hud: HudCard
    holding: HoldingRecord | None = None
    pending_write: PendingWrite | None = None
    suggested_check: DraftCheck | None = None


class ConfirmWriteRequest(BaseModel):
    pending_id: str
    override_text: str | None = None


class ConfirmWriteResponse(BaseModel):
    entry: JournalEntry
    holding: HoldingRecord | None = None
    synced_to_notion: bool = False
    enrichment_queued: bool = False
    hud: HudCard


class UndoWriteResponse(BaseModel):
    undone_id: int
    archived_in_notion: bool = False
    holding: HoldingRecord | None = None
    hud: HudCard


class UpdateQuestionRequest(BaseModel):
    saved_question: str = Field(..., min_length=3, max_length=300)


class DeckStateResponse(BaseModel):
    holdings: list[HoldingRecord]
    recent_journal: list[JournalEntry]
    pending_jobs: int
    mock_mode: bool
    notion_enabled: bool
