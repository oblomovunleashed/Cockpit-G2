from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re


PORTFOLIO_CSV_PATH = Path(__file__).resolve().parent / "data" / "portfolio.csv"

# Core indexed watchlist (top 3 positions = 45.15% of total portfolio)
CORE_WATCHLIST_TICKERS: tuple[str, ...] = ("NTRA", "STX", "AMZN")

# Short or common-English tickers that should only match as explicit $TICKER cashtags in free text
_AMBIGUOUS_TICKERS = {"u", "b", "se", "app", "sky", "hum", "fox", "aur"}

_LEGAL_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|corp|corporation|holdings|plc|ltd|limited|company|co|group|technology|technologies|n\.?v|s\.?a|calls only)\b\.?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PortfolioPosition:
    ticker: str
    company: str
    weight_pct: float

    @property
    def weight_label(self) -> str:
        return f"{self.weight_pct:.1f}%" if self.weight_pct >= 0.1 else f"{self.weight_pct:.2f}%"

    @property
    def display_name(self) -> str:
        return f"{self.company} ({self.ticker})"


def _load_portfolio_csv(path: Path = PORTFOLIO_CSV_PATH) -> tuple[PortfolioPosition, ...]:
    positions: list[PortfolioPosition] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            ticker = row["ticker"].strip().upper()
            company = row["company"].strip()
            weight_pct = float(row["weight_pct"])
            positions.append(PortfolioPosition(ticker=ticker, company=company, weight_pct=weight_pct))
    return tuple(positions)


PORTFOLIO_POSITIONS: tuple[PortfolioPosition, ...] = _load_portfolio_csv()

PORTFOLIO_BY_TICKER: dict[str, PortfolioPosition] = {
    pos.ticker.upper(): pos for pos in PORTFOLIO_POSITIONS
}


def _derived_name_phrases(company: str) -> tuple[str, ...]:
    """
    Derives clean searchable company phrases purely from the CSV company string
    (no hand-maintained alias tables).
    """
    normalized = re.sub(r"[—–\-]+", " ", company)
    normalized = re.sub(r"[,.]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()

    phrases: list[str] = [normalized]

    # Strip '.com' for names like "Amazon.com, Inc."
    no_dotcom = re.sub(r"\.com\b", "", company, flags=re.IGNORECASE)
    cleaned = _LEGAL_SUFFIX_RE.sub(" ", re.sub(r"[—–\-,]+", " ", no_dotcom))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()

    # Keep stripped phrase only if meaningful (avoid reducing "Sea Limited" -> "sea")
    if cleaned and (len(cleaned.split()) >= 2 or len(cleaned) >= 4):
        phrases.append(cleaned)

    # For share-class names like "Fox Corporation — Class A", allow "fox class a"
    # and allow bare root ("fox") ONLY on Class A (not Class B)
    class_m = re.search(r"\bclass\s+([a-z])\b", cleaned)
    if class_m:
        root = cleaned[: class_m.start()].strip()
        if root and len(root) >= 3:
            phrases.append(f"{root} class {class_m.group(1)}")
            if class_m.group(1) == "a":
                phrases.append(root)

    # Deduplicate preserving order
    return tuple(dict.fromkeys(p for p in phrases if p))


_DERIVED_PHRASES: dict[str, tuple[str, ...]] = {
    pos.ticker: _derived_name_phrases(pos.company) for pos in PORTFOLIO_POSITIONS
}


def lookup_portfolio_position(ticker_or_query: str | None) -> PortfolioPosition | None:
    """
    Fast O(1) lookup by ticker (e.g. 'INSM', 'FOX', 'NTRA'), or extracts a parenthesized
    ticker like 'Fox Corporation — Class B (FOX)', falling back to CSV company-name match.
    """
    if not ticker_or_query:
        return None
    cleaned = ticker_or_query.strip()
    upper = cleaned.upper()
    if upper in PORTFOLIO_BY_TICKER:
        return PORTFOLIO_BY_TICKER[upper]

    m = re.search(r"\(([A-Z]{1,6})\)", cleaned)
    if m and m.group(1) in PORTFOLIO_BY_TICKER:
        return PORTFOLIO_BY_TICKER[m.group(1)]

    return match_portfolio_position_in_text(cleaned)


def match_portfolio_position_in_text(
    text: str,
    *,
    exclude_tickers: set[str] | None = None,
) -> PortfolioPosition | None:
    """
    Offline/mock helper: finds the earliest, most specific CSV portfolio position
    mentioned in free text (longest span beats overlapping shorter spans).
    Live mode does not need this — the LLM resolves the ticker and calls lookup_portfolio_position().
    """
    excluded = {t.upper() for t in (exclude_tickers or set())}
    lower = text.lower()
    raw_hits: list[tuple[int, int, PortfolioPosition]] = []

    for pos in PORTFOLIO_POSITIONS:
        if pos.ticker in excluded:
            continue
        for phrase in _DERIVED_PHRASES[pos.ticker]:
            for m in re.finditer(rf"\b{re.escape(phrase)}\b", lower):
                raw_hits.append((m.start(), m.end(), pos))

        if pos.ticker.lower() in _AMBIGUOUS_TICKERS:
            for m_cash in re.finditer(rf"\${re.escape(pos.ticker)}\b", text):
                raw_hits.append((m_cash.start(), m_cash.end(), pos))
        else:
            for m_tick in re.finditer(rf"\b{re.escape(pos.ticker.lower())}\b", lower):
                raw_hits.append((m_tick.start(), m_tick.end(), pos))

    if not raw_hits:
        return None
    raw_hits.sort(key=lambda item: (item[0], -(item[1] - item[0]), -item[2].weight_pct))
    return raw_hits[0][2]


def format_weight_badge(ticker_or_query: str | None) -> str | None:
    pos = lookup_portfolio_position(ticker_or_query)
    return pos.weight_label if pos else None
