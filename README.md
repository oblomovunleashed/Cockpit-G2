![Cockpit — Heads-Up Command · Even G2](assets/cockpit-banner.png)

# Cockpit — Heads-Up Command Centre for Even G2

> **“Logged, logged — LLM worker.”**
> One universal hold-to-speak gesture across **Work**, **Personal**, and **Interests** on Even Realities G2 smart glasses (`576 × 288` monochrome green). **Jev** (System 1 on OpenRouter, `~220 ms`) decides whether to file a thought straight to **Notion** (with optional async enrichment behind the scenes) or wake the **System-2** worker for sourced earnings research — returning **one calm, verifiable card** at a glance.

Built for **[Heads Up: Even Realities Hackathon](https://dorahacks.io/hackathon/even-realities/detail)** (Nov 23–29, 2026) — **Track 04 · AI Assistants & Work** (`OpenRouter` + `Notion`).

---

## Why Cockpit?

Most smart-glasses assistants treat *"Remember to buy a birthday present"* the same as *"Did NVIDIA's margin thesis break?"* — sending every fleeting thought to a slow, chatty LLM that dumps paragraphs onto a narrow monochrome lens.

Cockpit pairs a **calm default `HOME` screen** (`COCKPIT | READY`) with a **three-tier compute hierarchy**:

| Utterance (Hold temple / ring / mic) | Tier & Jev Decision (`~220 ms`) | On-Lens LLM? | What Happens on G2 + Notion |
| :--- | :--- | :--- | :--- |
| *"Remember to buy a birthday present"* | **Tier 1** · `Personal · LOG_TASK` (`enrich: no`) | **No** | Instant `CONFIRM` card → **1 tap** logs to Notion `Cockpit Log` (`$0.000057`) |
| *"I enjoyed the latest Acquired podcast"* | **Tier 1 → Async Tier 2** · `Interests · LOG_NOTE` (`enrich: yes`) | **No** *(async)* | Instant `CONFIRM` + `SAVED` on glasses; background worker resolves latest episode + URL and patches Notion in place (never guesses unnamed shows → `Needs Detail`) |
| *"Preview NVIDIA before earnings"* | **Tier 0** · `Work · EARNINGS_PREVIEW` (tracked) | **No** | `< 5 ms` pre-call checklist straight from SQLite + Notion (`Saved Question`, prior baseline, open `Next Check`) |
| *"Can you please preview Alphabet earnings?"* | **Tier 1 + Tier 2 Web** · `Work · EARNINGS_PREVIEW` (any company) | **Yes** *(web)* | Works **held or not held** — date-aware OpenRouter web preview (`PREVIEW \| GOOGL \| WEB`) + stageable watchpoint |
| *"Can we review NVIDIA earnings?"* | **Tier 1 + Tier 2** · `Work · BRIEF_HOLDING` | **Yes** | Pulls latest Notion question first, checks real Q3 FY27 filing via FTS5 + Jev, renders **1 sourced delta card** (`HELD` / `AHEAD` / `WATCH`) |
| *"What's the read-across from TSMC to NVIDIA?"* | **Tier 1 + Tier 2** · `Work · PEER_READ_ACROSS` | **Yes** | Connects real Oct 2026 TSMC Q3 evidence to NVIDIA's thesis + proposes a 2-tap verified `Next Check` |

---

## Signature Work Demo: “One Release, Two Different Glances”

Cockpit indexes real Oct–Nov 2026 primary issuer filings for three sample watchlist holdings (**NVDA**, **MSFT**, **TSM**) — strictly read-only. Changing the single `Saved Question` on a Notion watchlist row changes the first card you see from the exact same release:

| `Saved Question` in Notion (`NVDA`) | What the wearer sees on G2 (`576 × 288`) |
| :--- | :--- |
| *“Does NVIDIA expect the margin trough to get deeper?”* | `Q4 FY27 gross margin guide \| 71–72%. Management had already flagged that range in August.` `[Q3 FY27 PR · Nov 17]` |
| *“Is revenue running ahead of management's guidance?”* | `Q3 FY27 revenue \| $115.8bn. About 7% above the company's $108bn guidance midpoint.` `[Q3 FY27 PR · Nov 17]` |

- **Follow-up retrieval + honest abstention:** Ask *"What did management expect to bring margins back up?"* to retrieve the August FY27 call (`p. 7`, Q1 FY28 price increases), or ask an unsupported question (*"Warranty reserves?"*) to see Cockpit honestly return **`No matching passage`** instead of hallucinating.
- **Zero-surprise human-in-the-loop controls:**
  - **2-tap preview before save:** AI-drafted checks are never written unseen — tap once to inspect exact wording on `CONFIRM CHECK`, tap again to save.
  - **Fix misclassifications on the glasses:** On any `CONFIRM` card, **`Swipe`** cycles destination in place (`Interests Note` ↔ `Personal Task` ↔ `Work Check`) without re-speaking; **`Double-tap`** discards.
  - **Stay-until-tap receipt + Undo:** After saving, the `SAVED` card stays on screen until you **`Tap`** back to calm `HOME` (or **`Swipe`** to **Undo**, deleting the SQLite row, restoring any prior `Next Check`, and archiving the Notion page).

---

## Architecture

```text
Even G2 Glasses / Even Hub Simulator (576x288 monochrome green, 16 kHz CoreAudio PCM mic)
        │  @evenrealities/even_hub_sdk (0.0.16)
        ▼
glasses/ (Vite + TypeScript plugin · 3 fixed LVGL text containers · serialized upgrades)
        │  HTTP (JSON + 16 kHz RIFF/WAV)
        ▼
backend/ (Python 3.13 · FastAPI · SQLite + FTS5 · Async Enrichment & Recompute Queues)
   ├── OpenRouter STT          POST /api/v1/audio/transcriptions  (openai/whisper-large-v3-turbo)
   ├── OpenRouter Jev          POST /api/alpha/decisions          (~typesafe/jev-latest, ~220 ms)
   ├── OpenRouter Synthesis    POST /api/v1/chat/completions      (deepseek/deepseek-v4.1-flash-1017 + web plugin)
   └── Notion API              Live 2-way sync (Cockpit Log w/ enrichment + Holdings Watchlist)
```

- **Technical architecture, G2 gesture map & Notion schema:** [docs/architecture.md](docs/architecture.md)
- **≤3-minute split-screen demo storyboard & 5 API feature requests:** [docs/demo-script.md](docs/demo-script.md)
- **Live OpenRouter, Jev, Notion & Even Hub Simulator learnings:** [docs/live-wiring-learnings.md](docs/live-wiring-learnings.md)

---

## Quickstart

### Prerequisites
- **Python 3.11+** (tested on Python 3.13)
- **Node.js 20+** and **npm** (tested on Node 23)
- `@evenrealities/evenhub-simulator` (`0.9.5`) and `@evenrealities/evenhub-cli` (`0.1.14`) are installed automatically via `make setup`.

### 1. Install Dependencies
```bash
make setup
```

### 2. Configure Environment (Offline Mock Mode or Live OpenRouter + Notion)
```bash
cp .env.example .env
```
- **Zero-key mock mode (default):** Leave `COCKPIT_MOCK_MODE=true` to run the full vertical slice locally (deterministic Jev router, FTS5 retrieval, local enrichment stubs, SQLite journal, and Even Hub Simulator) with no API keys.
- **Live OpenRouter + Notion mode:**
  1. Set `COCKPIT_MOCK_MODE=false` and add `OPENROUTER_API_KEY`.
  2. Create an internal Notion integration, share a blank Notion page with it, set `NOTION_TOKEN` + `NOTION_PARENT_PAGE_ID` in `.env`, and run:
     ```bash
     make setup-notion
     ```
     This auto-creates both **`Cockpit Log`** (with `Enrichment`, `Enriched`, `Due Date`, `Link`) and **`Holdings Watchlist`** (seeded with `NVDA`, `MSFT`, `TSM`) and writes their database IDs into `.env`.
  3. Verify OpenRouter STT, Jev, DeepSeek, and Notion end-to-end:
     ```bash
     make smoke-live
     ```
  *(Never commit `.env` or `backend/data/cockpit.db` — both are gitignored.)*

### 3. Run the Test Suite & Build
```bash
make test       # 30 Python pytest tests + 14 TypeScript Node tests
make build      # tsc --noEmit && vite build
```

### 4. Launch Backend, Glasses App, and Even Hub Simulator
In three terminals:
```bash
# Terminal 1 — FastAPI backend (http://127.0.0.1:8000)
make backend

# Terminal 2 — Even G2 TypeScript companion & HUD app (http://127.0.0.1:5173)
make glasses

# Terminal 3 — Even Hub G2 Simulator (576x288 LVGL display + native CoreAudio mic)
make sim
```

### 5. Build the Even Hub `.ehpk` Package
```bash
make pack
```
Produces `glasses/cockpit.ehpk` (`min_app_version 2.2.10`, stamped against SDK `0.0.16` via `@evenrealities/evenhub-cli`).

---

## Safety, Provenance & Hackathon Disclosures

- **Read-only financial data:** Cockpit never holds funds, places trades, connects to brokerage execution, or offers investment advice. All financial documents and web lookups are strictly read-only; confirmed writes go exclusively to the user's personal Notion/SQLite journal.
- **Build window:** All code and assets in this repository were created during the Nov 23–29, 2026 **Heads Up: Even Realities Hackathon** build window.
- **AI-assisted development disclosure:** Built solo with AI-assisted coding tools (disclosed per hackathon organizer guidelines).
- **API feature requests (up to 5):** Documented in [docs/demo-script.md](docs/demo-script.md).
