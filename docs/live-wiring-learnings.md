# Live Wiring & Work Skills Engineering Learnings

Real-world findings captured during live integration testing against **OpenRouter** (`~typesafe/jev-latest`, `openai/whisper-large-v3-turbo`, `deepseek/deepseek-v4.1-flash-1017`) and **Even Hub SDK (`0.0.16`)**.

---

## 1. OpenRouter Decisions API (`Jev`) — Live Integration Findings

1. **`noul` returns `"probability"`, not `"noul"`:**
   - Live `/api/alpha/decisions` responses return `{"type": "noul", "probability": 0.14}`. Parsing `answer.get("noul")` silently fell back to defaults until caught in live smoke testing.
2. **Each question in `questions` evaluates independently against `state`:**
   - Putting candidate filing passages only inside `best_chunk.criteria` left `is_answered` blind to the passages, causing valid NVIDIA questions to return `is_answered < 0.5`.
   - Fix: place both the wearer's question **and** all numbered candidate passages in `state`, and keep `criteria` concise references to chunk IDs.
3. **Why a three-tier compute hierarchy beats an agentic tool-calling LLM on smart glasses:**
   - Measured live latencies on OpenRouter:
     - **SQLite FTS5 / deterministic code:** `< 5 ms` (`$0`)
     - **Jev System-1 router (`typesafe/jev-1.14`):** `~226–246 ms` warm (`~$0.000033` / call)
     - **DeepSeek V4.1 Flash (`deepseek/deepseek-v4.1-flash-1017`):** `~2,180 ms`
     - **Whisper Large v3 Turbo (`openai/whisper-large-v3-turbo`):** `~2,189 ms`
   - Letting an LLM decide tools adds seconds of HUD freeze and hallucination risk. Instead, **Jev** routes mind, skill, target ticker, peer ticker, and `is_answered` in one ~230 ms call, while **DeepSeek** only runs when cross-text synthesis is genuinely earned (`thesis_review` on uncached questions, `source_followup`, and cross-company `peer_read_across`). Deterministic skills (`log_note`, `log_task`, `draft_next_check`, `earnings_preview`) skip the LLM completely.

---

## 2. Typed Work Skills Architecture

All three seeded holdings (`NVDA`, `TSM`, `MSFT`) now use real Q3/Q1 FY27 (Oct–Nov 2026) primary earnings figures and link together via Jev-dispatched skills:

| Skill | Compute Tier | Behavior on G2 (`576 × 288`) |
| :--- | :--- | :--- |
| `earnings_preview` | **Tier 0** (Code + SQLite, zero LLM) | Shows saved question, verified prior-quarter guidance baseline, and open `Next Check` in `< 5 ms`. |
| `brief_holding` (`thesis_review`) | **Tier 1 + Tier 2** (Jev + DeepSeek) | Pulls latest Notion question first, verifies `is_answered`, surfaces one sourced delta card (`HELD`/`AHEAD`/`WATCH`) or `No matching passage`. |
| `follow_up_source` | **Tier 1 + Tier 2** | Retrieves specific passage and returns `suggested_check` (never stages an unseen write; tap previews exact text on `CONFIRM CHECK`). |
| `peer_read_across` | **Tier 1 + Tier 2** | Jev resolves both `peer_holding` (`TSM`/`MSFT`) and target `holding` (`NVDA`); DeepSeek synthesizes a cross-company read-across card citing the peer filing + stageable check for the target holding. |

---

## 3. OpenRouter STT & Browser/Simulator Audio

- `openai/whisper-large-v3-turbo` on `POST /api/v1/audio/transcriptions` accepts base64-encoded 16 kHz 16-bit mono WAV cleanly (~2.2s round-trip) and transcribed spoken `"Can we review NVIDIA earnings?"` verbatim.
- Added `BrowserMicRecorder` (`glasses/src/audio.ts`) alongside `PcmAudioRecorder` (Even G2 `audioControl`), both capped at 30 seconds and routed through identical 16 kHz PCM → 44-byte RIFF/WAV encoding so live voice works in both Even Hub Simulator and desktop browser preview.
