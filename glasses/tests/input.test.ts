import assert from 'node:assert/strict';
import test from 'node:test';
import {
  OsEventTypeList,
  Sys_ItemEvent,
  Text_ItemEvent,
  type EvenHubEvent,
} from '@evenrealities/even_hub_sdk';
import { BrowserMicRecorder, PcmAudioRecorder } from '../src/audio.ts';
import { sanitizeLine } from '../src/hud.ts';
import {
  dispatchEvenHubEvent,
  executePullNotionAction,
  executeSwipeAction,
  executeTapAction,
  nextViewModeFromUtterance,
  resolveGestureEventType,
  startBridgeCapture,
  stopBridgeCapture,
  type BridgeCaptureState,
  type SwipeActionDeps,
  type TapActionDeps,
} from '../src/input.ts';
import {
  activeHolding,
  createInitialState,
  homeCard,
  type CockpitState,
} from '../src/state.ts';
import type { ConfirmWriteResponse, HudCard, UtteranceResponse } from '../src/types.ts';

function makeState(overrides: Partial<CockpitState> = {}): CockpitState {
  return {
    mode: 'BRIEFING',
    returnMode: 'BRIEFING',
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
  assert.equal(state.mode, 'SAVED');
  assert.equal(state.lastSavedEntry?.id, 1);
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
  const proposedText = 'At Q4 call, check whether ex-true-up gross margin stays above 63%';
  const state = makeState({
    mode: 'RESULT',
    suggestedCheck: {
      ticker: 'NTRA',
      content: proposedText,
      source_citation: 'Q3 2026 PR · Nov 5',
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
        header: 'WORK · NTRA · CONFIRM CHECK',
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
          ticker: 'NTRA',
          source_citation: 'Q3 2026 PR · Nov 5',
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
  assert.equal(renderedCards[0]?.header, 'WORK · NTRA · CONFIRM CHECK');
  assert.equal(renderedCards[0]?.body, proposedText);

  // Second tap confirms and saves after wearer has seen the wording
  await executeTapAction(state, deps);
  assert.equal(confirmCalls, 1);
  assert.equal(state.mode, 'SAVED');
});

test('tapping a holding with no matching passage does not stage or save a check', async () => {
  const state = makeState({
    mode: 'BRIEFING',
    holdings: [
      {
        ticker: 'NTRA',
        company: 'Natera, Inc.',
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

test('tapping a no-match follow-up card does not fall through to the holding old suggested check', async () => {
  const holdingWithCheck = {
    ticker: 'NTRA',
    company: 'Natera, Inc.',
    saved_question: 'Is Natera sustaining 63%+ ex-true-up gross margin as Signatera scales?',
    verdict: 'HELD' as const,
    latest_finding: 'Q3 GAAP GM 64.7%; ex-true-up non-GAAP GM 63.2% (+140 bps QoQ).',
    source_citation: 'Q3 2026 PR · Nov 5',
    suggested_next_check: 'Check whether ex-true-up gross margin stays above 63% as Signatera scales',
    next_check: '',
    notion_page_id: null,
    updated_at: '2026-11-24T14:35:00Z',
  };

  const noMatchResponse: UtteranceResponse = {
    transcript: 'What did management say about warranty reserves?',
    decision: {
      mind: 'work',
      action: 'follow_up_source',
      holding: 'NTRA',
      peer_holding: null,
      needs_llm_worker: true,
      confidence: 0.93,
      mode: 'mock',
      reasoning: 'Work follow-up question requires source retrieval + worker.',
    },
    hud: {
      header: 'WORK · NTRA · NO MATCH',
      body: 'No matching passage in indexed filings for this question.',
      footer: 'Hold: ask again or dictate check',
    },
    holding: holdingWithCheck,
    pending_write: null,
    suggested_check: null,
  };

  const state = makeState({
    mode: nextViewModeFromUtterance(noMatchResponse),
    holdings: [holdingWithCheck],
    pendingWrite: noMatchResponse.pending_write,
    suggestedCheck: noMatchResponse.suggested_check,
  });

  assert.equal(state.mode, 'RESULT');

  let stageSuggestedCalls = 0;
  let confirmCalls = 0;
  const deps: TapActionDeps = {
    isRecording: () => false,
    getActiveHolding: (s) => s.holdings[0] ?? null,
    stageCheck: async () => {
      throw new Error('should not stage draft check');
    },
    stageSuggestedCheck: async () => {
      stageSuggestedCalls += 1;
      return {
        pending_write: {
          id: 'old-check',
          mind: 'work',
          kind: 'check',
          content: holdingWithCheck.suggested_next_check,
          ticker: 'NTRA',
          source_citation: holdingWithCheck.source_citation,
          updates_next_check: true,
        },
        hud: {
          header: 'WORK · NTRA · CONFIRM CHECK',
          body: holdingWithCheck.suggested_next_check,
          footer: 'Tap: save to journal · Swipe: cancel',
        },
      };
    },
    confirmWrite: async () => {
      confirmCalls += 1;
      throw new Error('should not confirm');
    },
    refreshDeckAfterSave: async () => {},
    showCard: async () => {},
  };

  // Tapping on the NO MATCH follow-up card must NOT stage the holding's unrelated old check
  await executeTapAction(state, deps);
  assert.equal(stageSuggestedCalls, 0);
  assert.equal(confirmCalls, 0);
  assert.equal(state.mode, 'RESULT');

  // Once the user returns to BRIEFING deck, tapping on the holding card works normally again
  state.mode = 'BRIEFING';
  await executeTapAction(state, deps);
  assert.equal(stageSuggestedCalls, 1);
  assert.equal(state.mode, 'CONFIRM_WRITE');
});

test('earnings_preview stays in RESULT (no tap fallback) while peer_read_across previews draft check first', async () => {
  const previewRes: UtteranceResponse = {
    transcript: 'Preview Natera before earnings',
    decision: {
      mind: 'work',
      action: 'earnings_preview',
      holding: 'NTRA',
      peer_holding: null,
      needs_llm_worker: false,
      confidence: 0.95,
      mode: 'mock',
      reasoning: 'Deterministic earnings_preview skill (zero LLM).',
    },
    hud: {
      header: 'PREVIEW · NTRA · PRE-CALL',
      body: 'Q: Is Natera sustaining 63%+ ex-true-up gross margin as Signatera scales?',
      footer: 'Hold: update check · Swipe: holdings',
    },
    holding: null,
    pending_write: null,
    suggested_check: null,
  };
  assert.equal(nextViewModeFromUtterance(previewRes), 'RESULT');

  const peerRes: UtteranceResponse = {
    transcript: "What's the read-across from Amazon to Seagate?",
    decision: {
      mind: 'work',
      action: 'peer_read_across',
      holding: 'STX',
      peer_holding: 'AMZN',
      needs_llm_worker: true,
      confidence: 0.95,
      mode: 'mock',
      reasoning: 'Peer read-across AMZN -> STX',
    },
    hud: {
      header: 'PEER · AMZN → STX',
      body: 'Amazon ~$220B FY26 AI data-center capex supports Seagate nearline HAMR demand.',
      footer: 'Tap: review check · Hold: dictate',
    },
    holding: null,
    pending_write: null,
    suggested_check: {
      ticker: 'STX',
      content: 'Check on STX call whether Amazon AWS capex extends 2028 HAMR allocations',
      source_citation: 'Peer read: AMZN Q3 2026 Release · Oct 29',
    },
  };
  assert.equal(nextViewModeFromUtterance(peerRes), 'RESULT');

  const state = makeState({
    mode: nextViewModeFromUtterance(peerRes),
    suggestedCheck: peerRes.suggested_check,
  });

  const stagedBodies: string[] = [];
  let confirmCalls = 0;
  await executeTapAction(state, {
    isRecording: () => false,
    getActiveHolding: () => null,
    stageCheck: async (draft) => {
      stagedBodies.push(draft.content);
      return {
        pending_write: {
          id: 'peer-draft-1',
          mind: 'work',
          kind: 'check',
          content: draft.content,
          ticker: draft.ticker,
          source_citation: draft.source_citation,
          updates_next_check: true,
        },
        hud: {
          header: 'WORK · STX · CONFIRM CHECK',
          body: draft.content,
          footer: 'Tap: save to journal · Swipe: cancel',
        },
      };
    },
    stageSuggestedCheck: async () => {
      throw new Error('should not fall through to holding check');
    },
    confirmWrite: async () => {
      confirmCalls += 1;
      throw new Error('should not confirm on first tap');
    },
    refreshDeckAfterSave: async () => {},
    showCard: async () => {},
  });

  assert.equal(confirmCalls, 0);
  assert.deepEqual(stagedBodies, [
    'Check on STX call whether Amazon AWS capex extends 2028 HAMR allocations',
  ]);
  assert.equal(state.mode, 'CONFIRM_WRITE');
});

test('releasing mic button while getUserMedia permission is pending cancels capture and stops late stream', async () => {
  let resolveStream!: (stream: MediaStream) => void;
  const pendingStream = new Promise<MediaStream>((resolve) => {
    resolveStream = resolve;
  });

  let trackStops = 0;
  let audioContextCreated = false;

  const mic = new BrowserMicRecorder({
    getUserMedia: () => pendingStream,
    createAudioContext: () => {
      audioContextCreated = true;
      throw new Error('AudioContext should not be created after cancellation');
    },
  });

  const startPromise = mic.start();
  assert.equal(mic.isStarting, true);
  assert.equal(mic.isActive, true);
  assert.equal(mic.isRecording, false);

  // User releases button before permission prompt finishes
  const stopResult = await mic.stop();
  assert.equal(stopResult, null);
  assert.equal(mic.isActive, false);

  // Permission is subsequently granted -> late stream must be stopped immediately
  const fakeStream = {
    getTracks: () => [
      {
        stop: () => {
          trackStops += 1;
        },
      },
    ],
  } as unknown as MediaStream;
  resolveStream(fakeStream);

  const started = await startPromise;
  assert.equal(started, false);
  assert.equal(trackStops, 1);
  assert.equal(audioContextCreated, false);
  assert.equal(mic.isRecording, false);
  assert.equal(mic.isActive, false);
});

test('swipe on CONFIRM_WRITE reclassifies destination in place without re-speaking', async () => {
  const ntra = {
    ticker: 'NTRA',
    company: 'Natera, Inc.',
    saved_question: 'Is Natera sustaining 63%+ ex-true-up gross margin as Signatera scales?',
    verdict: 'HELD' as const,
    latest_finding: 'Q3 GAAP GM 64.7%; ex-true-up non-GAAP GM 63.2% (+140 bps QoQ).',
    source_citation: 'Q3 2026 PR',
    suggested_next_check: '',
    next_check: '',
    notion_page_id: null,
    updated_at: '2026-11-25T00:00:00Z',
  };
  const state = makeState({
    mode: 'CONFIRM_WRITE',
    holdings: [ntra],
    pendingWrite: {
      id: 'p-reclass',
      mind: 'interests',
      kind: 'note',
      content: 'Remind me to read that Stratechery piece',
      ticker: null,
      source_citation: null,
      updates_next_check: false,
    },
  });

  const calls: Array<{ mind: string; kind: string; ticker?: string | null }> = [];
  const cards: HudCard[] = [];
  const deps: SwipeActionDeps = {
    isRecording: () => false,
    getActiveHolding: (s) => s.holdings[0] ?? null,
    reclassifyPendingWrite: async (id, payload) => {
      calls.push(payload);
      return {
        pending_write: {
          id,
          mind: payload.mind,
          kind: payload.kind,
          content: state.pendingWrite!.content,
          ticker: payload.ticker ?? null,
          source_citation: null,
          updates_next_check: payload.mind === 'work',
        },
        hud: {
          header: `${payload.mind.toUpperCase()} · CONFIRM ${payload.kind.toUpperCase()}`,
          body: state.pendingWrite!.content,
          footer: 'Tap: save · Swipe: switch mind · 2x: cancel',
        },
      };
    },
    undoJournalEntry: async () => {
      throw new Error('should not undo during confirm');
    },
    refreshDeckAfterChange: async () => {},
    showCard: async (card) => {
      cards.push(card);
    },
  };

  // Swipe 1: Interests -> Personal Task
  await executeSwipeAction(state, 1, deps);
  assert.equal(state.mode, 'CONFIRM_WRITE');
  assert.equal(state.pendingWrite?.mind, 'personal');
  assert.equal(state.pendingWrite?.kind, 'task');
  assert.equal(cards.at(-1)?.header, 'PERSONAL · CONFIRM TASK');

  // Swipe 2: Personal -> Work Check (attaches active ticker NTRA)
  await executeSwipeAction(state, 1, deps);
  assert.equal(state.pendingWrite?.mind, 'work');
  assert.equal(state.pendingWrite?.ticker, 'NTRA');
  assert.deepEqual(calls, [
    { mind: 'personal', kind: 'task', ticker: null },
    { mind: 'work', kind: 'check', ticker: 'NTRA' },
  ]);
});

test('SAVED receipt stays until tap (returns to deck) or swipe (undoes entry)', async () => {
  const ntra = {
    ticker: 'NTRA',
    company: 'Natera, Inc.',
    saved_question: 'Is Natera sustaining 63%+ ex-true-up gross margin as Signatera scales?',
    verdict: 'HELD' as const,
    latest_finding: 'Q3 GAAP GM 64.7%; ex-true-up non-GAAP GM 63.2% (+140 bps QoQ).',
    source_citation: 'Q3 2026 PR',
    suggested_next_check: '',
    next_check: '',
    notion_page_id: null,
    updated_at: '2026-11-25T00:00:00Z',
  };

  // Case A: Tap on SAVED cleanly dismisses back to BRIEFING deck
  const stateTap = makeState({
    mode: 'SAVED',
    holdings: [ntra],
    lastSavedEntry: {
      id: 7,
      mind: 'interests',
      kind: 'note',
      content: 'Great episode',
      ticker: null,
      source_citation: null,
      notion_page_id: null,
      created_at: '2026-11-25T10:00:00Z',
    },
  });
  const tapCards: HudCard[] = [];
  await executeTapAction(stateTap, {
    isRecording: () => false,
    getActiveHolding: (s) => s.holdings[0] ?? null,
    confirmWrite: async () => {
      throw new Error('should not re-confirm');
    },
    stageCheck: async () => {
      throw new Error('should not stage');
    },
    stageSuggestedCheck: async () => {
      throw new Error('should not stage');
    },
    refreshDeckAfterSave: async () => {},
    showCard: async (c) => {
      tapCards.push(c);
    },
  });
  assert.equal(stateTap.mode, 'BRIEFING');
  assert.equal(stateTap.lastSavedEntry, null);
  assert.match(tapCards.at(-1)?.header ?? '', /NTRA/);

  // Case B: Swipe on SAVED triggers Undo
  const stateUndo = makeState({
    mode: 'SAVED',
    holdings: [ntra],
    lastSavedEntry: {
      id: 8,
      mind: 'personal',
      kind: 'task',
      content: 'Wrongly saved task',
      ticker: null,
      source_citation: null,
      notion_page_id: 'notion-8',
      created_at: '2026-11-25T10:01:00Z',
    },
  });
  const undoneIds: number[] = [];
  const undoCards: HudCard[] = [];
  await executeSwipeAction(stateUndo, 1, {
    isRecording: () => false,
    getActiveHolding: (s) => s.holdings[0] ?? null,
    reclassifyPendingWrite: async () => {
      throw new Error('should not reclassify');
    },
    undoJournalEntry: async (id) => {
      undoneIds.push(id);
      return {
        undone_id: id,
        archived_in_notion: true,
        holding: null,
        hud: {
          header: 'PERSONAL · UNDONE',
          body: 'Undid: Wrongly saved task',
          footer: 'Tap: deck · Hold: speak again',
        },
      };
    },
    refreshDeckAfterChange: async () => {},
    showCard: async (c) => {
      undoCards.push(c);
    },
  });
  assert.deepEqual(undoneIds, [8]);
  assert.equal(stateUndo.lastSavedEntry, null);
  assert.equal(stateUndo.mode, 'SAVED');
  assert.equal(undoCards.at(-1)?.header, 'PERSONAL · UNDONE');
});

test('PcmAudioRecorder cancels cleanly if stopped while bridge audioControl is pending', async () => {
  let resolveOpen!: (ok: boolean) => void;
  const openCalls: boolean[] = [];
  const fakeBridge = {
    audioControl: (isOpen: boolean) => {
      openCalls.push(isOpen);
      if (isOpen) {
        return new Promise<boolean>((resolve) => {
          resolveOpen = resolve;
        });
      }
      return Promise.resolve(true);
    },
  } as unknown as import('@evenrealities/even_hub_sdk').EvenAppBridge;

  const pcm = new PcmAudioRecorder();
  const startPromise = pcm.start(fakeBridge);
  assert.equal(pcm.isStarting, true);
  assert.equal(pcm.isActive, true);

  const blob = await pcm.stop(fakeBridge);
  assert.equal(blob, null);
  assert.equal(pcm.isActive, false);

  resolveOpen(true);
  const started = await startPromise;
  assert.equal(started, false);
  assert.equal(pcm.isRecording, false);
  assert.equal(openCalls.includes(false), true);
});

test('sanitizeLine replaces unsupported LVGL firmware glyphs cleanly', () => {
  const out = sanitizeLine('✓ PEER · AMZN → STX ● …', 58);
  assert.equal(out.includes('\u2713'), false);
  assert.equal(out.includes('\u2192'), false);
  assert.equal(out.includes('\u25CF'), false);
  assert.equal(out, 'OK: PEER | AMZN -> STX [REC] ...');
});

test('releasing mic while showCard is awaiting cancels capture before audioControl opens', async () => {
  let releaseShowCard!: () => void;
  let cardCalls = 0;
  const renderedCards: HudCard[] = [];
  const audioControlCalls: boolean[] = [];

  const fakeBridge = {
    audioControl: async (isOpen: boolean) => {
      audioControlCalls.push(isOpen);
      return true;
    },
  } as unknown as import('@evenrealities/even_hub_sdk').EvenAppBridge;

  const recorder = new PcmAudioRecorder();
  const capture: BridgeCaptureState = { activeMicKind: null };
  const state = makeState({
    mode: 'BRIEFING',
    holdings: [
      {
        ticker: 'NTRA',
        company: 'Natera, Inc.',
        saved_question: 'Is Natera sustaining 63%+ ex-true-up gross margin as Signatera scales?',
        verdict: 'HELD',
        latest_finding: 'Q3 GAAP GM 64.7%; ex-true-up non-GAAP GM 63.2% (+140 bps QoQ).',
        source_citation: 'Q3 2026 PR',
        suggested_next_check: 'Check ex-true-up GM',
        next_check: '',
        notion_page_id: null,
        updated_at: '2026-11-25T00:00:00Z',
      },
    ],
  });

  let labelActive = false;
  let audioReadyCalls = 0;
  const showCard = async (card: HudCard): Promise<void> => {
    renderedCards.push(card);
    cardCalls += 1;
    if (cardCalls === 1) {
      await new Promise<void>((resolve) => {
        releaseShowCard = resolve;
      });
    }
  };

  // Press mic button -> blocks inside first showCard() BEFORE bridge.audioControl(true)
  const startPromise = startBridgeCapture(state, capture, {
    recorder,
    bridge: fakeBridge,
    isOtherMicActive: () => false,
    setMicLabel: (recording) => {
      labelActive = recording;
    },
    showCard,
    onAudioReady: async () => {
      audioReadyCalls += 1;
    },
  });

  // Immediately active before showCard resolves
  assert.equal(recorder.isActive, true);
  assert.equal(recorder.isStarting, true);
  assert.equal(capture.activeMicKind, 'bridge');
  assert.equal(labelActive, true);

  // Release mic button WHILE showCard() is still awaiting bridge updates
  await stopBridgeCapture(state, capture, {
    recorder,
    bridge: fakeBridge,
    setMicLabel: (recording) => {
      labelActive = recording;
    },
    showCard,
    onAudioReady: async () => {
      audioReadyCalls += 1;
    },
  });

  // Now let the first showCard() resolve
  releaseShowCard();
  const outcome = await startPromise;

  assert.equal(outcome, 'cancelled');
  assert.deepEqual(audioControlCalls, []); // audioControl(true) was NEVER opened!
  assert.equal(recorder.isActive, false);
  assert.equal(recorder.isRecording, false);
  assert.equal(capture.activeMicKind, null);
  assert.equal(labelActive, false);
  assert.equal(audioReadyCalls, 0);
  assert.equal(state.mode, 'BRIEFING');
  assert.match(renderedCards.at(-1)?.header ?? '', /NTRA/);
});

test('Pull Notion while CONFIRM card is open clears and cancels pending write before deck taps', async () => {
  const ntra = {
    ticker: 'NTRA',
    company: 'Natera, Inc.',
    saved_question: "Is Natera oncology volume & FY26 revenue guide running ahead of expectations?",
    verdict: 'AHEAD' as const,
    latest_finding: 'Q3 rev $794.0M (+34.1% YoY); FY26 guide raised $140M to $3.005B-$3.035B.',
    source_citation: 'Q3 2026 PR',
    suggested_next_check: 'Track Q4 Signatera oncology volume growth',
    next_check: '',
    notion_page_id: 'page-ntra',
    updated_at: '2026-11-25T12:00:00Z',
  };

  // Case 1: Pull Notion with recomputed banner -> dismiss -> tap deck must NOT confirm old pending write
  const state = makeState({
    mode: 'CONFIRM_WRITE',
    holdings: [ntra],
    pendingWrite: {
      id: 'pending-stale-1',
      mind: 'interests',
      kind: 'note',
      content: 'Old unconfirmed note',
      ticker: null,
      source_citation: null,
      updates_next_check: false,
    },
  });

  const cancelledIds: string[] = [];
  const rendered: HudCard[] = [];
  await executePullNotionAction(state, {
    isRecording: () => false,
    cancelWrite: async (id) => {
      cancelledIds.push(id);
    },
    pullNotion: async () => ({ pulled_count: 3, recomputed: ['NTRA'] }),
    refreshDeckAfterPull: async () => {},
    showCard: async (card) => {
      rendered.push(card);
    },
  });

  assert.deepEqual(cancelledIds, ['pending-stale-1']);
  assert.equal(state.pendingWrite, null);
  assert.equal(state.mode, 'SAVED');
  assert.equal(rendered.at(-1)?.header, 'NOTION · UPDATED');

  // Tap 1 dismisses NOTION · UPDATED banner back to BRIEFING deck
  let confirmCalls = 0;
  let stageSuggestedCalls = 0;
  const tapDeps: TapActionDeps = {
    isRecording: () => false,
    getActiveHolding: (s) => s.holdings[0] ?? null,
    confirmWrite: async () => {
      confirmCalls += 1;
      throw new Error('stale pending write must never be confirmed');
    },
    stageCheck: async () => {
      throw new Error('unexpected');
    },
    stageSuggestedCheck: async (ticker) => {
      stageSuggestedCalls += 1;
      return {
        pending_write: {
          id: 'new-check-1',
          mind: 'work',
          kind: 'check',
          content: ntra.suggested_next_check,
          ticker,
          source_citation: ntra.source_citation,
          updates_next_check: true,
        },
        hud: {
          header: 'WORK · NTRA · CONFIRM CHECK',
          body: ntra.suggested_next_check,
          footer: 'Tap: save · Swipe: switch mind · 2x: cancel',
        },
      };
    },
    refreshDeckAfterSave: async () => {},
    showCard: async () => {},
  };

  await executeTapAction(state, tapDeps);
  assert.equal(state.mode, 'BRIEFING');
  assert.equal(confirmCalls, 0);

  // Tap 2 on BRIEFING deck previews NTRA's check instead of saving old unconfirmed note
  await executeTapAction(state, tapDeps);
  assert.equal(confirmCalls, 0);
  assert.equal(stageSuggestedCalls, 1);
  assert.equal(state.mode, 'CONFIRM_WRITE');
  assert.equal(state.pendingWrite?.id, 'new-check-1');

  // Case 2: Pull Notion with no recomputed changes directly returns to BRIEFING with pending cleared
  await executePullNotionAction(state, {
    isRecording: () => false,
    cancelWrite: async (id) => {
      cancelledIds.push(id);
    },
    pullNotion: async () => ({ pulled_count: 3, recomputed: [] }),
    refreshDeckAfterPull: async () => {},
    showCard: async () => {},
  });
  assert.deepEqual(cancelledIds, ['pending-stale-1', 'new-check-1']);
  assert.equal(state.pendingWrite, null);
  assert.equal(state.mode, 'BRIEFING');
});

test('Cockpit boots into calm HOME (not Natera) and returns to HOME after Personal/Interests saves', async () => {
  const ntra = {
    ticker: 'NTRA',
    company: 'Natera, Inc.',
    saved_question: 'Is Natera sustaining 63%+ ex-true-up gross margin as Signatera scales?',
    verdict: 'HELD' as const,
    latest_finding: 'Q3 GAAP GM 64.7%; ex-true-up non-GAAP GM 63.2% (+140 bps QoQ).',
    source_citation: 'Q3 2026 PR',
    suggested_next_check: 'Check ex-true-up GM',
    next_check: '',
    notion_page_id: null,
    updated_at: '2026-11-25T12:00:00Z',
  };

  // Fresh boot state starts in HOME with no leaked activeHolding
  const state = createInitialState();
  state.holdings = [ntra];
  assert.equal(state.mode, 'HOME');
  assert.equal(state.returnMode, 'HOME');
  assert.equal(activeHolding(state), null);
  assert.equal(homeCard(state).header, 'COCKPIT · READY');
  assert.equal(homeCard(state).body.includes('ex-true-up gross margin'), false);

  // Confirm an Interests note from HOME -> tap done returns to HOME (NOT NTRA!)
  state.mode = 'CONFIRM_WRITE';
  state.pendingWrite = {
    id: 'note-home-1',
    mind: 'interests',
    kind: 'note',
    content: 'I enjoyed the latest Acquired podcast',
    ticker: null,
    source_citation: null,
    updates_next_check: false,
  };

  const rendered: HudCard[] = [];
  const tapDeps: TapActionDeps = {
    isRecording: () => false,
    getActiveHolding: activeHolding,
    confirmWrite: async () => ({
      entry: {
        id: 55,
        mind: 'interests',
        kind: 'note',
        content: 'I enjoyed the latest Acquired podcast',
        ticker: null,
        source_citation: null,
        notion_page_id: null,
        created_at: '2026-11-25T12:30:00Z',
      },
      holding: null,
      synced_to_notion: false,
      hud: {
        header: 'INTERESTS · SAVED',
        body: 'OK: I enjoyed the latest Acquired podcast',
        footer: 'Tap: done · Swipe: undo · Hold: speak',
      },
    }),
    stageCheck: async () => {
      throw new Error('unexpected');
    },
    stageSuggestedCheck: async () => {
      throw new Error('unexpected');
    },
    refreshDeckAfterSave: async () => {
      state.recentJournal = [
        {
          id: 55,
          mind: 'interests',
          kind: 'note',
          content: 'I enjoyed the latest Acquired podcast',
          ticker: null,
          source_citation: null,
          notion_page_id: null,
          created_at: '2026-11-25T12:30:00Z',
        },
      ];
    },
    showCard: async (c) => {
      rendered.push(c);
    },
  };

  // Tap 1: confirm save
  await executeTapAction(state, tapDeps);
  assert.equal(state.mode, 'SAVED');
  assert.equal(state.returnMode, 'HOME');

  // Tap 2: dismiss SAVED -> lands back on calm HOME showing Last saved, NOT Natera
  await executeTapAction(state, tapDeps);
  assert.equal(state.mode, 'HOME');
  assert.equal(activeHolding(state), null);
  assert.equal(rendered.at(-1)?.header, 'COCKPIT · READY');
  assert.match(rendered.at(-1)?.body ?? '', /Acquired podcast/);

  // Explicit Swipe from HOME opens the watchlist at NTRA 1/1
  await executeSwipeAction(state, 1, {
    isRecording: () => false,
    getActiveHolding: activeHolding,
    reclassifyPendingWrite: async () => {
      throw new Error('unexpected');
    },
    undoJournalEntry: async () => {
      throw new Error('unexpected');
    },
    refreshDeckAfterChange: async () => {},
    showCard: async (c) => {
      rendered.push(c);
    },
  });
  assert.equal(state.mode, 'BRIEFING');
  assert.equal(activeHolding(state)?.ticker, 'NTRA');
  assert.match(rendered.at(-1)?.header ?? '', /WORK · NTRA/);
});
