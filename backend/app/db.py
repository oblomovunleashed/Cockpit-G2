from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.models import DocumentChunk, HoldingRecord, JournalEntry, Mind, PendingWrite, Verdict


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class CockpitDB:
    """SQLite persistence + FTS5 document chunk index for Cockpit-G2."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS holdings (
                    ticker TEXT PRIMARY KEY,
                    company TEXT NOT NULL,
                    saved_question TEXT NOT NULL,
                    verdict TEXT NOT NULL DEFAULT 'PENDING',
                    latest_finding TEXT NOT NULL DEFAULT '',
                    source_citation TEXT NOT NULL DEFAULT '',
                    suggested_next_check TEXT NOT NULL DEFAULT '',
                    next_check TEXT NOT NULL DEFAULT '',
                    notion_page_id TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS document_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    doc_title TEXT NOT NULL,
                    doc_date TEXT NOT NULL,
                    page_ref TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    headline TEXT NOT NULL,
                    summary_card TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    suggested_next_check TEXT NOT NULL,
                    excerpt TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT ''
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
                    ticker,
                    topic,
                    headline,
                    summary_card,
                    excerpt,
                    content='document_chunks',
                    content_rowid='id'
                );

                CREATE TRIGGER IF NOT EXISTS document_chunks_ai AFTER INSERT ON document_chunks BEGIN
                    INSERT INTO document_chunks_fts(rowid, ticker, topic, headline, summary_card, excerpt)
                    VALUES (new.id, new.ticker, new.topic, new.headline, new.summary_card, new.excerpt);
                END;

                CREATE TRIGGER IF NOT EXISTS document_chunks_ad AFTER DELETE ON document_chunks BEGIN
                    INSERT INTO document_chunks_fts(document_chunks_fts, rowid, ticker, topic, headline, summary_card, excerpt)
                    VALUES ('delete', old.id, old.ticker, old.topic, old.headline, old.summary_card, old.excerpt);
                END;

                CREATE TABLE IF NOT EXISTS journal_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mind TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    ticker TEXT,
                    source_citation TEXT,
                    notion_page_id TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pending_writes (
                    id TEXT PRIMARY KEY,
                    mind TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    ticker TEXT,
                    source_citation TEXT,
                    updates_next_check INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                """
            )

    # ------------------------------------------------------------------
    # Holdings
    # ------------------------------------------------------------------

    def list_holdings(self) -> list[HoldingRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM holdings ORDER BY rowid ASC").fetchall()
        return [self._row_to_holding(row) for row in rows]

    def get_holding(self, ticker: str) -> HoldingRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM holdings WHERE UPPER(ticker) = UPPER(?)",
                (ticker,),
            ).fetchone()
        return self._row_to_holding(row) if row else None

    def upsert_holding(self, holding: HoldingRecord) -> HoldingRecord:
        now = holding.updated_at or utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO holdings (
                    ticker, company, saved_question, verdict, latest_finding,
                    source_citation, suggested_next_check, next_check, notion_page_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    company=excluded.company,
                    saved_question=excluded.saved_question,
                    verdict=excluded.verdict,
                    latest_finding=excluded.latest_finding,
                    source_citation=excluded.source_citation,
                    suggested_next_check=excluded.suggested_next_check,
                    next_check=excluded.next_check,
                    notion_page_id=COALESCE(excluded.notion_page_id, holdings.notion_page_id),
                    updated_at=excluded.updated_at
                """,
                (
                    holding.ticker.upper(),
                    holding.company,
                    holding.saved_question,
                    holding.verdict.value if holding.verdict is not None else "",
                    holding.latest_finding,
                    holding.source_citation,
                    holding.suggested_next_check,
                    holding.next_check,
                    holding.notion_page_id,
                    now,
                ),
            )
        return self.get_holding(holding.ticker)  # type: ignore[return-value]

    def update_holding_question(self, ticker: str, saved_question: str) -> HoldingRecord | None:
        now = utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE holdings SET saved_question = ?, updated_at = ? WHERE UPPER(ticker) = UPPER(?)",
                (saved_question, now, ticker),
            )
            if cur.rowcount == 0:
                return None
        return self.get_holding(ticker)

    def update_holding_finding(
        self,
        ticker: str,
        *,
        verdict: Verdict | None,
        latest_finding: str,
        source_citation: str,
        suggested_next_check: str,
    ) -> HoldingRecord | None:
        now = utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE holdings
                SET verdict = ?, latest_finding = ?, source_citation = ?,
                    suggested_next_check = ?, updated_at = ?
                WHERE UPPER(ticker) = UPPER(?)
                """,
                (
                    verdict.value if verdict is not None else "",
                    latest_finding,
                    source_citation,
                    suggested_next_check,
                    now,
                    ticker,
                ),
            )
            if cur.rowcount == 0:
                return None
        return self.get_holding(ticker)

    def update_holding_next_check(self, ticker: str, next_check: str) -> HoldingRecord | None:
        now = utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE holdings SET next_check = ?, updated_at = ? WHERE UPPER(ticker) = UPPER(?)",
                (next_check, now, ticker),
            )
            if cur.rowcount == 0:
                return None
        return self.get_holding(ticker)

    def update_holding_notion_page_id(self, ticker: str, notion_page_id: str) -> HoldingRecord | None:
        now = utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE holdings SET notion_page_id = ?, updated_at = ? WHERE UPPER(ticker) = UPPER(?)",
                (notion_page_id, now, ticker),
            )
            if cur.rowcount == 0:
                return None
        return self.get_holding(ticker)

    def migrate_placeholder_holding(self, replacement: HoldingRecord) -> bool:
        """
        Idempotently upgrades an identifiable placeholder holding record to real
        seeded metadata/findings while preserving user-authored saved_question,
        next_check, and notion_page_id.
        """
        existing = self.get_holding(replacement.ticker)
        if existing is None:
            return False

        is_placeholder = (
            "placeholder" in existing.company.lower()
            or "placeholder" in existing.latest_finding.lower()
            or "replace with real filing" in existing.source_citation.lower()
        )
        if not is_placeholder:
            return False

        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE holdings
                SET company = ?, verdict = ?, latest_finding = ?, source_citation = ?,
                    suggested_next_check = ?, updated_at = ?
                WHERE UPPER(ticker) = UPPER(?)
                """,
                (
                    replacement.company,
                    replacement.verdict.value if replacement.verdict is not None else "",
                    replacement.latest_finding,
                    replacement.source_citation,
                    replacement.suggested_next_check,
                    now,
                    replacement.ticker,
                ),
            )
        return True

    def replace_placeholder_chunks(self, ticker: str, new_chunks: list[dict[str, str]]) -> int:
        """
        Idempotently replaces identifiable placeholder document_chunks for a ticker
        with real filing excerpts (keeping FTS5 triggers in sync).
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id FROM document_chunks
                WHERE UPPER(ticker) = UPPER(?)
                  AND (
                      LOWER(doc_title) LIKE 'placeholder%'
                      OR LOWER(page_ref) LIKE 'placeholder%'
                  )
                """,
                (ticker,),
            ).fetchall()
            if not rows:
                return 0

            conn.executemany(
                "DELETE FROM document_chunks WHERE id = ?",
                [(row["id"],) for row in rows],
            )
            conn.executemany(
                """
                INSERT INTO document_chunks (
                    ticker, doc_title, doc_date, page_ref, topic, headline,
                    summary_card, verdict, suggested_next_check, excerpt, source_url
                ) VALUES (
                    :ticker, :doc_title, :doc_date, :page_ref, :topic, :headline,
                    :summary_card, :verdict, :suggested_next_check, :excerpt, :source_url
                )
                """,
                new_chunks,
            )
        return len(new_chunks)

    # ------------------------------------------------------------------
    # Document chunks
    # ------------------------------------------------------------------

    def count_chunks(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM document_chunks").fetchone()
        return int(row["n"]) if row else 0

    def insert_chunks(self, chunks: Iterable[dict[str, str]]) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO document_chunks (
                    ticker, doc_title, doc_date, page_ref, topic, headline,
                    summary_card, verdict, suggested_next_check, excerpt, source_url
                ) VALUES (
                    :ticker, :doc_title, :doc_date, :page_ref, :topic, :headline,
                    :summary_card, :verdict, :suggested_next_check, :excerpt, :source_url
                )
                """,
                list(chunks),
            )

    def list_chunks_for_ticker(self, ticker: str) -> list[DocumentChunk]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM document_chunks WHERE UPPER(ticker) = UPPER(?) ORDER BY id ASC",
                (ticker,),
            ).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def search_chunks_fts(self, ticker: str, fts_query: str, limit: int = 5) -> list[DocumentChunk]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT dc.*
                FROM document_chunks_fts fts
                JOIN document_chunks dc ON dc.id = fts.rowid
                WHERE UPPER(dc.ticker) = UPPER(?)
                  AND document_chunks_fts MATCH ?
                ORDER BY bm25(document_chunks_fts)
                LIMIT ?
                """,
                (ticker, fts_query, limit),
            ).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    # ------------------------------------------------------------------
    # Pending writes & confirmed journal
    # ------------------------------------------------------------------

    def save_pending_write(self, pending: PendingWrite) -> PendingWrite:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pending_writes (
                    id, mind, kind, content, ticker, source_citation, updates_next_check, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pending.id,
                    pending.mind.value,
                    pending.kind,
                    pending.content,
                    pending.ticker,
                    pending.source_citation,
                    1 if pending.updates_next_check else 0,
                    utc_now_iso(),
                ),
            )
        return pending

    def get_pending_write(self, pending_id: str) -> PendingWrite | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM pending_writes WHERE id = ?", (pending_id,)).fetchone()
        if not row:
            return None
        return PendingWrite(
            id=row["id"],
            mind=Mind(row["mind"]),
            kind=row["kind"],
            content=row["content"],
            ticker=row["ticker"],
            source_citation=row["source_citation"],
            updates_next_check=bool(row["updates_next_check"]),
        )

    def delete_pending_write(self, pending_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM pending_writes WHERE id = ?", (pending_id,))
        return cur.rowcount > 0

    def claim_and_commit_pending_write(
        self,
        pending_id: str,
        *,
        override_text: str | None = None,
    ) -> tuple[PendingWrite, JournalEntry, HoldingRecord | None] | None:
        """
        Atomically consumes a pending write and commits the local journal entry
        (and optional holding next_check update) in a single IMMEDIATE SQLite transaction
        before any external Notion network call is awaited.
        """
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM pending_writes WHERE id = ?",
                (pending_id,),
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                return None

            pending = PendingWrite(
                id=row["id"],
                mind=Mind(row["mind"]),
                kind=row["kind"],
                content=row["content"],
                ticker=row["ticker"],
                source_citation=row["source_citation"],
                updates_next_check=bool(row["updates_next_check"]),
            )

            del_cur = conn.execute("DELETE FROM pending_writes WHERE id = ?", (pending_id,))
            if del_cur.rowcount != 1:
                conn.execute("ROLLBACK")
                return None

            now = utc_now_iso()
            override = override_text.strip() if override_text is not None else ""
            content = (override or pending.content).strip()
            ins_cur = conn.execute(
                """
                INSERT INTO journal_entries (
                    mind, kind, content, ticker, source_citation, notion_page_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pending.mind.value,
                    pending.kind,
                    content,
                    pending.ticker,
                    pending.source_citation,
                    None,
                    now,
                ),
            )
            entry_id = int(ins_cur.lastrowid)
            entry = JournalEntry(
                id=entry_id,
                mind=pending.mind,
                kind=pending.kind,
                content=content,
                ticker=pending.ticker,
                source_citation=pending.source_citation,
                notion_page_id=None,
                created_at=now,
            )

            updated_holding: HoldingRecord | None = None
            if pending.updates_next_check and pending.ticker:
                conn.execute(
                    "UPDATE holdings SET next_check = ?, updated_at = ? WHERE UPPER(ticker) = UPPER(?)",
                    (content, now, pending.ticker),
                )
                h_row = conn.execute(
                    "SELECT * FROM holdings WHERE UPPER(ticker) = UPPER(?)",
                    (pending.ticker,),
                ).fetchone()
                if h_row:
                    updated_holding = self._row_to_holding(h_row)

            conn.execute("COMMIT")
            return pending, entry, updated_holding
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def update_journal_notion_page_id(self, entry_id: int, notion_page_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE journal_entries SET notion_page_id = ? WHERE id = ?",
                (notion_page_id, entry_id),
            )

    def add_journal_entry(
        self,
        *,
        mind: Mind,
        kind: str,
        content: str,
        ticker: str | None = None,
        source_citation: str | None = None,
        notion_page_id: str | None = None,
    ) -> JournalEntry:
        now = utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO journal_entries (
                    mind, kind, content, ticker, source_citation, notion_page_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (mind.value, kind, content, ticker, source_citation, notion_page_id, now),
            )
            entry_id = int(cur.lastrowid)
        return JournalEntry(
            id=entry_id,
            mind=mind,
            kind=kind,  # type: ignore[arg-type]
            content=content,
            ticker=ticker,
            source_citation=source_citation,
            notion_page_id=notion_page_id,
            created_at=now,
        )

    def list_journal_entries(self, mind: Mind | None = None, limit: int = 25) -> list[JournalEntry]:
        with self._connect() as conn:
            if mind is not None:
                rows = conn.execute(
                    "SELECT * FROM journal_entries WHERE mind = ? ORDER BY id DESC LIMIT ?",
                    (mind.value, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM journal_entries ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_journal(row) for row in rows]

    # ------------------------------------------------------------------
    # Row mappers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_holding(row: sqlite3.Row) -> HoldingRecord:
        raw_verdict = (row["verdict"] or "").strip()
        return HoldingRecord(
            ticker=row["ticker"],
            company=row["company"],
            saved_question=row["saved_question"],
            verdict=Verdict(raw_verdict) if raw_verdict else None,
            latest_finding=row["latest_finding"],
            source_citation=row["source_citation"],
            suggested_next_check=row["suggested_next_check"],
            next_check=row["next_check"],
            notion_page_id=row["notion_page_id"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> DocumentChunk:
        return DocumentChunk(
            id=int(row["id"]),
            ticker=row["ticker"],
            doc_title=row["doc_title"],
            doc_date=row["doc_date"],
            page_ref=row["page_ref"],
            topic=row["topic"],
            headline=row["headline"],
            summary_card=row["summary_card"],
            verdict=Verdict(row["verdict"]),
            suggested_next_check=row["suggested_next_check"],
            excerpt=row["excerpt"],
            source_url=row["source_url"],
        )

    @staticmethod
    def _row_to_journal(row: sqlite3.Row) -> JournalEntry:
        return JournalEntry(
            id=int(row["id"]),
            mind=Mind(row["mind"]),
            kind=row["kind"],
            content=row["content"],
            ticker=row["ticker"],
            source_citation=row["source_citation"],
            notion_page_id=row["notion_page_id"],
            created_at=row["created_at"],
        )
