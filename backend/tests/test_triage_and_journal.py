from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        cockpit_mock_mode=True,
        cockpit_db_path=str(tmp_path / "test_cockpit.db"),
        openrouter_api_key="",
        notion_token="",
    )
    return TestClient(create_app(settings))


def test_logged_logged_then_llm_worker_flow(tmp_path: Path) -> None:
    """Verifies the signature 'logged, logged -> llm worker' System-1 triage flow."""
    with make_client(tmp_path) as client:
        # 1. Fast path: Interests note (NO LLM worker)
        r1 = client.post("/api/text", json={"transcript": "This podcast on TSMC was great"})
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["decision"]["mind"] == "interests"
        assert d1["decision"]["action"] == "log_note"
        assert d1["decision"]["needs_llm_worker"] is False
        assert d1["pending_write"] is not None

        c1 = client.post("/api/confirm", json={"pending_id": d1["pending_write"]["id"]})
        assert c1.status_code == 200
        assert c1.json()["entry"]["mind"] == "interests"

        # 2. Fast path: Personal task (NO LLM worker)
        r2 = client.post("/api/text", json={"transcript": "Remember to buy a birthday present"})
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["decision"]["mind"] == "personal"
        assert d2["decision"]["action"] == "log_task"
        assert d2["decision"]["needs_llm_worker"] is False
        assert d2["pending_write"] is not None

        c2 = client.post("/api/confirm", json={"pending_id": d2["pending_write"]["id"]})
        assert c2.status_code == 200
        assert c2.json()["entry"]["mind"] == "personal"

        # 3. Deep path: Work earnings review wakes the LLM worker
        r3 = client.post("/api/text", json={"transcript": "Can we review NVIDIA earnings?"})
        assert r3.status_code == 200
        d3 = r3.json()
        assert d3["decision"]["mind"] == "work"
        assert d3["decision"]["action"] == "brief_holding"
        assert d3["decision"]["needs_llm_worker"] is True
        assert d3["holding"]["ticker"] == "NVDA"
        assert "71-72%" in d3["hud"]["body"]


def test_one_release_two_different_glances_and_journal_persistence(tmp_path: Path) -> None:
    """
    Core build-week end-to-end test:
    1. Default NVDA margin question surfaces Q4 FY27 gross margin guide (71-72%).
    2. Follow-up retrieves August call p. 7 (Q1 FY28 price increases).
    3. Spoken follow-up check is confirmed and persists in the research journal.
    4. Changing NVDA's saved question to revenue flips the first card to $115.8bn (+7% vs $108bn).
    """
    with make_client(tmp_path) as client:
        # Step 1: Margin question card
        r_margin = client.post("/api/text", json={"transcript": "Review NVIDIA earnings"})
        assert r_margin.status_code == 200
        body_margin = r_margin.json()["hud"]["body"]
        assert "71-72%" in body_margin

        # Step 2: Follow-up on margin recovery retrieves August call p. 7
        r_follow = client.post(
            "/api/text",
            json={
                "transcript": "What did management expect to bring margins back up?",
                "active_ticker": "NVDA",
            },
        )
        assert r_follow.status_code == 200
        follow_data = r_follow.json()
        assert follow_data["decision"]["action"] == "follow_up_source"
        assert "Q1 FY28" in follow_data["hud"]["body"]
        assert "p. 7" in follow_data["hud"]["body"]

        # Step 3: Dictate next-quarter check and tap to save to research journal
        spoken_check = "At the next call, check whether those price increases are on schedule."
        r_draft = client.post(
            "/api/text",
            json={"transcript": spoken_check, "active_ticker": "NVDA"},
        )
        assert r_draft.status_code == 200
        pending = r_draft.json()["pending_write"]
        assert pending is not None
        assert pending["mind"] == "work"

        r_save = client.post("/api/confirm", json={"pending_id": pending["id"]})
        assert r_save.status_code == 200
        assert r_save.json()["holding"]["next_check"] == spoken_check

        journal = client.get("/api/journal", params={"mind": "work"}).json()
        assert any("price increases are on schedule" in item["content"] for item in journal)

        # Step 4: Flip saved question -> same NVIDIA Q3 release produces revenue card instead
        r_flip = client.patch(
            "/api/holdings/NVDA/question",
            json={"saved_question": "Is revenue running ahead of management's guidance?"},
        )
        assert r_flip.status_code == 200
        flipped = r_flip.json()
        assert "$115.8bn" in flipped["holding"]["latest_finding"]
        assert "$108bn" in flipped["holding"]["latest_finding"]
        assert "$115.8bn" in flipped["hud"]["body"]
        assert flipped["holding"]["verdict"] == "AHEAD"
