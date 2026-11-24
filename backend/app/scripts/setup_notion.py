from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path

import httpx

from app.config import REPO_ROOT, Settings
from app.db import CockpitDB
from app.seed import seed_if_empty
from app.services.notion_sync import NOTION_API_BASE, NOTION_VERSION, NotionSync


def normalize_notion_id(raw: str) -> str:
    """Accepts a raw Notion page UUID, 32-hex ID, or full Notion page URL."""
    cleaned = raw.strip()
    if not cleaned:
        return ""
    match = re.search(r"([0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", cleaned)
    if not match:
        return cleaned
    hex_only = match.group(1).replace("-", "").lower()
    return f"{hex_only[0:8]}-{hex_only[8:12]}-{hex_only[12:16]}-{hex_only[16:20]}-{hex_only[20:32]}"


def build_cockpit_log_database_payload(parent_page_id: str) -> dict:
    return {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": "Cockpit Log"}}],
        "properties": {
            "Entry": {"title": {}},
            "Mind": {
                "select": {
                    "options": [
                        {"name": "Work", "color": "green"},
                        {"name": "Personal", "color": "blue"},
                        {"name": "Interests", "color": "purple"},
                    ]
                }
            },
            "Kind": {
                "select": {
                    "options": [
                        {"name": "Note", "color": "gray"},
                        {"name": "Task", "color": "yellow"},
                        {"name": "Check", "color": "green"},
                    ]
                }
            },
            "Ticker": {"rich_text": {}},
            "Source": {"rich_text": {}},
            "Logged At": {"date": {}},
        },
    }


def build_holdings_database_payload(parent_page_id: str) -> dict:
    return {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": "Holdings Watchlist"}}],
        "properties": {
            "Ticker": {"title": {}},
            "Company": {"rich_text": {}},
            "Saved Question": {"rich_text": {}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "HELD", "color": "green"},
                        {"name": "AHEAD", "color": "blue"},
                        {"name": "WATCH", "color": "yellow"},
                        {"name": "PENDING", "color": "gray"},
                    ]
                }
            },
            "Latest Finding": {"rich_text": {}},
            "Source": {"rich_text": {}},
            "Next Check": {"rich_text": {}},
        },
    }


def update_env_file(env_path: Path, updates: dict[str, str]) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(updates)
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        replaced = False
        for key, value in list(remaining.items()):
            if stripped.startswith(f"{key}="):
                new_lines.append(f"{key}={value}")
                remaining.pop(key)
                replaced = True
                break
        if not replaced:
            new_lines.append(line)
    for key, value in remaining.items():
        new_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


async def run_setup(write_env: bool) -> None:
    settings = Settings()
    token = settings.notion_token.strip()
    parent_id = normalize_notion_id(settings.notion_parent_page_id)

    if not token:
        raise SystemExit("Missing NOTION_TOKEN in .env. Create an internal Notion integration first.")
    if not parent_id:
        raise SystemExit(
            "Missing NOTION_PARENT_PAGE_ID in .env.\n"
            "Create a blank Notion page (e.g. 'Cockpit'), share it with your integration "
            "(... -> Connections -> your integration), and paste the page URL or ID into NOTION_PARENT_PAGE_ID."
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        print("Creating 'Cockpit Log' database in Notion...")
        r_log = await client.post(
            f"{NOTION_API_BASE}/databases",
            headers=headers,
            json=build_cockpit_log_database_payload(parent_id),
        )
        if r_log.is_error:
            raise SystemExit(f"Failed creating Cockpit Log DB ({r_log.status_code}): {r_log.text}")
        log_db_id = str(r_log.json()["id"])

        print("Creating 'Holdings Watchlist' database in Notion...")
        r_hold = await client.post(
            f"{NOTION_API_BASE}/databases",
            headers=headers,
            json=build_holdings_database_payload(parent_id),
        )
        if r_hold.is_error:
            raise SystemExit(f"Failed creating Holdings Watchlist DB ({r_hold.status_code}): {r_hold.text}")
        holdings_db_id = str(r_hold.json()["id"])

    print(f"  NOTION_LOG_DATABASE_ID={log_db_id}")
    print(f"  NOTION_HOLDINGS_DATABASE_ID={holdings_db_id}")

    if write_env:
        env_path = REPO_ROOT / ".env"
        update_env_file(
            env_path,
            {
                "NOTION_LOG_DATABASE_ID": log_db_id,
                "NOTION_HOLDINGS_DATABASE_ID": holdings_db_id,
            },
        )
        print(f"Updated {env_path}")

    # Seed the 3 holdings into Notion Holdings Watchlist and store their page IDs in SQLite
    seeded_settings = settings.model_copy(
        update={
            "notion_log_database_id": log_db_id,
            "notion_holdings_database_id": holdings_db_id,
        }
    )
    db = CockpitDB(seeded_settings.cockpit_db_path)
    seed_if_empty(db)
    notion = NotionSync(seeded_settings)

    print("Seeding initial holdings into Notion Holdings Watchlist...")
    for holding in db.list_holdings():
        page_id = await notion.sync_holding_update(
            holding.model_copy(update={"notion_page_id": None}),
            include_finding=True,
            include_saved_question=True,
            include_next_check=True,
        )
        if page_id:
            db.update_holding_notion_page_id(holding.ticker, page_id)
            print(f"  Seeded {holding.ticker} -> Notion page {page_id}")
        else:
            print(f"  Warning: failed to seed {holding.ticker} into Notion")

    print("\nNotion setup complete! Run `make smoke-live` to verify OpenRouter + Notion end to end.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create Cockpit Log & Holdings Watchlist databases in Notion.")
    parser.add_argument("--write-env", action="store_true", help="Write created database IDs directly into .env")
    args = parser.parse_args()
    asyncio.run(run_setup(write_env=args.write_env))


if __name__ == "__main__":
    main()
