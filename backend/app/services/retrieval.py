from __future__ import annotations

import re

from app.db import CockpitDB
from app.models import DocumentChunk


STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "what", "when", "does",
    "did", "are", "was", "were", "have", "has", "about", "into", "their", "company",
    "management", "expect", "expected", "running", "get", "your", "our", "next",
    "not", "near", "how", "why", "will", "can", "should", "would", "could", "they",
    "its", "there", "these", "those", "any", "all",
}

ENTITY_TOKENS = {
    "ntra", "natera", "stx", "seagate", "amzn", "amazon",
    "nvda", "nvidia", "msft", "microsoft", "tsm", "tsmc",
    "corp", "inc", "ltd", "plc", "holdings",
}


def extract_content_tokens(query: str, ticker: str | None = None) -> list[str]:
    """Extracts meaningful non-stopword, non-entity search tokens from a question."""
    excluded = set(STOPWORDS) | set(ENTITY_TOKENS)
    if ticker:
        excluded.add(ticker.lower())
    raw_tokens = re.findall(r"[a-zA-Z0-9]+", query.lower())
    return list(
        dict.fromkeys(
            tok for tok in raw_tokens
            if len(tok) >= 3 and tok not in excluded
        )
    )


class DocumentRetriever:
    """Fast local SQLite FTS5 retriever over indexed issuer filing excerpts."""

    def __init__(self, db: CockpitDB) -> None:
        self.db = db

    def retrieve(self, ticker: str, query: str, limit: int = 4) -> list[DocumentChunk]:
        tokens = extract_content_tokens(query, ticker=ticker)
        if not tokens:
            return []

        # Quote every token so FTS5 operators cannot alter query semantics.
        fts_expr = " OR ".join(f'"{tok}"' for tok in tokens)
        try:
            return self.db.search_chunks_fts(ticker, fts_expr, limit=limit)
        except Exception:
            return []
