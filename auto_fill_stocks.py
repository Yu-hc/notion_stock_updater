import os
import sys
import time
import requests
import twstock
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

# Environment variables
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("DATABASE_ID")

NOTION_VERSION = "2022-06-28"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": NOTION_VERSION,
}


def find_unpopulated_stocks():
    """Query Notion for records where Ticker exists, but Stock or Sector is empty."""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"

    # Filter: (Stock is empty) OR (Sector is empty)
    payload = {
        "filter": {
            "or": [
                {"property": "Stock", "title": {"is_empty": True}},
                {"property": "Sector", "multi_select": {"is_empty": True}},
            ]
        }
    }

    try:
        response = requests.post(url, json=payload, headers=HEADERS)
        response.raise_for_status()
        return response.json().get("results", [])
    except requests.exceptions.RequestException as exc:
        print(f"[ERROR] Failed to query Notion database: {exc}")
        if response is not None:
            print(f"Response: {response.text}")
        return []


def update_notion_page(page_id, stock_name, sector_names):
    """Write stock name and sector list back to the Notion page, replacing existing values."""
    url = f"https://api.notion.com/v1/pages/{page_id}"

    if isinstance(sector_names, str):
        sector_names = [sector_names]

    cleaned_sector_names = [
        sector_name.strip()
        for sector_name in sector_names
        if sector_name and sector_name.strip()
    ]

    payload = {
        "properties": {
            "Stock": {
                "title": [{"text": {"content": stock_name}}]
            },
            "Sector": {
                "multi_select": [{"name": sector_name} for sector_name in cleaned_sector_names]
            },
        }
    }

    try:
        resp = requests.patch(url, json=payload, headers=HEADERS)
        resp.raise_for_status()
        print(f"[SUCCESS] Updated {stock_name} ({', '.join(cleaned_sector_names) if cleaned_sector_names else 'no sector'})")
    except requests.exceptions.RequestException as exc:
        print(f"[ERROR] Failed to update page {page_id}: {exc}")


def main():
    if not NOTION_TOKEN or not DATABASE_ID:
        print("[CRITICAL] Missing NOTION_TOKEN or DATABASE_ID in environment.")
        sys.exit(1)

    # print("Fetching updated Taiwan stock directory...")
    # twstock.__update_codes()
    
    print("Checking Notion database for new stock entries...")
    pending_pages = find_unpopulated_stocks()

    if not pending_pages:
        print("All stock rows are up-to-date. No action needed.")
        return

    print(f"Found {len(pending_pages)} row(s) to process.")

    for page in pending_pages:
        page_id = page["id"]

        # Extract the Ticker from the Title property
        title_field = page.get("properties", {}).get("Ticker", {}).get("title", [])
        print(f"[DEBUG] Processing page {page_id} with Ticker: {title_field}")
        continue
        if not title_field:
            print(f"[SKIP] Page {page_id} has no Ticker entered.")
            continue

        raw_ticker = title_field[0].get("text", {}).get("content", "").strip()

        # Check stock existence in twstock
        stock_data = twstock.codes.get(raw_ticker)
        if not stock_data:
            print(f"[WARN] Ticker '{raw_ticker}' not recognized by twstock. Skipping.")
            continue

        stock_name = stock_data.name
        industry = stock_data.group or "其他"

        print(f"Processing '{raw_ticker}': {stock_name} | {industry}")
        update_notion_page(page_id, stock_name, industry)

        # Rate limiting: sleep briefly between updates
        time.sleep(0.4)


if __name__ == "__main__":
    main()