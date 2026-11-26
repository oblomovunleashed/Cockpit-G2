from __future__ import annotations

from app.db import CockpitDB, utc_now_iso
from app.models import HoldingRecord, Verdict


NTRA_Q3_PR_URL = "https://investor.natera.com/news-releases/news-release-details/natera-reports-third-quarter-2026-financial-results"
STX_Q1_PR_URL = "https://investors.seagate.com/news/news-details/2026/Seagate-Technology-Reports-Fiscal-First-Quarter-2027-Financial-Results/default.aspx"
AMZN_Q3_PR_URL = "https://ir.aboutamazon.com/news-release/news-release-details/2026/Amazon-com-Announces-Third-Quarter-Results/default.aspx"


# Verified prior-quarter guidance baselines for deterministic (zero-LLM) earnings_preview skill
PRIOR_BASELINES: dict[str, str] = {
    "NTRA": "29.2% wt | Prior guide: FY26 rev $2.85-$2.91B; GM 64-66%; Q2 Signatera ASP ~$1,270.",
    "STX": "9.1% wt | Q1 FY27 baseline: rev $4.23B, non-GAAP GM 57.8%; Q2 guide $4.8B rev, $9.20 EPS.",
    "AMZN": "6.9% wt | Q3 2026 baseline: AWS $46.0B (+39.3% YoY); Q4 rev $240-$247B, op inc $28.5-$33.5B.",
}


SEED_HOLDINGS: list[HoldingRecord] = [
    HoldingRecord(
        ticker="NTRA",
        company="Natera, Inc. (29.2%)",
        saved_question="Is Natera's underlying gross margin expanding without revenue true-ups?",
        verdict=Verdict.AHEAD,
        latest_finding=(
            "Q3 2026 GAAP GM 64.7%; ex-true-ups 63.2% (+140 bps QoQ on COGS gains). "
            "True-ups fell to $32.3M vs $52.3M in Q2."
        ),
        source_citation="NTRA Q3 2026 PR & Call · Nov 5",
        suggested_next_check="Check whether Signatera ASP holds above $1,290 and MolDX coverage expands in Q4",
        next_check="",
    ),
    HoldingRecord(
        ticker="STX",
        company="Seagate Technology Holdings plc (9.1%)",
        saved_question="Is Seagate's HAMR Mozaic ramp sustaining gross margin expansion?",
        verdict=Verdict.AHEAD,
        latest_finding=(
            "Q1 FY27 rev $4.23B (+61% YoY), non-GAAP GM 57.8% (vs 40.1% YoY). "
            "Azure qualified Mozaic HAMR; Q2 rev guided $4.8B."
        ),
        source_citation="STX Q1 FY27 PR · Oct 27",
        suggested_next_check="Check Q2 Mozaic 44TB mix and whether non-GAAP GM expands further above 57.8%",
        next_check="",
    ),
    HoldingRecord(
        ticker="AMZN",
        company="Amazon.com, Inc. (6.9%)",
        saved_question="Is AWS growth accelerating enough to justify ~$220B FY26 companywide capex?",
        verdict=Verdict.AHEAD,
        latest_finding=(
            "Q3 AWS rev $46.0B (+39.3% YoY, $184B run rate), op margin 38.7%; "
            "record $28.1B op inc. Companywide FY26 cash capex reiterated ~$220B."
        ),
        source_citation="AMZN Q3 2026 PR · Oct 29",
        suggested_next_check="Check whether AWS sustains >35% YoY growth as ~$220B FY26 companywide cash capex ramps",
        next_check="",
    ),
]


SEED_CHUNKS: list[dict[str, str]] = [
    # Natera (NTRA · 29.22% weight) — Q3 2026 Release & Call (Nov 5, 2026)
    {
        "ticker": "NTRA",
        "doc_title": "Natera Q3 2026 Earnings Release & Call",
        "doc_date": "2026-11-05",
        "page_ref": "NTRA Q3 2026 PR · Nov 5",
        "topic": "gross margin underlying true-ups COGS efficiency Q3 2026 profitability",
        "headline": "Q3 GAAP GM 64.7%; ex-true-ups 63.2% (+140 bps QoQ)",
        "summary_card": (
            "Q3 2026 GAAP GM 64.7%; ex-true-ups 63.2% (+140 bps QoQ on COGS gains). "
            "True-ups fell to $32.3M vs $52.3M in Q2."
        ),
        "verdict": Verdict.AHEAD.value,
        "suggested_next_check": "Check whether ex-true-up gross margin keeps expanding above 63.2% in Q4",
        "excerpt": (
            "Natera reported Q3 2026 GAAP gross margin of 64.7% and underlying non-GAAP gross margin "
            "excluding revenue true-ups of 63.2%, improving roughly 140 basis points sequentially from Q2 2026 "
            "on COGS efficiencies as revenue true-ups declined to $32.3 million from $52.3 million in Q2. "
            "Full-year 2026 gross margin guidance remained 64% to 66%."
        ),
        "source_url": NTRA_Q3_PR_URL,
    },
    {
        "ticker": "NTRA",
        "doc_title": "Natera Q3 2026 Earnings Release",
        "doc_date": "2026-11-05",
        "page_ref": "NTRA Q3 2026 PR · Nov 5",
        "topic": "revenue growth Signatera oncology test volume full year guidance raise beat sales",
        "headline": "Q3 rev $794M (+34% YoY); Signatera oncology +61% YoY; FY26 guide raised $140M",
        "summary_card": (
            "Q3 rev $794.0M (+34% YoY); oncology tests 339.5k (+61% YoY). "
            "FY26 guide raised $140M at mid to $3.005-$3.035B."
        ),
        "verdict": Verdict.AHEAD.value,
        "suggested_next_check": "Check whether Signatera clinical MRD test volumes maintain >50% YoY growth in Q4",
        "excerpt": (
            "Natera generated Q3 2026 total revenue of $794.0 million (+34.1% YoY vs $592.2 million), "
            "processing ~1,093,100 total tests (+22.3% YoY) including ~339,500 oncology tests (+60.9% YoY), "
            "led by Signatera clinical MRD volume (~316,000 clinical MRD units including Signatera and Latitude MRD, +56% YoY). "
            "Management raised full-year 2026 revenue guidance by $140 million at the midpoint from "
            "$2.850-$2.910 billion to $3.005-$3.035 billion."
        ),
        "source_url": NTRA_Q3_PR_URL,
    },
    {
        "ticker": "NTRA",
        "doc_title": "Natera Q3 2026 Earnings Call Transcript",
        "doc_date": "2026-11-05",
        "page_ref": "NTRA Q3 2026 Call · Nov 5",
        "topic": "Signatera ASP average selling price reimbursement MolDX cash flow FCF balance sheet",
        "headline": "Signatera ASP rose to ~$1,290; cash up $5.7M to $1.10B (FCF positive)",
        "summary_card": (
            "Q3 call: Signatera ASP rose to ~$1,290 (vs $1,270 Q2) on MolDX/coverage wins; "
            "cash grew $5.7M to $1.10B, FCF-positive."
        ),
        "verdict": Verdict.WATCH.value,
        "suggested_next_check": "Check whether Signatera ASP holds above $1,290 and MolDX coverage expands in Q4",
        "excerpt": (
            "On the November 5 Q3 2026 earnings call, management reported Signatera ASP rose to ~$1,290 "
            "(up from ~$1,270 in Q2 2026) supported by expanding reimbursement, early-stage liver cancer "
            "surveillance, and MolDX milestones. Natera was cash-flow positive, lifting total cash to "
            "$1,097.2 million (+$5.7 million QoQ) while narrowing GAAP net loss to $59.9 million (-$0.42/sh)."
        ),
        "source_url": NTRA_Q3_PR_URL,
    },
    # Seagate (STX · 9.07% weight) — Q1 FY27 Release & Call (Oct 27, 2026)
    {
        "ticker": "STX",
        "doc_title": "Seagate Q1 FY27 Earnings Release",
        "doc_date": "2026-10-27",
        "page_ref": "STX Q1 FY27 PR · Oct 27",
        "topic": "revenue gross margin EPS guidance Q2 FY27 pricing profitability",
        "headline": "Q1 FY27 rev $4.23B (+61% YoY); non-GAAP GM 57.8%; Q2 guide $4.8B",
        "summary_card": (
            "Q1 FY27 rev $4.23B (+61% YoY), non-GAAP GM 57.8% (vs 40.1% YoY). "
            "Q2 FY27 guided $4.8B rev and $9.20 EPS."
        ),
        "verdict": Verdict.AHEAD.value,
        "suggested_next_check": "Check whether Q2 FY27 revenue hits $4.8B and non-GAAP GM expands above 57.8%",
        "excerpt": (
            "Seagate reported Q1 FY27 revenue of $4.23 billion (+61% YoY vs $2.63 billion), GAAP gross "
            "margin of 57.3% (up from 39.4% YoY), non-GAAP gross margin of 57.8% (up from 40.1% YoY), "
            "and non-GAAP EPS of $7.68. "
            "Management guided Q2 FY27 revenue to $4.8 billion (+/- $0.1B) and non-GAAP EPS to $9.20 (+/- $0.20)."
        ),
        "source_url": STX_Q1_PR_URL,
    },
    {
        "ticker": "STX",
        "doc_title": "Seagate Q1 FY27 Earnings Call",
        "doc_date": "2026-10-27",
        "page_ref": "STX Q1 FY27 Call · Oct 27",
        "topic": "HAMR Mozaic 44TB Azure qualification nearline exabytes data center supply allocation 2028",
        "headline": "Azure qualified Mozaic HAMR; 198 EB nearline shipped; supply allocated through 2028",
        "summary_card": (
            "Azure qualified Mozaic HAMR (up to 44TB); record 198 EB nearline shipped "
            "(DC rev $3.4B, +63% YoY); supply allocated to 2028."
        ),
        "verdict": Verdict.AHEAD.value,
        "suggested_next_check": "Track additional hyperscaler Mozaic 44TB qualifications and 2028 supply pricing",
        "excerpt": (
            "Data center revenue reached $3.4 billion (+63% YoY, 81% of total) on a record 198 exabytes "
            "of nearline capacity shipped. Microsoft Azure officially qualified Seagate's HAMR-based Mozaic "
            "platform (up to 44TB tiers), and management noted nearline supply is largely committed under "
            "long-term hyperscaler agreements extending through calendar 2028."
        ),
        "source_url": STX_Q1_PR_URL,
    },
    # Amazon (AMZN · 6.86% weight) — Q3 2026 Release & Call (Oct 29, 2026)
    {
        "ticker": "AMZN",
        "doc_title": "Amazon Q3 2026 Earnings Release",
        "doc_date": "2026-10-29",
        "page_ref": "AMZN Q3 2026 PR · Oct 29",
        "topic": "AWS cloud revenue growth operating margin income Trainium Bedrock Q3 2026",
        "headline": "AWS $46.0B (+39.3% YoY, 38.7% margin); record $28.1B op income",
        "summary_card": (
            "Q3 AWS rev $46.0B (+39.3% YoY, $184B run rate), op margin 38.7%; "
            "consolidated op income record $28.1B (13.9% margin)."
        ),
        "verdict": Verdict.AHEAD.value,
        "suggested_next_check": "Check whether AWS sustains >35% YoY growth and ~38% operating margin in Q4",
        "excerpt": (
            "Amazon posted Q3 2026 net sales of $201.9 billion (+12% YoY) and record operating income of "
            "$28.1 billion (+61% YoY, 13.9% margin). AWS net sales accelerated to $46.0 billion (+39.3% YoY, "
            "$184 billion annualized run rate) with $17.8 billion operating income (38.7% margin) driven by "
            "AI infrastructure demand, Trainium custom silicon, and Bedrock."
        ),
        "source_url": AMZN_Q3_PR_URL,
    },
    {
        "ticker": "AMZN",
        "doc_title": "Amazon Q3 2026 Earnings Call",
        "doc_date": "2026-10-29",
        "page_ref": "AMZN Q3 2026 Call · Oct 29",
        "topic": "capex capital expenditure AWS AI data center storage infrastructure Q4 guidance cash flow",
        "headline": "Companywide FY26 cash capex reiterated ~$220B (heavy AWS/AI spend); Q4 rev $240-$247B",
        "summary_card": (
            "Companywide FY26 cash capex reiterated ~$220B (heavy AWS/AI infra spend; "
            "TTM FCF -$42.4B); Q4 guide $240-$247B rev, $28.5-$33.5B op inc."
        ),
        "verdict": Verdict.WATCH.value,
        "suggested_next_check": "Track 2027 Amazon capex trajectory and AWS nearline storage pull-through",
        "excerpt": (
            "Amazon reiterated full-year 2026 companywide cash capital expenditures of approximately $220 billion, "
            "heavily weighted toward scaling AWS AI infrastructure, custom silicon, and cloud storage "
            "capacity alongside fulfillment and corporate investments (pushing TTM free cash flow to -$42.4 billion). "
            "Q4 2026 net sales were guided to "
            "$240.0-$247.0 billion (+12%-16% YoY) with operating income of $28.5-$33.5 billion."
        ),
        "source_url": AMZN_Q3_PR_URL,
    },
]


def seed_if_empty(db: CockpitDB) -> None:
    if not db.list_holdings():
        now = utc_now_iso()
        for holding in SEED_HOLDINGS:
            db.upsert_holding(holding.model_copy(update={"updated_at": now}))
    else:
        migrated = db.migrate_legacy_demo_portfolio(SEED_HOLDINGS, SEED_CHUNKS)
        if migrated:
            return
        for holding in SEED_HOLDINGS:
            db.migrate_placeholder_holding(holding)

    if db.count_chunks() == 0:
        db.insert_chunks(SEED_CHUNKS)
    else:
        chunks_by_ticker: dict[str, list[dict[str, str]]] = {}
        for chunk in SEED_CHUNKS:
            chunks_by_ticker.setdefault(chunk["ticker"].upper(), []).append(chunk)
        for ticker, ticker_chunks in chunks_by_ticker.items():
            db.replace_placeholder_chunks(ticker, ticker_chunks)
