from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


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
