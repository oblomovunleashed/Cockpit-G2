import {
  AudioInputSource,
  OsEventTypeList,
  waitForEvenAppBridge,
  type EvenAppBridge,
  type EvenHubEvent,
} from '@evenrealities/even_hub_sdk';
import { CockpitApiClient } from './api';
import { PcmAudioRecorder } from './audio';
import {
  G2HudRenderer,
  MENU_ITEM_NEXT_HOLDING,
  MENU_ITEM_PULL_NOTION,
  MENU_ITEM_REFRESH,
} from './hud';
import { activeHolding, createInitialState, holdingToCard, type CockpitState } from './state';
import type { HudCard, JevDecision } from './types';

const NVDA_MARGIN_Q = 'Does NVIDIA expect the margin trough to get deeper?';
const NVDA_REVENUE_Q = "Is revenue running ahead of management's guidance?";

const api = new CockpitApiClient();
const recorder = new PcmAudioRecorder();
const state: CockpitState = createInitialState();

let bridge: EvenAppBridge | null = null;
let hud: G2HudRenderer;

async function connectBridgeWithTimeout(timeoutMs = 1500): Promise<EvenAppBridge | null> {
  const pill = document.getElementById('bridge-status');
  try {
    const connected = await Promise.race<EvenAppBridge | null>([
      waitForEvenAppBridge(),
      new Promise<null>((resolve) => window.setTimeout(() => resolve(null), timeoutMs)),
    ]);
    if (pill) {
      pill.textContent = connected ? 'Bridge: G2 / Simulator' : 'Bridge: browser preview';
      if (connected) pill.classList.add('live');
    }
    return connected;
  } catch {
    if (pill) pill.textContent = 'Bridge: browser preview';
    return null;
  }
}

function updateCompanionPanel(decision?: JevDecision | null): void {
  const backendPill = document.getElementById('backend-status');
  if (backendPill) {
    const modeLabel = state.mockMode ? 'mock' : 'OpenRouter live';
    const notionLabel = state.notionEnabled ? 'Notion ON' : 'SQLite local';
    backendPill.textContent = `Backend: ${modeLabel} · ${notionLabel}`;
    backendPill.classList.add('live');
  }

  const traceEl = document.getElementById('jev-trace');
  if (traceEl && decision) {
    traceEl.textContent = [
      `mind: ${decision.mind.toUpperCase()}`,
      `action: ${decision.action}`,
      `llm_worker: ${decision.needs_llm_worker ? 'YES (System-2 Work)' : 'NO (System-1 fast log)'}`,
      `confidence: ${(decision.confidence * 100).toFixed(0)}% (${decision.mode})`,
      decision.reasoning ? `note: ${decision.reasoning}` : '',
    ]
      .filter(Boolean)
      .join(' | ');
  }

  const listEl = document.getElementById('journal-list');
  if (listEl) {
    if (state.recentJournal.length === 0) {
      listEl.innerHTML = '<li>No confirmed entries yet.</li>';
    } else {
      listEl.innerHTML = '';
      for (const entry of state.recentJournal.slice(0, 8)) {
        const li = document.createElement('li');
        const tickerTag = entry.ticker ? ` [${entry.ticker}]` : '';
        const notionTag = entry.notion_page_id ? ' · Notion' : '';
        li.textContent = `[${entry.mind.toUpperCase()} · ${entry.kind}]${tickerTag} ${entry.content}${notionTag}`;
        listEl.appendChild(li);
      }
    }
  }
}

async function refreshDeck(renderCurrent = true): Promise<void> {
  const deck = await api.getState();
  state.holdings = deck.holdings;
  state.recentJournal = deck.recent_journal;
  state.mockMode = deck.mock_mode;
  state.notionEnabled = deck.notion_enabled;
  if (state.holdingIndex >= state.holdings.length) {
    state.holdingIndex = 0;
  }
  updateCompanionPanel(state.lastDecision);
  if (renderCurrent) {
    state.mode = 'BRIEFING';
    state.pendingWrite = null;
    await hud.render(holdingToCard(state));
  }
}

async function showCard(card: HudCard): Promise<void> {
  await hud.render(card);
}

async function submitTextUtterance(transcript: string): Promise<void> {
  const clean = transcript.trim();
  if (!clean) return;

  state.mode = 'PROCESSING';
  await showCard({
    header: 'COCKPIT · JEV ROUTING',
    body: `"${clean}"\nChecking System-1 route...`,
    footer: 'Please wait...',
  });

  try {
    const current = activeHolding(state);
    const res = await api.sendText(clean, current?.ticker ?? null);
    state.lastDecision = res.decision;
    state.pendingWrite = res.pending_write;

    if (res.holding) {
      const idx = state.holdings.findIndex((h) => h.ticker === res.holding?.ticker);
      if (idx >= 0) {
        state.holdings[idx] = res.holding;
        state.holdingIndex = idx;
      }
    }

    state.mode = res.pending_write ? 'CONFIRM_WRITE' : 'BRIEFING';
    updateCompanionPanel(res.decision);
    await showCard(res.hud);
  } catch (err) {
    state.mode = 'BRIEFING';
    await showCard({
      header: 'COCKPIT · BACKEND ERROR',
      body: err instanceof Error ? err.message : 'Unable to reach local backend.',
      footer: 'Check http://127.0.0.1:8000',
    });
  }
}

async function handleTapAction(): Promise<void> {
  if (state.pendingWrite) {
    const pendingId = state.pendingWrite.id;
    state.mode = 'PROCESSING';
    await showCard({
      header: 'COCKPIT · SAVING',
      body: 'Writing confirmed entry to journal...',
      footer: 'Please wait...',
    });

    const confirmed = await api.confirmWrite(pendingId);
    state.pendingWrite = null;
    state.mode = 'RESULT';
    await refreshDeck(false);
    await showCard(confirmed.hud);
    return;
  }

  // Zero-dictation 1-tap path on a holding card: stage AI-drafted next-quarter check
  const current = activeHolding(state);
  if (current) {
    const staged = await api.stageSuggestedCheck(current.ticker);
    state.pendingWrite = staged.pending_write;
    state.mode = 'CONFIRM_WRITE';
    await showCard(staged.hud);
  }
}

async function handleSwipe(delta: number): Promise<void> {
  if (state.pendingWrite) {
    await api.cancelWrite(state.pendingWrite.id);
    state.pendingWrite = null;
    state.mode = 'BRIEFING';
    await showCard(holdingToCard(state));
    return;
  }

  if (state.holdings.length > 0) {
    state.holdingIndex =
      (state.holdingIndex + delta + state.holdings.length) % state.holdings.length;
    state.mode = 'BRIEFING';
    await showCard(holdingToCard(state));
  }
}

async function handleDoubleTap(): Promise<void> {
  if (state.mode !== 'BRIEFING' || state.pendingWrite) {
    if (state.pendingWrite) {
      await api.cancelWrite(state.pendingWrite.id);
      state.pendingWrite = null;
    }
    state.mode = 'BRIEFING';
    await showCard(holdingToCard(state));
    return;
  }

  // Even Hub QA requirement: root-page double-tap must call shutDownPageContainer(1)
  if (bridge) {
    await bridge.shutDownPageContainer(1);
  }
}

async function startVoiceCapture(): Promise<void> {
  if (recorder.isRecording) return;
  state.mode = 'LISTENING';
  await showCard({
    header: 'COCKPIT · ● LISTENING',
    body: 'Speak a note, task, or earnings question.\nRelease when finished.',
    footer: 'Release to route via Jev',
  });
  await recorder.start(bridge, AudioInputSource.Glasses);
}

async function stopVoiceCaptureAndRoute(): Promise<void> {
  if (!recorder.isRecording) return;
  const wavBlob = await recorder.stop(bridge);
  if (!wavBlob) {
    state.mode = 'BRIEFING';
    await showCard(holdingToCard(state));
    return;
  }

  state.mode = 'PROCESSING';
  await showCard({
    header: 'COCKPIT · TRANSCRIBING',
    body: 'Transcribing clip and routing via Jev...',
    footer: 'OpenRouter STT → Jev',
  });

  try {
    const current = activeHolding(state);
    const res = await api.sendVoice(wavBlob, current?.ticker ?? null);
    state.lastDecision = res.decision;
    state.pendingWrite = res.pending_write;
    if (res.holding) {
      const idx = state.holdings.findIndex((h) => h.ticker === res.holding?.ticker);
      if (idx >= 0) {
        state.holdings[idx] = res.holding;
        state.holdingIndex = idx;
      }
    }
    state.mode = res.pending_write ? 'CONFIRM_WRITE' : 'BRIEFING';
    updateCompanionPanel(res.decision);
    await showCard(res.hud);
  } catch (err) {
    state.mode = 'BRIEFING';
    await showCard({
      header: 'COCKPIT · VOICE ERROR',
      body: err instanceof Error ? err.message : 'Voice transcription failed.',
      footer: 'Hold to try again',
    });
  }
}

function handleEvenHubEvent(event: EvenHubEvent): void {
  if (event.audioEvent?.audioPcm) {
    recorder.pushChunk(event.audioEvent.audioPcm);
    return;
  }

  if (event.menuItemClickEvent?.itemID !== undefined) {
    const itemId = event.menuItemClickEvent.itemID;
    if (itemId === MENU_ITEM_PULL_NOTION) {
      void api.pullNotion().then(() => refreshDeck(true));
    } else if (itemId === MENU_ITEM_NEXT_HOLDING) {
      void handleSwipe(1);
    } else if (itemId === MENU_ITEM_REFRESH) {
      void refreshDeck(true);
    }
    return;
  }

  const eventType = event.sysEvent?.eventType ?? event.textEvent?.eventType;
  if (eventType === undefined) return;

  switch (eventType) {
    case OsEventTypeList.LONG_PRESS_EVENT:
      void startVoiceCapture();
      break;
    case OsEventTypeList.LONG_PRESS_RELEASE_EVENT:
      void stopVoiceCaptureAndRoute();
      break;
    case OsEventTypeList.CLICK_EVENT:
      void handleTapAction();
      break;
    case OsEventTypeList.SCROLL_BOTTOM_EVENT:
      void handleSwipe(1);
      break;
    case OsEventTypeList.SCROLL_TOP_EVENT:
      void handleSwipe(-1);
      break;
    case OsEventTypeList.DOUBLE_CLICK_EVENT:
      void handleDoubleTap();
      break;
    default:
      break;
  }
}

function wireCompanionControls(): void {
  document.querySelectorAll<HTMLButtonElement>('button[data-utterance]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const text = btn.dataset.utterance ?? '';
      void submitTextUtterance(text);
    });
  });

  document.getElementById('btn-tap')?.addEventListener('click', () => {
    void handleTapAction();
  });

  document.getElementById('btn-next')?.addEventListener('click', () => {
    void handleSwipe(1);
  });

  document.getElementById('btn-flip')?.addEventListener('click', async () => {
    const nvda = state.holdings.find((h) => h.ticker === 'NVDA');
    const nextQ = nvda?.saved_question === NVDA_MARGIN_Q ? NVDA_REVENUE_Q : NVDA_MARGIN_Q;
    const updated = await api.updateHoldingQuestion('NVDA', nextQ);
    const idx = state.holdings.findIndex((h) => h.ticker === 'NVDA');
    if (idx >= 0) {
      state.holdings[idx] = updated.holding;
      state.holdingIndex = idx;
    }
    state.pendingWrite = null;
    state.mode = 'BRIEFING';
    await showCard(updated.hud);
  });

  document.getElementById('btn-notion')?.addEventListener('click', async () => {
    await api.pullNotion();
    await refreshDeck(true);
  });

  const form = document.getElementById('utterance-form') as HTMLFormElement | null;
  const input = document.getElementById('utterance-input') as HTMLInputElement | null;
  form?.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!input) return;
    const text = input.value;
    input.value = '';
    void submitTextUtterance(text);
  });
}

async function boot(): Promise<void> {
  wireCompanionControls();
  bridge = await connectBridgeWithTimeout();
  hud = new G2HudRenderer(bridge);

  await hud.init({
    header: 'COCKPIT · LOADING',
    body: 'Connecting to local Cockpit service...',
    footer: 'Hold: speak · Tap: confirm · Swipe: next',
  });

  if (bridge) {
    bridge.onEvenHubEvent(handleEvenHubEvent);
  }

  try {
    await refreshDeck(true);
  } catch {
    await hud.render({
      header: 'COCKPIT · OFFLINE',
      body: 'Start the local FastAPI backend on http://127.0.0.1:8000 to load briefings.',
      footer: 'Run: make backend',
    });
  }
}

void boot();
