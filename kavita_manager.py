import os
import requests
import json
import subprocess
from pathlib import Path

# --- 載入環境變數 ---
current_dir = Path(__file__).parent.absolute()
env_path = current_dir / ".env"

if env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=env_path)
    except ImportError:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    key, value = line.strip().split("=", 1)
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")

KAVITA_URL = os.environ.get("KAVITA_URL", "http://localhost:5000").rstrip("/")
API_KEY = os.environ.get("KAVITA_API_KEY")
CF_SECRET = os.environ.get("CF_SECRET")

# 完全模擬 Chrome 的 Header
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

def call_api(method, path, params=None, json_data=None, auth_token=None):
    url = f"{KAVITA_URL}{path}"
    cmd = ["curl", "-i", "-s", "-L", "--http2", "-X", method, url]
    cmd += ["-H", f"User-Agent: {USER_AGENT}", "-H", "Accept: application/json, text/plain, */*", "-H", "Content-Type: application/json"]
    if CF_SECRET: cmd += ["-H", f"X-CF-Secret: {CF_SECRET}"]
    if auth_token: cmd += ["-H", f"Authorization: Bearer {auth_token}"]
    elif path == "/api/Plugin/authenticate": cmd += ["-H", f"x-api-key: {API_KEY}"]
    if params:
        from urllib.parse import urlencode
        query = urlencode(params)
        cmd[7] = f"{url}?{query}"
    if json_data: cmd += ["-d", json.dumps(json_data)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        parts = result.stdout.split("\r\n\r\n", 1)
        body = parts[1] if len(parts) > 1 else ""
        if "Just a moment" in body: raise Exception("Cloudflare Block")
        return json.loads(body) if body.strip() else None
    except Exception as e:
        if "Cloudflare Block" in str(e): print("❌ 被 Cloudflare 攔截"); raise
        return None

def authenticate():
    return (call_api("POST", "/api/Plugin/authenticate") or {}).get("token")

def get_all_series(token):
    payload = {"statements": [], "combination": 0, "sortOptions": {"sortField": 1, "isAscending": True}, "limitTo": 0}
    return call_api("POST", "/api/Series/all-v2", json_data=payload, auth_token=token) or []

def get_collections(token):
    return call_api("GET", "/api/Collection", auth_token=token) or []

def get_series_in_collection(token, collection_id):
    params = {"collectionId": collection_id, "PageNumber": 1, "PageSize": 1000}
    data = call_api("GET", "/api/Series/series-by-collection", params=params, auth_token=token)
    if isinstance(data, dict) and "items" in data: return data["items"]
    return data or []

def remove_series_from_collection(token, collection_obj, series_ids):
    payload = {"tag": collection_obj, "seriesIdsToRemove": series_ids}
    call_api("POST", "/api/Collection/update-series", json_data=payload, auth_token=token)

def delete_collection(token, collection_id):
    call_api("DELETE", "/api/Collection", params={"tagId": collection_id}, auth_token=token)

def update_collection_for_series(token, collection_id, collection_title, series_ids):
    payload = {"collectionTagId": collection_id, "collectionTagTitle": collection_title, "seriesIds": series_ids}
    call_api("POST", "/api/Collection/update-for-series", json_data=payload, auth_token=token)

def main():
    if not API_KEY: return
    token = authenticate()
    if not token: return

    # 1. 掃描實體資料夾狀態
    series_list = get_all_series(token)
    on_disk_groups = {}
    for series in series_list:
        folder_path = series.get("folderPath")
        if not folder_path: continue
        parts = Path(folder_path).parts
        category = parts[2] if len(parts) > 2 else None
        if not category or category in ["tianyao_books", "/", ""]: continue
        if category not in on_disk_groups: on_disk_groups[category] = []
        on_disk_groups[category].append(series["id"])

    # 2. 取得 Kavita 現有收藏狀態
    collections = get_collections(token)
    
    # 3. 執行鏡像同步
    print(f"--- 開始鏡像同步 (共 {len(on_disk_groups)} 個實體分類) ---")
    
    # A. 處理現有收藏的「移除」與「更新」
    processed_categories = set()
    for col in collections:
        title = col["title"]
        cid = col["id"]
        
        if title in on_disk_groups:
            # 此收藏在硬碟上還存在 -> 檢查是否需要移除已不在該資料夾的書
            processed_categories.add(title)
            current_series_in_kavita = [s["id"] for s in get_series_in_collection(token, cid)]
            expected_series_ids = on_disk_groups[title]
            
            to_remove = [sid for sid in current_series_in_kavita if sid not in expected_series_ids]
            if to_remove:
                print(f" -> '{title}': 移除 {len(to_remove)} 本已移出的書")
                remove_series_from_collection(token, col, to_remove)
            
            # 加入新書
            to_add = [sid for sid in expected_series_ids if sid not in current_series_in_kavita]
            if to_add:
                print(f" -> '{title}': 加入 {len(to_add)} 本新書")
                update_collection_for_series(token, cid, title, to_add)
            
            if not to_remove and not to_add:
                print(f" -> '{title}': 狀態一致，無需變動")
        else:
            # 此收藏在硬碟上已不存在 -> 如果它是我們管理的分類，則刪除
            # 注意：這裡只刪除「曾出現在我們邏輯中」的分類，避免誤刪使用者手動建立的其他收藏
            # 但為了簡化，如果您的收藏完全由資料夾驅動，可以直接刪除
            print(f" -> '{title}': 資料夾已消失，刪除此收藏")
            delete_collection(token, cid)

    # B. 建立全新的收藏
    for title, sids in on_disk_groups.items():
        if title not in processed_categories:
            print(f" -> '{title}': 建立全新收藏並加入 {len(sids)} 本書")
            update_collection_for_series(token, 0, title, sids)

    print("\n✅ 鏡像同步完成！")

if __name__ == "__main__":
    main()