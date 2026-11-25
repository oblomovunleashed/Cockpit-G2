from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class PortfolioPosition:
    ticker: str
    company: str
    weight_pct: float
    aliases: tuple[str, ...] = ()
    # True if bare lowercase ticker collides with common English words (e.g. U, B, SE, APP, SKY, HUM)
    ticker_case_sensitive: bool = False

    @property
    def weight_label(self) -> str:
        return f"{self.weight_pct:.1f}%" if self.weight_pct >= 0.1 else f"{self.weight_pct:.2f}%"

    @property
    def display_name(self) -> str:
        return f"{self.company} ({self.ticker})"


PORTFOLIO_POSITIONS: tuple[PortfolioPosition, ...] = (
    PortfolioPosition("NTRA", "Natera, Inc.", 29.22, ("natera", "signatera")),
    PortfolioPosition("STX", "Seagate Technology Holdings plc", 9.07, ("seagate", "seagate technology", "mozaic", "hamr")),
    PortfolioPosition("AMZN", "Amazon.com, Inc.", 6.86, ("amazon", "aws")),
    PortfolioPosition(
        "FOXA",
        "Fox Corporation — Class A",
        4.27,
        ("fox corporation class a", "fox corp class a", "fox class a", "fox corporation", "fox corp", "fox"),
    ),
    PortfolioPosition("INSM", "Insmed Incorporated", 4.19, ("insmed",)),
    PortfolioPosition("YPF", "YPF S.A.", 3.97, ("ypf",)),
    PortfolioPosition("U", "Unity Software Inc.", 3.62, ("unity software", "unity"), ticker_case_sensitive=True),
    PortfolioPosition("SE", "Sea Limited", 3.47, ("sea limited", "shopee", "garena"), ticker_case_sensitive=True),
    PortfolioPosition("EWZ", "iShares MSCI Brazil ETF", 2.81, ("msci brazil", "brazil etf")),
    PortfolioPosition("CDW", "CDW Corporation", 2.37, ("cdw",)),
    PortfolioPosition("RVMD", "Revolution Medicines, Inc.", 2.00, ("revolution medicines",)),
    PortfolioPosition("TBBB", "BBB Foods Inc.", 1.94, ("bbb foods", "tiendas 3b")),
    PortfolioPosition("STM", "STMicroelectronics N.V.", 1.93, ("stmicroelectronics", "stmicro")),
    PortfolioPosition("NAMS", "NewAmsterdam Pharma Company N.V.", 1.83, ("newamsterdam pharma", "newamsterdam")),
    PortfolioPosition("SGI", "Somnigroup International Inc.", 1.38, ("somnigroup", "tempur sealy")),
    PortfolioPosition("NEM", "Newmont Corporation", 0.98, ("newmont",)),
    PortfolioPosition("B", "Barrick Mining Corporation", 0.96, ("barrick mining", "barrick gold", "barrick"), ticker_case_sensitive=True),
    PortfolioPosition("AEM", "Agnico Eagle Mines Limited", 0.95, ("agnico eagle", "agnico")),
    PortfolioPosition("BTDR", "Bitdeer Technologies Group", 0.89, ("bitdeer",)),
    PortfolioPosition("CPNG", "Coupang, Inc.", 0.83, ("coupang",)),
    PortfolioPosition("WDC", "Western Digital Corporation", 0.82, ("western digital",)),
    PortfolioPosition("PURR", "Hyperliquid Strategies Inc.", 0.81, ("hyperliquid strategies", "hyperliquid")),
    PortfolioPosition(
        "FOX",
        "Fox Corporation — Class B",
        0.80,
        ("fox corporation class b", "fox corp class b", "fox class b"),
        ticker_case_sensitive=True,
    ),
    PortfolioPosition("FLR", "Fluor Corporation", 0.77, ("fluor",)),
    PortfolioPosition("SKY", "Champion Homes, Inc.", 0.71, ("champion homes", "skyline champion"), ticker_case_sensitive=True),
    PortfolioPosition("APP", "AppLovin Corporation", 0.71, ("applovin",), ticker_case_sensitive=True),
    PortfolioPosition("CVCO", "Cavco Industries, Inc.", 0.71, ("cavco",)),
    PortfolioPosition("DAKT", "Daktronics, Inc.", 0.70, ("daktronics",)),
    PortfolioPosition("SPCX", "Space Exploration Technologies Corp. — SpaceX", 0.68, ("spacex", "space exploration technologies")),
    PortfolioPosition("ROKU", "Roku, Inc.", 0.63, ("roku",)),
    PortfolioPosition("TTMI", "TTM Technologies, Inc.", 0.61, ("ttm technologies",)),
    PortfolioPosition("RYTM", "Rhythm Pharmaceuticals, Inc.", 0.57, ("rhythm pharmaceuticals", "rhythm pharma")),
    PortfolioPosition("HUM", "Humana Inc.", 0.56, ("humana",), ticker_case_sensitive=True),
    PortfolioPosition("TSLA", "Tesla, Inc. — calls only", 0.50, ("tesla",)),
    PortfolioPosition("ADMA", "ADMA Biologics, Inc.", 0.50, ("adma biologics", "adma")),
    PortfolioPosition("RIOT", "Riot Platforms, Inc.", 0.41, ("riot platforms", "riot blockchain")),
    PortfolioPosition("BLTE", "Belite Bio, Inc.", 0.40, ("belite bio", "belite")),
    PortfolioPosition("BEAM", "Beam Therapeutics Inc.", 0.38, ("beam therapeutics",)),
    PortfolioPosition("JBS", "JBS N.V.", 0.36, ("jbs",)),
    PortfolioPosition("RSP", "Invesco S&P 500 Equal Weight ETF", 0.35, ("equal weight etf", "invesco equal weight")),
    PortfolioPosition("RLAY", "Relay Therapeutics, Inc.", 0.34, ("relay therapeutics",)),
    PortfolioPosition("PLTR", "Palantir Technologies Inc.", 0.34, ("palantir",)),
    PortfolioPosition("TSM", "Taiwan Semiconductor Manufacturing Company Limited", 0.33, ("tsmc", "taiwan semiconductor")),
    PortfolioPosition("RGEN", "Repligen Corporation", 0.33, ("repligen",)),
    PortfolioPosition("SNX", "TD SYNNEX Corporation", 0.32, ("td synnex", "synnex")),
    PortfolioPosition("CRH", "CRH plc", 0.30, ("crh",)),
    PortfolioPosition("NUVB", "Nuvation Bio Inc.", 0.28, ("nuvation bio", "nuvation")),
    PortfolioPosition("PANW", "Palo Alto Networks, Inc.", 0.27, ("palo alto networks", "palo alto")),
    PortfolioPosition("PCT", "PureCycle Technologies, Inc.", 0.26, ("purecycle",)),
    PortfolioPosition("NSIT", "Insight Enterprises, Inc.", 0.23, ("insight enterprises",)),
    PortfolioPosition("SKE", "Skeena Resources Limited", 0.20, ("skeena resources", "skeena")),
    PortfolioPosition("KRSA", "Korsana Biosciences, Inc.", 0.20, ("korsana biosciences", "korsana")),
    PortfolioPosition("OLMA", "Olema Pharmaceuticals, Inc.", 0.19, ("olema pharmaceuticals", "olema")),
    PortfolioPosition("MKSI", "MKS Inc.", 0.19, ("mks instruments", "mks inc")),
    PortfolioPosition("RKT", "Rocket Companies, Inc.", 0.17, ("rocket companies",)),
    PortfolioPosition("COGT", "Cogent Biosciences, Inc.", 0.16, ("cogent biosciences", "cogent")),
    PortfolioPosition("MLTX", "MoonLake Immunotherapeutics", 0.13, ("moonlake",)),
    PortfolioPosition("LLY", "Eli Lilly and Company", 0.10, ("eli lilly", "lilly")),
    PortfolioPosition("AUR", "Aurora Innovation, Inc.", 0.08, ("aurora innovation",), ticker_case_sensitive=True),
    PortfolioPosition("DBVT", "DBV Technologies S.A.", 0.05, ("dbv technologies", "dbv")),
    PortfolioPosition("WVE", "Wave Life Sciences, Inc.", 0.01, ("wave life sciences",)),
)

PORTFOLIO_BY_TICKER: dict[str, PortfolioPosition] = {
    pos.ticker.upper(): pos for pos in PORTFOLIO_POSITIONS
}

# Core indexed watchlist (top 3 positions = 45.15% of total portfolio)
CORE_WATCHLIST_TICKERS: tuple[str, ...] = ("NTRA", "STX", "AMZN")


def lookup_portfolio_position(ticker_or_query: str | None) -> PortfolioPosition | None:
    """Looks up a position by exact ticker or by matching its company/ticker inside a string."""
    if not ticker_or_query:
        return None
    cleaned = ticker_or_query.strip()
    upper = cleaned.upper()
    if upper in PORTFOLIO_BY_TICKER:
        return PORTFOLIO_BY_TICKER[upper]

    # Check parenthesized ticker like "Insmed Incorporated (INSM)"
    m = re.search(r"\(([A-Z]{1,6})\)", cleaned)
    if m and m.group(1) in PORTFOLIO_BY_TICKER:
        return PORTFOLIO_BY_TICKER[m.group(1)]

    return match_portfolio_position_in_text(cleaned)


def find_portfolio_mentions_in_text(
    text: str,
    *,
    exclude_tickers: set[str] | None = None,
) -> list[tuple[int, int, PortfolioPosition]]:
    """
    Finds all non-overlapping portfolio holding mentions in free text as (start, end, position)
    spans, safely avoiding false matches on 1-letter or common-word tickers
    (U, B, SE, APP, SKY, HUM, FOX, AUR).

    More specific/longer spans (e.g. 'Fox Class B') always beat overlapping shorter spans
    (e.g. 'Fox' -> FOXA) regardless of portfolio weight.
    """
    excluded = {t.upper() for t in (exclude_tickers or set())}
    lower = text.lower()
    raw_hits: list[tuple[int, int, PortfolioPosition]] = []

    for pos in PORTFOLIO_POSITIONS:
        if pos.ticker.upper() in excluded:
            continue
        for alias in pos.aliases:
            for m in re.finditer(rf"\b{re.escape(alias.lower())}\b", lower):
                raw_hits.append((m.start(), m.end(), pos))

        if pos.ticker_case_sensitive:
            # Only match explicit cashtag ($U, $APP, $FOX)
            for m_cash in re.finditer(rf"\${re.escape(pos.ticker)}\b", text):
                raw_hits.append((m_cash.start(), m_cash.end(), pos))
        else:
            for m_tick in re.finditer(rf"\b{re.escape(pos.ticker.lower())}\b", lower):
                raw_hits.append((m_tick.start(), m_tick.end(), pos))

    if not raw_hits:
        return []

    # Longest span wins when starting at the same position; otherwise earliest start first
    raw_hits.sort(key=lambda item: (item[0], -(item[1] - item[0]), -item[2].weight_pct))

    accepted: list[tuple[int, int, PortfolioPosition]] = []
    for start, end, pos in raw_hits:
        # Drop any shorter/overlapping span subsumed by a previously accepted span
        if any(start < acc_end and end > acc_start for acc_start, acc_end, _ in accepted):
            continue
        accepted.append((start, end, pos))
    return accepted


def match_portfolio_position_in_text(
    text: str,
    *,
    exclude_tickers: set[str] | None = None,
) -> PortfolioPosition | None:
    """
    Finds the earliest, most specific portfolio holding mentioned in free text.
    """
    mentions = find_portfolio_mentions_in_text(text, exclude_tickers=exclude_tickers)
    return mentions[0][2] if mentions else None


def format_weight_badge(ticker_or_query: str | None) -> str | None:
    pos = lookup_portfolio_position(ticker_or_query)
    return pos.weight_label if pos else None
