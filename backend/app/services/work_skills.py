from __future__ import annotations

from app.models import DocumentChunk, DraftCheck, HoldingRecord, HudCard
from app.seed import PRIOR_BASELINES


def _clip(text: str, limit: int) -> str:
    clean = " ".join(text.strip().split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def render_earnings_preview(holding: HoldingRecord) -> HudCard:
    """
    Tier 0 deterministic skill (ZERO LLM calls, <5ms, zero hallucination):
    Shows the wearer's saved thesis question, verified prior-quarter guidance baseline,
    and any open next-quarter watch item before an earnings print.
    """
    baseline = PRIOR_BASELINES.get(holding.ticker.upper(), "")
    lines = [f"Q: {holding.saved_question}"]
    if baseline:
        lines.append(baseline)
    if holding.next_check:
        lines.append(f"Open check: {holding.next_check}")

    header = _clip(f"PREVIEW · {holding.ticker} · PRE-CALL", 60)
    body = _clip("\n".join(lines), 240)
    footer = "Hold: update check · Swipe: holdings"
    return HudCard(header=header, body=body, footer=footer)


def build_mock_peer_read_across(
    *,
    target: HoldingRecord,
    peer_ticker: str,
    peer_chunk: DocumentChunk,
) -> tuple[str, DraftCheck]:
    """Deterministic fallback for peer_read_across in mock mode."""
    target_t = target.ticker.upper()
    peer_t = peer_ticker.upper()

    if peer_t == "TSM" and target_t == "NVDA":
        finding = (
            "TSMC Q4 guide $52.3-54.3bn (+58% YoY) & maxed CoWoS supply confirm strong "
            "NVDA AI demand, while flagging packaging/wafer tightness."
        )
        check_text = "Check on NVDA call whether TSMC CoWoS tightness affects Q4/Q1 shipment mix or margins"
    elif peer_t == "MSFT" and target_t == "NVDA":
        finding = (
            "MSFT guided Q2 capex >$50bn (~$170bn CY26) with Azure +48% and faster GB300 "
            "rack deployments — constructive read-across for NVDA demand."
        )
        check_text = "Check whether hyperscaler capex (> $50bn/qtr at MSFT) sustains NVDA GB300 pricing"
    else:
        finding = (
            f"{peer_t} read-across for {target_t}: {peer_chunk.summary_card}"
        )
        check_text = f"Check {target_t} exposure to {peer_t} trend: {peer_chunk.headline}"

    draft = DraftCheck(
        content=check_text,
        ticker=target_t,
        source_citation=f"Peer read: {peer_chunk.page_ref}",
    )
    return _clip(finding, 195), draft
