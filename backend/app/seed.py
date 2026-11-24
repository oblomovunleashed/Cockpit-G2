from __future__ import annotations

from app.db import CockpitDB, utc_now_iso
from app.models import HoldingRecord, Verdict


NVDA_Q2_CALL_URL = (
    "https://investor.nvidia.com/files/content_files/"
    "TRANSCRIPT_-NVIDIA-Corp-NVDA-US-Q2-2027-Earnings-Call-26-August-2026-5_00-PM-ET.pdf"
)
NVDA_Q3_PR_URL = (
    "https://investor.nvidia.com/news/press-release-details/2026/"
    "NVIDIA-Announces-Financial-Results-for-Third-Quarter-Fiscal-2027/default.aspx"
)
TSM_Q3_PR_URL = "https://investor.tsmc.com/english/quarterly-results/2026/q3"
MSFT_Q1_PR_URL = "https://www.microsoft.com/en-us/Investor/earnings/FY-2027-Q1/press-release-webcast"


# Verified prior-quarter guidance baselines for deterministic (zero-LLM) earnings_preview skill
PRIOR_BASELINES: dict[str, str] = {
    "NVDA": "Aug call baseline: Q3 rev guide $108bn; Q4 GM trough 71-72%; Q1 FY28 price hikes.",
    "TSM": "Q3 2026 baseline: Q4 rev guide $52.3-54.3bn; Q4 GM 65.0-67.0%; FY26 capex $60-64bn.",
    "MSFT": "Q1 FY27 baseline: Q2 Azure guide 49-50% CC; Q2 capex >$50bn (CY26 ~$170bn).",
}


SEED_HOLDINGS: list[HoldingRecord] = [
    HoldingRecord(
        ticker="NVDA",
        company="NVIDIA Corp.",
        saved_question="Does NVIDIA expect the margin trough to get deeper?",
        verdict=Verdict.HELD,
        latest_finding=(
            "Q4 FY27 gross margin guide · 71-72%. "
            "Management had already flagged that range in August."
        ),
        source_citation="Q3 FY27 PR (Nov 17) & Q2 Call (Aug 26)",
        suggested_next_check="Check whether Q1 FY28 price increases are on schedule",
        next_check="",
    ),
    HoldingRecord(
        ticker="TSM",
        company="TSMC",
        saved_question="Is the N2 ramp diluting gross margin below guidance?",
        verdict=Verdict.HELD,
        latest_finding=(
            "Q3 2026 gross margin · 66.4% (vs 67.7% Q2) on N2 ramp (8% of wafers). "
            "Q4 GM guided 65.0-67.0%."
        ),
        source_citation="TSMC Q3 2026 PR · Oct 15",
        suggested_next_check="Check N2 margin dilution as N2 scales past 8% of wafer revenue",
        next_check="",
    ),
    HoldingRecord(
        ticker="MSFT",
        company="Microsoft Corp.",
        saved_question="Is Azure AI capacity growth accelerating despite delivery delays?",
        verdict=Verdict.AHEAD,
        latest_finding=(
            "Q1 FY27 Azure grew 48% YoY (vs 44-45% guide); Q2 guided 49-50% CC "
            "as AI demand outstrips supply."
        ),
        source_citation="MSFT Q1 FY27 PR · Oct 28",
        suggested_next_check="Check whether Q2 capex exceeds $50bn as delayed racks arrive",
        next_check="",
    ),
]


SEED_CHUNKS: list[dict[str, str]] = [
    # NVIDIA — Q2 FY27 Call (Aug 26, 2026) & Q3 FY27 Release (Nov 17, 2026)
    {
        "ticker": "NVDA",
        "doc_title": "NVIDIA Q3 FY27 Earnings Release",
        "doc_date": "2026-11-17",
        "page_ref": "Q3 FY27 PR · Nov 17",
        "topic": "gross margin trough guidance outlook Q4 FY27",
        "headline": "Q4 FY27 gross margin guide: 71-72%",
        "summary_card": (
            "Q4 FY27 gross margin guide · 71-72%. "
            "Management had already flagged that range in August."
        ),
        "verdict": Verdict.HELD.value,
        "suggested_next_check": "Check whether Q1 FY28 price increases are on schedule",
        "excerpt": (
            "Q4 FY27 GAAP and non-GAAP gross margins are expected to be 71% to 72%, "
            "consistent with the margin trough range management previously communicated "
            "on the August 26 Q2 FY27 earnings call."
        ),
        "source_url": NVDA_Q3_PR_URL,
    },
    {
        "ticker": "NVDA",
        "doc_title": "NVIDIA Q3 FY27 Earnings Release",
        "doc_date": "2026-11-17",
        "page_ref": "Q3 FY27 PR · Nov 17",
        "topic": "revenue beat guidance midpoint growth Q3 FY27 top line sales",
        "headline": "Q3 FY27 revenue: $115.8bn (+7% vs guide)",
        "summary_card": (
            "Q3 FY27 revenue · $115.8bn. "
            "About 7% above the company's $108bn guidance midpoint."
        ),
        "verdict": Verdict.AHEAD.value,
        "suggested_next_check": "Check whether Q4 data center shipment mix sustains the beat",
        "excerpt": (
            "NVIDIA reported Q3 FY27 revenue of $115.8 billion, roughly 7% ahead of "
            "management's $108 billion guidance midpoint provided in August."
        ),
        "source_url": NVDA_Q3_PR_URL,
    },
    {
        "ticker": "NVDA",
        "doc_title": "NVIDIA Q2 FY27 Earnings Call Transcript",
        "doc_date": "2026-08-26",
        "page_ref": "Aug FY27 Call · p. 7",
        "topic": "margin recovery bring margins back up price increases Q1 FY28",
        "headline": "Margin recovery driver: Q1 FY28 price increases",
        "summary_card": (
            "Aug call p. 7: management expects gross margins to recover as "
            "planned price increases take effect in Q1 FY28."
        ),
        "verdict": Verdict.WATCH.value,
        "suggested_next_check": "At next call, check whether Q1 FY28 price increases are on schedule",
        "excerpt": (
            "On page 7 of the August 26 Q2 FY27 earnings call transcript, management "
            "noted that gross margin recovery back above the trough is expected to be "
            "driven by price increases scheduled to take effect in Q1 FY28."
        ),
        "source_url": f"{NVDA_Q2_CALL_URL}#page=7",
    },
    # TSMC — Q3 2026 Earnings Release & Call (Oct 15, 2026)
    {
        "ticker": "TSM",
        "doc_title": "TSMC Q3 2026 Earnings Release",
        "doc_date": "2026-10-15",
        "page_ref": "TSMC Q3 2026 PR · Oct 15",
        "topic": "N2 2nm ramp gross margin dilution guidance Q4 wafer",
        "headline": "Q3 gross margin 66.4%; Q4 guided 65.0-67.0%",
        "summary_card": (
            "Q3 2026 gross margin · 66.4% (vs 67.7% Q2) on N2 ramp (8% of wafers). "
            "Q4 GM guided 65.0-67.0%."
        ),
        "verdict": Verdict.HELD.value,
        "suggested_next_check": "Check N2 margin dilution as N2 scales past 8% of wafer revenue",
        "excerpt": (
            "TSMC reported Q3 2026 gross margin of 66.4%, down modestly from 67.7% in Q2 "
            "due to initial high-volume N2 (2nm) ramp dilution as N2 reached 8% of wafer revenue. "
            "Management guided Q4 2026 gross margin to 65.0%-67.0%."
        ),
        "source_url": TSM_Q3_PR_URL,
    },
    {
        "ticker": "TSM",
        "doc_title": "TSMC Q3 2026 Earnings Release & Call",
        "doc_date": "2026-10-15",
        "page_ref": "TSMC Q3 2026 Call · Oct 15",
        "topic": "AI HPC CoWoS packaging capacity demand supply revenue guidance capex accelerators",
        "headline": "Q3 rev $46.72bn (+41% YoY); Q4 guide $52.3-54.3bn; CoWoS maxed",
        "summary_card": (
            "Q3 rev $46.72bn (+41% YoY); Q4 guided $52.3-54.3bn. "
            "AI/CoWoS demand still far outpaces supply ($60-64bn FY26 capex)."
        ),
        "verdict": Verdict.AHEAD.value,
        "suggested_next_check": "Track CoWoS packaging bottlenecks and N3/N2 wafer pricing into 2027",
        "excerpt": (
            "TSMC posted Q3 2026 revenue of $46.72 billion (+41.2% YoY), with HPC/AI accelerators "
            "at 68% of net revenue, and guided Q4 revenue to $52.3-$54.3 billion (~58% YoY). "
            "Chairman C.C. Wei reiterated AI demand dramatically outpaces CoWoS advanced packaging "
            "and leading-edge wafer supply, backed by $60-$64 billion in FY26 capex."
        ),
        "source_url": TSM_Q3_PR_URL,
    },
    # Microsoft — Q1 FY27 Earnings Release & Call (Oct 28, 2026)
    {
        "ticker": "MSFT",
        "doc_title": "Microsoft Q1 FY27 Earnings Release",
        "doc_date": "2026-10-28",
        "page_ref": "MSFT Q1 FY27 PR · Oct 28",
        "topic": "Azure cloud AI capacity growth acceleration guidance constant currency",
        "headline": "Q1 FY27 Azure +48% YoY; Q2 guided 49-50% CC",
        "summary_card": (
            "Q1 FY27 Azure grew 48% YoY (vs 44-45% guide); Q2 guided 49-50% CC "
            "as AI demand outstrips supply."
        ),
        "verdict": Verdict.AHEAD.value,
        "suggested_next_check": "Check whether Azure sustains 49-50% growth as new racks come online",
        "excerpt": (
            "Microsoft Q1 FY27 revenue reached $92.3 billion (+19% YoY). Azure and cloud services "
            "grew 48% YoY to $33.17 billion, beating prior 44%-45% guidance, with Q2 FY27 Azure "
            "growth guided to accelerate further to 49%-50% in constant currency as AI demand exceeds supply."
        ),
        "source_url": MSFT_Q1_PR_URL,
    },
    {
        "ticker": "MSFT",
        "doc_title": "Microsoft Q1 FY27 Earnings Call",
        "doc_date": "2026-10-28",
        "page_ref": "MSFT Q1 FY27 Call · Oct 28",
        "topic": "capex capital expenditure GPU GB300 racks delivery delays infrastructure spending",
        "headline": "Q1 capex $45.2bn on delivery slips; Q2 guided >$50bn",
        "summary_card": (
            "Q1 FY27 capex $45.2bn (slipped deliveries, not demand); "
            "Q2 guided >$50bn with faster GB300 rack dock-to-live."
        ),
        "verdict": Verdict.WATCH.value,
        "suggested_next_check": "Check whether Q2 capex exceeds $50bn and GB300 rack deliveries catch up",
        "excerpt": (
            "Q1 FY27 capital expenditures totaled $45.2 billion, below ~$50.6 billion consensus due to "
            "slipped third-party capacity deliveries rather than demand softness. CFO Amy Hood guided "
            "Q2 capex above $50 billion (targeting ~$170 billion in CY26) and highlighted faster "
            "dock-to-live deployment for GB300 GPU racks alongside Maia/Cobalt silicon."
        ),
        "source_url": MSFT_Q1_PR_URL,
    },
]


def seed_if_empty(db: CockpitDB) -> None:
    if not db.list_holdings():
        now = utc_now_iso()
        for holding in SEED_HOLDINGS:
            db.upsert_holding(holding.model_copy(update={"updated_at": now}))
    if db.count_chunks() == 0:
        db.insert_chunks(SEED_CHUNKS)
