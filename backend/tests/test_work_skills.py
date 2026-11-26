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
    runtime.db.update_holding_next_check("NTRA", "Verify Q4 Signatera ASP above $1,290")

    async def fail_if_llm_called(*_args, **_kwargs):
        raise AssertionError("LLM worker must not be called for earnings_preview")

    runtime.worker.synthesize_card = fail_if_llm_called  # type: ignore[method-assign]
    runtime.worker.synthesize_peer_read_across = fail_if_llm_called  # type: ignore[method-assign]

    with TestClient(app) as client:
        resp = client.post(
            "/api/text",
            json={"transcript": "Preview Natera before earnings", "active_ticker": "STX"},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["decision"]["mind"] == "work"
        assert payload["decision"]["action"] == "earnings_preview"
        assert payload["decision"]["needs_llm_worker"] is False
        assert payload["decision"]["holding"] == "NTRA"
        assert payload["pending_write"] is None
        assert payload["suggested_check"] is None
        assert payload["hud"]["header"] == "PREVIEW · NTRA · PRE-CALL"
        assert "true-ups" in payload["hud"]["body"]
        assert "29.2% wt" in payload["hud"]["body"]
        assert "Verify Q4 Signatera ASP above $1,290" in payload["hud"]["body"]


def test_peer_read_across_amzn_to_stx_with_preview_before_save(tmp_path: Path) -> None:
    """
    Peer read-across connects real Amazon AWS capex filings to Seagate's HAMR/Mozaic thesis,
    returns suggested_check (never an unseen pending_write), and saves to STX journal on confirm.
    """
    with TestClient(_make_app(tmp_path)) as client:
        r_amzn = client.post(
            "/api/text",
            json={"transcript": "What does Amazon capex mean for Seagate?", "active_ticker": "STX"},
        )
        assert r_amzn.status_code == 200
        amzn_data = r_amzn.json()
        assert amzn_data["decision"]["action"] == "peer_read_across"
        assert amzn_data["decision"]["holding"] == "STX"
        assert amzn_data["decision"]["peer_holding"] == "AMZN"
        assert amzn_data["decision"]["needs_llm_worker"] is True
        assert amzn_data["pending_write"] is None
        assert amzn_data["suggested_check"] is not None
        assert amzn_data["suggested_check"]["ticker"] == "STX"
        assert "$220B" in amzn_data["hud"]["body"]
        assert amzn_data["hud"]["header"] == "PEER · AMZN → STX"

        # Preview exact proposed check before confirming
        staged = client.post("/api/stage-check", json=amzn_data["suggested_check"])
        assert staged.status_code == 200
        assert staged.json()["hud"]["body"] == amzn_data["suggested_check"]["content"]

        saved = client.post("/api/confirm", json={"pending_id": staged.json()["pending_write"]["id"]})
        assert saved.status_code == 200
        assert saved.json()["entry"]["ticker"] == "STX"
        assert "Peer read:" in (saved.json()["entry"]["source_citation"] or "")


def test_peer_read_across_unsupported_topic_returns_no_match(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        resp = client.post(
            "/api/text",
            json={
                "transcript": "What's the read-across from Amazon to Seagate on warranty reserves?",
                "active_ticker": "STX",
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["decision"]["action"] == "peer_read_across"
        assert "NO MATCH" in payload["hud"]["header"]
        assert "No matching passage" in payload["hud"]["body"]
        assert payload["pending_write"] is None
        assert payload["suggested_check"] is None


def test_peer_read_across_topic_specific_margin_uses_selected_passage_not_canned_capex(
    tmp_path: Path,
) -> None:
    """
    Asking specifically about Amazon AWS operating margin read-across to Seagate must derive BOTH
    the fallback card and proposed check from the selected AWS margin passage (38.7% / $46.0B),
    never from a mismatched capex narrative.
    """
    with TestClient(_make_app(tmp_path)) as client:
        resp = client.post(
            "/api/text",
            json={
                "transcript": "What's the Amazon AWS operating margin read-across to Seagate?",
                "active_ticker": "STX",
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["decision"]["action"] == "peer_read_across"
        assert payload["hud"]["header"] == "PEER · AMZN → STX"
        assert "38.7%" in payload["hud"]["body"]
        assert "AMZN Q3 2026 PR" in payload["hud"]["body"]
        assert "38.7%" in payload["suggested_check"]["content"]
        assert payload["suggested_check"]["source_citation"] == "Peer read: AMZN Q3 2026 PR · Oct 29"


def test_seed_migrates_legacy_demo_portfolio_and_placeholders_while_preserving_journal(
    tmp_path: Path,
) -> None:
    """
    1. Upgrading a DB with the old NVDA/MSFT/TSM demo seed swaps in NTRA/STX/AMZN while keeping journal entries.
    2. Upgrading placeholder NTRA/STX/AMZN rows replaces placeholders while preserving custom questions/checks.
    """
    # Part 1: Legacy NVDA/MSFT/TSM demo DB migrates cleanly to NTRA/STX/AMZN
    legacy_db = CockpitDB(tmp_path / "legacy_nvda.db")
    for ticker in ("NVDA", "MSFT", "TSM"):
        legacy_db.upsert_holding(
            HoldingRecord(
                ticker=ticker,
                company=ticker,
                saved_question=f"Old {ticker} question",
                verdict=Verdict.HELD,
                latest_finding="Old finding",
                source_citation="Old source",
            )
        )
    legacy_db.add_journal_entry(mind=Mind.PERSONAL, kind="task", content="Buy birthday present")

    seed_if_empty(legacy_db)
    assert [h.ticker for h in legacy_db.list_holdings()] == ["NTRA", "STX", "AMZN"]
    assert legacy_db.list_journal_entries()[0].content == "Buy birthday present"

    # Part 2: Placeholder STX/AMZN rows upgrade idempotently while keeping custom user fields
    ph_db = CockpitDB(tmp_path / "placeholders.db")
    ph_db.upsert_holding(
        HoldingRecord(
            ticker="NTRA",
            company="Natera, Inc. (29.2%)",
            saved_question="Is Natera's underlying gross margin expanding without revenue true-ups?",
            verdict=Verdict.AHEAD,
            latest_finding="Q3 GAAP GM 64.7%.",
            source_citation="NTRA Q3 2026 PR · Nov 5",
            next_check="Custom NTRA check",
            notion_page_id="ntra-notion-1",
        )
    )
    ph_db.upsert_holding(
        HoldingRecord(
            ticker="STX",
            company="Seagate (placeholder slot — swap real filings)",
            saved_question="My custom Seagate HAMR question?",
            verdict=Verdict.WATCH,
            latest_finding="Seed placeholder ready for real STX filing.",
            source_citation="Replace with real filing excerpt",
            next_check="My custom STX next check",
            notion_page_id="stx-notion-1",
        )
    )
    ph_db.upsert_holding(
        HoldingRecord(
            ticker="AMZN",
            company="Amazon (placeholder slot — swap real filings)",
            saved_question="My custom Amazon AWS capex question?",
            verdict=Verdict.AHEAD,
            latest_finding="Seed placeholder ready for real AMZN filing.",
            source_citation="Replace with real filing excerpt",
            next_check="My custom AMZN next check",
            notion_page_id="amzn-notion-1",
        )
    )
    ph_db.insert_chunks(
        [
            {
                "ticker": "STX",
                "doc_title": "Placeholder Issuer Filing Slot (STX)",
                "doc_date": "2026-11-01",
                "page_ref": "Placeholder · Replace with real filing",
                "topic": "hamr",
                "headline": "STX placeholder",
                "summary_card": "Placeholder STX",
                "verdict": Verdict.WATCH.value,
                "suggested_next_check": "Placeholder check",
                "excerpt": "Placeholder STX excerpt.",
                "source_url": "",
            },
            {
                "ticker": "AMZN",
                "doc_title": "Placeholder Issuer Filing Slot (AMZN)",
                "doc_date": "2026-11-01",
                "page_ref": "Placeholder · Replace with real filing",
                "topic": "aws",
                "headline": "AMZN placeholder",
                "summary_card": "Placeholder AMZN",
                "verdict": Verdict.AHEAD.value,
                "suggested_next_check": "Placeholder check",
                "excerpt": "Placeholder AMZN excerpt.",
                "source_url": "",
            },
        ]
    )

    seed_if_empty(ph_db)
    count_after_first = ph_db.count_chunks()
    seed_if_empty(ph_db)
    assert ph_db.count_chunks() == count_after_first

    retriever = DocumentRetriever(ph_db)
    stx_hits = retriever.retrieve("STX", "Mozaic HAMR Azure", limit=4)
    assert len(stx_hits) == 1
    assert "198 exabytes" in stx_hits[0].excerpt

    amzn_hits = retriever.retrieve("AMZN", "AWS capex data centers", limit=4)
    assert len(amzn_hits) >= 1
    assert any("$220 billion" in hit.excerpt for hit in amzn_hits)

    stx = ph_db.get_holding("STX")
    assert stx is not None
    assert "placeholder" not in stx.company.lower()
    assert stx.saved_question == "My custom Seagate HAMR question?"
    assert stx.next_check == "My custom STX next check"
    assert stx.notion_page_id == "stx-notion-1"
