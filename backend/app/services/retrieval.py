from __future__ import annotations

import re

from app.db import CockpitDB
from app.models import DocumentChunk


STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "what", "when", "does",
    "did", "are", "was", "were", "have", "has", "about", "into", "their", "company",
    "management", "expect", "expected", "running", "get", "your", "our", "next",
}


class DocumentRetriever:
    """Fast local SQLite FTS5 retriever over indexed issuer filing excerpts."""

    def __init__(self, db: CockpitDB) -> None:
        self.db = db

    def retrieve(self, ticker: str, query: str, limit: int = 4) -> list[DocumentChunk]:
        tokens = [
            tok for tok in re.findall(r"[a-zA-Z0-9]+", query.lower())
            if len(tok) >= 3 and tok not in STOPWORDS
        ]
        if tokens:
            fts_expr = " OR ".join(dict.fromkeys(tokens))
            try:
                hits = self.db.search_chunks_fts(ticker, fts_expr, limit=limit)
                if hits:
                    return hits
            except Exception:
                # Fall back cleanly to all chunks for this ticker if FTS syntax rejects a token
                pass
        return self.db.list_chunks_for_ticker(ticker)[:limit]
