from __future__ import annotations

from datetime import date
from pathlib import Path
import sqlite3
import time
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import CockpitDB
from app.main import create_app
from app.models import ActionType, JournalEntry, Mind
from app.portfolio import PORTFOLIO_POSITIONS, match_portfolio_position_in_text
from app.services.jev_router import JevRouter
from app.services.llm_worker import LLMWorker, resolve_relative_date, strip_markdown


def _make_settings(tmp_path: Path, *, with_notion: bool = False) -> Settings:
    return Settings(
        cockpit_mock_mode=True,
        cockpit_db_path=str(tmp_path / "intel.db"),
        openrouter_api_key="",
        notion_token="secret-test" if with_notion else "",
        notion_log_database_id="log-db-id" if with_notion else "",
        notion_holdings_database_id="holdings-db-id" if with_notion else "",
    )


def test_full_61_position_portfolio_registry_weights_and_safe_matching() -> None:
    """Verifies exact 61-holding portfolio weights (100.00%) and safe 1-letter ticker matching."""
    assert len(PORTFOLIO_POSITIONS) == 61
    assert round(sum(p.weight_pct for p in PORTFOLIO_POSITIONS), 2) == 100.00
    assert PORTFOLIO_POSITIONS[0].ticker == "NTRA"
    assert PORTFOLIO_POSITIONS[0].weight_pct == 29.22
    assert PORTFOLIO_POSITIONS[1].ticker == "STX"
    assert PORTFOLIO_POSITIONS[2].ticker == "AMZN"

    # Common words containing 'u', 'b', 'app', 'sky', 'se' must NOT false-match case-sensitive tickers
    assert match_portfolio_position_in_text("Can u buy a birthday gift for this app in the sky?") is None
    # Explicit company names and cashtags resolve cleanly
    assert match_portfolio_position_in_text("Preview Unity Software earnings").ticker == "U"  # type: ignore[union-attr]
    assert match_portfolio_position_in_text("Review Barrick Mining earnings").ticker == "B"  # type: ignore[union-attr]
    assert match_portfolio_position_in_text("Preview Sea Limited earnings").ticker == "SE"  # type: ignore[union-attr]
    assert match_portfolio_position_in_text("Preview Insmed earnings").ticker == "INSM"  # type: ignore[union-attr]


def test_portfolio_and_external_web_preview_never_default_to_core_holding(tmp_path: Path) -> None:
    """
    Previewing another portfolio holding ('Insmed' -> 4.2% badge) or an external company ('Alphabet' -> no badge)
    while viewing NTRA must route to web preview/review and NEVER silently default to NTRA.
    """
    with TestClient(create_app(_make_settings(tmp_path))) as client:
        # 1. Held non-indexed portfolio holding (Insmed · 4.19% weight)
        r_insm = client.post(
            "/api/text",
            json={
                "transcript": "Can you please preview Insmed earnings?",
                "active_ticker": "NTRA",
            },
        )
        assert r_insm.status_code == 200
        insm = r_insm.json()
        assert insm["decision"]["mind"] == "work"
        assert insm["decision"]["action"] == "earnings_preview"
        assert insm["decision"]["holding"] is None
        assert "Insmed" in (insm["decision"]["requested_company"] or "")
        assert insm["decision"]["needs_llm_worker"] is True
        assert insm["hud"]["header"] == "PREVIEW · INSM (4.2%) · WEB"
        assert "4.2%" in insm["hud"]["body"]
        assert "Natera" not in insm["hud"]["body"]

        # 2. External non-held company (Alphabet -> no portfolio weight badge)
        r_preview = client.post(
            "/api/text",
            json={
                "transcript": "Can you please preview Alphabet earnings?",
                "active_ticker": "NTRA",
            },
        )
        assert r_preview.status_code == 200
        preview = r_preview.json()
        assert preview["decision"]["action"] == "earnings_preview"
        assert preview["decision"]["holding"] is None
        assert preview["hud"]["header"] == "PREVIEW · GOOGL · WEB"
        assert "Alphabet" in preview["hud"]["body"]
        assert preview["suggested_check"]["ticker"] == "GOOGL"

        # Staging & confirming watchpoint for non-indexed ticker works cleanly
        staged = client.post("/api/stage-check", json=preview["suggested_check"])
        assert staged.status_code == 200
        pending = staged.json()["pending_write"]
        assert pending["ticker"] == "GOOGL"
        assert pending["updates_next_check"] is False

        confirmed = client.post("/api/confirm", json={"pending_id": pending["id"]})
        assert confirmed.status_code == 200
        assert confirmed.json()["entry"]["ticker"] == "GOOGL"
        assert confirmed.json()["holding"] is None

        # 3. Review another portfolio holding (Sea Limited · 3.47% -> 3.5%)
        r_se = client.post(
            "/api/text",
            json={
                "transcript": "Can you review Sea Limited earnings?",
                "active_ticker": "NTRA",
            },
        )
        assert r_se.status_code == 200
        se = r_se.json()
        assert se["decision"]["action"] == "brief_holding"
        assert se["decision"]["holding"] is None
        assert se["hud"]["header"] == "WORK · SE (3.5%) · WEB"


def test_reclassify_pending_write_fixes_misclassification_in_place(tmp_path: Path) -> None:
    """Swiping on CONFIRM card reclassifies Mind/Kind via PATCH /api/pending/{id} without re-speaking."""
    with TestClient(create_app(_make_settings(tmp_path))) as client:
        staged = client.post(
            "/api/text",
            json={"transcript": "Check Ben Thompson's latest Stratechery article", "active_ticker": "NTRA"},
        )
        assert staged.status_code == 200
        pending = staged.json()["pending_write"]
        assert pending["mind"] == "interests"
        assert pending["kind"] == "note"

        r_personal = client.patch(
            f"/api/pending/{pending['id']}",
            json={"mind": "personal", "kind": "task"},
        )
        assert r_personal.status_code == 200
        assert r_personal.json()["pending_write"]["mind"] == "personal"
        assert r_personal.json()["pending_write"]["kind"] == "task"
        assert "PERSONAL · CONFIRM TASK" in r_personal.json()["hud"]["header"]

        r_work = client.patch(
            f"/api/pending/{pending['id']}",
            json={"mind": "work", "kind": "check", "ticker": "NTRA"},
        )
        assert r_work.status_code == 200
        work_pending = r_work.json()["pending_write"]
        assert work_pending["mind"] == "work"
        assert work_pending["ticker"] == "NTRA"
        assert work_pending["updates_next_check"] is True
        assert work_pending["needs_enrichment"] is False

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
    runtime.db.update_holding_notion_page_id("NTRA", "watchlist-ntra-1")
    runtime.db.update_holding_next_check("NTRA", "Original check before edit")

    archived_pages: list[str] = []
    synced_next_checks: list[str] = []

    async def fake_push_journal(_entry) -> str:
        return "notion-entry-42"

    async def fake_archive_page(page_id: str) -> bool:
        archived_pages.append(page_id)
        return True

    async def fake_sync_holding(holding, **_kwargs) -> str:
        synced_next_checks.append(holding.next_check)
        return holding.notion_page_id or "watchlist-ntra-1"

    runtime.notion.push_journal_entry = fake_push_journal  # type: ignore[method-assign]
    runtime.notion.archive_page = fake_archive_page  # type: ignore[method-assign]
    runtime.notion.sync_holding_update = fake_sync_holding  # type: ignore[method-assign]

    with TestClient(app) as client:
        draft = client.post(
            "/api/text",
            json={
                "transcript": "At the next call, check whether Signatera ASP holds above $1,290",
                "active_ticker": "NTRA",
            },
        )
        pending_id = draft.json()["pending_write"]["id"]
        saved = client.post("/api/confirm", json={"pending_id": pending_id})
        assert saved.status_code == 200
        entry_id = saved.json()["entry"]["id"]
        assert saved.json()["holding"]["next_check"] == "At the next call, check whether Signatera ASP holds above $1,290"

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
        r1 = client.post("/api/text", json={"transcript": "I enjoyed the latest Acquired podcast"})
        assert r1.json()["decision"]["needs_enrichment"] is True
        c1 = client.post("/api/confirm", json={"pending_id": r1.json()["pending_write"]["id"]})
        assert c1.json()["enrichment_queued"] is True
        e1 = _wait_for_enrichment(client, c1.json()["entry"]["id"])
        assert e1["enrichment_status"] == "enriched"
        assert "Acquired" in e1["enriched_summary"]
        assert e1["enriched_url"].startswith("https://")
        assert any(p[1] == "enriched" for p in patched_enrichments)

        r2 = client.post(
            "/api/text",
            json={"transcript": "I enjoy the latest, and that's the best podcast"},
        )
        c2 = client.post("/api/confirm", json={"pending_id": r2.json()["pending_write"]["id"]})
        assert c2.json()["enrichment_queued"] is True
        e2 = _wait_for_enrichment(client, c2.json()["entry"]["id"])
        assert e2["enrichment_status"] == "needs_detail"
        assert "Podcast name not specified" in e2["enriched_summary"]

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

        r4 = client.post("/api/text", json={"transcript": "Remember to buy a birthday present"})
        assert r4.json()["decision"]["needs_enrichment"] is False
        c4 = client.post("/api/confirm", json={"pending_id": r4.json()["pending_write"]["id"]})
        assert c4.json()["enrichment_queued"] is False
        assert c4.json()["entry"]["enrichment_status"] == "none"


def test_relative_date_and_markdown_helpers() -> None:
    wed = date(2026, 11, 25)
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
        "Can you please preview Insmed earnings?",
        available_tickers=["NTRA", "STX", "AMZN"],
        available_holdings={"NTRA": "Natera, Inc.", "STX": "Seagate Technology", "AMZN": "Amazon.com"},
        active_ticker="NTRA",
    )
    assert decision.mind == Mind.WORK
    assert decision.action == ActionType.EARNINGS_PREVIEW
    assert decision.holding is None
    assert decision.requested_company == "Insmed Incorporated (INSM)"
    assert decision.needs_llm_worker is True


def test_legacy_sqlite_schema_migrates_cleanly_for_enrichment_columns(tmp_path: Path) -> None:
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


def test_implicit_work_query_from_home_asks_which_company_instead_of_defaulting_to_core_holding(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_make_settings(tmp_path))) as client:
        r_preview = client.post(
            "/api/text",
            json={"transcript": "Preview earnings", "active_ticker": None},
        )
        assert r_preview.status_code == 200
        assert r_preview.json()["holding"] is None
        assert r_preview.json()["hud"]["header"] == "WORK · WHICH COMPANY?"

        r_review = client.post(
            "/api/text",
            json={"transcript": "What changed?", "active_ticker": None},
        )
        assert r_review.status_code == 200
        assert r_review.json()["holding"] is None
        assert r_review.json()["hud"]["header"] == "WORK · WHICH COMPANY?"


def test_documented_abstention_phrase_works_from_home_in_mock_mode(tmp_path: Path) -> None:
    """The documented mock-mode abstention example must route to NTRA follow-up and return NO MATCH."""
    with TestClient(create_app(_make_settings(tmp_path))) as client:
        resp = client.post(
            "/api/text",
            json={
                "transcript": "What did management say about Natera warranty reserves?",
                "active_ticker": None,
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["decision"]["mind"] == "work"
        assert payload["decision"]["action"] == "follow_up_source"
        assert payload["decision"]["holding"] == "NTRA"
        assert payload["hud"]["header"] == "WORK · NTRA · NO MATCH"
        assert "No matching passage" in payload["hud"]["body"]
        assert payload["pending_write"] is None
        assert payload["suggested_check"] is None


@pytest.mark.asyncio
async def test_enrichment_rejects_unverified_model_url_not_in_provider_annotations() -> None:
    """Live enrichment must only publish URLs backed by OpenRouter url_citation annotations."""
    worker = LLMWorker(Settings(cockpit_mock_mode=False, openrouter_api_key="sk-or-test"))
    entry = JournalEntry(
        id=101,
        mind=Mind.INTERESTS,
        kind="note",
        content="I enjoyed the latest Acquired podcast",
        created_at="2026-11-25T10:00:00Z",
    )

    async def fake_with_provider_citation(*_args, **_kwargs):
        return (
            {
                "resolved": True,
                "needs_detail": False,
                "enriched_summary": "Latest Acquired episode is Seinfeld.",
                "url": "https://evil-or-invented.example/phish",
                "due_date": None,
            },
            ["https://www.acquired.fm/episodes/seinfeld"],
        )

    worker._call_json_llm = fake_with_provider_citation  # type: ignore[method-assign]
    res1 = await worker.enrich_journal_entry(entry)
    assert res1.status == "enriched"
    assert res1.enriched_url == "https://www.acquired.fm/episodes/seinfeld"

    async def fake_without_annotations(*_args, **_kwargs):
        return (
            {
                "resolved": True,
                "needs_detail": False,
                "enriched_summary": "Latest Acquired episode is Seinfeld.",
                "url": "https://unverified.example/made-up",
                "due_date": None,
            },
            [],
        )

    worker._call_json_llm = fake_without_annotations  # type: ignore[method-assign]
    res2 = await worker.enrich_journal_entry(entry)
    assert res2.status == "enriched"
    assert res2.enriched_url == ""
