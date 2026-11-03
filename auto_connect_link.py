import os
import re
import time
from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

# ---------------------------------------------------------------------------
# 環境變數與資料庫欄位設定
# ---------------------------------------------------------------------------
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DAILY_NOTES_DB_ID = os.getenv("DAILY_NOTES_DATABASE_ID")
STOCK_DB_ID = os.getenv("STOCK_DATABASE_ID")

# 欄位名稱設定
RELATION_PROPERTY_NAME = "Stocks"       # Daily Notes 中連結至 Stock DB 的 Relation 欄位名稱
STOCK_TITLE_PROP_NAME = "Stock"         # Stock DB 的主標題 (Title) 欄位名稱，格式如：台塑（1301）
STOCK_TICKER_PROP_NAME = "Ticker"       # Stock DB 記錄股票代號的欄位名稱，格式如：1301

# 連結替換模式：
# True  -> 替換成 Notion 原生 @Page Mention，畫面會自動顯示為「@台塑（1301）」
# False -> 替換成純文字超連結 (href)，文字顯示為「@台塑（1301）」並帶有底線連結
USE_NATIVE_PAGE_MENTION = True

# 測試模式開關：True = 只印出預計動作不寫入 Notion；False = 實際寫入 Notion
DRY_RUN = False

notion = Client(auth=NOTION_TOKEN)


# ===========================================================================
# 模組 1：資料讀取與 Stock 頁面建立 (以 Ticker 為主)
# ===========================================================================

def extract_property_text(prop: dict) -> str:
    """通用的 Notion 欄位文字提取器 (支援 title, rich_text, number, select, formula)"""
    if not prop:
        return ""
    ptype = prop.get("type")
    if ptype == "title":
        return "".join(c.get("plain_text", "") for c in prop.get("title", [])).strip()
    elif ptype == "rich_text":
        return "".join(c.get("plain_text", "") for c in prop.get("rich_text", [])).strip()
    elif ptype == "number":
        val = prop.get("number")
        return str(val).strip() if val is not None else ""
    elif ptype == "select":
        sel = prop.get("select")
        return sel.get("name", "").strip() if sel else ""
    elif ptype == "formula":
        f = prop.get("formula", {})
        return str(f.get(f.get("type"), "")).strip()
    return ""


def fetch_all_stocks(stock_db_id: str) -> tuple[dict[str, dict], set[str]]:
    """
    抓取 Stock 資料庫中所有項目。
    回傳:
      1. stock_map: { "1301": {"id": page_id, "name": "台塑（1301）"}, ... } (以 Ticker 為主鍵)
      2. valid_page_ids: 所有 Stock Page ID 的集合 (去掉 '-' 的 32 碼格式，用來快速驗證已存在的連結)
    """
    stock_map = {}
    valid_page_ids = set()
    cursor = None

    while True:
        response = notion.databases.query(
            database_id=stock_db_id,
            start_cursor=cursor,
            page_size=100
        )
        for page in response.get("results", []):
            page_id = page["id"]
            clean_page_id = page_id.replace("-", "")
            valid_page_ids.add(clean_page_id)

            props = page.get("properties", {})
            stock_name = extract_property_text(props.get(STOCK_TITLE_PROP_NAME))
            ticker = extract_property_text(props.get(STOCK_TICKER_PROP_NAME))

            # 若 Ticker 欄位未填，嘗試從 stock 標題「台塑（1301）」括號中提取代號作為備援
            if not ticker and stock_name:
                m = re.search(r"[（(]([a-zA-Z0-9.]+)[）)]", stock_name)
                if m:
                    ticker = m.group(1)

            if ticker:
                stock_map[ticker] = {
                    "id": page_id,
                    "name": stock_name or ticker
                }

        cursor = response.get("next_cursor")
        if not response.get("has_more"):
            break

    return stock_map, valid_page_ids


def create_stock_page(stock_db_id: str, ticker: str) -> dict:
    """
    當股票不存在時，在 Stock DB 建立新頁面 (同時填入 stock 標題與 Ticker 欄位)
    回傳 {"id": new_page_id, "name": ticker}
    """
    if ticker.isdigit():
        ticker_num = int(ticker)
    else:
        try:
            ticker_num = float(ticker)
        except ValueError:
            ticker_num = None
    new_page_payload = {
        "parent": {"database_id": stock_db_id},
        "properties": {
            # 主標題欄位 (stock) 暫時填入 ticker，之後可在 Notion 補上中文名稱如「台塑（1301）」
            STOCK_TITLE_PROP_NAME: {
                "title": [{"type": "text", "text": {"content": ticker}}]
            },
            STOCK_TICKER_PROP_NAME: {
                "number": ticker_num
            }
        }
    }

    if DRY_RUN:
        fake_id = f"dry-run-stock-id-{ticker}"
        print(f"    [DRY RUN] Stock DB 無此代號，將建立新 Stock Page: Ticker='{ticker}' -> 模擬 ID: {fake_id}")
        return {"id": fake_id, "name": ticker}

    print(f"    [API 寫入] 正在 Stock DB 建立新股票頁面: Ticker='{ticker}'...")
    response = notion.pages.create(**new_page_payload)
    time.sleep(0.35)
    return {"id": response["id"], "name": ticker}


def get_or_create_stock(ticker: str, stock_map: dict[str, dict], valid_page_ids: set[str], stock_db_id: str) -> dict:
    """以 Ticker 查詢 stock_map，若無則建立新頁面並更新快取"""
    if ticker in stock_map:
        return stock_map[ticker]

    new_stock_info = create_stock_page(stock_db_id, ticker)
    stock_map[ticker] = new_stock_info
    valid_page_ids.add(new_stock_info["id"].replace("-", ""))
    return new_stock_info


# ===========================================================================
# 模組 2：功能一 — TradingView Bookmark 轉換為 Image Block
# ===========================================================================

def convert_tradingview_url_to_png(url: str) -> str | None:
    """
    範例: https://www.tradingview.com/x/LJfQUzAd/ -> https://s3.tradingview.com/snapshots/l/LJfQUzAd.png
    """
    match = re.search(r"tradingview\.com/x/([a-zA-Z0-9]+)", url)
    if match:
        snapshot_id = match.group(1)
        first_char = snapshot_id[0].lower()
        return f"https://s3.tradingview.com/snapshots/{first_char}/{snapshot_id}.png"
    return None


def replace_tradingview_bookmark_with_image(page_id: str, block: dict) -> bool:
    """檢查並將 TradingView Bookmark 替換為 Image Block"""
    if block.get("type") != "bookmark":
        return False

    bookmark_data = block.get("bookmark", {})
    url = bookmark_data.get("url", "")
    png_url = convert_tradingview_url_to_png(url)

    if not png_url:
        return False

    block_id = block["id"]
    caption = bookmark_data.get("caption", [])

    new_image_block = {
        "object": "block",
        "type": "image",
        "image": {
            "type": "external",
            "external": {"url": png_url},
            "caption": caption
        }
    }

    if DRY_RUN:
        print(f"    [DRY RUN] 偵測到 TradingView Bookmark ({block_id}):")
        print(f"              原網址: {url} -> 將替換為 Image: {png_url}")
        return True

    print(f"    [API 寫入] 替換 TradingView Bookmark 為圖片: {png_url}")
    notion.blocks.children.append(
        block_id=page_id,
        after=block_id,
        children=[new_image_block]
    )
    time.sleep(0.35)
    notion.blocks.delete(block_id=block_id)
    time.sleep(0.35)
    return True


# ===========================================================================
# 模組 3：功能二 — 識別 @台塑（1301）既有連結、轉換 @1301 並建立雙向關聯
# ===========================================================================

def build_stock_rich_text_item(stock_info: dict, use_mention: bool = USE_NATIVE_PAGE_MENTION) -> dict:
    """
    產生替換後的 rich_text 物件：
    - use_mention=True:  產生 Notion 原生 @Page Mention（畫面顯示 @台塑（1301））
    - use_mention=False: 產生文字超連結 href（文字顯示 @台塑（1301）並連往該頁面）
    """
    stock_page_id = stock_info["id"]
    display_name = stock_info["name"]  # 例如 "台塑（1301）"

    if use_mention:
        return {
            "type": "mention",
            "mention": {
                "type": "page",
                "page": {"id": stock_page_id}
            }
        }
    else:
        clean_id = stock_page_id.replace("-", "")
        page_url = f"https://www.notion.so/{clean_id}"
        return {
            "type": "text",
            "text": {
                "content": f"@{display_name}",
                "link": {"url": page_url}
            }
        }


def process_rich_text_stock_mentions(
    rich_texts: list,
    stock_map: dict[str, dict],
    valid_page_ids: set[str],
    stock_db_id: str
) -> tuple[list, set[str], bool]:
    """
    掃描 rich_text 陣列：
    1. 若已是原生 @台塑（1301）Mention 或帶有 href 的連結 -> 提取其 Page ID 加入雙向關聯清單，不重複改寫。
    2. 若為純文字 `@1301` 或 `@台塑（1301）` -> 取出代號 `1301`，查詢/新建 Stock Page，並替換為連結。
    """
    new_rich_text = []
    matched_stock_ids = set()
    is_modified = False

    # 匹配兩種純文字寫法：
    # Group 1: @台塑（1301） 或 @台積電(2330) -> 抓括號內的 Ticker
    # Group 2: @1301 或 @2330 -> 直接抓 @ 後面的英數代號
    pattern = re.compile(r"[@＠][^\s@＠（(]*[（(]([a-zA-Z0-9.]+)[）)]|[@＠]([a-zA-Z0-9.]+)")
    for item in rich_texts:
        # -------------------------------------------------------------------
        # 情況 A：已經建立好的 Notion 原生 @Page Mention（例如 @台塑（1301））
        # -------------------------------------------------------------------
        if item.get("type") == "mention":
            mention = item.get("mention", {})
            if mention.get("type") == "page":
                pid = mention.get("page", {}).get("id", "")
                if pid.replace("-", "") in valid_page_ids:
                    matched_stock_ids.add(pid)
            new_rich_text.append(item)
            continue

        # 情況 B：非純文字區塊直接保留
        if item.get("type") != "text":
            new_rich_text.append(item)
            continue

        # -------------------------------------------------------------------
        # 情況 C：已經帶有超連結 (href) 的文字（例如已轉換過的 [@台塑（1301）](url)）
        # -------------------------------------------------------------------
        existing_link = item.get("text", {}).get("link")
        if existing_link and existing_link.get("url"):
            url = existing_link["url"]
            content = item.get("text", {}).get("content", "")
            # 1. 嘗試從網址中提取 32 碼 Notion Page ID
            url_match = re.search(r"([a-fA-F0-9]{32})", url.replace("-", ""))
            if url_match and url_match.group(1) in valid_page_ids:
                matched_stock_ids.add(url_match.group(1))
            else:
                # 2. 嘗試從文字「@台塑（1301）」或「@1301」提取 Ticker
                m = pattern.search(content)
                if m:
                    ticker = m.group(1) or m.group(2)
                    if ticker in stock_map:
                        matched_stock_ids.add(stock_map[ticker]["id"])
            new_rich_text.append(item)
            continue

        # -------------------------------------------------------------------
        # 情況 D：未加連結的純文字，尋找 `@1301` 或 `@台塑（1301）`
        # -------------------------------------------------------------------
        content = item.get("text", {}).get("content", "")
        last_idx = 0

        for match in pattern.finditer(content):
            print('            [偵測到純文字股票代號] 原始內容: "{}" -> 匹配: "{}"'.format(content, match.group(0)))
            start, end = match.span()
            ticker = match.group(1) or match.group(2)  # 取出股票代號如 "1301"

            # 以 Ticker 取得或建立該股票頁面
            stock_info = get_or_create_stock(ticker, stock_map, valid_page_ids, stock_db_id)
            matched_stock_ids.add(stock_info["id"])
            is_modified = True

            # 1. 補入匹配位置前方的純文字
            if start > last_idx:
                new_rich_text.append({
                    "type": "text",
                    "text": {"content": content[last_idx:start]}
                })

            # 2. 插入轉換後的 @台塑（1301）連結或 Mention 物件
            new_rich_text.append(build_stock_rich_text_item(stock_info))
            last_idx = end

        if last_idx == 0:
            new_rich_text.append(item)
        elif last_idx < len(content):
            new_rich_text.append({
                "type": "text",
                "text": {"content": content[last_idx:]}
            })

    return new_rich_text, matched_stock_ids, is_modified


def update_block_stock_links(
    block: dict,
    stock_map: dict[str, dict],
    valid_page_ids: set[str],
    stock_db_id: str
) -> set[str]:
    """檢查單一 Block 的 rich_text，轉換 @Ticker 並回傳此 Block 提及的所有 Stock Page IDs"""
    block_id = block["id"]
    block_type = block["type"]
    block_content = block.get(block_type, {})

    if not isinstance(block_content, dict) or "rich_text" not in block_content:
        return set()
    original_rich_text = block_content["rich_text"]
    updated_rich_text, matched_ids, is_modified = process_rich_text_stock_mentions(
        original_rich_text, stock_map, valid_page_ids, stock_db_id
    )

    if is_modified:
        if DRY_RUN:
            print(f"    [DRY RUN] 將更新 Block ({block_id} | {block_type}) 內文股票連結:")
            print(f"              原內容: {[t.get('plain_text', '') for t in original_rich_text]}")
            print(f"              新結構: {updated_rich_text}")
        else:
            print(f"    [API 寫入] 更新 Block ({block_id}) 內文股票連結...")
            notion.blocks.update(
                block_id=block_id,
                **{block_type: {"rich_text": updated_rich_text}}
            )
            time.sleep(0.35)

    return matched_ids


def update_daily_note_bilateral_relation(page: dict, stock_ids_to_link: set[str], relation_prop_name: str = RELATION_PROPERTY_NAME):
    """將該篇 Daily Note 提及的所有 Stock Page IDs 寫入雙向 Relation 欄位"""
    if not stock_ids_to_link:
        return

    daily_page_id = page["id"]
    properties = page.get("properties", {})

    if relation_prop_name not in properties:
        print(f"    [警告] Daily Note 中找不到名為 '{relation_prop_name}' 的 Relation 欄位！")
        return

    existing_relations = [
        item["id"] for item in properties[relation_prop_name].get("relation", [])
    ]
    existing_clean_set = {rid.replace("-", "") for rid in existing_relations}

    # 將新抓到的 ID (忽略連字號差異) 合併進清單
    combined_ids = list(existing_relations)
    for sid in stock_ids_to_link:
        if sid.replace("-", "") not in existing_clean_set:
            combined_ids.append(sid)
            existing_clean_set.add(sid.replace("-", ""))

    if len(combined_ids) == len(existing_relations):
        print("    [略過] Relation 欄位已包含所有提及的股票，無須更新。")
        return

    if DRY_RUN:
        print(f"    [DRY RUN] 將更新 Daily Note ({daily_page_id}) 的 '{relation_prop_name}' Relation 欄位:")
        print(f"              原有關聯數: {len(existing_relations)} -> 更新後總數: {len(combined_ids)}")
        print(f"              關聯 Page IDs: {combined_ids}")
        return

    print(f"    [API 寫入] 回填 {len(combined_ids)} 檔股票至 Daily Note Relation 欄位...")
    notion.pages.update(
        page_id=daily_page_id,
        properties={
            relation_prop_name: {
                "relation": [{"id": sid} for sid in combined_ids]
            }
        }
    )
    time.sleep(0.35)


# ===========================================================================
# 模組 4：單篇 Daily Note 處理流程與主程式
# ===========================================================================

def fetch_all_daily_notes(daily_notes_db_id: str) -> list[dict]:
    """抓取 Daily Notes 資料庫中所有頁面"""
    pages = []
    cursor = None
    while True:
        response = notion.databases.query(
            database_id=daily_notes_db_id,
            start_cursor=cursor,
            page_size=100
        )
        pages.extend(response.get("results", []))
        cursor = response.get("next_cursor")
        if not response.get("has_more"):
            break
    return pages


def fetch_page_blocks(page_id: str) -> list[dict]:
    """抓取單一頁面下第一層的所有 Blocks"""
    all_blocks = []
    cursor = None
    while True:
        response = notion.blocks.children.list(
            block_id=page_id,
            start_cursor=cursor,
            page_size=100
        )
        all_blocks.extend(response.get("results", []))
        cursor = response.get("next_cursor")
        if not response.get("has_more"):
            break
    return all_blocks


def process_single_daily_note(page: dict, stock_map: dict[str, dict], valid_page_ids: set[str]):
    """處理單篇 Daily Note"""
    daily_page_id = page["id"]

    title_text = "Untitled"
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title" and prop.get("title"):
            title_text = "".join([c.get("plain_text", "") for c in prop["title"]])
            break

    print(f"\n[處理中] Daily Note: {title_text} ({daily_page_id})")

    all_blocks = fetch_page_blocks(daily_page_id)
    total_stock_ids_for_page = set()

    for block in all_blocks:
        block_type = block["type"]
        # print(f"  - Block ID: {block['id']} | Type: {block_type}")
        # print(f"    原始內容: {block.get(block_type, {}).get('rich_text', [])}")
        # 1. 轉換 TradingView Bookmark 為 Image
        if block_type == "bookmark":
            replace_tradingview_bookmark_with_image(daily_page_id, block)
            continue

        # 2. 解析內文 @1301 或已存在的 @台塑（1301），更新連結並收集 Stock Page IDs
        matched_ids = update_block_stock_links(block, stock_map, valid_page_ids, STOCK_DB_ID)
        total_stock_ids_for_page.update(matched_ids)

    # 3. 更新 Daily Note 的雙向 Relation 欄位
    if total_stock_ids_for_page:
        update_daily_note_bilateral_relation(page, total_stock_ids_for_page)


def main():
    print(f"=== 開始執行 (DRY_RUN = {DRY_RUN}) ===")

    print("\n1. 抓取 Stock 資料庫以建立 Ticker 對照表...")
    stock_map, valid_page_ids = fetch_all_stocks(STOCK_DB_ID)
    sample_items = list(stock_map.items())[:5]
    print(f"   已索引 {len(stock_map)} 檔股票，範例: {sample_items}")

    print("\n2. 查詢 Daily Notes 資料庫...")
    daily_notes = fetch_all_daily_notes(DAILY_NOTES_DB_ID)
    print(f"   共取得 {len(daily_notes)} 篇 Daily Notes。")

    print("\n3. 開始遍歷 Daily Notes...")
    for page in daily_notes:
        process_single_daily_note(page, stock_map, valid_page_ids)

    print("\n=== 處理完成！ ===")


if __name__ == "__main__":
    main()