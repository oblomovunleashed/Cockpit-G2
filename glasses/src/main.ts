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
import {
  dispatchEvenHubEvent,
  executePullNotionAction,
  executeSwipeAction,
  executeTapAction,
  nextViewModeFromUtterance,
  startBridgeCapture,
  stopBridgeCapture,
  type BridgeCaptureState,
} from './input';
import { activeHolding, createInitialState, holdingToCard, type CockpitState } from './state';
import type { ConfirmWriteResponse, HudCard, JevDecision, JournalEntry, Mind } from './types';

const NVDA_MARGIN_Q = 'Does NVIDIA expect the margin trough to get deeper?';
const NVDA_REVENUE_Q = "Is revenue running ahead of management's guidance?";

const api = new CockpitApiClient();
const recorder = new PcmAudioRecorder();
const browserMic = new BrowserMicRecorder();
const state: CockpitState = createInitialState();

const captureState: BridgeCaptureState = { activeMicKind: null };
let enrichmentPollTimer: number | null = null;

function isAnyMicRecording(): boolean {
  return captureState.activeMicKind !== null || recorder.isActive || browserMic.isActive;
}

async function discardPendingWriteIfAny(): Promise<void> {
  const pending = state.pendingWrite;
  state.pendingWrite = null;
  state.suggestedCheck = null;
  state.lastSavedEntry = null;
  if (pending) {
    try {
      await api.cancelWrite(pending.id);
    } catch {
      // Best-effort cancel; local pending state is already cleared.
    }
  }
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

function setMicButtonLabel(recording: boolean, sourceLabel = ''): void {
  const micBtn = document.getElementById('btn-mic');
  if (!micBtn) return;
  if (recording) {
    micBtn.textContent = sourceLabel ? `[REC] Listening (${sourceLabel}) - release` : '[REC] Listening - release';
    micBtn.classList.add('recording');
  } else {
    micBtn.textContent = 'Hold to Speak (Mic)';
    micBtn.classList.remove('recording');
  }
}

function updatePendingControls(): void {
  const bar = document.getElementById('pending-controls');
  if (bar) {
    bar.style.display = state.pendingWrite ? 'flex' : 'none';
  }
  const undoBtn = document.getElementById('btn-undo') as HTMLButtonElement | null;
  if (undoBtn) {
    undoBtn.disabled = !state.lastSavedEntry;
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
    const scopePart =
      decision.peer_holding && decision.holding
        ? `route: ${decision.peer_holding} -> ${decision.holding}`
        : decision.holding
          ? `holding: ${decision.holding}`
          : decision.requested_company
            ? `web company: ${decision.requested_company}`
            : '';
    const enrichPart = decision.needs_enrichment ? 'post_enrich: YES (async)' : '';
    traceEl.textContent = [
      `mind: ${decision.mind.toUpperCase()}`,
      `skill: ${decision.action}`,
      scopePart,
      `llm_worker: ${decision.needs_llm_worker ? 'YES (System-2 DeepSeek)' : 'NO (System-1 / Deterministic)'}`,
      enrichPart,
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
      for (const entry of state.recentJournal.slice(0, 10)) {
        const li = document.createElement('li');
        const tickerTag = entry.ticker ? ` [${entry.ticker}]` : '';
        const notionTag = entry.notion_page_id ? ' · Notion' : '';
        const statusTag =
          entry.enrichment_status && entry.enrichment_status !== 'none'
            ? ` · ${entry.enrichment_status.toUpperCase()}`
            : '';
        const dueTag = entry.due_date ? ` · Due ${entry.due_date}` : '';
        li.textContent = `[${entry.mind.toUpperCase()} · ${entry.kind}]${tickerTag} ${entry.content}${dueTag}${statusTag}${notionTag}`;

        if (entry.enriched_summary) {
          const detail = document.createElement('div');
          detail.className = 'enriched-note';
          detail.textContent = `-> ${entry.enriched_summary}`;
          if (entry.enriched_url) {
            const link = document.createElement('a');
            link.href = entry.enriched_url;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = ' [source]';
            detail.appendChild(link);
          }
          li.appendChild(detail);
        }
        listEl.appendChild(li);
      }
    }
  }

  updatePendingControls();
}

function stopEnrichmentPoll(): void {
  if (enrichmentPollTimer !== null) {
    window.clearInterval(enrichmentPollTimer);
    enrichmentPollTimer = null;
  }
}

function startEnrichmentPoll(entryId: number): void {
  stopEnrichmentPoll();
  let attempts = 0;
  enrichmentPollTimer = window.setInterval(() => {
    attempts += 1;
    if (attempts > 18) {
      stopEnrichmentPoll();
      return;
    }
    void api
      .getJournalEntry(entryId)
      .then(async (latest: JournalEntry) => {
        if (!latest.enrichment_status || latest.enrichment_status === 'pending') {
          return;
        }
        stopEnrichmentPoll();
        await refreshDeck(false);

        // If wearer is still viewing this entry's SAVED receipt card, update the G2 lens in place!
        if (
          state.mode === 'SAVED' &&
          state.lastSavedEntry &&
          state.lastSavedEntry.id === latest.id &&
          !isAnyMicRecording()
        ) {
          state.lastSavedEntry = latest;
          const badge =
            latest.enrichment_status === 'enriched'
              ? 'ENRICHED'
              : latest.enrichment_status === 'needs_detail'
                ? 'NEEDS DETAIL'
                : 'SAVED';
          const duePart = latest.due_date ? ` [Due ${latest.due_date}]` : '';
          await showCard({
            header: `${latest.mind.toUpperCase()} · ${badge}`,
            body: `${latest.enriched_summary || latest.content}${duePart}`,
            footer: 'Tap: done · Swipe: undo · Hold: speak',
          });
        }
      })
      .catch(() => {
        // Entry may have been undone
        stopEnrichmentPoll();
      });
  }, 2000);
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
    state.lastSavedEntry = null;
    await hud.render(holdingToCard(state));
  }
}

async function showCard(card: HudCard): Promise<void> {
  updatePendingControls();
  await hud.render(card);
}

async function submitTextUtterance(transcript: string): Promise<void> {
  if (state.mode === 'PROCESSING' || isAnyMicRecording()) return;
  const clean = transcript.trim();
  if (!clean) return;

  stopEnrichmentPoll();
  await discardPendingWriteIfAny();
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
    onSaved: (confirmed: ConfirmWriteResponse) => {
      if (confirmed.enrichment_queued) {
        startEnrichmentPoll(confirmed.entry.id);
      }
    },
  });
  updatePendingControls();
}

async function handleSwipe(delta: number): Promise<void> {
  await executeSwipeAction(state, delta, {
    isRecording: () => isAnyMicRecording(),
    getActiveHolding: activeHolding,
    reclassifyPendingWrite: (pendingId, payload) => api.updatePendingWrite(pendingId, payload),
    undoJournalEntry: (entryId) => {
      stopEnrichmentPoll();
      return api.undoJournalEntry(entryId);
    },
    refreshDeckAfterChange: () => refreshDeck(false),
    showCard,
  });
  updatePendingControls();
}

async function handleReclassifyDirect(mind: Mind, kind: 'note' | 'task' | 'check'): Promise<void> {
  if (!state.pendingWrite || state.mode === 'PROCESSING' || isAnyMicRecording()) return;
  const active = activeHolding(state);
  const ticker = mind === 'work' ? (state.pendingWrite.ticker ?? active?.ticker ?? null) : null;
  state.mode = 'PROCESSING';
  try {
    const updated = await api.updatePendingWrite(state.pendingWrite.id, { mind, kind, ticker });
    state.pendingWrite = updated.pending_write;
    state.mode = 'CONFIRM_WRITE';
    await showCard(updated.hud);
  } catch (err) {
    state.mode = 'CONFIRM_WRITE';
    await showCard({
      header: 'COCKPIT · RECLASSIFY ERROR',
      body: err instanceof Error ? err.message : 'Failed to switch mind.',
      footer: 'Tap: save · 2x: cancel',
    });
  }
}

async function handleUndoDirect(): Promise<void> {
  if (!state.lastSavedEntry || state.mode === 'PROCESSING' || isAnyMicRecording()) return;
  stopEnrichmentPoll();
  const entryId = state.lastSavedEntry.id;
  state.lastSavedEntry = null;
  state.mode = 'PROCESSING';
  try {
    const undone = await api.undoJournalEntry(entryId);
    await refreshDeck(false);
    state.mode = 'SAVED';
    await showCard(undone.hud);
  } catch (err) {
    state.mode = 'BRIEFING';
    await showCard({
      header: 'COCKPIT · UNDO ERROR',
      body: err instanceof Error ? err.message : 'Could not undo entry.',
      footer: 'Swipe: deck',
    });
  }
}

async function handleDoubleTap(): Promise<void> {
  if (state.mode === 'PROCESSING') return;

  if (
    state.mode !== 'BRIEFING' ||
    state.pendingWrite ||
    state.suggestedCheck ||
    state.lastSavedEntry
  ) {
    stopEnrichmentPoll();
    if (state.pendingWrite) {
      const pendingId = state.pendingWrite.id;
      state.pendingWrite = null;
      await api.cancelWrite(pendingId);
    }
    state.suggestedCheck = null;
    state.lastSavedEntry = null;
    state.mode = 'BRIEFING';
    await showCard(holdingToCard(state));
    return;
  }

  // Even Hub QA requirement: root-page double-tap must call shutDownPageContainer(1)
  if (bridge) {
    await bridge.shutDownPageContainer(1);
    hud.reset();
  }
}

async function startVoiceCapture(): Promise<void> {
  const outcome = await startBridgeCapture(state, captureState, {
    recorder,
    bridge,
    source: AudioInputSource.Glasses,
    isOtherMicActive: () => browserMic.isActive,
    setMicLabel: setMicButtonLabel,
    showCard,
    onBeforeStart: stopEnrichmentPoll,
    onAudioReady: (wavBlob) => routeWavBlob(wavBlob, 'G2/Simulator'),
  });
  if (outcome === 'failed' && state.mode === 'LISTENING') {
    state.mode = 'BRIEFING';
    await showCard(holdingToCard(state));
  }
}

async function routeWavBlob(wavBlob: Blob | null, sourceLabel: string): Promise<void> {
  if (!wavBlob) {
    await discardPendingWriteIfAny();
    state.mode = 'SAVED';
    await showCard({
      header: 'COCKPIT · NO AUDIO',
      body: `No ${sourceLabel} mic frames received. Hold the button for 1-2s while speaking and check macOS Microphone permission.`,
      footer: 'Tap: deck · Hold: try again',
    });
    return;
  }

  await discardPendingWriteIfAny();
  state.mode = 'PROCESSING';
  await showCard({
    header: 'COCKPIT · TRANSCRIBING',
    body: `Sending ${(wavBlob.size / 1024).toFixed(1)} KB audio clip to OpenRouter Whisper -> Jev...`,
    footer: 'Please wait...',
  });

  try {
    const current = activeHolding(state);
    const res = await api.sendVoice(wavBlob, current?.ticker ?? null);
    state.lastDecision = res.decision;
    state.pendingWrite = res.pending_write;
    state.suggestedCheck = res.suggested_check;
    state.lastSavedEntry = null;
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
    state.mode = 'SAVED';
    state.lastSavedEntry = null;
    await showCard({
      header: 'COCKPIT · VOICE ERROR',
      body: err instanceof Error ? err.message : 'Voice transcription failed.',
      footer: 'Tap: deck · Hold: try again',
    });
  }
}

async function stopVoiceCaptureAndRoute(): Promise<void> {
  await stopBridgeCapture(state, captureState, {
    recorder,
    bridge,
    setMicLabel: setMicButtonLabel,
    showCard,
    onAudioReady: (wavBlob) => routeWavBlob(wavBlob, 'G2/Simulator'),
  });
}

async function startUnifiedMicButtonCapture(): Promise<void> {
  if (state.mode === 'PROCESSING' || isAnyMicRecording()) return;

  // Prefer native Even Hub Bridge audioControl when running inside Simulator or G2 glasses!
  if (bridge) {
    const outcome = await startBridgeCapture(state, captureState, {
      recorder,
      bridge,
      source: AudioInputSource.Glasses,
      isOtherMicActive: () => browserMic.isActive,
      setMicLabel: setMicButtonLabel,
      showCard,
      onBeforeStart: stopEnrichmentPoll,
      onAudioReady: (wavBlob) => routeWavBlob(wavBlob, 'G2/Simulator'),
      listeningBody: 'Listening via Even Hub mic...\nHold while speaking, release to send.',
    });
    if (outcome === 'started' || outcome === 'cancelled' || outcome === 'ignored') {
      return;
    }
  }

  // Fallback for standalone browser preview (http://127.0.0.1:5173 in Chrome/Safari)
  stopEnrichmentPoll();
  captureState.activeMicKind = 'browser';
  try {
    const started = await browserMic.start();
    if (!started || captureState.activeMicKind !== 'browser') {
      captureState.activeMicKind = null;
      setMicButtonLabel(false);
      return;
    }
    state.mode = 'LISTENING';
    setMicButtonLabel(true, 'Browser');
    await showCard({
      header: 'COCKPIT · [REC] LISTENING',
      body: 'Listening via browser mic...\nHold while speaking, release to send.',
      footer: 'Release to transcribe via OpenRouter',
    });
  } catch (err) {
    captureState.activeMicKind = null;
    setMicButtonLabel(false);
    await discardPendingWriteIfAny();
    state.mode = 'SAVED';
    await showCard({
      header: 'COCKPIT · MIC ERROR',
      body: err instanceof Error ? err.message : 'Could not open microphone.',
      footer: 'Tap: deck · Check mic permission',
    });
  }
}

async function stopUnifiedMicButtonAndRoute(): Promise<void> {
  if (captureState.activeMicKind === 'bridge' || recorder.isActive) {
    await stopVoiceCaptureAndRoute();
    return;
  }
  if (!browserMic.isActive && captureState.activeMicKind !== 'browser') return;
  const wasPreStart = browserMic.isStarting && !browserMic.isRecording;
  const wasListening = state.mode === 'LISTENING';
  captureState.activeMicKind = null;
  setMicButtonLabel(false);
  const wavBlob = await browserMic.stop();
  if (!wavBlob) {
    if (wasPreStart) {
      if (wasListening) {
        state.mode = 'BRIEFING';
        await showCard(holdingToCard(state));
      }
      return;
    }
    if (!wasListening) {
      return;
    }
  }
  await routeWavBlob(wavBlob, 'browser');
}

function handleEvenHubEvent(event: EvenHubEvent): void {
  dispatchEvenHubEvent(event, {
    onAudioPcm: (pcm) => recorder.pushChunk(pcm),
    onMenuItemClick: (itemId) => {
      if (itemId === MENU_ITEM_PULL_NOTION) {
        void handlePullNotion();
      } else if (itemId === MENU_ITEM_NEXT_HOLDING) {
        void handleSwipe(1);
      } else if (itemId === MENU_ITEM_REFRESH) {
        void discardPendingWriteIfAny().then(() => refreshDeck(true));
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

async function handlePullNotion(): Promise<void> {
  await executePullNotionAction(state, {
    isRecording: () => isAnyMicRecording(),
    cancelWrite: (pendingId) => api.cancelWrite(pendingId),
    pullNotion: () => api.pullNotion(),
    refreshDeckAfterPull: () => refreshDeck(false),
    showCard,
    onBeforeSync: stopEnrichmentPoll,
  });
  updatePendingControls();
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
    try {
      micBtn.setPointerCapture(e.pointerId);
    } catch {
      // ignore if capture unavailable
    }
    void startUnifiedMicButtonCapture();
  });
  micBtn?.addEventListener('pointerup', (e) => {
    try {
      if (micBtn.hasPointerCapture(e.pointerId)) {
        micBtn.releasePointerCapture(e.pointerId);
      }
    } catch {
      // ignore
    }
    void stopUnifiedMicButtonAndRoute();
  });
  micBtn?.addEventListener('pointercancel', () => {
    void stopUnifiedMicButtonAndRoute();
  });

  document.getElementById('btn-tap')?.addEventListener('click', () => {
    void handleTapAction();
  });

  document.getElementById('btn-next')?.addEventListener('click', () => {
    void handleSwipe(1);
  });

  document.getElementById('btn-undo')?.addEventListener('click', () => {
    void handleUndoDirect();
  });

  document.getElementById('btn-reclassify-work')?.addEventListener('click', () => {
    void handleReclassifyDirect('work', 'check');
  });
  document.getElementById('btn-reclassify-personal')?.addEventListener('click', () => {
    void handleReclassifyDirect('personal', 'task');
  });
  document.getElementById('btn-reclassify-interests')?.addEventListener('click', () => {
    void handleReclassifyDirect('interests', 'note');
  });
  document.getElementById('btn-cancel-pending')?.addEventListener('click', () => {
    void handleDoubleTap();
  });

  document.getElementById('btn-flip')?.addEventListener('click', async () => {
    if (state.mode === 'PROCESSING' || isAnyMicRecording()) return;
    stopEnrichmentPoll();
    await discardPendingWriteIfAny();
    const nvda = state.holdings.find((h) => h.ticker === 'NVDA');
    const nextQ = nvda?.saved_question === NVDA_MARGIN_Q ? NVDA_REVENUE_Q : NVDA_MARGIN_Q;
    const updated = await api.updateHoldingQuestion('NVDA', nextQ);
    const idx = state.holdings.findIndex((h) => h.ticker === 'NVDA');
    if (idx >= 0) {
      state.holdings[idx] = updated.holding;
      state.holdingIndex = idx;
    }
    state.mode = 'BRIEFING';
    await showCard(updated.hud);
  });

  document.getElementById('btn-notion')?.addEventListener('click', () => {
    void handlePullNotion();
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
