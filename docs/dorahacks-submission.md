# Cockpit G2 — DoraHacks Submission Kit

Ready-to-paste fields, gallery screenshot mapping, and checklist for **Heads Up: Even Realities Hackathon · Track 04 (AI Assistants & Work)**.

---

## 1. Quick Form Fields (Copy & Paste)

- **BUIDL Name:** `Cockpit G2`
- **One-Line Tagline:** `The Control Centre for your second Brain — 3-mind AI cockpit on Even G2, OpenRouter Jev & two-way Notion.`
- **Track:** `Track 04 · AI Assistants & Work`
- **Ecosystem / Tech Tags:** `Even Realities G2`, `Even Hub SDK`, `OpenRouter`, `TypeSafe Jev`, `DeepSeek`, `Whisper`, `Notion`, `FastAPI`, `TypeScript`, `SQLite FTS5`
- **GitHub Repository:** `https://github.com/oblomovunleashed/Cockpit-G2` *(flip repo from `PRIVATE` to `PUBLIC` before submitting!)*
- **Demo Video (`2m 04s`):** Upload `/Users/dominic/Desktop/Cockpit - Your Control Centre (Live Demo).mp4` to **YouTube (Unlisted)** and paste the URL here.
- **Avatar / Logo:** `assets/cockpit-logo-512.png`
- **Cover Banner (`16:9`):** `docs/screenshots/01-cover-cockpit-g2.png` *(or `assets/cockpit-banner.png`)*

### Gallery Screenshots (Upload in Exact Order)
1. `docs/screenshots/01-cover-cockpit-g2.png` — **Cockpit G2** · Title card & 3-mind architecture
2. `docs/screenshots/02-calm-home-even-g2.png` — **Calm Default Home** · `COCKPIT | READY` on Even Hub Simulator `0.9.5` (`576 × 288`)
3. `docs/screenshots/03-interests-live-enrichment.png` — **Fast Path + Async Enrichment** · `Jev 461 ms ($0.00006, no blocking LLM)` → live `EP.502 Scott Wu` + cited Spotify link in Notion
4. `docs/screenshots/04-personal-local-date-undo.png` — **Local Date + 1-Swipe Live Undo** · `Jev 259 ms` → `Fri 4 Dec 2026` → 1 swipe deletes locally & archives live in Notion (`archived: true`)
5. `docs/screenshots/05-work-natera-verified-write.png` — **2-Tap Verified Write** · Natera (`29.2%`) Q3 2026 review + call follow-up → preview exact check before writing to Notion Log & Watchlist
6. `docs/screenshots/06-two-way-notion-two-glances.png` — **Signature Moment: One Release, Two Glances** · Edit `Saved Question` live in Notion → pull → exact same Nov 5 release flips card to `+56%` Signatera MRD & `$140M` guide raise
7. `docs/screenshots/07-zero-hallucination-abstention.png` — **Honest Abstention Guard** · `Warranty reserves?` → `WORK | NTRA | NO MATCH` (zero fabricated claims)
8. `docs/screenshots/08-61-position-live-web-insmed.png` — **Zero Hardcoded Tickers: CSV + Live Web** · Live Jev (`203 ms`) → DeepSeek Web → 1-line `portfolio.csv` lookup stamps `PREVIEW | INSM (4.2%) | WEB` from a drop-in sample watchlist
9. `docs/screenshots/09-architecture-and-safety.png` — **Stack, Telemetry & Safety Summary** · 51 automated tests, `15/15` live smoke checks, packaged `.ehpk`

---

## 2. Full BUIDL Description (Paste Directly Into DoraHacks Markdown Editor)

```markdown
# Cockpit G2 — The Control Centre for your second Brain

**One day. Too many places to put it.** Stopping to sort every thought—a quick podcast takeaway, a personal reminder for next Friday, or what actually changed in your largest holding's earnings release—interrupts whatever you are doing.

Most smart-glasses AI assistants either spam your `576 × 288` lens with chatty walls of text or stall for 5+ seconds waiting on an LLM for every single utterance.

**Cockpit G2** is a calm, voice-first **three-mind assistant** (**Work**, **Personal**, and **Interests**) for **Even Realities G2**, powered by a **three-tier compute hierarchy** using **OpenRouter (Whisper + TypeSafe Jev + DeepSeek Web)** and **live two-way Notion**.

---

## Why Cockpit G2 Is Different: System-1 Jev vs. System-2 LLM

Every spoken thought enters through **TypeSafe Jev** (`POST /api/alpha/decisions` on OpenRouter) in **~200–460 ms** at **`~$0.00006/call`** before a generative LLM is ever touched:

| Utterance (Hold temple / ring / mic) | Tier & Live Jev Decision | Blocking LLM on Lens? | What Happens on Even G2 + Live Notion |
| :--- | :--- | :---: | :--- |
| *"I really liked the latest Invest Like The Best podcast."* | **Tier 1 → Async Tier 2** · `Interests · LOG_NOTE` (`461 ms`, `$0.00006`) | **No** *(async)* | Instant `CONFIRM` → 1 tap saves to Notion (`SAVED \| ENRICHING`); background worker resolves **EP.502 with Scott Wu** + provider-cited Spotify link and updates both lens and Notion in place. |
| *"Remember to book dinner with my wife next Friday."* | **Tier 1** · `Personal · LOG_TASK` (`259 ms`, `$0.00006`) | **No** | Instant `CONFIRM` → 1 tap saves with `Due Date: 2026-12-04` resolved locally (zero LLM). **1 swipe** on `SAVED` triggers **live Undo**, deleting locally and archiving the Notion row (`archived: true`). |
| *"Can we review Natera earnings?"* | **Tier 1 + Tier 2** · `Work · BRIEF_HOLDING` (`202 ms`) | **Yes** | Pulls latest Notion `Saved Question`, checks real Nov 5 Q3 2026 primary filings via SQLite FTS5 + Jev, renders **1 sourced card** (`63.2% ex-true-up GM, +140 bps QoQ`). |
| *"What drove Signatera ASP and cash flow?"* | **Tier 1 + Tier 2** · `Work · FOLLOW_UP_SOURCE` (`206 ms`) | **Yes** | Retrieves exact Q3 call passage (`~$1,290 ASP; cash $1,097.2M, FCF-positive`). **Tap 1** previews exact proposed check (`CONFIRM CHECK`); **Tap 2** writes live to Notion Log & updates `Next Check` on your watchlist. |
| *"Can you please preview Insmed earnings?"* | **Tier 1 + Tier 2 Web** · `Work · EARNINGS_PREVIEW` (`203 ms`, `untracked`) | **Yes** *(web)* | **Zero hardcoded tickers:** Jev routes `untracked` directly to DeepSeek Web, resolves `INSM`, and a 1-line dict lookup in `portfolio.csv` stamps **`PREVIEW \| INSM (4.2%) \| WEB`** (external stocks work identically). |

---

## Signature Moment: “One Release, Two Different Glances” (Live Two-Way Notion)

As a concrete **Work** example, Cockpit ships with a drop-in sample watchlist (`backend/app/data/portfolio.csv`) and indexes real Oct–Nov 2026 primary earnings releases for three sample holdings (**Natera `NTRA`**, **Seagate `STX`**, and **Amazon `AMZN`**) — strictly read-only, and adaptable to any user CSV or public company with zero code changes:

1. With `NTRA`'s `Saved Question` in Notion set to *"Is Natera's underlying gross margin expanding without revenue true-ups?"*, Cockpit surfaces `ex-true-ups 63.2% (+140 bps QoQ); true-ups fell to $32.3M vs $52.3M` `[NTRA Q3 2026 PR · Nov 5]`.
2. Edit that single `Saved Question` cell directly in Notion to *"Is Signatera volume and revenue running ahead of full-year guidance?"* and pull to the glasses: the **exact same Nov 5 release** flips the G2 card to **`WORK | NTRA 1/3 | AHEAD` — `Signatera-led clinical MRD ~316,000 units (+56% YoY); Q3 revenue $794.0M (+34.1% YoY); FY26 guide raised $140M`**.
3. Ask something the filing doesn't support (*"What did management say about Natera warranty reserves?"*): Cockpit refuses to guess and returns **`WORK | NTRA | NO MATCH`** (`No matching passage in indexed filings`).

---

## Built for Even G2 (`576 × 288`), Voice & Safety

- **Calm Default `HOME`:** Boots into `COCKPIT | READY` instead of jumping straight into ticker spam, and returns calmly after Personal/Interests saves.
- **Fast Read & Write Without Long Dictation:**
  - **1-tap confirm** on fast Personal/Interests logs, with **Swipe on `CONFIRM`** to cycle Mind/Kind in place if misclassified.
  - **2-tap preview before write** on every AI-proposed Work check — AI never writes unseen.
  - **Stay-until-tap receipt + 1-swipe live Undo** (`POST /api/journal/{id}/undo`) removing locally and archiving the Notion page (`archived: true`).
- **Even G2 Firmware Craft:** Fixed 3-container LVGL layout (`header / body / footer`), serialized `G2HudRenderer` upgrade queue, ASCII glyph sanitizer (`✓` → `OK:`, `→` → `->`, `·` → `|`) to avoid missing LVGL glyphs, protobuf zero-value `CLICK_EVENT` handling, and native Even Hub Simulator `0.9.5` CoreAudio 16 kHz PCM capture (`bridge.audioControl`).
- **Strict Safety & Provenance:**
  - Strictly read-only financial inputs — **no trading, no brokerage execution, no buy/sell advice**.
  - Background enrichment only publishes clickable links verified in OpenRouter provider `url_citation` annotations (model-invented URLs are dropped).
  - Transparent partial-failure receipts: local SQLite commits first; HUD distinguishes `Saved to Notion + Journal.` vs. local-only `Saved to Journal.`.

---

## Architecture & Measured Performance

```text
Even G2 Glasses / Even Hub Simulator 0.9.5 (576×288 green HUD, 16 kHz CoreAudio PCM mic)
        │  @evenrealities/even_hub_sdk (0.0.16) · packaged desktop cockpit.ehpk
        ▼
glasses/ (Vite + TypeScript · 3 fixed LVGL containers · serialized upgrades)
        │  HTTP (JSON + 16 kHz RIFF/WAV -> desktop loopback 127.0.0.1:8000)
        ▼
backend/ (Python 3.13 · FastAPI · SQLite + FTS5 · Drop-in CSV Registry · Async Queues)
   ├── OpenRouter STT          openai/whisper-large-v3-turbo     (~2.1 s spoken transcription)
   ├── OpenRouter Jev          ~typesafe/jev-latest              (~200–460 ms, ~$0.00006/call)
   ├── OpenRouter System-2     deepseek/deepseek-v4.1-flash-1017 (+ web plugin for any CSV or external stock)
   └── Live Two-Way Notion     Cockpit Log (writes + async enrichment + Undo archive) & Holdings Watchlist (read + patch)
```

- **Local compute (Tier 0):** SQLite FTS5 retrieval & indexed `earnings_preview` render in **`< 5 ms`** (`$0`).
- **System-1 routing (Tier 1):** OpenRouter Jev averages **`~200–460 ms`** live (`~$0.00006/call`) with **zero hardcoded tickers**.
- **Verification:** **51 automated tests** (`37` Python `pytest` + `14` TypeScript Node tests), **`15/15` live OpenRouter + Notion smoke checks passing**, reproducible `.ehpk` (`min_app_version 2.2.10`, `g2-microphone` declared; desktop loopback setup clearly documented in README), and a **zero-key mock mode** (`COCKPIT_MOCK_MODE=true`) so evaluators can run it locally in 60 seconds without API keys.

---

## Business Potential

Active investors, buy-side analysts, and portfolio managers face acute information overload on earnings weeks and have high willingness to pay for calm, zero-hallucination, thesis-anchored briefings that fit naturally into walking, commuting, and between-meeting moments—while the underlying 3-mind architecture (`Work / Personal / Interests` + two-way Notion + Jev routing) generalizes cleanly to any knowledge worker's Notion second brain.

---

## 5 Concrete API & SDK Feature Requests (Collected During Build Week)

1. **[Even Hub SDK]** Normalize protobuf zero-value `CLICK_EVENT` (`0`) inside the SDK before invoking `onEvenHubEvent` listeners instead of emitting `eventType: undefined` on single taps.
2. **[Even Hub SDK / Simulator]** Document the exact supported LVGL firmware glyph set (and optionally warn at pack time when characters like `✓` `U+2713` are missing), plus add a `--audio-file sample.wav` simulator flag so `audioEvent.audioPcm` can be replayed deterministically in automated tests and demo recordings.
3. **[OpenRouter Decisions API / Jev]** Highlight prominently in quickstart docs that `noul` responses return `"probability"` (not `"noul"`) and that sibling questions in `questions` evaluate independently against `state` (so shared retrieval passages must live in `state`).
4. **[OpenRouter STT]** Support streaming chunked PCM / WebSocket transcription alongside base64 WAV on `/api/v1/audio/transcriptions` to shave ~1 s off push-to-talk smart-glasses latency.
5. **[Notion API]** Provide a first-party local-dev / SSE change stream (or CLI tunnel) for property-change webhooks with low-latency delivery on rapid consecutive edits, so local-first companion apps can react to live `Saved Question` changes without hosting a public HTTPS relay.
```

---

## 3. Final Pre-Submit Checklist (Before Nov 29, 19:59 UTC)

- [ ] **GitHub visibility:** Flip `oblomovunleashed/Cockpit-G2` from `PRIVATE` to `PUBLIC`.
- [ ] **YouTube upload:** Upload `/Users/dominic/Desktop/Cockpit - Your Control Centre (Live Demo).mp4` as **Unlisted** with chapter timestamps (`0:00`–`1:54`, `2:04` total).
- [ ] **README video link:** Add the YouTube URL at the top of `README.md` (and push).
- [ ] **DoraHacks BUIDL page:** Paste the Name, Tagline, Description, GitHub link, YouTube link, Avatar (`assets/cockpit-logo-512.png`), Cover (`docs/screenshots/01-cover-cockpit-g2.png`), and Gallery Screenshots (`docs/screenshots/02-...` through `09-...`).
- [ ] **Optional bonus:** Create a GitHub Release (`v0.2.0`) attaching `glasses/cockpit.ehpk` (clearly labeled for desktop simulator / local loopback setup) so judges can download it in 1 click.
