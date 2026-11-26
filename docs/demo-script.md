# Cockpit — 5-Clip Demo Shot List & Feature-Request Tracker

**Target total runtime:** `~2:10` (hard hackathon limit: `3:00`).  
**Format:** Screen capture of **Even Hub Simulator (`0.9.5`)** + **live Notion** side-by-side (no webcam needed). Record each of the 5 clips separately against the real live APIs, trim dead waiting pauses cleanly, and stitch in QuickTime / iMovie / CapCut with crisp voiceover.

---

## Pre-Flight Setup (2 minutes before hitting Record)

1. **Start the live stack** in 3 terminals (with `COCKPIT_MOCK_MODE=false` in `.env`):
   - `make backend` → `http://127.0.0.1:8000`
   - `make glasses` → `http://localhost:5173`
   - `make sim` → opens Even Hub Simulator (`0.9.5`) connected to `http://localhost:5173`
2. **Arrange 2 clean windows (16:9 screen, Do Not Disturb ON):**
   - **Left (~60% width):** Even Hub G2 Simulator showing the large `576 × 288` monochrome green G2 lens + the compact Cockpit Companion trace (`mind`, `skill`, `llm_worker`, `post_enrich`, latency & cost).
   - **Right (~40% width, zoom `115%`):** Live Notion workspace showing **`Cockpit Log`** (top) and **`Holdings Watchlist`** (`NTRA 29.2%`, `STX 9.1%`, `AMZN 6.9%`, bottom).
3. **Verify starting state:**
   - G2 lens rests calmly on **`COCKPIT | READY`**.
   - `NTRA`'s `Saved Question` in Notion is: *"Is Natera's underlying gross margin expanding without revenue true-ups?"* and `Next Check` is blank.

---

## 5-Clip Recording Shot List (`~2:10` total)

### Clip 1 (`0:00–0:15` · 15 s) — Hook & Calm `HOME`
- **Action on screen:** Hold on the calm `COCKPIT | READY` screen (left) and your live Notion `Holdings Watchlist` + `Cockpit Log` (right). No clicking yet.
- **Voiceover:**
  > *"Most smart-glasses AI assistants either clutter your lens or freeze for five seconds on an LLM for everything. Cockpit is a three-mind assistant for investors — Personal, Interests, and Work — wired to a live 61-position portfolio, OpenRouter Jev, and two-way Notion."*

### Clip 2 (`0:15–0:45` · 30 s) — “Logged, Logged”: Fast Path, Async Enrichment & 1-Swipe Undo
- **Beat 2A (Live voice + async enrichment):**
  - Hold mic and speak live: ***"I enjoyed the latest Acquired podcast."***
  - Jev routes in `~300 ms`: `Interests · LOG_NOTE (llm_worker: NO, post_enrich: YES, ~$0.00006)`.
  - Optional 1-second flex: **`Swipe`** once on `CONFIRM` to show in-place mind cycling, then swipe back and **`Tap`** to save.
  - G2 shows `INTERESTS | SAVED | ENRICHING` as the row lands live in Notion `Cockpit Log`. Seconds later, background enrichment patches the row in place with the resolved episode + provider-cited link, and the lens updates to **`INTERESTS | ENRICHED`**. **`Tap`** returns calmly to `HOME`.
- **Beat 2B (Local date resolution + Undo):**
  - Hold mic and speak live: ***"Remember to buy a birthday present next Friday."***
  - **`Tap`** to confirm → row appears in Notion with `Due Date` resolved locally (`2026-12-04`, zero LLM).
  - **`Swipe`** on `SAVED` → **`PERSONAL | UNDONE`** removes the local entry and archives the Notion row live.
- **Voiceover:**
  > *"Simple thoughts never block on an LLM. Jev routes in 300 milliseconds for fractions of a cent: one tap logs my podcast note to Notion, then a background worker resolves the latest episode and cited link without holding up the glasses. Personal reminders resolve dates locally — and if I change my mind, one swipe undoes it locally and archives it in Notion."*

### Clip 3 (`0:45–1:25` · 40 s) — “LLM Worker”: Natera (`29.2%`) Thesis Review + 2-Tap Verified Write
- **Beat 3A (Thesis review):**
  - Hold mic and speak live: ***"Can we review Natera earnings?"***
  - Jev wakes System-2 DeepSeek against Natera's real Nov 5 Q3 2026 release.
  - G2 renders one sourced card answering your saved question:
    `Q3 2026 GAAP GM 64.7%; ex-true-ups 63.2% (+140 bps QoQ on COGS gains). True-ups fell to $32.3M vs $52.3M in Q2.` `[NTRA Q3 2026 PR · Nov 5]`
- **Beat 3B (Follow-up + 2-tap verified write):**
  - Speak live: ***"What drove Signatera ASP and cash flow?"*** → pulls the Q3 call passage (`Signatera ASP ~$1,290 vs $1,270 Q2; cash +$5.7M to $1.10B, FCF-positive`).
  - **`Tap` once** to preview exact AI-proposed wording on **`WORK | NTRA | CONFIRM CHECK`**.
  - **`Tap` again** to commit live to Notion `Cockpit Log` **and** update Natera's `Next Check` cell in `Holdings Watchlist`.
- **Voiceover:**
  > *"When I ask about my number-one holding, Natera, Jev wakes DeepSeek against the November 5 Q3 filing and answers my exact thesis question in one glance. A quick follow-up pulls Signatera ASP and cash flow from the call — and Cockpit never writes unseen: one tap previews the proposed next-quarter check, a second tap writes it straight into Notion."*

### Clip 4 (`1:25–1:50` · 25 s) — Signature Moment: “One Release, Two Different Glances” + Abstention
- **Beat 4A (Live 2-way Notion flip):**
  - Click directly into Notion on the right side, change `NTRA`'s `Saved Question` to:
    ***"Is Signatera volume and revenue running ahead of full-year guidance?"***
  - Click **`Pull Notion`** → `NOTION | UPDATED` → **`Tap`** to show the **exact same Nov 5 release** now surfacing:
    `Q3 rev $794.0M (+34% YoY); oncology tests 339.5k (+61% YoY). FY26 guide raised $140M at mid to $3.005-$3.035B.`
- **Beat 4B (Honest abstention — 5 s):**
  - Submit: ***"What did management say about Natera warranty reserves?"*** → **`WORK | NTRA | NO MATCH`** (`No matching passage in indexed filings`).
- **Voiceover:**
  > *"Change your thesis question directly in Notion, pull it to the glasses, and the exact same earnings release gives you a completely different card. And if a filing doesn't support what you asked — like warranty reserves — Cockpit refuses to hallucinate and says 'No matching passage.'"*

### Clip 5 (`1:50–2:12` · 22 s) — Portfolio-Wide Skills (`AMZN → STX`, `INSM 4.2%`) & Close
- **Beat 5A (Peer read-across):**
  - Trigger **`Peer Read: Amazon → Seagate`** (*"What does Amazon capex mean for Seagate?"*) → **`PEER | AMZN -> STX`** connects Amazon's `~$220B` companywide FY26 cash capex guidance (heavily weighted to AWS/AI storage) to Seagate's `9.1%` HAMR Mozaic holding + proposes a check.
- **Beat 5B (Any holding in your 61-position CSV or external stock):**
  - Trigger **`Preview Insmed earnings`** → Jev passes the raw utterance straight to DeepSeek Web with zero hardcoded ticker lists, resolves `INSM`, and stamps **`PREVIEW | INSM (4.2%) | WEB`** via a 1-line lookup against `portfolio.csv`.
- **Voiceover (Close):**
  > *"It scales across your whole book: Amazon's 220-billion-dollar capex reads straight across to Seagate, and asking about any other holding — like Insmed — routes straight to live web search and stamps your exact 4.2% portfolio weight from CSV. Built on Even Hub SDK, OpenRouter Whisper, Jev, DeepSeek, and two-way Notion — 51 automated tests, packaged `.ehpk`, and zero-key mock mode ready to try. Strictly read-only research, human-confirmed writes only."*

---

## Editing & Upload Checklist
- Keep **real spoken audio** on at least 2–3 live mic prompts (Clips 2 & 3) so judges hear real STT working, and trim only empty waiting gaps (label `2x` if speeding up a 5–8 s web search).
- Export `1080p` (`1920 × 1080`) `.mp4` and upload to **YouTube (Unlisted)** with chapter timestamps (`0:00`, `0:15`, `0:45`, `1:25`, `1:50`).
- Before publishing the submission, flip `oblomovunleashed/Cockpit-G2` from `PRIVATE` to `PUBLIC` and paste the YouTube link into both `README.md` and DoraHacks.

---

## Optional API Feature Requests (5 — Collected During Build Week)

Concrete developer friction encountered while building live with Even Hub SDK (`0.0.16`), Even Hub Simulator (`0.9.5`), OpenRouter/Jev, and Notion:

1. **[Even Hub SDK]** Normalize protobuf zero-value `CLICK_EVENT` (`0`) inside the SDK before invoking `onEvenHubEvent` listeners instead of emitting `eventType: undefined` on single taps.
2. **[Even Hub SDK / Simulator]** Document the exact supported LVGL firmware glyph set (and optionally warn at compile/pack time when characters like `✓` `U+2713` are missing), plus add a `--audio-file sample.wav` simulator flag so `audioEvent.audioPcm` can be replayed deterministically in automated tests and demo recordings.
3. **[OpenRouter Decisions API / Jev]** Document prominently in quickstart examples that `noul` responses return `"probability"` (not `"noul"`) and that every question in `questions` evaluates independently against `state` (so shared retrieval passages must live inside `state`, not sibling `criteria`).
4. **[OpenRouter STT]** Support streaming chunked PCM/WebSocket transcription alongside base64 WAV on `/api/v1/audio/transcriptions` to shave ~1 s off push-to-talk smart-glasses latency.
5. **[Notion API]** Support a first-party local-dev / SSE change stream (or CLI tunnel) for property-change webhooks with near-immediate delivery on rapid consecutive edits, so local-first / loopback companion apps can react to live Notion `Saved Question` changes without hosting a public HTTPS relay or waiting on webhook aggregation windows.
