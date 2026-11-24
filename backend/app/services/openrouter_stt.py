from __future__ import annotations

import base64
import httpx

from app.config import Settings


MAX_AUDIO_BYTES = 5 * 1024 * 1024  # 5 MB (~2.5 minutes of 16 kHz mono 16-bit WAV)


class OpenRouterSTT:
    """Speech-to-text via OpenRouter's /v1/audio/transcriptions JSON base64 endpoint."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def transcribe_wav(self, wav_bytes: bytes, *, fallback_transcript: str | None = None) -> str:
        if not wav_bytes:
            if fallback_transcript:
                return fallback_transcript.strip()
            raise ValueError("Empty audio payload received.")
        if len(wav_bytes) > MAX_AUDIO_BYTES:
            raise ValueError(f"Audio clip exceeds {MAX_AUDIO_BYTES // (1024 * 1024)} MB limit.")

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
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.is_error:
                raise RuntimeError(
                    f"OpenRouter STT error {resp.status_code} "
                    f"(model={self.settings.openrouter_stt_model}): {resp.text[:300]}"
                )
            data = resp.json()

        text = str(data.get("text", "")).strip()
        if not text:
            raise RuntimeError(f"OpenRouter STT returned empty transcription: {data}")
        return text

    async def list_transcription_models(self) -> list[str]:
        """Discovers live speech-to-text model IDs available on OpenRouter."""
        url = f"{self.settings.openrouter_base_url.rstrip('/')}/v1/models"
        headers = {}
        if self.settings.openrouter_api_key:
            headers["Authorization"] = f"Bearer {self.settings.openrouter_api_key}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url,
                headers=headers,
                params={"output_modalities": "transcription"},
            )
            resp.raise_for_status()
            models = resp.json().get("data", [])
        return [str(m["id"]) for m in models if "id" in m]
