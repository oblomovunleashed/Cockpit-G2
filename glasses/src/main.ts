import {
  AudioInputSource,
  waitForEvenAppBridge,
  type EvenAppBridge,
  type EvenHubEvent,
} from '@evenrealities/even_hub_sdk';
import { CockpitApiClient } from './api';
import { BrowserMicRecorder, PcmAudioRecorder } from './audio';
import {
  G2HudRenderer,
  MENU_ITEM_NEXT_HOLDING,
  MENU_ITEM_PULL_NOTION,
  MENU_ITEM_REFRESH,
} from './hud';
import { dispatchEvenHubEvent, executeTapAction, nextViewModeFromUtterance } from './input';
import { activeHolding, createInitialState, holdingToCard, type CockpitState } from './state';
import type { HudCard, JevDecision } from './types';

const NVDA_MARGIN_Q = 'Does NVIDIA expect the margin trough to get deeper?';
const NVDA_REVENUE_Q = "Is revenue running ahead of management's guidance?";

const api = new CockpitApiClient();
const recorder = new PcmAudioRecorder();
const browserMic = new BrowserMicRecorder();
const state: CockpitState = createInitialState();

function isAnyMicRecording(): boolean {
  return recorder.isRecording || browserMic.isActive;
}

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
    const scopePart = decision.peer_holding && decision.holding
      ? `route: ${decision.peer_holding} → ${decision.holding}`
      : decision.holding
        ? `holding: ${decision.holding}`
        : '';
    traceEl.textContent = [
      `mind: ${decision.mind.toUpperCase()}`,
      `skill: ${decision.action}`,
      scopePart,
      `llm_worker: ${decision.needs_llm_worker ? 'YES (System-2 DeepSeek)' : 'NO (System-1 / Deterministic)'}`,
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
    state.suggestedCheck = null;
    await hud.render(holdingToCard(state));
  }
}

async function showCard(card: HudCard): Promise<void> {
  await hud.render(card);
}

async function submitTextUtterance(transcript: string): Promise<void> {
  if (state.mode === 'PROCESSING' || isAnyMicRecording()) return;
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
    state.suggestedCheck = res.suggested_check;

    if (res.holding) {
      const idx = state.holdings.findIndex((h) => h.ticker === res.holding?.ticker);
      if (idx >= 0) {
        state.holdings[idx] = res.holding;
        state.holdingIndex = idx;
      }
    }

    state.mode = nextViewModeFromUtterance(res);
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
  await executeTapAction(state, {
    isRecording: () => isAnyMicRecording(),
    getActiveHolding: activeHolding,
    confirmWrite: (pendingId) => api.confirmWrite(pendingId),
    stageCheck: (draft) => api.stageCheck(draft),
    stageSuggestedCheck: (ticker) => api.stageSuggestedCheck(ticker),
    refreshDeckAfterSave: () => refreshDeck(false),
    showCard,
  });
}

async function handleSwipe(delta: number): Promise<void> {
  if (state.mode === 'PROCESSING' || isAnyMicRecording()) return;

  if (state.pendingWrite) {
    const pendingId = state.pendingWrite.id;
    state.pendingWrite = null;
    state.suggestedCheck = null;
    await api.cancelWrite(pendingId);
    state.mode = 'BRIEFING';
    await showCard(holdingToCard(state));
    return;
  }

  state.suggestedCheck = null;
  if (state.holdings.length > 0) {
    state.holdingIndex =
      (state.holdingIndex + delta + state.holdings.length) % state.holdings.length;
    state.mode = 'BRIEFING';
    await showCard(holdingToCard(state));
  }
}

async function handleDoubleTap(): Promise<void> {
  if (state.mode === 'PROCESSING') return;

  if (state.mode !== 'BRIEFING' || state.pendingWrite || state.suggestedCheck) {
    if (state.pendingWrite) {
      const pendingId = state.pendingWrite.id;
      state.pendingWrite = null;
      await api.cancelWrite(pendingId);
    }
    state.suggestedCheck = null;
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
  if (state.mode === 'PROCESSING' || isAnyMicRecording()) return;
  state.mode = 'LISTENING';
  await showCard({
    header: 'COCKPIT · ● LISTENING',
    body: 'Speak a note, task, or earnings question.\nRelease when finished.',
    footer: 'Release to route via Jev',
  });
  await recorder.start(bridge, AudioInputSource.Glasses);
}

async function routeWavBlob(wavBlob: Blob | null): Promise<void> {
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
    state.suggestedCheck = res.suggested_check;
    if (res.holding) {
      const idx = state.holdings.findIndex((h) => h.ticker === res.holding?.ticker);
      if (idx >= 0) {
        state.holdings[idx] = res.holding;
        state.holdingIndex = idx;
      }
    }
    state.mode = nextViewModeFromUtterance(res);
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

async function stopVoiceCaptureAndRoute(): Promise<void> {
  if (!recorder.isRecording) return;
  const wavBlob = await recorder.stop(bridge);
  await routeWavBlob(wavBlob);
}

async function startBrowserMicCapture(): Promise<void> {
  if (state.mode === 'PROCESSING' || isAnyMicRecording()) return;
  try {
    const started = await browserMic.start();
    if (!started) {
      // Button was released while getUserMedia permission was pending; stay on current view.
      return;
    }
    state.mode = 'LISTENING';
    await showCard({
      header: 'COCKPIT · ● LISTENING (MIC)',
      body: 'Speak a note, task, or earnings question.\nRelease button when finished.',
      footer: 'Release to transcribe via OpenRouter',
    });
  } catch (err) {
    state.mode = 'BRIEFING';
    await showCard({
      header: 'COCKPIT · MIC ERROR',
      body: err instanceof Error ? err.message : 'Could not open microphone.',
      footer: 'Check macOS/browser mic permission',
    });
  }
}

async function stopBrowserMicAndRoute(): Promise<void> {
  if (!browserMic.isActive) return;
  const wasListening = state.mode === 'LISTENING';
  const wavBlob = await browserMic.stop();
  if (!wavBlob) {
    if (wasListening) {
      state.mode = 'BRIEFING';
      await showCard(holdingToCard(state));
    }
    return;
  }
  await routeWavBlob(wavBlob);
}

function handleEvenHubEvent(event: EvenHubEvent): void {
  dispatchEvenHubEvent(event, {
    onAudioPcm: (pcm) => recorder.pushChunk(pcm),
    onMenuItemClick: (itemId) => {
      if (itemId === MENU_ITEM_PULL_NOTION) {
        void api.pullNotion().then(() => refreshDeck(true));
      } else if (itemId === MENU_ITEM_NEXT_HOLDING) {
        void handleSwipe(1);
      } else if (itemId === MENU_ITEM_REFRESH) {
        void refreshDeck(true);
      }
    },
    onLongPress: () => void startVoiceCapture(),
    onLongPressRelease: () => void stopVoiceCaptureAndRoute(),
    onTap: () => void handleTapAction(),
    onSwipeNext: () => void handleSwipe(1),
    onSwipePrev: () => void handleSwipe(-1),
    onDoubleTap: () => void handleDoubleTap(),
  });
}

function wireCompanionControls(): void {
  document.querySelectorAll<HTMLButtonElement>('button[data-utterance]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const text = btn.dataset.utterance ?? '';
      void submitTextUtterance(text);
    });
  });

  const micBtn = document.getElementById('btn-mic');
  micBtn?.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    void startBrowserMicCapture();
  });
  micBtn?.addEventListener('pointerup', () => {
    void stopBrowserMicAndRoute();
  });
  micBtn?.addEventListener('pointerleave', () => {
    if (browserMic.isActive) {
      void stopBrowserMicAndRoute();
    }
  });
  micBtn?.addEventListener('pointercancel', () => {
    if (browserMic.isActive) {
      void stopBrowserMicAndRoute();
    }
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
    state.suggestedCheck = null;
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
    footer: 'Hold: speak · Tap: review check · Swipe: next',
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
