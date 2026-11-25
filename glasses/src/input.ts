import {
  AudioInputSource,
  OsEventTypeList,
  type EvenAppBridge,
  type EvenHubEvent,
} from '@evenrealities/even_hub_sdk';
import type { PcmAudioRecorder } from './audio.ts';
import {
  holdingToCard,
  homeCard,
  nextMindTarget,
  restCard,
  type CockpitState,
  type ViewMode,
} from './state.ts';
import type {
  ConfirmWriteResponse,
  DraftCheck,
  HoldingRecord,
  HudCard,
  Mind,
  PendingWrite,
  UndoWriteResponse,
  UtteranceResponse,
} from './types';

export interface EvenHubHandlers {
  onAudioPcm: (pcm: Uint8Array) => void;
  onMenuItemClick: (itemId: number) => void;
  onLongPress: () => void;
  onLongPressRelease: () => void;
  onTap: () => void;
  onSwipeNext: () => void;
  onSwipePrev: () => void;
  onDoubleTap: () => void;
}

export interface TapActionDeps {
  isRecording: () => boolean;
  getActiveHolding: (state: CockpitState) => HoldingRecord | null;
  confirmWrite: (pendingId: string) => Promise<ConfirmWriteResponse>;
  stageCheck: (draft: DraftCheck) => Promise<{ pending_write: PendingWrite; hud: HudCard }>;
  stageSuggestedCheck: (ticker: string) => Promise<{ pending_write: PendingWrite; hud: HudCard }>;
  refreshDeckAfterSave: () => Promise<void>;
  showCard: (card: HudCard) => Promise<void>;
  onSaved?: (confirmed: ConfirmWriteResponse) => void;
}

export interface SwipeActionDeps {
  isRecording: () => boolean;
  getActiveHolding: (state: CockpitState) => HoldingRecord | null;
  reclassifyPendingWrite: (
    pendingId: string,
    payload: { mind: Mind; kind: 'note' | 'task' | 'check'; ticker?: string | null },
  ) => Promise<{ pending_write: PendingWrite; hud: HudCard }>;
  undoJournalEntry: (entryId: number) => Promise<UndoWriteResponse>;
  refreshDeckAfterChange: () => Promise<void>;
  showCard: (card: HudCard) => Promise<void>;
}

export interface PullNotionDeps {
  isRecording: () => boolean;
  cancelWrite: (pendingId: string) => Promise<void>;
  pullNotion: () => Promise<{ pulled_count: number; recomputed: string[] }>;
  refreshDeckAfterPull: () => Promise<void>;
  showCard: (card: HudCard) => Promise<void>;
  onBeforeSync?: () => void;
}

export interface BridgeCaptureState {
  activeMicKind: 'bridge' | 'browser' | null;
}

export interface BridgeCaptureDeps {
  recorder: Pick<
    PcmAudioRecorder,
    'isActive' | 'isStarting' | 'isRecording' | 'beginStart' | 'isSessionCurrent' | 'start' | 'stop'
  >;
  bridge: EvenAppBridge | null;
  source?: AudioInputSource;
  isOtherMicActive: () => boolean;
  setMicLabel: (recording: boolean, sourceLabel?: string) => void;
  showCard: (card: HudCard) => Promise<void>;
  onBeforeStart?: () => void;
  onAudioReady: (wavBlob: Blob | null) => Promise<void>;
  listeningBody?: string;
}

function pendingToConfirmCard(pending: PendingWrite): HudCard {
  const tickerPart = pending.ticker ? ` · ${pending.ticker}` : '';
  return {
    header: `${pending.mind.toUpperCase()}${tickerPart} · CONFIRM ${pending.kind.toUpperCase()}`,
    body: pending.content,
    footer: 'Tap: save · Swipe: switch mind · 2x: cancel',
  };
}

/**
 * Resolves the gesture OsEventTypeList from an EvenHubEvent.
 *
 * Protobuf 3 omits zero-value fields on the wire, and `OsEventTypeList.CLICK_EVENT === 0`.
 * Consequently, a real single tap on Even G2 / R1 ring frequently arrives inside a present
 * `textEvent`, `listEvent`, or `sysEvent` object with `eventType === undefined`.
 */
export function resolveGestureEventType(event: EvenHubEvent): OsEventTypeList | undefined {
  if (event.sysEvent !== undefined) {
    if (event.sysEvent.eventType !== undefined) {
      return event.sysEvent.eventType;
    }
    if (
      event.sysEvent.imuData === undefined &&
      event.sysEvent.systemExitReasonCode === undefined
    ) {
      return OsEventTypeList.CLICK_EVENT;
    }
    return undefined;
  }

  if (event.textEvent !== undefined) {
    return event.textEvent.eventType ?? OsEventTypeList.CLICK_EVENT;
  }

  if (event.listEvent !== undefined) {
    return event.listEvent.eventType ?? OsEventTypeList.CLICK_EVENT;
  }

  return undefined;
}

export function dispatchEvenHubEvent(event: EvenHubEvent, handlers: EvenHubHandlers): void {
  if (event.audioEvent?.audioPcm) {
    handlers.onAudioPcm(event.audioEvent.audioPcm);
    return;
  }

  if (event.menuItemClickEvent?.itemID !== undefined) {
    handlers.onMenuItemClick(event.menuItemClickEvent.itemID);
    return;
  }

  const eventType = resolveGestureEventType(event);
  if (eventType === undefined) return;

  switch (eventType) {
    case OsEventTypeList.LONG_PRESS_EVENT:
      handlers.onLongPress();
      break;
    case OsEventTypeList.LONG_PRESS_RELEASE_EVENT:
      handlers.onLongPressRelease();
      break;
    case OsEventTypeList.CLICK_EVENT:
      handlers.onTap();
      break;
    case OsEventTypeList.SCROLL_BOTTOM_EVENT:
      handlers.onSwipeNext();
      break;
    case OsEventTypeList.SCROLL_TOP_EVENT:
      handlers.onSwipePrev();
      break;
    case OsEventTypeList.DOUBLE_CLICK_EVENT:
      handlers.onDoubleTap();
      break;
    default:
      break;
  }
}

/**
 * Determines the view mode after processing an utterance response.
 * Follow-up, preview, and ad-hoc web cards enter RESULT rather than BRIEFING
 * so tapping on a NO MATCH card never falls through to a holding's old check.
 */
export function nextViewModeFromUtterance(res: UtteranceResponse): ViewMode {
  if (res.pending_write) {
    return 'CONFIRM_WRITE';
  }
  if (res.decision.action === 'brief_holding' && res.holding) {
    return 'BRIEFING';
  }
  return 'RESULT';
}

/**
 * Executes the single-tap action with core safety invariants:
 * 1. Never fires duplicate saves while PROCESSING or recording.
 * 2. Never saves a draft whose exact wording has not already been displayed on the CONFIRM card.
 * 3. On SAVED / UNDONE receipt cards, tapping cleanly returns to the calm BRIEFING deck.
 */
export async function executeTapAction(state: CockpitState, deps: TapActionDeps): Promise<void> {
  if (state.mode === 'PROCESSING' || deps.isRecording()) return;

  // Post-confirm or post-undo receipt screen stays until wearer taps to dismiss back to HOME or watchlist
  if (state.mode === 'SAVED') {
    state.lastSavedEntry = null;
    state.mode = state.returnMode;
    if (state.mode === 'HOME') {
      state.activeTicker = null;
      await deps.showCard(homeCard(state));
    } else {
      const h = state.holdings[state.holdingIndex] ?? null;
      state.activeTicker = h?.ticker ?? null;
      await deps.showCard(holdingToCard(state));
    }
    return;
  }

  // Step 2: Confirm an already-displayed pending write
  if (state.pendingWrite) {
    const pending = state.pendingWrite;
    state.pendingWrite = null;
    state.suggestedCheck = null;
    state.mode = 'PROCESSING';
    await deps.showCard({
      header: 'COCKPIT · SAVING',
      body: 'Writing confirmed entry to journal...',
      footer: 'Please wait...',
    });

    try {
      const confirmed = await deps.confirmWrite(pending.id);
      state.mode = 'SAVED';
      state.lastSavedEntry = confirmed.entry;
      if (confirmed.holding) {
        const idx = state.holdings.findIndex((h) => h.ticker === confirmed.holding?.ticker);
        if (idx >= 0) {
          state.holdings[idx] = confirmed.holding;
          state.holdingIndex = idx;
        }
        state.returnMode = 'BRIEFING';
        state.activeTicker = confirmed.holding.ticker;
      } else {
        state.returnMode = 'HOME';
        state.activeTicker = null;
      }
      await deps.refreshDeckAfterSave();
      await deps.showCard(confirmed.hud);
      deps.onSaved?.(confirmed);
    } catch (err) {
      state.mode = state.returnMode;
      await deps.showCard({
        header: 'COCKPIT · SAVE ERROR',
        body: err instanceof Error ? err.message : 'Failed to save entry.',
        footer: 'Swipe: watchlist · 2x: home',
      });
    }
    return;
  }

  // Step 1a: On a source follow-up / peer read / ad-hoc web card, preview exact check before saving
  if (state.suggestedCheck) {
    const draft = state.suggestedCheck;
    state.suggestedCheck = null;
    state.mode = 'PROCESSING';
    try {
      const staged = await deps.stageCheck(draft);
      state.pendingWrite = staged.pending_write;
      state.mode = 'CONFIRM_WRITE';
      await deps.showCard(staged.hud);
    } catch (err) {
      state.mode = 'BRIEFING';
      await deps.showCard({
        header: 'COCKPIT · ERROR',
        body: err instanceof Error ? err.message : 'Could not stage check.',
        footer: 'Try again',
      });
    }
    return;
  }

  // Step 1b: Only on a BRIEFING holding card, preview its suggested check if one exists
  if (state.mode !== 'BRIEFING') {
    return;
  }

  const current = deps.getActiveHolding(state);
  if (current && current.suggested_next_check.trim()) {
    state.mode = 'PROCESSING';
    try {
      const staged = await deps.stageSuggestedCheck(current.ticker);
      state.pendingWrite = staged.pending_write;
      state.mode = 'CONFIRM_WRITE';
      await deps.showCard(staged.hud);
    } catch (err) {
      state.mode = 'BRIEFING';
      await deps.showCard({
        header: 'COCKPIT · ERROR',
        body: err instanceof Error ? err.message : 'Could not stage check.',
        footer: 'Try again',
      });
    }
  }
}

/**
 * Executes swipe gestures contextually:
 * - On CONFIRM_WRITE: cycles Mind/Kind in place to fix misclassifications without re-speaking.
 * - On SAVED (with lastSavedEntry): triggers Undo (removes SQLite + archives Notion row).
 * - Otherwise: navigates holdings deck.
 */
export async function executeSwipeAction(
  state: CockpitState,
  delta: number,
  deps: SwipeActionDeps,
): Promise<void> {
  if (state.mode === 'PROCESSING' || deps.isRecording()) return;

  // Misclassification recovery right on the glasses CONFIRM card
  if (state.pendingWrite) {
    const pending = state.pendingWrite;
    const nextTarget = nextMindTarget(pending.mind, delta);
    const active = deps.getActiveHolding(state);
    const ticker = nextTarget.mind === 'work' ? (pending.ticker ?? active?.ticker ?? null) : null;

    state.mode = 'PROCESSING';
    try {
      const updated = await deps.reclassifyPendingWrite(pending.id, {
        mind: nextTarget.mind,
        kind: nextTarget.kind,
        ticker,
      });
      state.pendingWrite = updated.pending_write;
      state.mode = 'CONFIRM_WRITE';
      await deps.showCard(updated.hud);
    } catch (err) {
      state.mode = 'CONFIRM_WRITE';
      await deps.showCard({
        header: 'COCKPIT · RECLASSIFY ERROR',
        body: err instanceof Error ? err.message : 'Could not switch mind.',
        footer: 'Tap: save · 2x: cancel',
      });
    }
    return;
  }

  // Undo immediately from the SAVED receipt screen
  if (state.mode === 'SAVED' && state.lastSavedEntry) {
    const entryToUndo = state.lastSavedEntry;
    state.lastSavedEntry = null;
    state.mode = 'PROCESSING';
    await deps.showCard({
      header: 'COCKPIT · UNDOING',
      body: 'Removing entry from journal and Notion...',
      footer: 'Please wait...',
    });
    try {
      const undone = await deps.undoJournalEntry(entryToUndo.id);
      await deps.refreshDeckAfterChange();
      state.mode = 'SAVED';
      await deps.showCard(undone.hud);
    } catch (err) {
      state.mode = 'BRIEFING';
      await deps.showCard({
        header: 'COCKPIT · UNDO ERROR',
        body: err instanceof Error ? err.message : 'Could not undo entry.',
        footer: 'Swipe: deck',
      });
    }
    return;
  }

  state.suggestedCheck = null;
  state.lastSavedEntry = null;
  if (state.holdings.length > 0) {
    if (state.mode === 'HOME') {
      // Entering watchlist from HOME starts at the current holding (NVDA 1/3) instead of skipping it
      state.holdingIndex =
        ((state.holdingIndex % state.holdings.length) + state.holdings.length) %
        state.holdings.length;
    } else {
      state.holdingIndex =
        (state.holdingIndex + delta + state.holdings.length) % state.holdings.length;
    }
    state.mode = 'BRIEFING';
    state.returnMode = 'BRIEFING';
    state.activeTicker = state.holdings[state.holdingIndex]?.ticker ?? null;
    await deps.showCard(holdingToCard(state));
  }
}

/**
 * Pulls updated questions from Notion and updates the deck.
 * Always clears and cancels any staged pending confirmation BEFORE leaving CONFIRM_WRITE
 * so a subsequent tap on the deck can never accidentally commit an old unseen entry.
 */
export async function executePullNotionAction(
  state: CockpitState,
  deps: PullNotionDeps,
): Promise<void> {
  if (state.mode === 'PROCESSING' || deps.isRecording()) return;

  const pendingToCancel = state.pendingWrite;
  state.pendingWrite = null;
  state.suggestedCheck = null;
  state.lastSavedEntry = null;
  deps.onBeforeSync?.();
  if (pendingToCancel) {
    try {
      await deps.cancelWrite(pendingToCancel.id);
    } catch {
      // Best-effort cancel; local pending state is already cleared.
    }
  }

  state.mode = 'PROCESSING';
  await deps.showCard({
    header: 'COCKPIT · SYNCING NOTION',
    body: 'Pulling updated questions from Holdings Watchlist...',
    footer: 'Please wait...',
  });

  try {
    const res = await deps.pullNotion();
    await deps.refreshDeckAfterPull();
    if (res.recomputed.length > 0) {
      const idx = state.holdings.findIndex((h) => h.ticker === res.recomputed[0]);
      if (idx >= 0) state.holdingIndex = idx;
      state.returnMode = 'BRIEFING';
      state.activeTicker = res.recomputed[0] ?? null;
      state.mode = 'SAVED';
      state.lastSavedEntry = null;
      await deps.showCard({
        header: 'NOTION · UPDATED',
        body: `Refreshed ${res.recomputed.join(', ')} from edited Saved Question in Notion.`,
        footer: 'Tap: view updated card · Swipe: next',
      });
    } else {
      state.mode = state.returnMode;
      if (state.mode === 'HOME') {
        state.activeTicker = null;
      }
      await deps.showCard(restCard(state));
    }
  } catch (err) {
    state.mode = state.returnMode;
    await deps.showCard({
      header: 'NOTION · SYNC ERROR',
      body: err instanceof Error ? err.message : 'Failed to pull Notion watchlist.',
      footer: 'Swipe: watchlist · 2x: home',
    });
  }
}

/**
 * Starts native Even Hub bridge PCM capture, marking the session active BEFORE awaiting
 * HUD rendering so a release arriving mid-render cancels startup cleanly.
 */
export async function startBridgeCapture(
  state: CockpitState,
  capture: BridgeCaptureState,
  deps: BridgeCaptureDeps,
): Promise<'started' | 'cancelled' | 'failed' | 'ignored'> {
  if (
    state.mode === 'PROCESSING' ||
    capture.activeMicKind !== null ||
    deps.recorder.isActive ||
    deps.isOtherMicActive()
  ) {
    return 'ignored';
  }
  deps.onBeforeStart?.();

  const token = deps.recorder.beginStart();
  capture.activeMicKind = 'bridge';
  state.mode = 'LISTENING';
  deps.setMicLabel(true, 'G2/Sim');

  await deps.showCard({
    header: 'COCKPIT · [REC] LISTENING',
    body:
      deps.listeningBody ?? 'Speak a note, task, or earnings question.\nRelease when finished.',
    footer: 'Release to transcribe via OpenRouter',
  });

  if (!deps.recorder.isSessionCurrent(token)) {
    return 'cancelled';
  }

  const started = await deps.recorder.start(
    deps.bridge,
    deps.source ?? AudioInputSource.Glasses,
    token,
  );
  if (started) {
    return 'started';
  }

  if (!deps.recorder.isSessionCurrent(token)) {
    return 'cancelled';
  }

  capture.activeMicKind = null;
  deps.setMicLabel(false);
  return 'failed';
}

export async function stopBridgeCapture(
  state: CockpitState,
  capture: BridgeCaptureState,
  deps: Pick<
    BridgeCaptureDeps,
    'recorder' | 'bridge' | 'setMicLabel' | 'showCard' | 'onAudioReady'
  >,
): Promise<void> {
  if (capture.activeMicKind !== 'bridge' && !deps.recorder.isActive) {
    return;
  }

  const wasPreStart = deps.recorder.isStarting && !deps.recorder.isRecording;
  const wasListening = state.mode === 'LISTENING';
  capture.activeMicKind = null;
  deps.setMicLabel(false);

  const wavBlob = await deps.recorder.stop(deps.bridge);
  if (!wavBlob) {
    if (wasPreStart) {
      if (wasListening) {
        if (state.pendingWrite) {
          state.mode = 'CONFIRM_WRITE';
          await deps.showCard(pendingToConfirmCard(state.pendingWrite));
        } else {
          state.mode = state.returnMode;
          if (state.mode === 'HOME') {
            state.activeTicker = null;
          }
          await deps.showCard(restCard(state));
        }
      }
      return;
    }
    if (!wasListening) {
      return;
    }
  }

  await deps.onAudioReady(wavBlob);
}
