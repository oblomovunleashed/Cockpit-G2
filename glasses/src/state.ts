import type {
  DraftCheck,
  HoldingRecord,
  HudCard,
  JevDecision,
  JournalEntry,
  Mind,
  PendingWrite,
} from './types';

export type ViewMode =
  | 'BRIEFING'
  | 'LISTENING'
  | 'PROCESSING'
  | 'CONFIRM_WRITE'
  | 'SAVED'
  | 'RESULT';

export interface CockpitState {
  mode: ViewMode;
  holdings: HoldingRecord[];
  holdingIndex: number;
  pendingWrite: PendingWrite | null;
  suggestedCheck: DraftCheck | null;
  lastSavedEntry: JournalEntry | null;
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
    suggestedCheck: null,
    lastSavedEntry: null,
    lastDecision: null,
    recentJournal: [],
    mockMode: true,
    notionEnabled: false,
  };
}

export function activeHolding(state: CockpitState): HoldingRecord | null {
  if (state.holdings.length === 0) return null;
  const idx =
    ((state.holdingIndex % state.holdings.length) + state.holdings.length) %
    state.holdings.length;
  return state.holdings[idx] ?? null;
}

const MIND_CYCLE: ReadonlyArray<{ mind: Mind; kind: 'note' | 'task' | 'check' }> = [
  { mind: 'interests', kind: 'note' },
  { mind: 'personal', kind: 'task' },
  { mind: 'work', kind: 'check' },
];

export function nextMindTarget(
  currentMind: Mind,
  delta: number,
): { mind: Mind; kind: 'note' | 'task' | 'check' } {
  const idx = MIND_CYCLE.findIndex((item) => item.mind === currentMind);
  const start = idx >= 0 ? idx : 0;
  const step = delta >= 0 ? 1 : -1;
  const nextIdx = (start + step + MIND_CYCLE.length) % MIND_CYCLE.length;
  return MIND_CYCLE[nextIdx]!;
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
  const verdictPart = holding.verdict ? ` · ${holding.verdict}` : '';
  const sourceLine = holding.source_citation ? `\n[${holding.source_citation}]` : '';
  const nextLine = holding.next_check ? `\nNext: ${holding.next_check}` : '';
  const footer = holding.suggested_next_check
    ? 'Hold: speak · Tap: review check · Swipe: next'
    : 'Hold: ask or dictate check · Swipe: next';

  return {
    header: `WORK · ${holding.ticker} ${pos}/${total}${verdictPart}`,
    body: `${holding.latest_finding}${sourceLine}${nextLine}`,
    footer,
  };
}
