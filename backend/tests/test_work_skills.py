from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import CockpitDB
from app.main import create_app
from app.models import HoldingRecord, Mind, Verdict
from app.seed import seed_if_empty
from app.services.retrieval import DocumentRetriever


def _make_app(tmp_path: Path):
    settings = Settings(
        cockpit_mock_mode=True,
        cockpit_db_path=str(tmp_path / "skills.db"),
        openrouter_api_key="",
        notion_token="",
    )
    return create_app(settings)


def test_earnings_preview_skill_is_deterministic_zero_llm(tmp_path: Path) -> None:
    """earnings_preview runs strictly via Tier-0 code + SQLite with ZERO LLM worker calls."""
    app = _make_app(tmp_path)
    runtime = app.state.runtime
    runtime.db.update_holding_next_check("NVDA", "Verify Q1 FY28 price hike timing")

    async def fail_if_llm_called(*_args, **_kwargs):
        raise AssertionError("LLM worker must not be called for earnings_preview")

    runtime.worker.synthesize_card = fail_if_llm_called  # type: ignore[method-assign]
    runtime.worker.synthesize_peer_read_across = fail_if_llm_called  # type: ignore[method-assign]

    with TestClient(app) as client:
        resp = client.post(
            "/api/text",
            json={"transcript": "Preview NVIDIA before earnings", "active_ticker": "TSM"},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["decision"]["mind"] == "work"
        assert payload["decision"]["action"] == "earnings_preview"
        assert payload["decision"]["needs_llm_worker"] is False
        assert payload["decision"]["holding"] == "NVDA"
        assert payload["pending_write"] is None
        assert payload["suggested_check"] is None
        assert payload["hud"]["header"] == "PREVIEW · NVDA · PRE-CALL"
        assert "margin trough" in payload["hud"]["body"]
        assert "$108bn" in payload["hud"]["body"]
        assert "Verify Q1 FY28 price hike timing" in payload["hud"]["body"]


def test_peer_read_across_tsm_and_msft_to_nvda_with_preview_before_save(tmp_path: Path) -> None:
    """
    Peer read-across connects real TSMC / Microsoft filings to NVIDIA's thesis,
    returns suggested_check (never an unseen pending_write), and saves to NVDA journal on confirm.
    """
    with TestClient(_make_app(tmp_path)) as client:
        # 1. TSMC -> NVIDIA read-across
        r_tsm = client.post(
            "/api/text",
            json={"transcript": "What's the read-across from TSMC to NVIDIA?", "active_ticker": "NVDA"},
        )
        assert r_tsm.status_code == 200
        tsm_data = r_tsm.json()
        assert tsm_data["decision"]["action"] == "peer_read_across"
        assert tsm_data["decision"]["holding"] == "NVDA"
        assert tsm_data["decision"]["peer_holding"] == "TSM"
        assert tsm_data["decision"]["needs_llm_worker"] is True
        assert tsm_data["pending_write"] is None
        assert tsm_data["suggested_check"] is not None
        assert tsm_data["suggested_check"]["ticker"] == "NVDA"
        assert "TSMC" in tsm_data["hud"]["body"] or "CoWoS" in tsm_data["hud"]["body"]
        assert tsm_data["hud"]["header"] == "PEER · TSM → NVDA"

        # Preview exact proposed check before confirming
        staged = client.post("/api/stage-check", json=tsm_data["suggested_check"])
        assert staged.status_code == 200
        assert staged.json()["hud"]["body"] == tsm_data["suggested_check"]["content"]

        saved = client.post("/api/confirm", json={"pending_id": staged.json()["pending_write"]["id"]})
        assert saved.status_code == 200
        assert saved.json()["entry"]["ticker"] == "NVDA"
        assert "Peer read:" in (saved.json()["entry"]["source_citation"] or "")

        # 2. Microsoft -> NVIDIA read-across
        r_msft = client.post(
            "/api/text",
            json={"transcript": "What does Microsoft capex mean for NVIDIA?", "active_ticker": "NVDA"},
        )
        assert r_msft.status_code == 200
        msft_data = r_msft.json()
        assert msft_data["decision"]["action"] == "peer_read_across"
        assert msft_data["decision"]["holding"] == "NVDA"
        assert msft_data["decision"]["peer_holding"] == "MSFT"
        assert msft_data["hud"]["header"] == "PEER · MSFT → NVDA"
        assert "$50bn" in msft_data["hud"]["body"]
        assert msft_data["pending_write"] is None
        assert msft_data["suggested_check"] is not None


def test_peer_read_across_unsupported_topic_returns_no_match(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        resp = client.post(
            "/api/text",
            json={
                "transcript": "What's the read-across from TSMC to NVIDIA on warranty reserves?",
                "active_ticker": "NVDA",
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["decision"]["action"] == "peer_read_across"
        assert "NO MATCH" in payload["hud"]["header"]
        assert "No matching passage" in payload["hud"]["body"]
        assert payload["pending_write"] is None
        assert payload["suggested_check"] is None


def test_peer_read_across_topic_specific_margin_uses_selected_passage_not_canned_cowos(
    tmp_path: Path,
) -> None:
    """
    Asking specifically about TSMC gross-margin read-across to NVIDIA must derive BOTH the
    fallback card and proposed check from the selected margin passage (66.4% / N2 ramp),
    never from a canned CoWoS/revenue narrative mismatched with the citation.
    """
    with TestClient(_make_app(tmp_path)) as client:
        resp = client.post(
            "/api/text",
            json={
                "transcript": "What's the TSMC gross margin read-across to NVIDIA?",
                "active_ticker": "NVDA",
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["decision"]["action"] == "peer_read_across"
        assert payload["hud"]["header"] == "PEER · TSM → NVDA"
        assert "66.4%" in payload["hud"]["body"]
        assert "TSMC Q3 2026 PR" in payload["hud"]["body"]
        assert "CoWoS" not in payload["hud"]["body"]
        assert "66.4%" in payload["suggested_check"]["content"]
        assert "CoWoS" not in payload["suggested_check"]["content"]
        assert payload["suggested_check"]["source_citation"] == "Peer read: TSMC Q3 2026 PR · Oct 15"


def test_seed_migrates_legacy_placeholders_idempotently_while_preserving_user_data(
    tmp_path: Path,
) -> None:
    """Upgrading a pre-existing DB with placeholder TSM/MSFT rows replaces placeholders without clobbering user data."""
    db = CockpitDB(tmp_path / "legacy.db")

    # Seed legacy placeholder holdings and chunks exactly as created by PR #1-#3
    db.upsert_holding(
        HoldingRecord(
            ticker="NVDA",
            company="NVIDIA Corp.",
            saved_question="Does NVIDIA expect the margin trough to get deeper?",
            verdict=Verdict.HELD,
            latest_finding="Q4 FY27 gross margin guide · 71-72%.",
            source_citation="Q3 FY27 PR · Nov 17",
            suggested_next_check="Check price hikes",
            next_check="Custom NVDA check",
            notion_page_id="nvda-notion-1",
            updated_at="2026-11-24T00:00:00Z",
        )
    )
    db.upsert_holding(
        HoldingRecord(
            ticker="MSFT",
            company="Microsoft Corp. (placeholder slot — swap real filings)",
            saved_question="My custom Microsoft Azure capex question?",
            verdict=Verdict.WATCH,
            latest_finding="Seed placeholder ready for real MSFT issuer release/transcript ingestion.",
            source_citation="Replace with real filing excerpt",
            suggested_next_check="Check Azure capacity ramp cadence next quarter",
            next_check="My custom MSFT next check",
            notion_page_id="msft-notion-1",
            updated_at="2026-11-24T00:00:00Z",
        )
    )
    db.upsert_holding(
        HoldingRecord(
            ticker="TSM",
            company="TSMC (placeholder slot — swap real filings)",
            saved_question="My custom TSMC N2 margin question?",
            verdict=Verdict.AHEAD,
            latest_finding="Seed placeholder ready for real TSMC issuer release/transcript ingestion.",
            source_citation="Replace with real filing excerpt",
            suggested_next_check="Check N2 utilization",
            next_check="My custom TSM next check",
            notion_page_id="tsm-notion-1",
            updated_at="2026-11-24T00:00:00Z",
        )
    )
    db.insert_chunks(
        [
            {
                "ticker": "MSFT",
                "doc_title": "Placeholder Issuer Filing Slot (MSFT)",
                "doc_date": "2026-11-01",
                "page_ref": "Placeholder · Replace with real filing",
                "topic": "azure cloud",
                "headline": "MSFT placeholder finding",
                "summary_card": "Placeholder MSFT card",
                "verdict": Verdict.WATCH.value,
                "suggested_next_check": "Placeholder check",
                "excerpt": "Placeholder passage for MSFT.",
                "source_url": "",
            },
            {
                "ticker": "TSM",
                "doc_title": "Placeholder Issuer Filing Slot (TSM)",
                "doc_date": "2026-11-01",
                "page_ref": "Placeholder · Replace with real filing",
                "topic": "n2 wafer",
                "headline": "TSM placeholder finding",
                "summary_card": "Placeholder TSM card",
                "verdict": Verdict.AHEAD.value,
                "suggested_next_check": "Placeholder check",
                "excerpt": "Placeholder passage for TSM.",
                "source_url": "",
            },
        ]
    )
    db.add_journal_entry(mind=Mind.PERSONAL, kind="task", content="Buy birthday present")

    # Run migration twice to confirm idempotency
    seed_if_empty(db)
    chunk_count_after_first = db.count_chunks()
    seed_if_empty(db)
    assert db.count_chunks() == chunk_count_after_first

    # Real filings replaced placeholders and are searchable in FTS5
    retriever = DocumentRetriever(db)
    tsm_hits = retriever.retrieve("TSM", "CoWoS packaging", limit=4)
    assert len(tsm_hits) == 1
    assert "$52.3-$54.3 billion" in tsm_hits[0].excerpt

    msft_hits = retriever.retrieve("MSFT", "GB300 capex", limit=4)
    assert len(msft_hits) == 1
    assert "$45.2 billion" in msft_hits[0].excerpt

    # Holding metadata upgraded while user saved_question, next_check, notion_page_id, and journal survive
    msft = db.get_holding("MSFT")
    assert msft is not None
    assert "placeholder" not in msft.company.lower()
    assert "48% YoY" in msft.latest_finding
    assert msft.saved_question == "My custom Microsoft Azure capex question?"
    assert msft.next_check == "My custom MSFT next check"
    assert msft.notion_page_id == "msft-notion-1"

    tsm = db.get_holding("TSM")
    assert tsm is not None
    assert "placeholder" not in tsm.company.lower()
    assert "66.4%" in tsm.latest_finding
    assert tsm.saved_question == "My custom TSMC N2 margin question?"
    assert tsm.next_check == "My custom TSM next check"
    assert tsm.notion_page_id == "tsm-notion-1"

    journal = db.list_journal_entries()
    assert len(journal) == 1
    assert journal[0].content == "Buy birthday present"
