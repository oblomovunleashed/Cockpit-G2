# Cockpit — 3-Minute Demo Storyboard & Feature-Request Tracker

## Split-Screen Recording Layout
- **Left half:** Even Hub Simulator (`evenhub-simulator`) displaying the live `576 × 288` monochrome green G2 HUD + gesture events.
- **Right half:** Live Notion workspace (`Cockpit Log` + `Holdings Watchlist`) alongside the subtle Cockpit Companion trace showing Jev's System-1 decision (`mind`, `action`, `confidence`, `llm_worker: false | true`).

## Shot List (Target: 2m 45s, Max: 3m 00s)

1. **0:00–0:20 · Hook**
   - Problem: Smart glasses shouldn't run a chatty LLM for every fleeting thought or dump dashboards onto a `576 × 288` lens.
   - Cockpit puts **Jev (System 1)** at the front door: fast logs go straight to Notion with zero LLM prose; deep work questions wake the document worker.
2. **0:20–0:55 · "Logged, Logged" (System-1 Fast Path)**
   - Utterance 1: *"This Acquired podcast on TSMC was great."* → Jev: `Interests · LOG_NOTE (llm_worker: false)` → 1 tap → row appears in Notion `Cockpit Log`.
   - Utterance 2: *"Remember to buy a birthday present."* → Jev: `Personal · LOG_TASK (llm_worker: false)` → 1 tap → row appears in Notion `Cockpit Log`.
3. **0:55–1:55 · "LLM Worker" (NVIDIA Earnings Review + Read/Write Loop)**
   - Utterance 3: *"Can we review NVIDIA earnings?"* → Jev: `Work · BRIEF_HOLDING (llm_worker: true)`.
   - G2 renders the card tailored to your saved question (*"Does NVIDIA expect the margin trough to get deeper?"*):
     `Q4 FY27 gross margin guide · 71–72%. Management had already flagged that range in August.`
   - Follow-up: *"What did management expect to bring margins back up?"* → retrieves August call p. 7 (Q1 FY28 price increases) and stages a 1-tap next-quarter check.
   - Confirm with 1 tap → saved live to Notion `Cockpit Log` + `Holdings Watchlist` (`Next Check`).
4. **1:55–2:20 · "One Release, Two Different Glances"**
   - Edit NVDA's `Saved Question` in Notion to *"Is revenue running ahead of management's guidance?"* and sync.
   - Re-open NVDA on G2 → same Q3 FY27 release now renders `Q3 FY27 revenue · $115.8bn. About 7% above the company's $108bn guidance midpoint.`
5. **2:20–2:45 · Portfolio Swipe + Safety & Architecture**
   - Swipe across Holdings `2/3` and `3/3`; close with stack overview (Even Hub SDK `0.0.16`, OpenRouter STT + Jev + LLM, Notion; read-only financial inputs, confirmed writes only).

---

## Optional API Feature Requests (Up to 5 — Collect During Build Week)

Record concrete friction encountered while building with Even Hub SDK, OpenRouter/Jev, and Notion:

1. *[Even Hub SDK]* ...
2. *[OpenRouter / Jev]* ...
3. *[Notion API]* ...
4. ...
5. ...
