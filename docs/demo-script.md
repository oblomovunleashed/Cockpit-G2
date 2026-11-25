# Cockpit — 3-Minute Demo Storyboard & Feature-Request Tracker

## Split-Screen Recording Layout
- **Left half:** Even Hub G2 Simulator (`make sim`) displaying the live `576 × 288` monochrome green G2 lens + Cockpit Companion trace (`mind`, `skill`, `llm_worker`, `post_enrich`, latency & cost).
- **Right half:** Live Notion workspace showing **`Cockpit Log`** (with `Enrichment`, `Enriched`, `Due Date`, `Link`) and **`Holdings Watchlist`** (`NTRA 29.2%`, `STX 9.1%`, `AMZN 6.9%`).

## Shot List (Target: 2m 45s · Hard Limit: 3m 00s)

1. **0:00–0:20 · Hook & Calm `HOME`**
   - Show Cockpit resting calmly on **`COCKPIT | READY`** — no ticker spam, no chatty walls of text.
   - Frame the architecture: **Jev (System 1 on OpenRouter, ~220 ms triage)** sits at the front door, routing across Work, Personal, and Interests so simple logs never block on an LLM.
2. **0:20–0:55 · “Logged, Logged” + Async Enrichment & Misclassification Recovery**
   - Speak / submit *"I enjoyed the latest Acquired podcast."* → Jev: `Interests · LOG_NOTE (llm_worker: NO, post_enrich: YES, ~$0.000057)`.
   - Briefly **`Swipe`** on the `CONFIRM` card to show in-place destination cycling (`Interests Note` → `Personal Task` → back to `Interests Note`), then **`Tap`** to save.
   - Show `INTERESTS | SAVED | ENRICHING` on G2 once the Notion write returns → seconds later, background worker patches the Notion row (`Enriched: Seinfeld episode` + provider-cited `acquired.fm` URL) and updates the lens in place to **`INTERESTS | ENRICHED`**. **`Tap`** returns calmly to `HOME`.
   - Utterance 2: *"Remember to buy a birthday present next Friday."* → 1-tap confirm → logs to Notion `Cockpit Log` with `Due Date` resolved locally (`2026-12-04`). *(Optional: show 1-swipe **Undo** removing locally and archiving the Notion page.)*
3. **0:55–1:45 · “LLM Worker” — Natera (`29.2%` #1 Holding) Earnings Review + 2-Tap Verified Write**
   - *"Can we review Natera earnings?"* → Jev wakes System-2 DeepSeek (`Work · BRIEF_HOLDING`).
   - G2 shows the card tailored to your saved question (*"Is Natera's underlying gross margin expanding without revenue true-ups?"*):
     `Q3 2026 GAAP GM 64.7%; ex-true-ups 63.2% (+140 bps QoQ on COGS gains). True-ups fell to $32.3M vs $52.3M in Q2.` `[NTRA Q3 2026 PR · Nov 5]`
   - Follow-up: *"What drove Signatera ASP and cash flow?"* → retrieves Q3 call passage (`Signatera ASP ~$1,290 vs $1,270 Q2; cash +$5.7M to $1.10B, FCF-positive`).
   - **Tap once** to preview exact proposed wording on `WORK | NTRA | CONFIRM CHECK` → **Tap again** to commit live to Notion `Cockpit Log` + `Holdings Watchlist` (`Next Check`).
4. **1:45–2:15 · “One Release, Two Different Glances” (Live Notion Edit)**
   - Edit `NTRA`'s `Saved Question` directly in Notion to *"Is Signatera volume and revenue running ahead of full-year guidance?"* and click **`Pull Notion`**.
   - `NOTION | UPDATED` → tap to see the exact same Natera Q3 2026 release flip to `Q3 rev $794.0M (+34% YoY); oncology tests 339.5k (+61% YoY). FY26 guide raised $140M at mid to $3.005-$3.035B.`
5. **2:15–2:45 · Portfolio-Wide Work Skills (`AMZN → STX`, `INSM 4.2%`, Non-Held Names) + Close**
   - **`Preview Natera`** (Tier-0 local render `< 5 ms`, zero LLM call; end-to-end spoken flow adds STT + Jev + Notion pull) → **`Peer Read: Amazon → Seagate`** (connects Amazon's `$220B` FY26 AWS AI capex directly to Seagate's `9.1%` HAMR Mozaic holding) → **`Preview Insmed earnings`** (`PREVIEW | INSM (4.2%) | WEB` — shows exact weight from the 61-position portfolio; external names like Alphabet work identically).
   - Close on safety & stack: Even Hub SDK `0.0.16` (desktop `.ehpk` packed), OpenRouter STT + Jev + DeepSeek Web, Notion 2-way sync; strictly read-only financial inputs and human-confirmed writes only.

---

## Optional API Feature Requests (5 — Collected During Build Week)

Concrete developer friction encountered while building live with Even Hub SDK (`0.0.16`), Even Hub Simulator (`0.9.5`), OpenRouter/Jev, and Notion:

1. **[Even Hub SDK]** Normalize protobuf zero-value `CLICK_EVENT` (`0`) inside the SDK before invoking `onEvenHubEvent` listeners instead of emitting `eventType: undefined` on single taps.
2. **[Even Hub SDK / Simulator]** Document the exact supported LVGL firmware glyph set (and optionally warn at compile/pack time when characters like `✓` `U+2713` are missing), plus add a `--audio-file sample.wav` simulator flag so `audioEvent.audioPcm` can be replayed deterministically in automated tests and demo recordings.
3. **[OpenRouter Decisions API / Jev]** Document prominently in quickstart examples that `noul` responses return `"probability"` (not `"noul"`) and that every question in `questions` evaluates independently against `state` (so shared retrieval passages must live inside `state`, not sibling `criteria`).
4. **[OpenRouter STT]** Support streaming chunked PCM/WebSocket transcription alongside base64 WAV on `/api/v1/audio/transcriptions` to shave ~1 s off push-to-talk smart-glasses latency.
5. **[Notion API]** Support a first-party local-dev / SSE change stream (or CLI tunnel) for property-change webhooks with near-immediate delivery on rapid consecutive edits, so local-first / loopback companion apps can react to live Notion `Saved Question` changes without hosting a public HTTPS relay or waiting on webhook aggregation windows.
