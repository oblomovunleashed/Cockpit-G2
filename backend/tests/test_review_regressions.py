from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _make_settings(tmp_path: Path, *, with_notion: bool = False) -> Settings:
    return Settings(
        cockpit_mock_mode=True,
        cockpit_db_path=str(tmp_path / "regression.db"),
        openrouter_api_key="",
        notion_token="secret-test-token" if with_notion else "",
        notion_log_database_id="log-db-id" if with_notion else "",
        notion_holdings_database_id="holdings-db-id" if with_notion else "",
    )


@pytest.mark.asyncio
async def test_concurrent_confirmations_consume_pending_write_atomically(tmp_path: Path) -> None:
    """Two overlapping confirmations of the same pending_id must create only one journal & Notion entry."""
    app = create_app(_make_settings(tmp_path, with_notion=True))
    runtime = app.state.runtime

    notion_calls = 0

    async def slow_push_journal(_entry) -> str:
        nonlocal notion_calls
        notion_calls += 1
        await asyncio.sleep(0.05)
        return f"notion-journal-{notion_calls}"

    runtime.notion.push_journal_entry = slow_push_journal  # type: ignore[method-assign]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        staged = await client.post(
            "/api/text",
            json={"transcript": "Remember to buy a birthday present", "active_ticker": "NVDA"},
        )
        assert staged.status_code == 200
        pending_id = staged.json()["pending_write"]["id"]

        r1, r2 = await asyncio.gather(
            client.post("/api/confirm", json={"pending_id": pending_id}),
            client.post("/api/confirm", json={"pending_id": pending_id}),
        )

    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 404]
    assert notion_calls == 1

    entries = runtime.db.list_journal_entries()
    assert len(entries) == 1
    assert entries[0].notion_page_id == "notion-journal-1"


def test_long_transcripts_preserved_in_pending_and_confirmed_writes(tmp_path: Path) -> None:
    """Transcripts longer than 200 chars are clipped only for HUD rendering, never in persistence."""
    long_note = (
        "This podcast on TSMC was great: "
        + " ".join(f"detail{i}" for i in range(45))
    )
    assert len(long_note) > 260

    long_check = (
        "At the next call, check whether those Q1 FY28 price increases are on schedule and "
        + " ".join(f"verify{i}" for i in range(40))
    )
    assert len(long_check) > 260

    with TestClient(create_app(_make_settings(tmp_path))) as client:
        # 1. Fast-path note
        r_note = client.post("/api/text", json={"transcript": long_note, "active_ticker": "NVDA"})
        assert r_note.status_code == 200
        note_payload = r_note.json()
        assert note_payload["pending_write"]["content"] == long_note
        assert len(note_payload["hud"]["body"]) <= 240

        c_note = client.post("/api/confirm", json={"pending_id": note_payload["pending_write"]["id"]})
        assert c_note.status_code == 200
        assert c_note.json()["entry"]["content"] == long_note

        # 2. Spoken Work next-quarter check
        r_check = client.post("/api/text", json={"transcript": long_check, "active_ticker": "NVDA"})
        assert r_check.status_code == 200
        check_payload = r_check.json()
        assert check_payload["pending_write"]["content"] == long_check
        assert len(check_payload["hud"]["body"]) <= 240

        c_check = client.post("/api/confirm", json={"pending_id": check_payload["pending_write"]["id"]})
        assert c_check.status_code == 200
        assert c_check.json()["entry"]["content"] == long_check
        assert c_check.json()["holding"]["next_check"] == long_check


def test_active_ticker_context_does_not_force_unrelated_note_into_work(tmp_path: Path) -> None:
    """Unmatched utterances with active_ticker='NVDA' must still route to default note capture."""
    with TestClient(create_app(_make_settings(tmp_path))) as client:
        r_idea = client.post(
            "/api/text",
            json={
                "transcript": "An idea: build a solar-powered boat",
                "active_ticker": "NVDA",
            },
        )
        assert r_idea.status_code == 200
        idea = r_idea.json()
        assert idea["decision"]["mind"] == "interests"
        assert idea["decision"]["action"] == "log_note"
        assert idea["decision"]["needs_llm_worker"] is False
        assert idea["decision"]["holding"] is None

        # Explicit Work intent still resolves active_ticker context
        r_work = client.post(
            "/api/text",
            json={"transcript": "What changed?", "active_ticker": "NVDA"},
        )
        assert r_work.status_code == 200
        work = r_work.json()
        assert work["decision"]["mind"] == "work"
        assert work["decision"]["action"] == "brief_holding"
        assert work["decision"]["holding"] == "NVDA"


def test_watchlist_notion_page_id_persisted_across_confirmations(tmp_path: Path) -> None:
    """Confirming a holding check must persist the created Notion watchlist page ID to avoid duplicates."""
    app = create_app(_make_settings(tmp_path, with_notion=True))
    runtime = app.state.runtime

    seen_page_ids: list[str | None] = []

    async def fake_push_journal(_entry) -> str:
        return "log-page-1"

    async def fake_sync_holding(holding) -> str:
        seen_page_ids.append(holding.notion_page_id)
        return holding.notion_page_id or "watchlist-nvda-1"

    runtime.notion.push_journal_entry = fake_push_journal  # type: ignore[method-assign]
    runtime.notion.sync_holding_update = fake_sync_holding  # type: ignore[method-assign]

    with TestClient(app) as client:
        # 1st confirmed check creates watchlist page and persists its ID
        s1 = client.post("/api/holdings/NVDA/stage-suggested-check")
        assert s1.status_code == 200
        c1 = client.post("/api/confirm", json={"pending_id": s1.json()["pending_write"]["id"]})
        assert c1.status_code == 200
        assert c1.json()["holding"]["notion_page_id"] == "watchlist-nvda-1"
        assert runtime.db.get_holding("NVDA").notion_page_id == "watchlist-nvda-1"

        # 2nd confirmed check reuses persisted watchlist page ID instead of creating a duplicate
        s2 = client.post("/api/holdings/NVDA/stage-suggested-check")
        assert s2.status_code == 200
        c2 = client.post("/api/confirm", json={"pending_id": s2.json()["pending_write"]["id"]})
        assert c2.status_code == 200
        assert seen_page_ids == [None, "watchlist-nvda-1"]
