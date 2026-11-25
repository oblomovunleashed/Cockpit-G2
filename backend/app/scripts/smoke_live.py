from __future__ import annotations

import argparse
import asyncio
import math
import shutil
import struct
import subprocess
import tempfile
import time
from pathlib import Path

from app.config import Settings
from app.db import CockpitDB, utc_now_iso
from app.models import JournalEntry, Mind
from app.seed import seed_if_empty
from app.services.jev_router import JevRouter
from app.services.llm_worker import LLMWorker
from app.services.notion_sync import NotionSync
from app.services.openrouter_stt import OpenRouterSTT
from app.services.retrieval import DocumentRetriever


def _make_tone_wav(duration_ms: int = 500, sample_rate: int = 16000) -> bytes:
    """Builds a tiny valid 16 kHz mono 16-bit WAV clip for STT endpoint smoke testing."""
    num_samples = int(sample_rate * duration_ms / 1000)
    samples = bytearray()
    for i in range(num_samples):
        val = int(1200 * math.sin(2.0 * math.pi * 440.0 * i / sample_rate))
        samples.extend(struct.pack("<h", val))
    data_size = len(samples)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        data_size,
    )
    return header + bytes(samples)


def _make_speech_wav(phrase: str = "Can we review NVIDIA earnings?") -> tuple[bytes, str]:
    """Uses macOS `say` to generate a real 16 kHz mono 16-bit spoken WAV clip when available."""
    if shutil.which("say"):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "smoke.wav"
            proc = subprocess.run(
                ["say", "-o", str(out_path), "--data-format=LEI16@16000", phrase],
                capture_output=True,
                check=False,
            )
            if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 44:
                return out_path.read_bytes(), f"macOS speech ({phrase!r})"
    return _make_tone_wav(), "440Hz tone fallback"


async def run_smoke(*, test_notion_write: bool) -> None:
    settings = Settings()
    print("=== Cockpit-G2 Live Smoke Check ===")
    print(f"  COCKPIT_MOCK_MODE        = {settings.cockpit_mock_mode}")
    print(f"  OPENROUTER_API_KEY       = {'SET' if settings.openrouter_api_key.strip() else 'MISSING'}")
    print(f"  OPENROUTER_JEV_MODEL     = {settings.openrouter_jev_model}")
    print(f"  OPENROUTER_STT_MODEL     = {settings.openrouter_stt_model}")
    print(f"  OPENROUTER_LLM_MODEL     = {settings.openrouter_llm_model}")
    print(f"  NOTION_TOKEN             = {'SET' if settings.notion_token.strip() else 'MISSING'}")
    print(f"  NOTION_LOG_DATABASE_ID   = {settings.notion_log_database_id or 'MISSING'}")
    print(f"  NOTION_HOLDINGS_DB_ID    = {settings.notion_holdings_database_id or 'MISSING'}")
    print()

    db = CockpitDB(settings.cockpit_db_path)
    seed_if_empty(db)
    tickers = [h.ticker for h in db.list_holdings()]
    checks_ran = 0

    if not settings.openrouter_api_key.strip():
        print("[SKIP] OpenRouter checks skipped — set OPENROUTER_API_KEY and COCKPIT_MOCK_MODE=false in .env.")
    else:
        checks_ran += 1
        live_settings = settings.model_copy(update={"cockpit_mock_mode": False})
        jev = JevRouter(live_settings)
        retriever = DocumentRetriever(db)
        worker = LLMWorker(live_settings)
        stt = OpenRouterSTT(live_settings)

        print("1) OpenRouter Jev System-1 Triage & Work Skill Dispatch (POST /api/alpha/decisions)")
        utterances = [
            "This podcast on TSMC was great",
            "Remember to buy a birthday present",
            "Preview NVIDIA before earnings",
            "Can we review NVIDIA earnings?",
            "What did management expect to bring margins back up?",
            "What's the read-across from TSMC to NVIDIA?",
            "At the next call, check whether those price increases are on schedule.",
        ]
        for text in utterances:
            try:
                dec = await jev.route_utterance(text, available_tickers=tickers, active_ticker="NVDA")
                peer_part = f" peer={dec.peer_holding}" if dec.peer_holding else ""
                print(
                    f"   ✓ '{text}'\n"
                    f"     -> mind={dec.mind.value} skill={dec.action.value} "
                    f"worker={dec.needs_llm_worker} holding={dec.holding}{peer_part} "
                    f"conf={dec.confidence:.2f} | {dec.reasoning}"
                )
            except Exception as exc:
                print(f"   ✗ Jev failed on '{text}': {exc}")

        print("\n2) OpenRouter Jev Passage Selection + No-Match Guard")
        for question in (
            "Does NVIDIA expect the margin trough to get deeper?",
            "Is revenue running ahead of management's guidance?",
            "Warranty reserves?",
        ):
            chunks = retriever.retrieve("NVDA", question, limit=4)
            if not chunks:
                print(f"   ✓ '{question}' -> Retriever returned 0 hits (No matching passage)")
                continue
            try:
                match = await jev.select_best_chunk(question, chunks)
                if match is None:
                    print(f"   ✓ '{question}' -> Jev returned None (No matching passage)")
                else:
                    chunk, verdict, conf = match
                    print(f"   ✓ '{question}' -> [{verdict.value}] {chunk.headline} (conf={conf:.2f})")
            except Exception as exc:
                print(f"   ✗ Jev selection failed on '{question}': {exc}")

        print("\n3) OpenRouter System-2 LLM Synthesis (thesis_review + peer_read_across)")
        nvda = db.get_holding("NVDA")
        nvda_chunks = retriever.retrieve("NVDA", "margin trough", limit=2)
        if nvda_chunks:
            t0 = time.perf_counter()
            finding, next_check = await worker.synthesize_card(
                ticker="NVDA",
                question="Does NVIDIA expect the margin trough to get deeper?",
                chunk=nvda_chunks[0],
                verdict=nvda_chunks[0].verdict,
            )
            ms = int((time.perf_counter() - t0) * 1000)
            print(f"   ✓ thesis_review ({ms}ms, {len(finding)} chars): {finding}")
            print(f"     suggested_next_check: {next_check}")

        tsm_chunks = retriever.retrieve("TSM", "AI CoWoS revenue guidance", limit=2)
        if nvda and tsm_chunks:
            t0 = time.perf_counter()
            ra_finding, ra_draft = await worker.synthesize_peer_read_across(
                target=nvda,
                peer_ticker="TSM",
                peer_chunk=tsm_chunks[0],
                utterance="What's the read-across from TSMC to NVIDIA?",
            )
            ms = int((time.perf_counter() - t0) * 1000)
            print(f"   ✓ peer_read_across TSM→NVDA ({ms}ms, {len(ra_finding)} chars): {ra_finding}")
            print(f"     draft check ({ra_draft.source_citation}): {ra_draft.content}")

        print("\n4) OpenRouter STT Check (POST /api/v1/audio/transcriptions)")
        try:
            t0 = time.perf_counter()
            wav, wav_desc = _make_speech_wav()
            txt = await stt.transcribe_wav(wav)
            ms = int((time.perf_counter() - t0) * 1000)
            print(f"   ✓ STT succeeded ({ms}ms, source={wav_desc}) -> {txt!r}")
        except Exception as exc:
            print(f"   ! STT returned: {exc}")
            try:
                models = await stt.list_transcription_models()
                if models:
                    print(f"     Available OpenRouter STT models: {', '.join(models[:8])}")
            except Exception as list_exc:
                print(f"     (Could not list STT models: {list_exc})")

    print("\n5) Notion Live Sync Check")
    notion = NotionSync(settings)
    if not notion.holdings_enabled and not notion.enabled:
        print("[SKIP] Notion not configured yet — set NOTION_TOKEN + NOTION_PARENT_PAGE_ID and run `make setup-notion`.")
    else:
        checks_ran += 1
        snapshots = await notion.pull_holdings()
        print(f"   ✓ Pulled {len(snapshots)} holding(s) from Notion Holdings Watchlist:")
        for ticker, snap in snapshots.items():
            print(f"     - {ticker}: question={snap.saved_question!r} | next_check={snap.next_check!r}")
        if test_notion_write and notion.enabled:
            dummy = JournalEntry(
                id=0,
                mind=Mind.INTERESTS,
                kind="note",
                content="Cockpit smoke-live check",
                ticker=None,
                source_citation=None,
                notion_page_id=None,
                created_at=utc_now_iso(),
            )
            page_id = await notion.push_journal_entry(dummy)
            print(f"   ✓ Test write to Cockpit Log -> page_id={page_id}")
        elif notion.enabled:
            print("   (Notion write check skipped by default; pass `--write` to append a test row to Cockpit Log.)")

    if checks_ran == 0:
        raise SystemExit(
            "No live providers configured (both OpenRouter and Notion checks were skipped). "
            "Set OPENROUTER_API_KEY and/or NOTION_TOKEN in .env first."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test live OpenRouter (Jev, STT, LLM) and Notion wiring.")
    parser.add_argument("--write", action="store_true", help="Also push a test row to Notion Cockpit Log.")
    args = parser.parse_args()
    asyncio.run(run_smoke(test_notion_write=args.write))


if __name__ == "__main__":
    main()
