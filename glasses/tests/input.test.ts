import assert from 'node:assert/strict';
import test from 'node:test';
import {
  OsEventTypeList,
  Sys_ItemEvent,
  Text_ItemEvent,
  type EvenHubEvent,
} from '@evenrealities/even_hub_sdk';
import {
  dispatchEvenHubEvent,
  executeTapAction,
  resolveGestureEventType,
  type TapActionDeps,
} from '../src/input.ts';
import type { CockpitState } from '../src/state.ts';
import type { ConfirmWriteResponse, HudCard } from '../src/types.ts';

function makeState(overrides: Partial<CockpitState> = {}): CockpitState {
  return {
    mode: 'BRIEFING',
    holdings: [],
    holdingIndex: 0,
    pendingWrite: null,
    suggestedCheck: null,
    lastDecision: null,
    recentJournal: [],
    mockMode: true,
    notionEnabled: false,
    ...overrides,
  };
}

test('documented G2 single tap with omitted protobuf eventType confirms exactly one save', async () => {
  const state = makeState({
    mode: 'CONFIRM_WRITE',
    pendingWrite: {
      id: 'pending-123',
      mind: 'personal',
      kind: 'task',
      content: 'Remember to buy a birthday present',
      ticker: null,
      source_citation: null,
      updates_next_check: false,
    },
  });

  const savedIds: string[] = [];
  const renderedCards: HudCard[] = [];
  let releaseSave!: () => void;
  const saveGate = new Promise<void>((resolve) => {
    releaseSave = resolve;
  });

  const deps: TapActionDeps = {
    isRecording: () => false,
    getActiveHolding: () => null,
    confirmWrite: async (pendingId: string): Promise<ConfirmWriteResponse> => {
      savedIds.push(pendingId);
      await saveGate;
      return {
        entry: {
          id: 1,
          mind: 'personal',
          kind: 'task',
          content: 'Remember to buy a birthday present',
          ticker: null,
          source_citation: null,
          notion_page_id: null,
          created_at: '2026-11-24T14:30:00Z',
        },
        holding: null,
        synced_to_notion: false,
        hud: {
          header: 'PERSONAL · SAVED',
          body: '✓ Remember to buy a birthday present\nSaved to Journal.',
          footer: 'Hold: speak · Swipe: holdings',
        },
      };
    },
    stageCheck: async () => {
      throw new Error('should not stage during confirm');
    },
    stageSuggestedCheck: async () => {
      throw new Error('should not stage during confirm');
    },
    refreshDeckAfterSave: async () => {},
    showCard: async (card) => {
      renderedCards.push(card);
    },
  };

  // Documented Even G2 single tap: protobuf omits zero-value CLICK_EVENT (0), so eventType is undefined.
  const g2TextTap: EvenHubEvent = {
    textEvent: new Text_ItemEvent({ containerID: 2, containerName: 'body' }),
  };
  assert.equal(resolveGestureEventType(g2TextTap), OsEventTypeList.CLICK_EVENT);

  const inFlight: Promise<void>[] = [];
  const handlers = {
    onAudioPcm: () => {},
    onMenuItemClick: () => {},
    onLongPress: () => {},
    onLongPressRelease: () => {},
    onTap: () => {
      inFlight.push(executeTapAction(state, deps));
    },
    onSwipeNext: () => {},
    onSwipePrev: () => {},
    onDoubleTap: () => {},
  };

  // Fire documented tap with omitted eventType + overlapping rapid second tap while PROCESSING
  dispatchEvenHubEvent(g2TextTap, handlers);
  dispatchEvenHubEvent({ sysEvent: new Sys_ItemEvent({}) }, handlers);

  releaseSave();
  await Promise.all(inFlight);

  assert.deepEqual(savedIds, ['pending-123']);
  assert.equal(state.pendingWrite, null);
  assert.equal(state.mode, 'RESULT');
  assert.equal(renderedCards.at(-1)?.header, 'PERSONAL · SAVED');
});

test('non-gesture sysEvents and empty envelopes do not trigger phantom taps', () => {
  assert.equal(resolveGestureEventType({}), undefined);
  assert.equal(
    resolveGestureEventType({
      sysEvent: new Sys_ItemEvent({ imuData: { x: 0.1, y: 0.2, z: 9.8 } }),
    }),
    undefined,
  );
  assert.equal(
    resolveGestureEventType({
      sysEvent: new Sys_ItemEvent({ systemExitReasonCode: 1 }),
    }),
    undefined,
  );
  // Explicit non-zero events still resolve normally
  assert.equal(
    resolveGestureEventType({
      sysEvent: new Sys_ItemEvent({ eventType: OsEventTypeList.DOUBLE_CLICK_EVENT }),
    }),
    OsEventTypeList.DOUBLE_CLICK_EVENT,
  );
});

test('follow-up tap previews exact proposed draft on glasses before confirming save', async () => {
  const proposedText = 'At next call, check whether Q1 FY28 price increases are on schedule';
  const state = makeState({
    mode: 'RESULT',
    suggestedCheck: {
      ticker: 'NVDA',
      content: proposedText,
      source_citation: 'Aug FY27 Call · p. 7',
    },
  });

  const renderedCards: HudCard[] = [];
  let confirmCalls = 0;

  const deps: TapActionDeps = {
    isRecording: () => false,
    getActiveHolding: () => null,
    stageCheck: async (draft) => ({
      pending_write: {
        id: 'draft-42',
        mind: 'work',
        kind: 'check',
        content: draft.content,
        ticker: draft.ticker,
        source_citation: draft.source_citation,
        updates_next_check: true,
      },
      hud: {
        header: 'WORK · NVDA · CONFIRM CHECK',
        body: draft.content,
        footer: 'Tap: save to journal · Swipe: cancel',
      },
    }),
    stageSuggestedCheck: async () => {
      throw new Error('unexpected');
    },
    confirmWrite: async () => {
      confirmCalls += 1;
      return {
        entry: {
          id: 2,
          mind: 'work',
          kind: 'check',
          content: proposedText,
          ticker: 'NVDA',
          source_citation: 'Aug FY27 Call · p. 7',
          notion_page_id: null,
          created_at: '2026-11-24T14:31:00Z',
        },
        holding: null,
        synced_to_notion: false,
        hud: {
          header: 'WORK · SAVED',
          body: `✓ ${proposedText}\nSaved to Journal.`,
          footer: 'Hold: speak · Swipe: holdings',
        },
      };
    },
    refreshDeckAfterSave: async () => {},
    showCard: async (card) => {
      renderedCards.push(card);
    },
  };

  // First tap displays the exact proposed journal entry without saving yet
  await executeTapAction(state, deps);
  assert.equal(confirmCalls, 0);
  assert.equal(state.mode, 'CONFIRM_WRITE');
  assert.equal(renderedCards[0]?.header, 'WORK · NVDA · CONFIRM CHECK');
  assert.equal(renderedCards[0]?.body, proposedText);

  // Second tap confirms and saves after wearer has seen the wording
  await executeTapAction(state, deps);
  assert.equal(confirmCalls, 1);
  assert.equal(state.mode, 'RESULT');
});

test('tapping a holding with no matching passage does not stage or save a check', async () => {
  const state = makeState({
    mode: 'BRIEFING',
    holdings: [
      {
        ticker: 'NVDA',
        company: 'NVIDIA Corp.',
        saved_question: 'Warranty reserves?',
        verdict: null,
        latest_finding: 'No matching passage in indexed filings for this question.',
        source_citation: '',
        suggested_next_check: '',
        next_check: '',
        notion_page_id: null,
        updated_at: '2026-11-24T14:32:00Z',
      },
    ],
  });

  let stageCalls = 0;
  let confirmCalls = 0;
  await executeTapAction(state, {
    isRecording: () => false,
    getActiveHolding: (s) => s.holdings[0] ?? null,
    stageCheck: async () => {
      stageCalls += 1;
      throw new Error('should not stage');
    },
    stageSuggestedCheck: async () => {
      stageCalls += 1;
      throw new Error('should not stage');
    },
    confirmWrite: async () => {
      confirmCalls += 1;
      throw new Error('should not confirm');
    },
    refreshDeckAfterSave: async () => {},
    showCard: async () => {},
  });

  assert.equal(stageCalls, 0);
  assert.equal(confirmCalls, 0);
  assert.equal(state.mode, 'BRIEFING');
});
