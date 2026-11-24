export type Mind = 'work' | 'personal' | 'interests';

export type ActionType =
  | 'log_note'
  | 'log_task'
  | 'brief_holding'
  | 'follow_up_source'
  | 'draft_next_check'
  | 'clarify';

export type Verdict = 'HELD' | 'AHEAD' | 'WATCH' | 'PENDING';

export interface HudCard {
  header: string;
  body: string;
  footer: string;
}

export interface JevDecision {
  mind: Mind;
  action: ActionType;
  holding: string | null;
  needs_llm_worker: boolean;
  confidence: number;
  mode: 'openrouter' | 'mock';
  reasoning: string | null;
}

export interface HoldingRecord {
  ticker: string;
  company: string;
  saved_question: string;
  verdict: Verdict | null;
  latest_finding: string;
  source_citation: string;
  suggested_next_check: string;
  next_check: string;
  notion_page_id: string | null;
  updated_at: string;
}

export interface JournalEntry {
  id: number;
  mind: Mind;
  kind: 'note' | 'task' | 'check';
  content: string;
  ticker: string | null;
  source_citation: string | null;
  notion_page_id: string | null;
  created_at: string;
}

export interface PendingWrite {
  id: string;
  mind: Mind;
  kind: 'note' | 'task' | 'check';
  content: string;
  ticker: string | null;
  source_citation: string | null;
  updates_next_check: boolean;
}

export interface DraftCheck {
  content: string;
  ticker: string;
  source_citation: string | null;
}

export interface UtteranceResponse {
  transcript: string;
  decision: JevDecision;
  hud: HudCard;
  holding: HoldingRecord | null;
  pending_write: PendingWrite | null;
  suggested_check: DraftCheck | null;
}

export interface ConfirmWriteResponse {
  entry: JournalEntry;
  holding: HoldingRecord | null;
  synced_to_notion: boolean;
  hud: HudCard;
}

export interface DeckStateResponse {
  holdings: HoldingRecord[];
  recent_journal: JournalEntry[];
  pending_jobs: number;
  mock_mode: boolean;
  notion_enabled: boolean;
}
