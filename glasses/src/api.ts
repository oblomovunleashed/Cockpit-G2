import type {
  ConfirmWriteResponse,
  DeckStateResponse,
  DraftCheck,
  HoldingRecord,
  HudCard,
  PendingWrite,
  UtteranceResponse,
} from './types';

const DEFAULT_BASE_URL = 'http://127.0.0.1:8000';

export class CockpitApiClient {
  private readonly baseUrl: string;

  constructor(baseUrl: string = DEFAULT_BASE_URL) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
  }

  async getState(): Promise<DeckStateResponse> {
    const res = await fetch(`${this.baseUrl}/api/state`);
    if (!res.ok) throw new Error(`GET /api/state failed (${res.status})`);
    return (await res.json()) as DeckStateResponse;
  }

  async sendText(transcript: string, activeTicker: string | null): Promise<UtteranceResponse> {
    const res = await fetch(`${this.baseUrl}/api/text`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcript, active_ticker: activeTicker }),
    });
    if (!res.ok) throw new Error(`POST /api/text failed (${res.status})`);
    return (await res.json()) as UtteranceResponse;
  }

  async sendVoice(
    wavBlob: Blob,
    activeTicker: string | null,
    fallbackTranscript?: string,
  ): Promise<UtteranceResponse> {
    const form = new FormData();
    form.append('audio', wavBlob, 'clip.wav');
    if (activeTicker) form.append('active_ticker', activeTicker);
    if (fallbackTranscript) form.append('fallback_transcript', fallbackTranscript);

    const res = await fetch(`${this.baseUrl}/api/voice`, {
      method: 'POST',
      body: form,
    });
    if (!res.ok) throw new Error(`POST /api/voice failed (${res.status})`);
    return (await res.json()) as UtteranceResponse;
  }

  async stageCheck(
    draft: DraftCheck,
  ): Promise<{ pending_write: PendingWrite; hud: HudCard }> {
    const res = await fetch(`${this.baseUrl}/api/stage-check`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ticker: draft.ticker,
        content: draft.content,
        source_citation: draft.source_citation,
      }),
    });
    if (!res.ok) throw new Error(`POST /api/stage-check failed (${res.status})`);
    return (await res.json()) as { pending_write: PendingWrite; hud: HudCard };
  }

  async stageSuggestedCheck(
    ticker: string,
  ): Promise<{ pending_write: PendingWrite; hud: HudCard }> {
    const res = await fetch(
      `${this.baseUrl}/api/holdings/${encodeURIComponent(ticker)}/stage-suggested-check`,
      { method: 'POST' },
    );
    if (!res.ok) throw new Error(`POST stage-suggested-check failed (${res.status})`);
    return (await res.json()) as { pending_write: PendingWrite; hud: HudCard };
  }

  async confirmWrite(pendingId: string, overrideText?: string): Promise<ConfirmWriteResponse> {
    const res = await fetch(`${this.baseUrl}/api/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pending_id: pendingId, override_text: overrideText ?? null }),
    });
    if (!res.ok) throw new Error(`POST /api/confirm failed (${res.status})`);
    return (await res.json()) as ConfirmWriteResponse;
  }

  async cancelWrite(pendingId: string): Promise<void> {
    await fetch(`${this.baseUrl}/api/cancel/${encodeURIComponent(pendingId)}`, {
      method: 'POST',
    });
  }

  async updateHoldingQuestion(
    ticker: string,
    savedQuestion: string,
  ): Promise<{ holding: HoldingRecord; hud: HudCard }> {
    const res = await fetch(`${this.baseUrl}/api/holdings/${encodeURIComponent(ticker)}/question`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ saved_question: savedQuestion }),
    });
    if (!res.ok) throw new Error(`PATCH question failed (${res.status})`);
    return (await res.json()) as { holding: HoldingRecord; hud: HudCard };
  }

  async pullNotion(): Promise<{ pulled_count: number; recomputed: string[] }> {
    const res = await fetch(`${this.baseUrl}/api/notion/pull`, { method: 'POST' });
    if (!res.ok) throw new Error(`POST /api/notion/pull failed (${res.status})`);
    return (await res.json()) as { pulled_count: number; recomputed: string[] };
  }
}
