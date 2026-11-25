from __future__ import annotations

from pathlib import Path
import pytest

from app.config import Settings
from app.models import ActionType, DocumentChunk, Mind, Verdict
from app.scripts.setup_notion import (
    build_cockpit_log_database_payload,
    build_holdings_database_payload,
    normalize_notion_id,
    update_env_file,
)
from app.services.jev_router import JevRouter
from app.services.openrouter_stt import MAX_AUDIO_BYTES, OpenRouterSTT


@pytest.mark.asyncio
async def test_jev_router_parses_live_openrouter_probability_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies real OpenRouter /api/alpha/decisions response fields (`probability`, `usage.cost`)."""
    settings = Settings(
        cockpit_mock_mode=False,
        openrouter_api_key="sk-or-test",
        openrouter_jev_model="~typesafe/jev-latest",
    )
    router = JevRouter(settings)

    class FakeResp:
        is_error = False
        status_code = 200
        text = ""

        def json(self) -> dict:
            return {
                "model": "typesafe/jev-1.14-20260917",
                "answers": {
                    "mind": {"type": "choice", "choice": "personal", "confidence": 0.96},
                    "action": {"type": "choice", "choice": "log_task", "confidence": 0.95},
                    "holding": {"type": "choice", "choice": "AMZN", "confidence": 0.99},
                    "peer_holding": {"type": "choice", "choice": "AMZN", "confidence": 0.99},
                    "needs_llm_worker": {"type": "noul", "probability": 0.04},
                },
                "usage": {"input_tokens": 140, "output_tokens": 0, "cost": 0.000006},
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
        "Remember to buy a birthday present",
        available_tickers=["NTRA", "STX", "AMZN"],
        active_ticker="NTRA",
    )
    assert decision.mode == "openrouter"
    assert decision.mind == Mind.PERSONAL
    assert decision.action == ActionType.LOG_TASK
    assert decision.needs_llm_worker is False
    assert decision.holding is None
    assert decision.peer_holding is None
    assert "worker_p=0.04" in (decision.reasoning or "")
    assert "$0.000006" in (decision.reasoning or "")


@pytest.mark.asyncio
async def test_jev_live_select_best_chunk_respects_is_answered_probability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When OpenRouter Jev returns noul `probability < 0.5`, select_best_chunk must return None."""
    settings = Settings(
        cockpit_mock_mode=False,
        openrouter_api_key="sk-or-test",
    )
    router = JevRouter(settings)

    class FakeResp:
        is_error = False
        status_code = 200
        text = ""

        def json(self) -> dict:
            return {
                "model": "typesafe/jev-1.14",
                "answers": {
                    "is_answered": {"type": "noul", "probability": 0.11},
                    "best_chunk": {"type": "choice", "choice": "chunk_1", "confidence": 0.4},
                    "verdict": {"type": "choice", "choice": "HELD", "confidence": 0.5},
                },
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

    chunk = DocumentChunk(
        id=1,
        ticker="NTRA",
        doc_title="Q3 2026 Release",
        doc_date="2026-11-05",
        page_ref="Q3 PR",
        topic="margin",
        headline="Margin",
        summary_card="Margin card",
        verdict=Verdict.HELD,
        suggested_next_check="Check ex-true-up gross margin",
        excerpt="Ex-true-up non-GAAP gross margin 63.2%",
    )
    result = await router.select_best_chunk("Warranty reserves?", [chunk])
    assert result is None


@pytest.mark.asyncio
async def test_openrouter_stt_enforces_size_limit() -> None:
    stt = OpenRouterSTT(Settings(cockpit_mock_mode=True))
    with pytest.raises(ValueError, match="MB limit"):
        await stt.transcribe_wav(b"0" * (MAX_AUDIO_BYTES + 1))


def test_notion_setup_helpers_and_env_update(tmp_path: Path) -> None:
    page_url = "https://www.notion.so/Cockpit-1234567890abcdef1234567890abcdef?pvs=4"
    normalized = normalize_notion_id(page_url)
    assert normalized == "12345678-90ab-cdef-1234-567890abcdef"

    log_payload = build_cockpit_log_database_payload(normalized)
    assert set(log_payload["properties"].keys()) == {
        "Entry",
        "Mind",
        "Kind",
        "Ticker",
        "Source",
        "Logged At",
        "Enrichment",
        "Enriched",
        "Due Date",
        "Link",
    }

    hold_payload = build_holdings_database_payload(normalized)
    assert set(hold_payload["properties"].keys()) == {
        "Ticker",
        "Company",
        "Saved Question",
        "Status",
        "Latest Finding",
        "Source",
        "Next Check",
    }

    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=keep-me\nNOTION_LOG_DATABASE_ID=\n", encoding="utf-8")
    update_env_file(
        env_file,
        {
            "NOTION_LOG_DATABASE_ID": "log-123",
            "NOTION_HOLDINGS_DATABASE_ID": "hold-456",
        },
    )
    text = env_file.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY=keep-me" in text
    assert "NOTION_LOG_DATABASE_ID=log-123" in text
    assert "NOTION_HOLDINGS_DATABASE_ID=hold-456" in text


@pytest.mark.asyncio
async def test_smoke_live_exits_nonzero_on_notion_failures_and_passes_on_valid_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Smoke check must exit non-zero when Notion pull returns 0 rows or write returns page_id=None."""
    from app.scripts import smoke_live
    from app.services.notion_sync import NotionHoldingSnapshot

    # Case 1: Unconfigured providers -> exits non-zero
    monkeypatch.setattr(
        smoke_live,
        "Settings",
        lambda: Settings(
            cockpit_db_path=str(tmp_path / "smoke1.db"),
            openrouter_api_key="",
            notion_token="",
            notion_log_database_id="",
            notion_holdings_database_id="",
        ),
    )
    with pytest.raises(SystemExit):
        await smoke_live.run_smoke(test_notion_write=False)

    # Case 2: Notion configured, pull_holdings returns {} (simulated failure) -> exits non-zero, no ✓ on pull
    notion_settings = Settings(
        cockpit_db_path=str(tmp_path / "smoke2.db"),
        openrouter_api_key="",
        notion_token="secret-test",
        notion_log_database_id="log-db-id",
        notion_holdings_database_id="hold-db-id",
    )
    monkeypatch.setattr(smoke_live, "Settings", lambda: notion_settings)

    class FailingPullNotion:
        def __init__(self, _settings: Settings) -> None:
            self.enabled = True
            self.holdings_enabled = True

        async def pull_holdings(self) -> dict:
            return {}

        async def push_journal_entry(self, _entry) -> str | None:
            return None

    monkeypatch.setattr(smoke_live, "NotionSync", FailingPullNotion)
    capsys.readouterr()
    with pytest.raises(SystemExit):
        await smoke_live.run_smoke(test_notion_write=False)
    out = capsys.readouterr().out
    assert "✗ Pulled 0 holdings" in out
    assert "✓ Pulled 0" not in out

    # Case 3: Pull succeeds (1 row), but --write returns page_id=None -> exits non-zero, no ✓ on write
    class FailingWriteNotion:
        def __init__(self, _settings: Settings) -> None:
            self.enabled = True
            self.holdings_enabled = True

        async def pull_holdings(self) -> dict:
            return {
                "NTRA": NotionHoldingSnapshot(
                    ticker="NTRA",
                    saved_question="Test?",
                    next_check="",
                    notion_page_id="page-ntra",
                )
            }

        async def push_journal_entry(self, _entry) -> str | None:
            return None

    monkeypatch.setattr(smoke_live, "NotionSync", FailingWriteNotion)
    with pytest.raises(SystemExit):
        await smoke_live.run_smoke(test_notion_write=True)
    out = capsys.readouterr().out
    assert "✗ Test write to Cockpit Log failed (page_id=None)" in out
    assert "✓ Test write to Cockpit Log -> page_id=None" not in out

    # Case 4: Both pull (>=1 row) and --write (valid page_id) succeed -> exits cleanly
    class PassingNotion(FailingWriteNotion):
        async def push_journal_entry(self, _entry) -> str | None:
            return "page-write-ok"

    monkeypatch.setattr(smoke_live, "NotionSync", PassingNotion)
    await smoke_live.run_smoke(test_notion_write=True)
    out = capsys.readouterr().out
    assert "✓ Pulled 1 holding(s)" in out
    assert "✓ Test write to Cockpit Log -> page_id=page-write-ok" in out
    assert "Smoke check passed (2 check(s) succeeded, 0 failures)" in out
