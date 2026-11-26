from __future__ import annotations

import struct
from pathlib import Path
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _make_dummy_wav(duration_ms: int = 100, sample_rate: int = 16000) -> bytes:
    num_samples = int(sample_rate * duration_ms / 1000)
    pcm = b"\x00\x00" * num_samples
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        1,  # mono
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        data_size,
    )
    return header + pcm


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        cockpit_mock_mode=True,
        cockpit_db_path=str(tmp_path / "test_api.db"),
    )
    return TestClient(create_app(settings))


def test_health_state_and_voice_endpoint(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        state = client.get("/api/state")
        assert state.status_code == 200
        assert [h["ticker"] for h in state.json()["holdings"]] == ["NTRA", "STX", "AMZN"]

        portfolio = client.get("/api/portfolio")
        assert portfolio.status_code == 200
        assert portfolio.json()["total_positions"] == 61
        assert portfolio.json()["total_weight_pct"] == 100.0

        wav = _make_dummy_wav()
        voice = client.post(
            "/api/voice",
            files={"audio": ("clip.wav", wav, "audio/wav")},
            data={
                "active_ticker": "NTRA",
                "fallback_transcript": "Remember to buy a birthday present",
            },
        )
        assert voice.status_code == 200
        payload = voice.json()
        assert payload["decision"]["mind"] == "personal"
        assert payload["pending_write"] is not None

        # Cancel pending write cleanly
        cancel = client.post(f"/api/cancel/{payload['pending_write']['id']}")
        assert cancel.status_code == 200
        assert cancel.json()["cancelled"] is True


def test_zero_dictation_one_tap_suggested_check(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        staged = client.post("/api/holdings/NTRA/stage-suggested-check")
        assert staged.status_code == 200
        pending = staged.json()["pending_write"]
        assert pending["ticker"] == "NTRA"
        assert "$1,290" in pending["content"]

        confirmed = client.post("/api/confirm", json={"pending_id": pending["id"]})
        assert confirmed.status_code == 200
        assert "$1,290" in confirmed.json()["holding"]["next_check"]
