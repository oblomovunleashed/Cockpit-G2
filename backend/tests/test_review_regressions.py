from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import HoldingRecord, Verdict
from app.services.notion_sync import NotionHoldingSnapshot, NotionSync


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

    async def fake_pull_holdings():
        return {}

    async def fake_sync_holding(holding, **_kwargs) -> str:
        seen_page_ids.append(holding.notion_page_id)
        return holding.notion_page_id or "watchlist-nvda-1"

    runtime.notion.push_journal_entry = fake_push_journal  # type: ignore[method-assign]
    runtime.notion.pull_holdings = fake_pull_holdings  # type: ignore[method-assign]
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


def test_followup_never_stages_unseen_pending_write(tmp_path: Path) -> None:
    """
    Follow-up explanation cards must NOT silently stage a pending write whose text
    was never shown to the wearer. Staging must return a CONFIRM card showing the exact wording.
    """
    with TestClient(create_app(_make_settings(tmp_path))) as client:
        follow = client.post(
            "/api/text",
            json={
                "transcript": "What did management expect to bring margins back up?",
                "active_ticker": "NVDA",
            },
        )
        assert follow.status_code == 200
        data = follow.json()
        assert data["decision"]["action"] == "follow_up_source"
        assert "Q1 FY28" in data["hud"]["body"]
        # Nothing silently staged while showing the explanation card
        assert data["pending_write"] is None
        assert data["suggested_check"] is not None
        proposed_text = data["suggested_check"]["content"]
        assert "price increases" in proposed_text

        # Explicitly stage the proposed check -> glasses display the exact wording before confirm
        staged = client.post("/api/stage-check", json=data["suggested_check"])
        assert staged.status_code == 200
        staged_payload = staged.json()
        assert staged_payload["hud"]["header"] == "WORK · NVDA · CONFIRM CHECK"
        assert staged_payload["hud"]["body"] == proposed_text
        assert staged_payload["pending_write"]["content"] == proposed_text

        # Confirm saves the exact text shown on the CONFIRM card
        confirmed = client.post(
            "/api/confirm",
            json={"pending_id": staged_payload["pending_write"]["id"]},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["entry"]["content"] == proposed_text


def test_unsupported_question_returns_no_matching_passage(tmp_path: Path) -> None:
    """
    Questions unsupported by indexed filings (e.g. 'Warranty reserves?') must return a
    'No matching passage' card with verdict and suggested_next_check suppressed.
    """
    with TestClient(create_app(_make_settings(tmp_path))) as client:
        for question in ("Warranty reserves?", "What are NVIDIA's warranty reserves?"):
            resp = client.patch(
                "/api/holdings/NVDA/question",
                json={"saved_question": question},
            )
            assert resp.status_code == 200
            payload = resp.json()
            holding = payload["holding"]
            hud = payload["hud"]

            assert holding["verdict"] is None
            assert "No matching passage" in holding["latest_finding"]
            assert holding["source_citation"] == ""
            assert holding["suggested_next_check"] == ""
            assert "HELD" not in hud["header"]
            assert "No matching passage" in hud["body"]

            # Attempting to stage a suggested check when unsupported must be rejected
            stage_resp = client.post("/api/holdings/NVDA/stage-suggested-check")
            assert stage_resp.status_code == 409


def test_opening_briefing_preserves_and_uses_newer_notion_edit(tmp_path: Path) -> None:
    """
    Opening a briefing when Notion is enabled must pull the latest Notion Saved Question
    first and must NOT overwrite user-edited Saved Question or Next Check fields on PATCH.
    """
    app = create_app(_make_settings(tmp_path, with_notion=True))
    runtime = app.state.runtime
    runtime.db.update_holding_notion_page_id("NVDA", "watchlist-nvda-1")

    sync_calls: list[dict[str, object]] = []

    async def fake_pull_holdings():
        # Wearer edited NVDA's Saved Question and Next Check directly in Notion
        return {
            "NVDA": NotionHoldingSnapshot(
                ticker="NVDA",
                saved_question="Is revenue running ahead of management's guidance?",
                next_check="Check Q4 data center backlog in Notion",
                notion_page_id="watchlist-nvda-1",
            )
        }

    async def fake_sync_holding(
        holding,
        *,
        include_finding: bool = True,
        include_saved_question: bool = False,
        include_next_check: bool = False,
    ) -> str:
        sync_calls.append(
            {
                "ticker": holding.ticker,
                "saved_question": holding.saved_question,
                "next_check": holding.next_check,
                "include_finding": include_finding,
                "include_saved_question": include_saved_question,
                "include_next_check": include_next_check,
            }
        )
        return holding.notion_page_id or "watchlist-nvda-1"

    runtime.notion.pull_holdings = fake_pull_holdings  # type: ignore[method-assign]
    runtime.notion.sync_holding_update = fake_sync_holding  # type: ignore[method-assign]

    with TestClient(app) as client:
        brief = client.post("/api/text", json={"transcript": "Can we review NVIDIA earnings?"})
        assert brief.status_code == 200
        payload = brief.json()

        # Recomputed using the newer Notion question ($115.8bn revenue card, not stale margin card)
        assert payload["holding"]["saved_question"] == "Is revenue running ahead of management's guidance?"
        assert payload["holding"]["next_check"] == "Check Q4 data center backlog in Notion"
        assert "$115.8bn" in payload["hud"]["body"]
        assert payload["holding"]["verdict"] == "AHEAD"

        # Sync only patches computed finding fields, never clobbering Saved Question or Next Check
        assert len(sync_calls) == 1
        assert sync_calls[0]["include_finding"] is True
        assert sync_calls[0]["include_saved_question"] is False
        assert sync_calls[0]["include_next_check"] is False


@pytest.mark.asyncio
async def test_notion_recompute_patch_omits_saved_question_and_next_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wire-level verification that PATCH during recompute never sends Saved Question or Next Check."""
    notion = NotionSync(_make_settings(tmp_path, with_notion=True))
    captured: list[tuple[str, str, dict]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"id": "watchlist-nvda-1"}

    class FakeAsyncClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def patch(self, url: str, *, headers: dict, json: dict):
            captured.append(("PATCH", url, json))
            return FakeResponse()

    monkeypatch.setattr("app.services.notion_sync.httpx.AsyncClient", FakeAsyncClient)

    holding = HoldingRecord(
        ticker="NVDA",
        company="NVIDIA Corp.",
        saved_question="Stale local question",
        verdict=Verdict.AHEAD,
        latest_finding="Q3 FY27 revenue · $115.8bn.",
        source_citation="Q3 FY27 PR · Nov 17",
        next_check="Stale local next check",
        notion_page_id="watchlist-nvda-1",
    )

    page_id = await notion.sync_holding_update(
        holding,
        include_finding=True,
        include_saved_question=False,
        include_next_check=False,
    )
    assert page_id == "watchlist-nvda-1"
    assert len(captured) == 1
    method, url, body = captured[0]
    assert method == "PATCH"
    assert url.endswith("/pages/watchlist-nvda-1")
    props = body["properties"]
    assert set(props.keys()) == {"Status", "Latest Finding", "Source"}
    assert "Saved Question" not in props
    assert "Next Check" not in props
