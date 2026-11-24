# Cockpit Architecture & Technical Design

## 1. Even G2 Display Contract (`576 × 288` Monochrome Green)

To eliminate full-page redraw flicker on hardware and stay well inside the Even Hub SDK container limits (max 8 text/list + 4 image containers), Cockpit initializes **3 fixed `TextContainerProperty` containers once** via `createStartUpPageContainer` and updates them in-place with `textContainerUpgrade`:

| Container | ID | Position (`x, y`) | Size (`w × h`) | Brightness (`textColor`) | Role |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `header` | `1` | `8, 4` | `560 × 44` | `2` (dim) | Mind / ticker / verdict badge (e.g., `WORK · NVDA 1/3 · HELD`) |
| `body` | `2` | `8, 52` | `560 × 184` | `4` (bright) | Single sourced finding or draft log (`<= 220` chars, plain text + Unicode glyphs only, `isEventCapture: 1`) |
| `footer` | `3` | `8, 240` | `560 × 44` | `1` (subtle) | Contextual 1-line gesture hint (`Hold: speak · Tap: confirm · Swipe: next`) |

**Display hygiene rules enforced in `glasses/src/hud.ts`:**
- Zero emoji (not supported by the fixed LVGL font); use ASCII + Unicode glyphs (`·`, `▸`, `●`, `─`, `✓`).
- Zero `rebuildPageContainer` during normal card navigation or status changes.
- Non-empty initial content on boot (no black screen on first launch).

---

## 2. Input & Gesture State Machine (`glasses/src/state.ts`)

All events arrive through `bridge.onEvenHubEvent` from both the G2 temple touchpad and the R1 ring:

| Gesture | SDK Event | Behavior |
| :--- | :--- | :--- |
| **Hold & release** | `LONG_PRESS_EVENT` → `LONG_PRESS_RELEASE_EVENT` | Starts/stops 16 kHz mono PCM recording (`audioControl`), encodes 44-byte RIFF/WAV header, POSTs to `/api/voice` |
| **Single tap** | `CLICK_EVENT` | Confirms a pending log / next-quarter check (`POST /api/confirm`), or accepts the AI-drafted check on a finding card |
| **Swipe up / down** | `SCROLL_TOP_EVENT` / `SCROLL_BOTTOM_EVENT` | Cancels a pending draft if in `CONFIRM_WRITE`; otherwise cycles holdings (`1/3 → 2/3 → 3/3`) |
| **Double tap** | `DOUBLE_CLICK_EVENT` | Returns to briefing deck from a sub-card; on root briefing screen calls `bridge.shutDownPageContainer(1)` (Even Hub QA requirement) |

---

## 3. System-1 vs. System-2 Pipeline (`backend/app/`)

### Stage 1 — Transcription (`services/openrouter_stt.py`)
- Accepts raw WAV bytes from `POST /api/voice`, base64-encodes them, and calls `POST https://openrouter.ai/api/v1/audio/transcriptions`.

### Stage 2 — System-1 Triage (`services/jev_router.py`)
- Calls `POST https://openrouter.ai/api/alpha/decisions` (`model: "typesafe/jev-latest"`).
- Typed questions asked in a single low-latency request:
  - `mind` (`choice`): `work` | `personal` | `interests`
  - `action` (`choice`): `log_note` | `log_task` | `brief_holding` | `follow_up_source` | `draft_next_check` | `clarify`
  - `holding` (`choice`): `NVDA` | `HOLDING_2` | `HOLDING_3` | `none`
  - `needs_llm_worker` (`noul`): probability float deciding whether to bypass System-2 synthesis entirely.
- **Fast Path (`log_note` / `log_task`):** No LLM generation call. Immediately returns a `CONFIRM_WRITE` card to G2.
- **Deep Path (`brief_holding` / `follow_up_source`):** Wakes the Work worker.

### Stage 3 — Work Worker (`services/retrieval.py` + `services/jev_router.py` + `services/llm_worker.py`)
1. Reads the holding's current `saved_question` from SQLite (synced from Notion when enabled).
2. Queries SQLite FTS5 over pre-chunked real issuer documents (storing `doc_title`, `doc_date`, `page_ref`, `excerpt`, `tags`).
3. Uses **Jev** (`choice`) to pick the single candidate passage that best answers the wearer's saved question + verdict (`HELD` | `AHEAD` | `WATCH`).
4. Uses a fast OpenRouter text model to format the finding into `<= 200` chars with source attribution and propose a 1-tap `suggested_next_check`.

### Stage 4 — Confirmed Writes & Background Jobs (`services/notion_sync.py` + `services/job_runner.py`)
- Only explicit user confirmation (`POST /api/confirm`) writes to SQLite `journal_entries` and (when configured) Notion.
- When a holding's `saved_question` is updated (either from G2 or edited in Notion), the async `job_runner` recomputes and caches the holding's top card so opening Cockpit is instant.

---

## 4. Notion Database Schemas (Track 4 Integration)

### Database A: `Cockpit Log` (`NOTION_LOG_DATABASE_ID`)
| Property | Type | Example |
| :--- | :--- | :--- |
| `Entry` | Title | `Buy Sam's birthday present` |
| `Mind` | Select (`Work`, `Personal`, `Interests`) | `Personal` |
| `Kind` | Select (`Note`, `Task`, `Check`) | `Task` |
| `Ticker` | Rich text (optional) | `NVDA` |
| `Source` | Rich text (optional) | `Aug FY27 call, p. 7` |
| `Logged At` | Date | `2026-11-24T13:15:00Z` |

### Database B: `Holdings Watchlist` (`NOTION_HOLDINGS_DATABASE_ID`)
| Property | Type | Example |
| :--- | :--- | :--- |
| `Ticker` | Title | `NVDA` |
| `Company` | Rich text | `NVIDIA Corp.` |
| `Saved Question` | Rich text | `Does NVIDIA expect the margin trough to get deeper?` |
| `Status` | Select (`HELD`, `AHEAD`, `WATCH`, `PENDING`) | `HELD` |
| `Latest Finding` | Rich text | `Q4 FY27 gross margin guide · 71–72%. Flagged in August.` |
| `Source` | Rich text | `Q3 FY27 PR · Nov 17` |
| `Next Check` | Rich text | `Check whether Q1 FY28 price increases are on schedule` |
