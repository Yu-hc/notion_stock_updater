import os
import requests
from dotenv import load_dotenv
import twstock
load_dotenv()  # Load environment variables from .env file

# Set your credentials (or load from environment variables)
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("DATABASE_ID")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}
def update_twstock_directory():
    """Update the local Taiwan stock directory using twstock."""
    print("Updating Taiwan stock directory...")
    twstock.__update_codes()
    print("Taiwan stock directory updated.")
    # print(twstock.codes.get("8027"))


def overwrite_stock_and_sector(page_id, stock_name, sector_name):
    """Replace the current Stock and Sector values on a Notion page."""
    url = f"https://api.notion.com/v1/pages/{page_id}"

    payload = {
        "properties": {
            "Stock": {
                "title": [{"text": {"content": stock_name}}]
            },
            "Sector": {
                "multi_select": [{"name": sector_name}] if sector_name else []
            },
        }
    }

    response = requests.patch(url, json=payload, headers=HEADERS)

    if response.status_code not in (200, 201):
        print(f"❌ Failed to update page {page_id}: {response.status_code} - {response.text}")
        return False

    # print(f"✅ Updated {page_id}: Stock='{stock_name}', Sector='{sector_name}'")
    return True


def check_connection_and_dump_all():
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"

    all_pages = []
    has_more = True
    start_cursor = None

    print("Connecting to Notion database...")

    # Handles pagination in case your database has more than 100 entries
    while has_more:
        payload = {"page_size": 100}
        if start_cursor:
            payload["start_cursor"] = start_cursor

        response = requests.post(url, json=payload, headers=HEADERS)

        if response.status_code == 401:
            print("❌ Authentication Failed: Invalid NOTION_TOKEN.")
            return
        elif response.status_code == 404:
            print("❌ Not Found: Double check DATABASE_ID, and make sure the connection is added via 'Connect to' on the database.")
            return
        elif response.status_code != 200:
            print(f"❌ Error {response.status_code}: {response.text}")
            return

        data = response.json()
        results = data.get("results", [])
        all_pages.extend(results)

        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    print(f"\n✅ Connection Successful! Found {len(all_pages)} total items in the database.\n")
    print("-" * 50)

    # Print out summary of each entry and replace the original Stock/Sector values
    for idx, page in enumerate(all_pages, start=1):
        page_id = page["id"]
        properties = page.get("properties", {})

        ticker = properties.get("Ticker", {}).get("number")
        prop = properties.get("Stock", {}).get("title", [])
        current_stock_name = prop[0]["plain_text"] if prop else ""

        sector_values = properties.get("Sector", {}).get("multi_select", [])
        current_sector_names = [item["name"] for item in sector_values]
        print(f"[{idx}] Page ID: {page_id} | Ticker: {ticker}")
        print(f"    Current Stock: {current_stock_name}")
        print(f"    Current Sector: {current_sector_names}")

        tw_stock_raw = twstock.codes.get(str(ticker))
        tw_stock_name = tw_stock_raw.name if tw_stock_raw else "N/A"
        tw_stock_sector = tw_stock_raw.group if tw_stock_raw else "N/A"
        tw_stock_market = tw_stock_raw.market if tw_stock_raw else "N/A"

        combined_name = f"{tw_stock_name} ({ticker})" if tw_stock_name and ticker is not None else "N/A"

        print(f"    twstock Name: {tw_stock_name}")
        print(f"    twstock Sector: {tw_stock_sector}")
        print(f"    twstock Market: {tw_stock_market}")
        print(f"    Combined Name: {combined_name}")

        overwrite_stock_and_sector(page_id, combined_name, tw_stock_sector)


if __name__ == "__main__":
    update_twstock_directory()
    check_connection_and_dump_all()