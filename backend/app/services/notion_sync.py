from __future__ import annotations

import logging
from dataclasses import dataclass
import httpx

from app.config import Settings
from app.models import HoldingRecord, JournalEntry

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


@dataclass(frozen=True)
class NotionHoldingSnapshot:
    ticker: str
    saved_question: str
    next_check: str
    notion_page_id: str


class NotionSync:
    """
    Live Notion integration for Track 4 (AI Assistants & Work).
    - Pushes confirmed journal writes (Personal / Interests / Work) into `Cockpit Log`.
    - Syncs saved thesis questions & next-quarter checks with `Holdings Watchlist`.
    - Uses surgical PATCH updates on existing pages so briefing recomputes never
      overwrite newer user-edited Saved Question or Next Check values in Notion.
    Gracefully no-ops when NOTION_TOKEN is unset so local/mock mode works immediately.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.notion_enabled

    @property
    def holdings_enabled(self) -> bool:
        return bool(
            self.settings.notion_token.strip()
            and self.settings.notion_holdings_database_id.strip()
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.notion_token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    async def push_journal_entry(self, entry: JournalEntry) -> str | None:
        if not self.enabled:
            return None

        properties: dict = {
            "Entry": {"title": [{"text": {"content": entry.content[:2000]}}]},
            "Mind": {"select": {"name": entry.mind.value.capitalize()}},
            "Kind": {"select": {"name": entry.kind.capitalize()}},
            "Logged At": {"date": {"start": entry.created_at}},
        }
        if entry.ticker:
            properties["Ticker"] = {"rich_text": [{"text": {"content": entry.ticker}}]}
        if entry.source_citation:
            properties["Source"] = {"rich_text": [{"text": {"content": entry.source_citation[:2000]}}]}

        payload = {
            "parent": {"database_id": self.settings.notion_log_database_id},
            "properties": properties,
        }
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(
                    f"{NOTION_API_BASE}/pages",
                    headers=self._headers(),
                    json=payload,
                )
                resp.raise_for_status()
                return str(resp.json().get("id", "")) or None
        except Exception as exc:
            logger.warning("Notion journal push failed (SQLite write preserved): %s", exc)
            return None

    async def sync_holding_update(
        self,
        holding: HoldingRecord,
        *,
        include_finding: bool = True,
        include_saved_question: bool = False,
        include_next_check: bool = False,
    ) -> str | None:
        """
        Update or create a row in the Holdings Watchlist database.
        When updating an existing page (notion_page_id is present), only the requested
        subset of properties is patched so user edits in Notion are never clobbered.
        """
        if not self.holdings_enabled:
            return None

        if holding.notion_page_id:
            properties: dict = {}
            if include_finding:
                properties["Status"] = (
                    {"select": {"name": holding.verdict.value}}
                    if holding.verdict is not None
                    else {"select": None}
                )
                properties["Latest Finding"] = {
                    "rich_text": [{"text": {"content": holding.latest_finding[:2000]}}]
                }
                properties["Source"] = {
                    "rich_text": [{"text": {"content": holding.source_citation[:2000]}}]
                }
            if include_saved_question:
                properties["Saved Question"] = {
                    "rich_text": [{"text": {"content": holding.saved_question[:2000]}}]
                }
            if include_next_check:
                properties["Next Check"] = {
                    "rich_text": [{"text": {"content": holding.next_check[:2000]}}]
                }
            if not properties:
                return holding.notion_page_id
        else:
            properties = {
                "Ticker": {"title": [{"text": {"content": holding.ticker}}]},
                "Company": {"rich_text": [{"text": {"content": holding.company[:2000]}}]},
                "Saved Question": {"rich_text": [{"text": {"content": holding.saved_question[:2000]}}]},
                "Latest Finding": {"rich_text": [{"text": {"content": holding.latest_finding[:2000]}}]},
                "Source": {"rich_text": [{"text": {"content": holding.source_citation[:2000]}}]},
                "Next Check": {"rich_text": [{"text": {"content": holding.next_check[:2000]}}]},
            }
            if holding.verdict is not None:
                properties["Status"] = {"select": {"name": holding.verdict.value}}

        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                if holding.notion_page_id:
                    resp = await client.patch(
                        f"{NOTION_API_BASE}/pages/{holding.notion_page_id}",
                        headers=self._headers(),
                        json={"properties": properties},
                    )
                else:
                    resp = await client.post(
                        f"{NOTION_API_BASE}/pages",
                        headers=self._headers(),
                        json={
                            "parent": {"database_id": self.settings.notion_holdings_database_id},
                            "properties": properties,
                        },
                    )
                resp.raise_for_status()
                return str(resp.json().get("id", "")) or holding.notion_page_id
        except Exception as exc:
            logger.warning("Notion holding sync failed for %s: %s", holding.ticker, exc)
            return holding.notion_page_id

    async def pull_holdings(self) -> dict[str, NotionHoldingSnapshot]:
        """
        Pulls live {TICKER: NotionHoldingSnapshot} from the Notion Holdings Watchlist.
        Used both before briefing recomputation (so Notion edits win) and on manual pull.
        """
        if not self.holdings_enabled:
            return {}

        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(
                    f"{NOTION_API_BASE}/databases/{self.settings.notion_holdings_database_id}/query",
                    headers=self._headers(),
                    json={"page_size": 25},
                )
                resp.raise_for_status()
                results = resp.json().get("results", [])
        except Exception as exc:
            logger.warning("Notion holdings pull failed: %s", exc)
            return {}

        snapshots: dict[str, NotionHoldingSnapshot] = {}
        for page in results:
            props = page.get("properties", {})
            ticker_items = props.get("Ticker", {}).get("title", [])
            question_items = props.get("Saved Question", {}).get("rich_text", [])
            next_check_items = props.get("Next Check", {}).get("rich_text", [])
            ticker = "".join(t.get("plain_text", "") for t in ticker_items).strip().upper()
            question = "".join(t.get("plain_text", "") for t in question_items).strip()
            next_check = "".join(t.get("plain_text", "") for t in next_check_items).strip()
            page_id = str(page.get("id", "")).strip()
            if ticker and page_id:
                snapshots[ticker] = NotionHoldingSnapshot(
                    ticker=ticker,
                    saved_question=question,
                    next_check=next_check,
                    notion_page_id=page_id,
                )
        return snapshots

    async def pull_saved_questions(self) -> dict[str, tuple[str, str]]:
        """Backwards-compatible wrapper returning {TICKER: (saved_question, notion_page_id)}."""
        snapshots = await self.pull_holdings()
        return {
            ticker: (snap.saved_question, snap.notion_page_id)
            for ticker, snap in snapshots.items()
            if snap.saved_question
        }
