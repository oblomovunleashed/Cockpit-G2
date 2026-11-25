# Cockpit Architecture & Technical Design

## 1. Even G2 Display Contract (`576 × 288` Monochrome Green)

To eliminate full-page redraw flicker on hardware and stay well inside the Even Hub SDK container limits (max 8 text/list + 4 image containers), Cockpit initializes **3 fixed `TextContainerProperty` containers once** via `createStartUpPageContainer` and updates them in-place with serialized `textContainerUpgrade` calls:

| Container | ID | Position (`x, y`) | Size (`w × h`) | Brightness (`textColor`) | Role |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `header` | `1` | `8, 4` | `560 × 44` | `2` (dim) | Mode / mind / ticker / status badge (e.g. `COCKPIT \| READY`, `WORK \| NVDA 1/3 \| HELD`, `PREVIEW \| GOOGL \| WEB`) |
| `body` | `2` | `8, 52` | `560 × 184` | `4` (bright) | Single sourced finding, calm `HOME` summary, or draft log (`<= 240` chars, `isEventCapture: 1`) |
| `footer` | `3` | `8, 240` | `560 × 44` | `1` (subtle) | Contextual 1-line gesture hint (e.g. `Hold: speak \| Swipe: watchlist \| 2x: exit`) |

**Display & bridge hygiene enforced in `glasses/src/hud.ts`:**
- **LVGL firmware font hygiene:** `sanitizeLine()` strips astral emoji and replaces glyphs absent from G2's LVGL font (`✓` `U+2713` → `OK:`, `→` → `->`, `●` → `[REC]`, `…` → `...`, `·` → `|`) so zero missing-glyph boxes appear on lens.
- **Serialized upgrade queue:** `G2HudRenderer.render()` chains container upgrades through a promise queue so rapid state transitions (e.g. cancelling a mid-render `LISTENING` card) never interleave partial container writes.
- **Clean recovery after root exit:** Calling `bridge.shutDownPageContainer(1)` resets `initialized = false` so subsequent renders recreate startup containers instead of failing on destroyed IDs.
- **Protobuf zero-value `CLICK_EVENT` normalization (`glasses/src/input.ts`):** Handles `eventType === undefined` on present `textEvent` / `listEvent` / `sysEvent` envelopes while filtering out non-gesture `imuData` / `systemExitReasonCode` envelopes.

---

## 2. Calm `HOME`-First State Machine & Gesture Map (`glasses/src/state.ts`, `glasses/src/input.ts`)

Cockpit boots and rests on **`HOME` (`COCKPIT | READY`)** — never forcing a Work ticker onto Personal or Interests workflows. While on `HOME`, `activeHolding` is `null` so unrelated voice notes never inherit a stale ticker.

| ViewMode | Hold & Release | Single Tap (`CLICK`) | Swipe (`SCROLL_TOP` / `BOTTOM`) | Double-Tap (`DOUBLE_CLICK`) |
| :--- | :--- | :--- | :--- | :--- |
| **`HOME`** (default) | Capture voice (16 kHz PCM → `/api/voice`) | No-op (calm) | Opens `BRIEFING` watchlist (`NVDA 1/3`) | Exits app (`shutDownPageContainer(1)`) |
| **`BRIEFING`** (Work watchlist) | Capture voice (passes active ticker context) | Previews holding's `suggested_next_check` on `CONFIRM` card | Cycles `NVDA ↔ MSFT ↔ TSM` | Returns to calm `HOME` |
| **`CONFIRM_WRITE`** | Capture voice | Atomically commits to SQLite + Notion → `SAVED` | **Reclassifies in place** (`Interests Note ↔ Personal Task ↔ Work Check`) via `PATCH /api/pending/{id}` | Cancels pending write → returns to `HOME` |
| **`SAVED`** (receipt / enriched / undone / Notion updated) | Capture voice | Dismisses back to `returnMode` (`HOME` for Personal/Interests/ad-hoc; `BRIEFING` for tracked holding check or `NOTION \| UPDATED`) | **Undo** (`POST /api/journal/{id}/undo` — deletes SQLite row, restores prior `Next Check`, archives Notion page) | Returns to calm `HOME` |
| **`RESULT`** (follow-up / preview / peer read / ad-hoc web / `NO MATCH`) | Capture voice | If `suggestedCheck` present, previews wording on `CONFIRM` card (2-tap safety); otherwise stays in `RESULT` (never falls through to old holding check) | Opens/cycles `BRIEFING` watchlist | Returns to calm `HOME` |

---

## 3. Three-Tier Compute & Voice Pipeline (`backend/app/`)

### Stage 1 — Audio Capture & Transcription (`glasses/src/audio.ts` + `services/openrouter_stt.py`)
- **Inside Even Hub Simulator / G2 glasses:** Uses native `bridge.audioControl(true, AudioInputSource.Glasses)` (CoreAudio on macOS simulator, temple mic on G2) streaming 16 kHz 16-bit LE mono PCM chunks into a 44-byte RIFF/WAV buffer (30s safety cap, pending-release cancellation across both `showCard()` and `audioControl()` awaits).
- **Standalone browser preview:** Falls back to `BrowserMicRecorder` (`getUserMedia` + `AudioContext.resume()` + downsampling to 16 kHz PCM WAV).
- `POST /api/voice` base64-encodes WAV bytes and calls OpenRouter `POST /api/v1/audio/transcriptions` (`openai/whisper-large-v3-turbo`).

### Stage 2 — System-1 Triage (`services/jev_router.py`)
- Single `~220 ms` call to `POST https://openrouter.ai/api/alpha/decisions` (`model: "~typesafe/jev-latest"`):
  - `mind` (`choice`): `work` | `personal` | `interests`
  - `action` (`choice`): `log_note` | `log_task` | `earnings_preview` | `brief_holding` | `follow_up_source` | `peer_read_across` | `draft_next_check` | `clarify`
  - `holding` (`choice`): tracked tickers (`NVDA`, `MSFT`, `TSM` with company names) | `untracked` | `implicit_active` | `none`
  - `peer_holding` (`choice`): tracked peer tickers | `none`
  - `needs_llm_worker` (`noul` → `probability`): gates System-2 on-lens synthesis
  - `needs_enrichment` (`noul` → `probability`): gates async post-confirm background enrichment
- **Tier 0 (Zero LLM, `< 5 ms`):** Tracked-holding `earnings_preview` renders saved question, verified prior-quarter guidance baseline, and open `Next Check` directly from SQLite + Notion.
- **Fast Path (`log_note` / `log_task` / `draft_next_check`):** Zero blocking LLM on glasses; stages a `PendingWrite` immediately.

### Stage 3 — System-2 Work Skills (`services/retrieval.py` + `services/jev_router.py` + `services/llm_worker.py`)
1. **Tracked holdings (`NVDA`, `MSFT`, `TSM`):** Pulls latest Notion `Saved Question` first → retrieves candidates via SQLite FTS5 → uses Jev (`is_answered` + `best_chunk` + `verdict`, with all candidate passages placed in `state`) → synthesizes `<= 190`-char sourced card via `deepseek/deepseek-v4.1-flash-1017` (or returns `No matching passage` with `verdict: null`).
2. **Peer read-across (`TSM → NVDA`, `MSFT → NVDA`):** Connects verified peer filing passage to target holding's saved thesis question and proposes a stageable check.
3. **Any-company web preview & review (held or not held):** When Jev routes `holding = untracked` (e.g. Alphabet/GOOGL, Apple/AAPL), `synthesize_adhoc_earnings()` runs a date-aware OpenRouter web-grounded call (`plugins: [{"id": "web", "max_results": 3}]`), strips markdown links for LVGL clarity, and returns a `PREVIEW | <TICKER> | WEB` or `WORK | <TICKER> | WEB` card with a stageable watchpoint.

### Stage 4 — Confirmed Writes, Undo & Async Background Enrichment (`db.py` + `notion_sync.py` + `job_runner.py`)
- `POST /api/confirm` consumes `pending_writes` inside a single SQLite `BEGIN IMMEDIATE` transaction (preventing duplicate concurrent confirms and recording `previous_next_check` for rollback), pushes to Notion, and — if `needs_enrichment` is true — enqueues the entry onto `AsyncJobQueue[int]` *after* `notion_page_id` is saved.
- **Background enrichment worker (`LLMWorker.enrich_journal_entry`):**
  - Resolves relative dates (`tomorrow`, `next Friday`, `in 3 days`) deterministically via calendar math.
  - Uses date-aware OpenRouter web lookup to resolve named podcasts/books/articles (e.g. latest *Acquired* episode + URL) and patches SQLite + Notion in place.
  - Enforces strict anti-hallucination: unnamed entities (e.g. *"the latest podcast"* with no show name) are never invented and are marked `Needs Detail`.
- **Undo (`POST /api/journal/{id}/undo`):** Atomically removes the SQLite row, restores `previous_next_check` on the holding if applicable, and archives the Notion page (`{"archived": true}`).

---

## 4. Notion Database Schemas (Track 4 Integration)

Provisioned automatically via `make setup-notion` (`backend/app/scripts/setup_notion.py`); existing `Cockpit Log` databases are upgraded idempotently via `PATCH /v1/databases/{id}`.

### Database A: `Cockpit Log` (`NOTION_LOG_DATABASE_ID`)
| Property | Type | Example |
| :--- | :--- | :--- |
| `Entry` | Title | `I enjoyed the latest Acquired podcast` |
| `Mind` | Select (`Work`, `Personal`, `Interests`) | `Interests` |
| `Kind` | Select (`Note`, `Task`, `Check`) | `Note` |
| `Ticker` | Rich text *(optional)* | `NVDA`, `GOOGL` |
| `Source` | Rich text *(optional)* | `Aug FY27 Call · p. 7` |
| `Logged At` | Date | `2026-11-25T10:40:00Z` |
| `Enrichment` | Select (`None`, `Pending`, `Enriched`, `Needs Detail`, `Failed`) | `Enriched` |
| `Enriched` | Rich text *(patched asynchronously)* | `Latest Acquired episode (Oct 18, 2026) is 'Seinfeld'...` |
| `Due Date` | Date *(optional, resolved from relative dates)* | `2026-12-04` |
| `Link` | URL *(optional, verified citation URL)* | `https://www.acquired.fm/episodes/seinfeld` |

### Database B: `Holdings Watchlist` (`NOTION_HOLDINGS_DATABASE_ID`)
| Property | Type | Sync Semantics |
| :--- | :--- | :--- |
| `Ticker` | Title | `NVDA`, `MSFT`, `TSM` |
| `Company` | Rich text | `NVIDIA Corp.` |
| `Saved Question` | Rich text | **User-owned in Notion** (pulled before recompute; never clobbered by briefing `PATCH`) |
| `Status` | Select (`HELD`, `AHEAD`, `WATCH`, `PENDING`) | Computed by Jev + Work worker |
| `Latest Finding` | Rich text | Computed concise delta card |
| `Source` | Rich text | Primary filing citation |
| `Next Check` | Rich text | Updated only on confirmed Work checks; restored on Undo; clearing in Notion clears local cache |
