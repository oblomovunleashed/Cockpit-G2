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


def _make_speech_wav(phrase: str = "Can we review Natera earnings?") -> tuple[bytes, str]:
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
    passed_checks = 0
    failures: list[str] = []

    if not settings.openrouter_api_key.strip():
        print("[SKIP] OpenRouter checks skipped — set OPENROUTER_API_KEY and COCKPIT_MOCK_MODE=false in .env.")
    else:
        live_settings = settings.model_copy(update={"cockpit_mock_mode": False})
        jev = JevRouter(live_settings)
        retriever = DocumentRetriever(db)
        worker = LLMWorker(live_settings)
        stt = OpenRouterSTT(live_settings)

        print("1) OpenRouter Jev System-1 Triage & Work Skill Dispatch (POST /api/alpha/decisions)")
        holdings_map = {h.ticker.upper(): h.company for h in db.list_holdings()}
        utterances = [
            "I enjoyed the latest Acquired podcast",
            "Remember to buy a birthday present",
            "Preview Natera before earnings",
            "Can we review Natera earnings?",
            "What drove Signatera ASP and cash flow?",
            "What does Amazon capex mean for Seagate?",
            "Can you please preview Insmed earnings?",
            "At the next call, check whether Signatera ASP holds above $1,290.",
        ]
        for text in utterances:
            try:
                dec = await jev.route_utterance(
                    text,
                    available_tickers=tickers,
                    available_holdings=holdings_map,
                    active_ticker="NTRA",
                )
                peer_part = f" peer={dec.peer_holding}" if dec.peer_holding else ""
                req_part = f" req={dec.requested_company}" if dec.requested_company else ""
                print(
                    f"   ✓ '{text}'\n"
                    f"     -> mind={dec.mind.value} skill={dec.action.value} "
                    f"worker={dec.needs_llm_worker} holding={dec.holding}{peer_part}{req_part} "
                    f"conf={dec.confidence:.2f} | {dec.reasoning}"
                )
                passed_checks += 1
            except Exception as exc:
                print(f"   ✗ Jev failed on '{text}': {exc}")
                failures.append(f"Jev route failed on {text!r}: {exc}")

        print("\n2) OpenRouter Jev Passage Selection + No-Match Guard")
        for question, expect_match in (
            ("Is Natera's underlying gross margin expanding without revenue true-ups?", True),
            ("Is Signatera volume and revenue running ahead of full-year guidance?", True),
            ("Warranty reserves?", False),
        ):
            chunks = retriever.retrieve("NTRA", question, limit=4)
            if not chunks:
                if not expect_match:
                    print(f"   ✓ '{question}' -> Retriever returned 0 hits (No matching passage)")
                    passed_checks += 1
                else:
                    print(f"   ✗ '{question}' -> Retriever returned 0 hits unexpectedly")
                    failures.append(f"Retriever returned 0 hits for {question!r}")
                continue
            try:
                match = await jev.select_best_chunk(question, chunks)
                if match is None:
                    if not expect_match:
                        print(f"   ✓ '{question}' -> Jev returned None (No matching passage)")
                        passed_checks += 1
                    else:
                        print(f"   ✗ '{question}' -> Jev returned None unexpectedly")
                        failures.append(f"Jev returned None for {question!r}")
                else:
                    chunk, verdict, conf = match
                    if expect_match:
                        print(f"   ✓ '{question}' -> [{verdict.value}] {chunk.headline} (conf={conf:.2f})")
                        passed_checks += 1
                    else:
                        print(f"   ✗ '{question}' -> Expected no match, got [{verdict.value}] {chunk.headline}")
                        failures.append(f"No-match guard failed for {question!r}")
            except Exception as exc:
                print(f"   ✗ Jev selection failed on '{question}': {exc}")
                failures.append(f"Jev selection failed on {question!r}: {exc}")

        print("\n3) OpenRouter System-2 LLM Synthesis (thesis_review + peer_read_across)")
        ntra_chunks = retriever.retrieve("NTRA", "underlying gross margin true-ups", limit=2)
        if not ntra_chunks:
            print("   ✗ thesis_review skipped: no NTRA chunks found")
            failures.append("No NTRA chunks found for thesis_review")
        else:
            try:
                t0 = time.perf_counter()
                finding, next_check = await worker.synthesize_card(
                    ticker="NTRA",
                    question="Is Natera's underlying gross margin expanding without revenue true-ups?",
                    chunk=ntra_chunks[0],
                    verdict=ntra_chunks[0].verdict,
                )
                ms = int((time.perf_counter() - t0) * 1000)
                if not finding.strip():
                    print("   ✗ thesis_review returned empty finding")
                    failures.append("thesis_review returned empty finding")
                else:
                    print(f"   ✓ thesis_review ({ms}ms, {len(finding)} chars): {finding}")
                    print(f"     suggested_next_check: {next_check}")
                    passed_checks += 1
            except Exception as exc:
                print(f"   ✗ thesis_review failed: {exc}")
                failures.append(f"thesis_review failed: {exc}")

        stx = db.get_holding("STX")
        amzn_chunks = retriever.retrieve("AMZN", "companywide cash capex AWS infrastructure", limit=2)
        if not (stx and amzn_chunks):
            print("   ✗ peer_read_across skipped: missing STX holding or AMZN capex chunks")
            failures.append("Missing STX holding or AMZN capex chunks")
        else:
            try:
                t0 = time.perf_counter()
                ra_finding, ra_draft = await worker.synthesize_peer_read_across(
                    target=stx,
                    peer_ticker="AMZN",
                    peer_chunk=amzn_chunks[0],
                    utterance="What does Amazon capex mean for Seagate?",
                )
                ms = int((time.perf_counter() - t0) * 1000)
                if not ra_finding.strip():
                    print("   ✗ peer_read_across returned empty finding")
                    failures.append("peer_read_across returned empty finding")
                else:
                    print(f"   ✓ peer_read_across AMZN→STX ({ms}ms, {len(ra_finding)} chars): {ra_finding}")
                    print(f"     draft check ({ra_draft.source_citation}): {ra_draft.content}")
                    passed_checks += 1
            except Exception as exc:
                print(f"   ✗ peer_read_across failed: {exc}")
                failures.append(f"peer_read_across failed: {exc}")

        print("\n4) OpenRouter STT Check (POST /api/v1/audio/transcriptions)")
        try:
            t0 = time.perf_counter()
            wav, wav_desc = _make_speech_wav()
            txt = await stt.transcribe_wav(wav)
            ms = int((time.perf_counter() - t0) * 1000)
            if not txt.strip():
                print(f"   ✗ STT returned empty transcript ({ms}ms, source={wav_desc})")
                failures.append("STT returned empty transcript")
            else:
                print(f"   ✓ STT succeeded ({ms}ms, source={wav_desc}) -> {txt!r}")
                passed_checks += 1
        except Exception as exc:
            print(f"   ✗ STT failed: {exc}")
            failures.append(f"STT failed: {exc}")
            try:
                models = await stt.list_transcription_models()
                if models:
                    print(f"     Available OpenRouter STT models: {', '.join(models[:8])}")
            except Exception as list_exc:
                print(f"     (Could not list STT models: {list_exc})")

    print("\n5) Notion Live Sync Check")
    notion = NotionSync(settings)
    if not notion.holdings_enabled and not notion.enabled:
        if test_notion_write:
            print("   ✗ Notion write requested (--write), but Notion is not configured.")
            failures.append("Notion write requested but NOTION_TOKEN / NOTION_LOG_DATABASE_ID missing")
        else:
            print("[SKIP] Notion not configured yet — set NOTION_TOKEN + NOTION_PARENT_PAGE_ID and run `make setup-notion`.")
    else:
        if notion.holdings_enabled:
            try:
                snapshots = await notion.pull_holdings()
                if not snapshots:
                    print(
                        "   ✗ Pulled 0 holdings from Notion Holdings Watchlist "
                        "(check NOTION_TOKEN, NOTION_HOLDINGS_DATABASE_ID, or permissions)."
                    )
                    failures.append("Notion holdings pull returned 0 rows")
                else:
                    print(f"   ✓ Pulled {len(snapshots)} holding(s) from Notion Holdings Watchlist:")
                    for ticker, snap in snapshots.items():
                        print(f"     - {ticker}: question={snap.saved_question!r} | next_check={snap.next_check!r}")
                    passed_checks += 1
            except Exception as exc:
                print(f"   ✗ Notion holdings pull failed: {exc}")
                failures.append(f"Notion holdings pull failed: {exc}")

        if test_notion_write:
            if not notion.enabled:
                print("   ✗ Notion write requested (--write), but NOTION_LOG_DATABASE_ID is not configured.")
                failures.append("Notion write requested but NOTION_LOG_DATABASE_ID missing")
            else:
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
                try:
                    page_id = await notion.push_journal_entry(dummy)
                    if not page_id:
                        print("   ✗ Test write to Cockpit Log failed (page_id=None).")
                        failures.append("Notion journal write returned page_id=None")
                    else:
                        print(f"   ✓ Test write to Cockpit Log -> page_id={page_id}")
                        passed_checks += 1
                except Exception as exc:
                    print(f"   ✗ Test write to Cockpit Log failed: {exc}")
                    failures.append(f"Notion journal write failed: {exc}")
        elif notion.enabled:
            print("   (Notion write check skipped by default; pass `--write` to append a test row to Cockpit Log.)")

    if failures:
        raise SystemExit(
            f"Live smoke check failed ({len(failures)} failure(s), {passed_checks} passed): "
            + "; ".join(failures[:5])
        )

    if passed_checks == 0:
        raise SystemExit(
            "No live checks succeeded (both OpenRouter and Notion checks were skipped). "
            "Set OPENROUTER_API_KEY and/or NOTION_TOKEN in .env first."
        )

    print(f"\n=== Smoke check passed ({passed_checks} check(s) succeeded, 0 failures) ===")


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test live OpenRouter (Jev, STT, LLM) and Notion wiring.")
    parser.add_argument("--write", action="store_true", help="Also push a test row to Notion Cockpit Log.")
    args = parser.parse_args()
    asyncio.run(run_smoke(test_notion_write=args.write))


if __name__ == "__main__":
    main()
