# Cockpit — Heads-Up Command Centre for Even G2

> **“Logged, logged — LLM worker.”**
> One universal voice gesture on Even Realities G2 smart glasses. **Jev** (System 1 on OpenRouter) decides in milliseconds whether to just file a thought/task straight to **Notion** — or wake the **Work** LLM worker to compare your saved thesis question against real earnings documents and return **one sourced card** at a glance.

Built for **[Heads Up: Even Realities Hackathon](https://dorahacks.io/hackathon/even-realities/detail)** (Nov 23–29, 2026) — **Track 04 · AI Assistants & Work** (`OpenRouter` + `Notion`).

---

## Core Idea

Current smart-glasses assistants treat *"remember to buy a birthday present"* the same as *"did NVIDIA's margin thesis break?"* — sending both to a slow, chatty LLM that dumps paragraphs onto a `576 × 288` monochrome display.

Cockpit separates **System 1 triage** from **System 2 retrieval & synthesis**:

| Utterance (Hold temple / ring) | Jev Decision (System 1) | LLM Worker? | What Happens on G2 |
| :--- | :--- | :--- | :--- |
| *"This podcast on TSMC was great"* | `Interests · LOG_NOTE` | **No** | Instant confirm card → **1 tap** logs to Notion |
| *"Remember to buy a birthday present"* | `Personal · LOG_TASK` | **No** | Instant confirm card → **1 tap** logs to Notion |
| *"Can we review NVIDIA earnings?"* | `Work · BRIEF_HOLDING` | **Yes** | Retrieves passage matching your saved question → **1 sourced card** |

### One Release, Two Different Glances (Work Demo)

Using a read-only sample portfolio of three holdings backed by real issuer filings (starting with NVIDIA's Q2 FY27 earnings call and Q3 FY27 earnings release), the exact same release produces a different first card depending on the question you saved before earnings:

| Question saved before earnings | What the wearer sees on G2 |
| :--- | :--- |
| *“Does NVIDIA expect the margin trough to get deeper?”* | `Q4 FY27 gross margin guide · 71–72%. Management had already flagged that range in August.` |
| *“Is revenue running ahead of management's guidance?”* | `Q3 FY27 revenue · $115.8bn. About 7% above the company's $108bn guidance midpoint.` |

From the margin card, ask *"What did management expect to bring margins back up?"* to pull the August call passage on Q1 FY28 price increases, then **1-tap accept** the drafted next-quarter check (or dictate your own) to save it to your Notion research journal.

---

## Architecture

```text
Even G2 Glasses / Even Hub Simulator (576x288 monochrome green, 16kHz PCM mic)
        │  @evenrealities/even_hub_sdk (0.0.16)
        ▼
glasses/ (Vite + TypeScript plugin inside Even App WebView)
        │  HTTP (JSON + WAV audio)
        ▼
backend/ (Python 3.13 · FastAPI · SQLite + FTS5 · Async Job Runner)
   ├── OpenRouter STT          POST /api/v1/audio/transcriptions (base64 WAV)
   ├── OpenRouter Jev          POST /api/alpha/decisions (typesafe/jev-latest)
   ├── OpenRouter Text Model   POST /api/v1/chat/completions (Work synthesis only)
   └── Notion API              Live 2-way sync (Cockpit Log + Holdings Watchlist)
```

See [docs/architecture.md](docs/architecture.md) for the G2 gesture map, Jev typed decision schema, and Notion database schema, and [docs/demo-script.md](docs/demo-script.md) for the ≤3-minute split-screen demo storyboard.

---

## Quickstart

### Prerequisites
- **Python 3.11+** (tested on Python 3.13)
- **Node.js 20+** and **npm** (tested on Node 23)
- Optional: `@evenrealities/evenhub-simulator` (`0.9.5`) and `@evenrealities/evenhub-cli` (`0.2.0`)

### 1. Configure Environment
```bash
cp .env.example .env
```
By default, `COCKPIT_MOCK_MODE=true` lets you run the full vertical slice locally (deterministic Jev + retrieval + SQLite journal) with **zero API keys** so you can verify the simulator flow immediately.

To run live against OpenRouter and Notion, set `COCKPIT_MOCK_MODE=false` and fill in `OPENROUTER_API_KEY`, `NOTION_TOKEN`, `NOTION_LOG_DATABASE_ID`, and `NOTION_HOLDINGS_DATABASE_ID` in `.env`. **Never commit `.env`.**

### 2. Install Dependencies
```bash
make setup
```

### 3. Run Tests & Typecheck
```bash
make test
make typecheck
```

### 4. Start Backend, Glasses App, and Even Hub Simulator
In three terminals:
```bash
# Terminal 1 — FastAPI backend (http://127.0.0.1:8000)
make backend

# Terminal 2 — Even G2 TypeScript app (http://127.0.0.1:5173)
make glasses

# Terminal 3 — Even Hub Simulator (576x288 LVGL preview + WebView companion)
make sim
```

---

## Safety, Provenance & Hackathon Disclosures

- **Read-only financial data:** Cockpit never holds funds, places trades, connects to brokerage execution, or offers financial advice. All financial documents are strictly read-only; confirmed writes go exclusively to the user's personal research journal (SQLite + Notion).
- **Build window:** All code in this repository is built during the Nov 23–29, 2026 hackathon build window.
- **AI-assisted development disclosure:** Built solo with AI-assisted coding tools (disclosed per organizer Q&A guidelines).
- **API feature requests (tracked during build week):** Maintained in [docs/demo-script.md](docs/demo-script.md).
