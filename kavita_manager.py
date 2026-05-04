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

KAVITA_URL = os.environ.get("KAVITA_URL", "http://localhost:5000")
API_KEY = os.environ.get("KAVITA_API_KEY")
CF_SECRET = os.environ.get("CF_SECRET")

# 完全模擬 Chrome 的 Header
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

def call_api(method, path, params=None, json_data=None, auth_token=None):
    """
    使用系統的 curl 命令來執行請求，這比 Python requests 更有機會繞過 Cloudflare 的 JA3 指紋檢查。
    """
    url = f"{KAVITA_URL}{path}"
    
    # 構建 curl 指令
    cmd = ["curl", "-s", "-X", method, url]
    
    # 加入基礎 Headers
    cmd += ["-H", f"User-Agent: {USER_AGENT}"]
    cmd += ["-H", "Accept: application/json, text/plain, */*"]
    cmd += ["-H", "Content-Type: application/json"]
    
    # 加入 Cloudflare Bypass Header
    if CF_SECRET:
        cmd += ["-H", f"X-CF-Secret: {CF_SECRET}"]
    
    # 加入 API Key 或 JWT Token
    if auth_token:
        cmd += ["-H", f"Authorization: Bearer {auth_token}"]
    elif path == "/api/Plugin/authenticate":
        cmd += ["-H", f"x-api-key: {API_KEY}"]

    # 加入 Query Parameters
    if params:
        query_str = "&".join([f"{k}={v}" for k, v in params.items()])
        if "?" in url:
            cmd[4] = f"{url}&{query_str}"
        else:
            cmd[4] = f"{url}?{query_str}"

    # 加入 JSON Body
    if json_data:
        cmd += ["-d", json.dumps(json_data)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        if not result.stdout:
            return None
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"DEBUG [API Error]: {e.stderr}")
        raise
    except json.JSONDecodeError:
        # 如果回傳的不是 JSON，可能是 403 挑戰頁面
        if "Just a moment" in result.stdout:
            print("❌ 錯誤：依然被 Cloudflare 攔截 (Managed Challenge)")
        else:
            print(f"❌ 錯誤：回傳內容不是 JSON: {result.stdout[:200]}")
        raise Exception("API 請求失敗")

def authenticate():
    data = call_api("POST", "/api/Plugin/authenticate")
    return data.get("token")

def get_all_series(token):
    payload = {"statements": [], "combination": 0, "sortOptions": {"sortField": 1, "isAscending": True}, "limitTo": 0}
    return call_api("POST", "/api/Series/all-v2", json_data=payload, auth_token=token)

def get_reading_lists(token):
    params = {"PageNumber": 1, "PageSize": 1000}
    data = call_api("POST", "/api/ReadingList/lists", json_data={}, params=params, auth_token=token)
    return data["items"] if isinstance(data, dict) and "items" in data else data

def delete_reading_list(token, list_id):
    call_api("DELETE", "/api/ReadingList", params={"readingListId": list_id}, auth_token=token)

def get_collections(token):
    return call_api("GET", "/api/Collection", auth_token=token)

def update_collection_for_series(token, collection_id, collection_title, series_ids):
    payload = {
        "collectionTagId": collection_id,
        "collectionTagTitle": collection_title,
        "seriesIds": series_ids
    }
    call_api("POST", "/api/Collection/update-for-series", json_data=payload, auth_token=token)

def main():
    if not API_KEY:
        print("錯誤：找不到有效的 API Key！")
        return

    try:
        token = authenticate()
        print("✅ 驗證成功！")
    except Exception:
        return

    # 1. 清理書單
    print("正在清理舊書單...")
    try:
        reading_lists = get_reading_lists(token)
        target_names = ['資訊技術', 'others', '良好習慣', 'TCP研究', '00. 考試相關資料', 
                        '心靈成長', '財經', '天界之舟', '生活品味', '修行金句', 'tianyao_books', '未分類']
        for lst in reading_lists:
            if lst["title"] in target_names:
                print(f" -> 刪除: {lst['title']}")
                delete_reading_list(token, lst["id"])
    except: pass

    # 2. 分類
    print("正在取得系列資訊...")
    series_list = get_all_series(token)
    folder_groups = {}
    for series in series_list:
        folder_path = series.get("folderPath")
        if not folder_path: continue
        parts = Path(folder_path).parts
        if len(parts) > 2: category = parts[2]
        elif len(parts) == 2: category = "未分類"
        else: continue
        if category in ["tianyao_books", "/", ""]: continue
        if category not in folder_groups: folder_groups[category] = []
        folder_groups[category].append(series["id"])

    # 3. 同步收藏
    collections = get_collections(token)
    existing_map = {c["title"]: c["id"] for c in collections}

    for folder_name, series_ids in folder_groups.items():
        print(f"處理: '{folder_name}' ({len(series_ids)} 本)")
        cid = existing_map.get(folder_name, 0)
        update_collection_for_series(token, cid, folder_name, series_ids)
        
    print("\n✅ 同步完成！")

if __name__ == "__main__":
    main()