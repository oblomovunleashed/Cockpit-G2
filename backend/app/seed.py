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
        ticker="MSFT",
        company="Microsoft Corp. (placeholder slot — swap real filings)",
        saved_question="Is Azure AI capacity growth re-accelerating in the second half?",
        verdict=Verdict.WATCH,
        latest_finding=(
            "Seed placeholder ready for real MSFT issuer release/transcript ingestion."
        ),
        source_citation="Replace with real filing excerpt",
        suggested_next_check="Check Azure capacity ramp cadence next quarter",
        next_check="",
    ),
    HoldingRecord(
        ticker="TSM",
        company="TSMC (placeholder slot — swap real filings)",
        saved_question="Is N2 wafer demand supporting full-year gross margin guidance?",
        verdict=Verdict.AHEAD,
        latest_finding=(
            "Seed placeholder ready for real TSMC issuer release/transcript ingestion."
        ),
        source_citation="Replace with real filing excerpt",
        suggested_next_check="Check N2 utilization and overseas fab dilution next call",
        next_check="",
    ),
]


SEED_CHUNKS: list[dict[str, str]] = [
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
    {
        "ticker": "MSFT",
        "doc_title": "Placeholder Issuer Filing Slot (MSFT)",
        "doc_date": "2026-11-01",
        "page_ref": "Placeholder · Replace with real filing",
        "topic": "azure cloud ai capacity capex growth second half",
        "headline": "MSFT placeholder finding",
        "summary_card": (
            "Placeholder MSFT card: swap in real primary filing excerpts during build week."
        ),
        "verdict": Verdict.WATCH.value,
        "suggested_next_check": "Verify Azure AI capacity online dates in next release",
        "excerpt": (
            "Placeholder passage for Holding 2 (MSFT). Replace with verified real issuer "
            "release and transcript chunks before recording the demo."
        ),
        "source_url": "",
    },
    {
        "ticker": "TSM",
        "doc_title": "Placeholder Issuer Filing Slot (TSM)",
        "doc_date": "2026-11-01",
        "page_ref": "Placeholder · Replace with real filing",
        "topic": "n2 wafer demand gross margin overseas fab dilution",
        "headline": "TSM placeholder finding",
        "summary_card": (
            "Placeholder TSM card: swap in real primary filing excerpts during build week."
        ),
        "verdict": Verdict.AHEAD.value,
        "suggested_next_check": "Verify N2 ramp margin impact on next earnings call",
        "excerpt": (
            "Placeholder passage for Holding 3 (TSM). Replace with verified real issuer "
            "release and transcript chunks before recording the demo."
        ),
        "source_url": "",
    },
]


def seed_if_empty(db: CockpitDB) -> None:
    if not db.list_holdings():
        now = utc_now_iso()
        for holding in SEED_HOLDINGS:
            db.upsert_holding(holding.model_copy(update={"updated_at": now}))
    if db.count_chunks() == 0:
        db.insert_chunks(SEED_CHUNKS)
