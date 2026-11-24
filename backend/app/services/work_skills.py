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


def build_grounded_peer_read_across(
    *,
    target: HoldingRecord,
    peer_ticker: str,
    peer_chunk: DocumentChunk,
) -> tuple[str, DraftCheck]:
    """
    Grounded fallback for peer_read_across (used in mock mode and if live LLM JSON
    synthesis fails). Always derives BOTH the card text and proposed check strictly
    from the selected `peer_chunk` so citations and content can never diverge.
    """
    target_t = target.ticker.upper()
    peer_t = peer_ticker.upper()

    finding = _clip(f"{peer_t} read-across for {target_t}: {peer_chunk.summary_card}", 195)
    check_text = _clip(
        f"Check on {target_t} call how {peer_t} trend applies: {peer_chunk.headline}",
        120,
    )
    draft = DraftCheck(
        content=check_text,
        ticker=target_t,
        source_citation=f"Peer read: {peer_chunk.page_ref}",
    )
    return finding, draft
