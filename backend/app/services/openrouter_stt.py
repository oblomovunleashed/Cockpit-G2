from __future__ import annotations

import base64
import httpx

from app.config import Settings


class OpenRouterSTT:
    """Speech-to-text via OpenRouter's /v1/audio/transcriptions JSON base64 endpoint."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def transcribe_wav(self, wav_bytes: bytes, *, fallback_transcript: str | None = None) -> str:
        if not wav_bytes:
            if fallback_transcript:
                return fallback_transcript.strip()
            raise ValueError("Empty audio payload received.")

        if not self.settings.live_openrouter_enabled:
            if fallback_transcript:
                return fallback_transcript.strip()
            return "Can we review NVIDIA earnings?"

        encoded_audio = base64.b64encode(wav_bytes).decode("ascii")
        url = f"{self.settings.openrouter_base_url.rstrip('/')}/v1/audio/transcriptions"
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "X-OpenRouter-Title": "Cockpit-G2",
        }
        payload = {
            "model": self.settings.openrouter_stt_model,
            "input_audio": {
                "data": encoded_audio,
                "format": "wav",
            },
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        text = str(data.get("text", "")).strip()
        if not text:
            raise RuntimeError(f"OpenRouter STT returned empty transcription: {data}")
        return text
