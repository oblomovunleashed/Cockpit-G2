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
        # 1. Fast path: Interests note (NO blocking LLM worker)
        r1 = client.post("/api/text", json={"transcript": "I enjoyed the latest Acquired podcast"})
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["decision"]["mind"] == "interests"
        assert d1["decision"]["action"] == "log_note"
        assert d1["decision"]["needs_llm_worker"] is False
        assert d1["pending_write"] is not None

        c1 = client.post("/api/confirm", json={"pending_id": d1["pending_write"]["id"]})
        assert c1.status_code == 200
        assert c1.json()["entry"]["mind"] == "interests"

        # 2. Fast path: Personal task (NO blocking LLM worker)
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

        # 3. Deep path: Work earnings review on #1 holding Natera wakes the LLM worker
        r3 = client.post("/api/text", json={"transcript": "Can we review Natera earnings?"})
        assert r3.status_code == 200
        d3 = r3.json()
        assert d3["decision"]["mind"] == "work"
        assert d3["decision"]["action"] == "brief_holding"
        assert d3["decision"]["needs_llm_worker"] is True
        assert d3["holding"]["ticker"] == "NTRA"
        assert "63.2%" in d3["hud"]["body"]


def test_one_release_two_different_glances_and_journal_persistence(tmp_path: Path) -> None:
    """
    Core build-week end-to-end test on Natera (NTRA, 29.22% portfolio weight):
    1. Default NTRA gross-margin question surfaces Q3 2026 ex-true-up GM 63.2% (+140 bps QoQ).
    2. Follow-up retrieves Q3 call passage on Signatera ASP (~$1,290) and positive cash flow ($1.10B).
    3. Spoken follow-up check is confirmed and persists in the research journal.
    4. Changing NTRA's saved question to Signatera volume/guidance flips the card to $794.0M (+34% YoY)
       and FY26 guidance raised $140M to $3.005-$3.035B.
    """
    with make_client(tmp_path) as client:
        # Step 1: Margin question card
        r_margin = client.post("/api/text", json={"transcript": "Review Natera earnings"})
        assert r_margin.status_code == 200
        body_margin = r_margin.json()["hud"]["body"]
        assert "63.2%" in body_margin
        assert "$32.3M" in body_margin

        # Step 2: Follow-up on Signatera ASP & cash flow retrieves Q3 call passage
        r_follow = client.post(
            "/api/text",
            json={
                "transcript": "What drove Signatera ASP and cash flow?",
                "active_ticker": "NTRA",
            },
        )
        assert r_follow.status_code == 200
        follow_data = r_follow.json()
        assert follow_data["decision"]["action"] == "follow_up_source"
        assert "$1,290" in follow_data["hud"]["body"]
        assert "NTRA Q3 2026 Call" in follow_data["hud"]["body"]

        # Step 3: Dictate next-quarter check and tap to save to research journal
        spoken_check = "At the next call, check whether Signatera ASP holds above $1,290."
        r_draft = client.post(
            "/api/text",
            json={"transcript": spoken_check, "active_ticker": "NTRA"},
        )
        assert r_draft.status_code == 200
        pending = r_draft.json()["pending_write"]
        assert pending is not None
        assert pending["mind"] == "work"

        r_save = client.post("/api/confirm", json={"pending_id": pending["id"]})
        assert r_save.status_code == 200
        assert r_save.json()["holding"]["next_check"] == spoken_check

        journal = client.get("/api/journal", params={"mind": "work"}).json()
        assert any("Signatera ASP holds above $1,290" in item["content"] for item in journal)

        # Step 4: Flip saved question -> same Natera Q3 release produces growth/guidance card instead
        r_flip = client.patch(
            "/api/holdings/NTRA/question",
            json={"saved_question": "Is Signatera volume and revenue running ahead of full-year guidance?"},
        )
        assert r_flip.status_code == 200
        flipped = r_flip.json()
        assert "$794.0M" in flipped["holding"]["latest_finding"]
        assert "$3.005-$3.035B" in flipped["holding"]["latest_finding"]
        assert "$794.0M" in flipped["hud"]["body"]
        assert flipped["holding"]["verdict"] == "AHEAD"
