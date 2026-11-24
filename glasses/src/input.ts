import { OsEventTypeList, type EvenHubEvent } from '@evenrealities/even_hub_sdk';
import type { CockpitState } from './state';
import type {
  ConfirmWriteResponse,
  DraftCheck,
  HoldingRecord,
  HudCard,
  PendingWrite,
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
    // Non-gesture sysEvents carry imuData or systemExitReasonCode; treat bare sysEvent as CLICK.
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
 * Executes the single-tap action with two core safety invariants:
 * 1. Never fires duplicate saves while PROCESSING or recording.
 * 2. Never saves a draft whose exact wording has not already been displayed on the CONFIRM card.
 */
export async function executeTapAction(state: CockpitState, deps: TapActionDeps): Promise<void> {
  if (state.mode === 'PROCESSING' || deps.isRecording()) return;

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
      state.mode = 'RESULT';
      await deps.refreshDeckAfterSave();
      await deps.showCard(confirmed.hud);
    } catch (err) {
      state.mode = 'BRIEFING';
      await deps.showCard({
        header: 'COCKPIT · SAVE ERROR',
        body: err instanceof Error ? err.message : 'Failed to save entry.',
        footer: 'Swipe to return to deck',
      });
    }
    return;
  }

  // Step 1a: On a source follow-up card, preview the exact proposed check before saving
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

  // Step 1b: On a holding card, preview its suggested check only if one exists
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
