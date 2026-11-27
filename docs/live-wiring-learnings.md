# Live Wiring & Work Skills Engineering Learnings

Real-world findings captured during live integration testing against **OpenRouter** (`~typesafe/jev-latest`, `openai/whisper-large-v3-turbo`, `deepseek/deepseek-v4.1-flash-1017`) and **Even Hub SDK (`0.0.16`)**.

---

## 1. OpenRouter Decisions API (`Jev`) — Live Integration Findings

1. **`noul` returns `"probability"`, not `"noul"`:**
   - Live `/api/alpha/decisions` responses return `{"type": "noul", "probability": 0.14}`. Parsing `answer.get("noul")` silently fell back to defaults until caught in live smoke testing.
2. **Each question in `questions` evaluates independently against `state`:**
   - Putting candidate filing passages only inside `best_chunk.criteria` left `is_answered` blind to the passages, causing valid earnings questions to return `is_answered < 0.5`.
   - Fix: place both the wearer's question **and** all numbered candidate passages in `state`, and keep `criteria` concise references to chunk IDs.
3. **Why a three-tier compute hierarchy beats an agentic tool-calling LLM on smart glasses:**
   - Measured live component latencies on OpenRouter:
     - **SQLite FTS5 / deterministic local code (Tier 0):** `< 5 ms` (`$0`)
     - **Jev System-1 router (`typesafe/jev-1.14`):** `~206–315 ms` warm (`~$0.000057` / call)
     - **DeepSeek V4.1 Flash (`deepseek/deepseek-v4.1-flash-1017`):** `~2,180 ms` (indexed synthesis) / `~10 s` (with OpenRouter `web` plugin)
     - **Whisper Large v3 Turbo (`openai/whisper-large-v3-turbo`):** `~1,580–2,190 ms`
   - Letting an LLM decide tools adds seconds of HUD freeze and hallucination risk. Instead, **Jev** routes mind, skill, target ticker (`tracked` vs. `untracked` vs. `implicit_active`), peer ticker, `is_answered`, and `needs_enrichment` in one ~220 ms call, while **DeepSeek** only runs when cross-text or web synthesis is genuinely earned. Deterministic skills (`log_note`, `log_task`, `draft_next_check`, indexed `earnings_preview`) skip on-lens LLM generation completely.

---

## 2. Typed Work Skills & 61-Position Portfolio Architecture

Cockpit loads the full **61-position portfolio (`100.00%` weight)** from a plain 3-column CSV ([backend/app/data/portfolio.csv](../backend/app/data/portfolio.csv)) with zero hardcoded tickers in the live Jev router, indexing real Oct–Nov 2026 primary filings for the top three core holdings (**`NTRA` 29.22%**, **`STX` 9.07%**, **`AMZN` 6.86%** — 45.15% combined weight):

| Skill | Compute Tier | Behavior on G2 (`576 × 288`) |
| :--- | :--- | :--- |
| `earnings_preview` (core indexed) | **Tier 0** (Code + SQLite, zero LLM) | Local render `< 5 ms` showing portfolio weight, saved question, verified prior baseline, and open `Next Check` (spoken flow adds STT + Jev + optional Notion pull). |
| `earnings_preview` / `brief_holding` (other 58 holdings or external names) | **Tier 1 + Tier 2 Web** (Jev + DeepSeek Web) | Date-aware web-grounded preview/review with automatic weight badge when held (e.g. `PREVIEW \| INSM (4.2%) \| WEB`) and no badge when external (`PREVIEW \| GOOGL \| WEB`). |
| `brief_holding` (`thesis_review`, core indexed) | **Tier 1 + Tier 2** (Jev + DeepSeek) | Pulls latest Notion question first, verifies `is_answered`, surfaces one sourced delta card (`HELD`/`AHEAD`/`WATCH`) or `No matching passage`. |
| `follow_up_source` | **Tier 1 + Tier 2** | Retrieves specific passage (e.g. Signatera ASP & FCF on Natera Q3 call) and returns `suggested_check` (never stages an unseen write; tap previews exact text on `CONFIRM CHECK`). |
| `peer_read_across` | **Tier 1 + Tier 2** | Jev resolves both `peer_holding` (`AMZN`) and target `holding` (`STX`); DeepSeek synthesizes a cross-company read-across card citing Amazon's `~$220B` companywide FY26 cash capex guidance + stageable `STX` check (unsupported peers return a clarification card). |

---

## 3. OpenRouter STT & Browser/Simulator Audio

- `openai/whisper-large-v3-turbo` on `POST /api/v1/audio/transcriptions` accepts base64-encoded 16 kHz 16-bit mono WAV cleanly (~1.6–2.2s round-trip).
- **Even Hub Simulator (`0.9.5`) native CoreAudio mic vs. WKWebView `getUserMedia`:** On macOS, the simulator natively captures the MacBook microphone via `bridge.audioControl(true, AudioInputSource.Glasses)` and streams 16 kHz 16-bit LE mono PCM `audioEvent`s (3200 bytes / 100 ms), whereas WKWebView blocks `getUserMedia`. Routing the companion `Hold to Speak (Mic)` button through `bridge.audioControl` first (marking the capture session active before `showCard()` awaits, and falling back to `BrowserMicRecorder` with `AudioContext.resume()` in standalone browsers) makes live voice capture work reliably in both environments.

---

## 4. Even G2 Simulator & Live Product Iteration Learnings

1. **LVGL missing glyphs on G2 firmware font:**
   - The Even Hub Simulator logged `lv_draw_letter: glyph dsc. not found for U+2713` (`✓`). `G2HudRenderer.sanitizeLine()` normalizes unsupported glyphs (`✓` → `OK:`, `→` → `->`, `●` → `[REC]`, `·` → `|`) before sending text to G2 containers so zero missing-glyph boxes appear.
2. **Re-initializing HUD containers after root double-tap (`shutDownPageContainer`):**
   - Calling `bridge.shutDownPageContainer(1)` tears down containers `1/2/3`, causing subsequent `textContainerUpgrade` calls to warn `container not found`. `G2HudRenderer` resets `initialized = false` on shutdown or failed upgrades and serializes upgrades through a queue.
3. **Calm `HOME` default + never hijacking non-indexed companies:**
   - Booting and resting on `COCKPIT | READY` (`activeHolding === null`) prevents the top holding from bleeding into Personal/Interests captures, while splitting Jev's `holding` choices into indexed tickers + `untracked` + `implicit_active` + `none` lets any portfolio or external company route cleanly to web-grounded preview/review.
4. **Fast capture on glasses + Jev-gated async post-confirm enrichment:**
   - Adding a `needs_enrichment` `noul` question to the same Jev round-trip (`~220 ms`) lets simple reminders (`enrich_p=0.09`) skip LLMs completely, while media/relative-date logs (`enrich_p=0.88–0.91`) confirm immediately on glasses and enrich asynchronously—constraining published links strictly to OpenRouter provider `url_citation` annotations and honestly marking `Needs Detail` if a show/title was never named.

---

## 5. API & SDK Feature Requests (5 — Collected During Build Week)

Concrete developer friction encountered while building live with Even Hub SDK (`0.0.16`), Even Hub Simulator (`0.9.5`), OpenRouter/Jev, and Notion:

1. **[Even Hub SDK]** Normalize protobuf zero-value `CLICK_EVENT` (`0`) inside the SDK before invoking `onEvenHubEvent` listeners instead of emitting `eventType: undefined` on single taps.
2. **[Even Hub SDK / Simulator]** Document the exact supported LVGL firmware glyph set (and optionally warn at compile/pack time when characters like `✓` `U+2713` are missing), plus add a `--audio-file sample.wav` simulator flag so `audioEvent.audioPcm` can be replayed deterministically in automated tests and demo recordings.
3. **[OpenRouter Decisions API / Jev]** Document prominently in quickstart examples that `noul` responses return `"probability"` (not `"noul"`) and that every question in `questions` evaluates independently against `state` (so shared retrieval passages must live inside `state`, not sibling `criteria`).
4. **[OpenRouter STT]** Support streaming chunked PCM/WebSocket transcription alongside base64 WAV on `/api/v1/audio/transcriptions` to shave ~1 s off push-to-talk smart-glasses latency.
5. **[Notion API]** Support a first-party local-dev / SSE change stream (or CLI tunnel) for property-change webhooks with near-immediate delivery on rapid consecutive edits, so local-first / loopback companion apps can react to live Notion `Saved Question` changes without hosting a public HTTPS relay or waiting on webhook aggregation windows.
