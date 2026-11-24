import type { HoldingRecord, HudCard, JevDecision, JournalEntry, PendingWrite } from './types';

export type ViewMode = 'BRIEFING' | 'LISTENING' | 'PROCESSING' | 'CONFIRM_WRITE' | 'RESULT';

export interface CockpitState {
  mode: ViewMode;
  holdings: HoldingRecord[];
  holdingIndex: number;
  pendingWrite: PendingWrite | null;
  lastDecision: JevDecision | null;
  recentJournal: JournalEntry[];
  mockMode: boolean;
  notionEnabled: boolean;
}

export function createInitialState(): CockpitState {
  return {
    mode: 'BRIEFING',
    holdings: [],
    holdingIndex: 0,
    pendingWrite: null,
    lastDecision: null,
    recentJournal: [],
    mockMode: true,
    notionEnabled: false,
  };
}

export function activeHolding(state: CockpitState): HoldingRecord | null {
  if (state.holdings.length === 0) return null;
  const idx = ((state.holdingIndex % state.holdings.length) + state.holdings.length) % state.holdings.length;
  return state.holdings[idx] ?? null;
}

export function holdingToCard(state: CockpitState): HudCard {
  const holding = activeHolding(state);
  if (!holding) {
    return {
      header: 'COCKPIT · READY',
      body: 'No holdings loaded. Hold to log a note, task, or review earnings.',
      footer: 'Hold: speak · Double-tap: exit',
    };
  }
  const pos = state.holdingIndex + 1;
  const total = state.holdings.length;
  const nextLine = holding.next_check ? `\nNext: ${holding.next_check}` : '';
  return {
    header: `WORK · ${holding.ticker} ${pos}/${total} · ${holding.verdict}`,
    body: `${holding.latest_finding}\n[${holding.source_citation}]${nextLine}`,
    footer: 'Hold: speak · Tap: save check · Swipe: next',
  };
}
