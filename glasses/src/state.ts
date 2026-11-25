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
  | 'HOME'
  | 'BRIEFING'
  | 'LISTENING'
  | 'PROCESSING'
  | 'CONFIRM_WRITE'
  | 'SAVED'
  | 'RESULT';

export type RestMode = 'HOME' | 'BRIEFING';

export interface CockpitState {
  mode: ViewMode;
  returnMode: RestMode;
  activeTicker: string | null;
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
    mode: 'HOME',
    returnMode: 'HOME',
    activeTicker: null,
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

/**
 * Returns the currently focused holding ONLY when the wearer is actively viewing
 * the Work watchlist or has a tracked holding in focus — never leaks NVDA from HOME.
 */
export function activeHolding(state: CockpitState): HoldingRecord | null {
  if (state.holdings.length === 0) return null;
  if (state.mode === 'HOME') return null;

  if (state.mode === 'BRIEFING') {
    const idx =
      ((state.holdingIndex % state.holdings.length) + state.holdings.length) %
      state.holdings.length;
    return state.holdings[idx] ?? null;
  }

  if (state.activeTicker) {
    return (
      state.holdings.find(
        (h) => h.ticker.toUpperCase() === state.activeTicker?.toUpperCase(),
      ) ?? null
    );
  }

  return null;
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

export function homeCard(state: CockpitState): HudCard {
  const tickers = state.holdings.map((h) => h.ticker).join(', ');
  const latest = state.recentJournal[0];
  const lines = ['Hold to speak (Work, Personal, Interests).'];

  if (latest) {
    const tag = latest.ticker
      ? `${latest.mind.toUpperCase()} ${latest.ticker}`
      : latest.mind.toUpperCase();
    const snippet = latest.enriched_summary?.trim() || latest.content;
    lines.push(`Last [${tag}]: ${snippet}`);
  } else if (tickers) {
    lines.push(`Watchlist ready: ${tickers}`);
  }

  return {
    header: 'COCKPIT · READY',
    body: lines.join('\n'),
    footer: 'Hold: speak · Swipe: watchlist · 2x: exit',
  };
}

export function holdingToCard(state: CockpitState): HudCard {
  if (state.holdings.length === 0) {
    return homeCard(state);
  }
  const idx =
    ((state.holdingIndex % state.holdings.length) + state.holdings.length) %
    state.holdings.length;
  const holding = state.holdings[idx];
  if (!holding) {
    return homeCard(state);
  }
  const pos = idx + 1;
  const total = state.holdings.length;
  const verdictPart = holding.verdict ? ` · ${holding.verdict}` : '';
  const sourceLine = holding.source_citation ? `\n[${holding.source_citation}]` : '';
  const nextLine = holding.next_check ? `\nNext: ${holding.next_check}` : '';
  const footer = holding.suggested_next_check
    ? 'Tap: review check · Swipe: next · 2x: home'
    : 'Hold: speak · Swipe: next · 2x: home';

  return {
    header: `WORK · ${holding.ticker} ${pos}/${total}${verdictPart}`,
    body: `${holding.latest_finding}${sourceLine}${nextLine}`,
    footer,
  };
}

/** Returns the appropriate calm rest card (HOME by default, or BRIEFING only when browsing watchlist). */
export function restCard(state: CockpitState): HudCard {
  if (state.returnMode === 'BRIEFING' && state.holdings.length > 0) {
    return holdingToCard(state);
  }
  return homeCard(state);
}
