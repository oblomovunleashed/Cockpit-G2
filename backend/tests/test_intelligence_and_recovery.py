from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
import sqlite3
import time
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import CockpitDB
from app.main import create_app
from app.models import ActionType, Mind
from app.services.jev_router import JevRouter
from app.services.llm_worker import resolve_relative_date, strip_markdown


def _make_settings(tmp_path: Path, *, with_notion: bool = False) -> Settings:
    return Settings(
        cockpit_mock_mode=True,
        cockpit_db_path=str(tmp_path / "intel.db"),
        openrouter_api_key="",
        notion_token="secret-test" if with_notion else "",
        notion_log_database_id="log-db-id" if with_notion else "",
        notion_holdings_database_id="holdings-db-id" if with_notion else "",
    )


def test_untracked_company_preview_and_review_never_default_to_nvidia(tmp_path: Path) -> None:
    """
    'Can you please preview Alphabet earnings?' and 'Review Apple earnings' while viewing NVDA
    must route as ad-hoc web skills for Alphabet / Apple, NEVER silently falling back to NVDA.
    """
    with TestClient(create_app(_make_settings(tmp_path))) as client:
        r_preview = client.post(
            "/api/text",
            json={
                "transcript": "Can you please preview Alphabet earnings?",
                "active_ticker": "NVDA",
            },
        )
        assert r_preview.status_code == 200
        preview = r_preview.json()
        assert preview["decision"]["mind"] == "work"
        assert preview["decision"]["action"] == "earnings_preview"
        assert preview["decision"]["holding"] is None
        assert "Alphabet" in (preview["decision"]["requested_company"] or "")
        assert preview["decision"]["needs_llm_worker"] is True
        assert preview["holding"] is None
        assert preview["hud"]["header"] == "PREVIEW · GOOGL · WEB"
        assert "Alphabet" in preview["hud"]["body"]
        assert "NVIDIA" not in preview["hud"]["body"]
        assert preview["suggested_check"] is not None
        assert preview["suggested_check"]["ticker"] == "GOOGL"

        # Staging and confirming a watchpoint for a non-held company works cleanly
        staged = client.post("/api/stage-check", json=preview["suggested_check"])
        assert staged.status_code == 200
        pending = staged.json()["pending_write"]
        assert pending["ticker"] == "GOOGL"
        assert pending["updates_next_check"] is False

        confirmed = client.post("/api/confirm", json={"pending_id": pending["id"]})
        assert confirmed.status_code == 200
        assert confirmed.json()["entry"]["ticker"] == "GOOGL"
        assert confirmed.json()["holding"] is None

        # Non-held earnings review also routes cleanly to Apple, not NVDA
        r_review = client.post(
            "/api/text",
            json={
                "transcript": "Can you review Apple earnings?",
                "active_ticker": "NVDA",
            },
        )
        assert r_review.status_code == 200
        review = r_review.json()
        assert review["decision"]["action"] == "brief_holding"
        assert review["decision"]["holding"] is None
        assert review["hud"]["header"] == "WORK · AAPL · WEB"
        assert "Apple" in review["hud"]["body"]


def test_reclassify_pending_write_fixes_misclassification_in_place(tmp_path: Path) -> None:
    """Swiping on CONFIRM card reclassifies Mind/Kind via PATCH /api/pending/{id} without re-speaking."""
    with TestClient(create_app(_make_settings(tmp_path))) as client:
        staged = client.post(
            "/api/text",
            json={"transcript": "Check Ben Thompson's latest Stratechery article", "active_ticker": "NVDA"},
        )
        assert staged.status_code == 200
        pending = staged.json()["pending_write"]
        assert pending["mind"] == "interests"
        assert pending["kind"] == "note"

        # Switch to Personal Task
        r_personal = client.patch(
            f"/api/pending/{pending['id']}",
            json={"mind": "personal", "kind": "task"},
        )
        assert r_personal.status_code == 200
        assert r_personal.json()["pending_write"]["mind"] == "personal"
        assert r_personal.json()["pending_write"]["kind"] == "task"
        assert "PERSONAL · CONFIRM TASK" in r_personal.json()["hud"]["header"]

        # Switch to Work Check on NVDA -> updates_next_check becomes True, enrichment turns off
        r_work = client.patch(
            f"/api/pending/{pending['id']}",
            json={"mind": "work", "kind": "check", "ticker": "NVDA"},
        )
        assert r_work.status_code == 200
        work_pending = r_work.json()["pending_write"]
        assert work_pending["mind"] == "work"
        assert work_pending["ticker"] == "NVDA"
        assert work_pending["updates_next_check"] is True
        assert work_pending["needs_enrichment"] is False

        # Confirm writes directly to Work journal + updates NVDA next_check
        confirmed = client.post("/api/confirm", json={"pending_id": pending["id"]})
        assert confirmed.status_code == 200
        assert confirmed.json()["entry"]["mind"] == "work"
        assert confirmed.json()["entry"]["enrichment_status"] == "none"
        assert confirmed.json()["holding"]["next_check"] == "Check Ben Thompson's latest Stratechery article"


def test_undo_confirmed_entry_archives_notion_and_restores_previous_next_check(
    tmp_path: Path,
) -> None:
    """Undo removes the journal entry, archives its Notion page, and restores the prior holding next_check."""
    app = create_app(_make_settings(tmp_path, with_notion=True))
    runtime = app.state.runtime
    runtime.db.update_holding_notion_page_id("NVDA", "watchlist-nvda-1")
    runtime.db.update_holding_next_check("NVDA", "Original check before edit")

    archived_pages: list[str] = []
    synced_next_checks: list[str] = []

    async def fake_push_journal(_entry) -> str:
        return "notion-entry-42"

    async def fake_archive_page(page_id: str) -> bool:
        archived_pages.append(page_id)
        return True

    async def fake_sync_holding(holding, **_kwargs) -> str:
        synced_next_checks.append(holding.next_check)
        return holding.notion_page_id or "watchlist-nvda-1"

    runtime.notion.push_journal_entry = fake_push_journal  # type: ignore[method-assign]
    runtime.notion.archive_page = fake_archive_page  # type: ignore[method-assign]
    runtime.notion.sync_holding_update = fake_sync_holding  # type: ignore[method-assign]

    with TestClient(app) as client:
        draft = client.post(
            "/api/text",
            json={
                "transcript": "At the next call, check whether Q1 price hikes landed",
                "active_ticker": "NVDA",
            },
        )
        pending_id = draft.json()["pending_write"]["id"]
        saved = client.post("/api/confirm", json={"pending_id": pending_id})
        assert saved.status_code == 200
        entry_id = saved.json()["entry"]["id"]
        assert saved.json()["holding"]["next_check"] == "At the next call, check whether Q1 price hikes landed"

        # Undo immediately
        undone = client.post(f"/api/journal/{entry_id}/undo")
        assert undone.status_code == 200
        payload = undone.json()
        assert payload["undone_id"] == entry_id
        assert payload["archived_in_notion"] is True
        assert payload["holding"]["next_check"] == "Original check before edit"
        assert archived_pages == ["notion-entry-42"]
        assert synced_next_checks[-1] == "Original check before edit"
        assert runtime.db.get_journal_entry(entry_id) is None


def test_post_confirm_enrichment_named_podcast_vs_unnamed_vs_relative_date(
    tmp_path: Path,
) -> None:
    """
    Verifies post-confirm async enrichment:
      1. Named podcast ('latest Acquired podcast') -> enriches + patches Notion in place.
      2. Unnamed podcast ('I enjoy the latest, and that's the best podcast') -> honestly marks needs_detail.
      3. Personal task with relative date ('next Friday') -> resolves due_date deterministically.
      4. Plain reminder ('Remember to buy a birthday present') -> zero enrichment queued.
    """
    app = create_app(_make_settings(tmp_path, with_notion=True))
    runtime = app.state.runtime

    patched_enrichments: list[tuple[str, str, str]] = []

    async def fake_push_journal(entry) -> str:
        return f"notion-log-{entry.id}"

    async def fake_update_enrichment(
        page_id: str,
        *,
        status: str,
        enriched_summary: str,
        enriched_url: str = "",
        due_date: str | None = None,
    ) -> bool:
        patched_enrichments.append((page_id, status, enriched_summary))
        return True

    runtime.notion.push_journal_entry = fake_push_journal  # type: ignore[method-assign]
    runtime.notion.update_journal_enrichment = fake_update_enrichment  # type: ignore[method-assign]

    def _wait_for_enrichment(client: TestClient, entry_id: int) -> dict:
        for _ in range(30):
            data = client.get(f"/api/journal/{entry_id}").json()
            if data["enrichment_status"] != "pending":
                return data
            time.sleep(0.05)
        return client.get(f"/api/journal/{entry_id}").json()

    with TestClient(app) as client:
        # 1. Named podcast -> enriched + Notion patched in place
        r1 = client.post("/api/text", json={"transcript": "I enjoyed the latest Acquired podcast"})
        assert r1.json()["decision"]["needs_enrichment"] is True
        c1 = client.post("/api/confirm", json={"pending_id": r1.json()["pending_write"]["id"]})
        assert c1.json()["enrichment_queued"] is True
        e1 = _wait_for_enrichment(client, c1.json()["entry"]["id"])
        assert e1["enrichment_status"] == "enriched"
        assert "Acquired" in e1["enriched_summary"]
        assert e1["enriched_url"].startswith("https://")
        assert any(p[1] == "enriched" for p in patched_enrichments)

        # 2. Unnamed podcast -> refuses to guess, marks needs_detail
        r2 = client.post(
            "/api/text",
            json={"transcript": "I enjoy the latest, and that's the best podcast"},
        )
        c2 = client.post("/api/confirm", json={"pending_id": r2.json()["pending_write"]["id"]})
        assert c2.json()["enrichment_queued"] is True
        e2 = _wait_for_enrichment(client, c2.json()["entry"]["id"])
        assert e2["enrichment_status"] == "needs_detail"
        assert "Podcast name not specified" in e2["enriched_summary"]

        # 3. Relative-date personal task -> resolves due_date
        r3 = client.post(
            "/api/text",
            json={"transcript": "Remember to buy a birthday present next Friday"},
        )
        assert r3.json()["decision"]["needs_enrichment"] is True
        c3 = client.post("/api/confirm", json={"pending_id": r3.json()["pending_write"]["id"]})
        assert c3.json()["enrichment_queued"] is True
        e3 = _wait_for_enrichment(client, c3.json()["entry"]["id"])
        assert e3["enrichment_status"] == "enriched"
        assert e3["due_date"] is not None

        # 4. Plain reminder -> no enrichment queued
        r4 = client.post("/api/text", json={"transcript": "Remember to buy a birthday present"})
        assert r4.json()["decision"]["needs_enrichment"] is False
        c4 = client.post("/api/confirm", json={"pending_id": r4.json()["pending_write"]["id"]})
        assert c4.json()["enrichment_queued"] is False
        assert c4.json()["entry"]["enrichment_status"] == "none"


def test_relative_date_and_markdown_helpers() -> None:
    wed = date(2026, 11, 25)  # Wednesday
    assert resolve_relative_date("Call Sarah tomorrow", wed) == "2026-11-26"
    assert resolve_relative_date("Buy a gift this Friday", wed) == "2026-11-27"
    assert resolve_relative_date("Buy a gift next Friday", wed) == "2026-12-04"
    assert resolve_relative_date("Follow up in 3 days", wed) == "2026-11-28"
    assert strip_markdown("Latest is **Seinfeld** ([acquired.fm](https://www.acquired.fm/episodes/seinfeld)).") == (
        "Latest is Seinfeld (acquired.fm)."
    )


@pytest.mark.asyncio
async def test_live_jev_untracked_holding_never_hijacked_by_active_ticker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies live Jev parsing handles `untracked` holding and `needs_enrichment` cleanly."""
    router = JevRouter(Settings(cockpit_mock_mode=False, openrouter_api_key="sk-or-test"))

    class FakeResp:
        is_error = False
        status_code = 200
        text = ""

        def json(self) -> dict:
            return {
                "model": "typesafe/jev-1.14",
                "answers": {
                    "mind": {"type": "choice", "choice": "work", "confidence": 0.97},
                    "action": {"type": "choice", "choice": "earnings_preview", "confidence": 0.96},
                    "holding": {"type": "choice", "choice": "untracked", "confidence": 0.94},
                    "peer_holding": {"type": "choice", "choice": "none", "confidence": 0.99},
                    "needs_llm_worker": {"type": "noul", "probability": 0.91},
                    "needs_enrichment": {"type": "noul", "probability": 0.05},
                },
                "usage": {"cost": 0.00005},
            }

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResp()

    monkeypatch.setattr("app.services.jev_router.httpx.AsyncClient", FakeClient)

    decision = await router.route_utterance(
        "Can you please preview Alphabet earnings?",
        available_tickers=["NVDA", "MSFT", "TSM"],
        available_holdings={"NVDA": "NVIDIA Corp.", "MSFT": "Microsoft Corp.", "TSM": "TSMC"},
        active_ticker="NVDA",
    )
    assert decision.mind == Mind.WORK
    assert decision.action == ActionType.EARNINGS_PREVIEW
    assert decision.holding is None
    assert decision.requested_company == "Alphabet (GOOGL)"
    assert decision.needs_llm_worker is True


def test_legacy_sqlite_schema_migrates_cleanly_for_enrichment_columns(tmp_path: Path) -> None:
    """Pre-existing cockpit.db files without enrichment columns upgrade idempotently without data loss."""
    db_file = tmp_path / "legacy_schema.db"
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        CREATE TABLE journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mind TEXT NOT NULL,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            ticker TEXT,
            source_citation TEXT,
            notion_page_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE pending_writes (
            id TEXT PRIMARY KEY,
            mind TEXT NOT NULL,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            ticker TEXT,
            source_citation TEXT,
            updates_next_check INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        INSERT INTO journal_entries (mind, kind, content, created_at)
        VALUES ('personal', 'task', 'Legacy saved task', '2026-11-24T12:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    db = CockpitDB(db_file)
    entries = db.list_journal_entries()
    assert len(entries) == 1
    assert entries[0].content == "Legacy saved task"
    assert entries[0].enrichment_status == "none"
