import os
import requests
import json
import subprocess
from pathlib import Path
from urllib.parse import urlparse

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

# 極度擬真的 Chrome Headers
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

def call_api(method, path, params=None, json_data=None, auth_token=None):
    url = f"{KAVITA_URL}{path}"
    
    # 基礎 curl 指令 (加入 -i 以取得 Response Headers)
    # --http2: 模擬現代瀏覽器行為
    # -L: 跟隨重定向
    cmd = ["curl", "-i", "-s", "-L", "--http2", "-X", method, url]
    
    # 偽裝標頭
    cmd += ["-H", f"User-Agent: {USER_AGENT}"]
    cmd += ["-H", "Accept: application/json, text/plain, */*"]
    cmd += ["-H", "Accept-Language: zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"]
    cmd += ["-H", f"Origin: {KAVITA_URL}"]
    cmd += ["-H", f"Referer: {KAVITA_URL}/"]
    cmd += ["-H", "Content-Type: application/json"]
    
    if CF_SECRET:
        cmd += ["-H", f"X-CF-Secret: {CF_SECRET}"]
    
    if auth_token:
        cmd += ["-H", f"Authorization: Bearer {auth_token}"]
    elif path == "/api/Plugin/authenticate":
        cmd += ["-H", f"x-api-key: {API_KEY}"]

    if params:
        from urllib.parse import urlencode
        query = urlencode(params)
        cmd[7] = f"{url}?{query}"

    if json_data:
        cmd += ["-d", json.dumps(json_data)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        raw_output = result.stdout
        
        # 分離 Header 與 Body
        parts = raw_output.split("\r\n\r\n", 1)
        headers_raw = parts[0]
        body = parts[1] if len(parts) > 1 else ""
        
        # 取得 CF-Ray ID 方便除錯
        cf_ray = "Unknown"
        for line in headers_raw.split("\n"):
            if line.lower().startswith("cf-ray:"):
                cf_ray = line.split(":", 1)[1].strip()
        
        if "Just a moment" in body:
            print(f"❌ 被 Cloudflare 攔截 (Managed Challenge)")
            print(f"DEBUG: CF-Ray ID: {cf_ray}")
            print("請至 Cloudflare 控制台搜尋此 Ray ID 以查看具體原因。")
            raise Exception("Cloudflare Block")

        if not body.strip():
            return None
            
        return json.loads(body)
        
    except Exception as e:
        if "Cloudflare Block" not in str(e):
            print(f"❌ 請求失敗: {e}")
        raise

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
    payload = {"collectionTagId": collection_id, "collectionTagTitle": collection_title, "seriesIds": series_ids}
    call_api("POST", "/api/Collection/update-for-series", json_data=payload, auth_token=token)

def main():
    if not API_KEY:
        print("錯誤：找不到有效的 API Key！")
        return
    try:
        token = authenticate()
        print("✅ 驗證成功！")
        series_list = get_all_series(token)
        print(f"正在分析資料夾並同步 {len(series_list)} 個系列...")
        
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

        collections = get_collections(token)
        existing_map = {c["title"]: c["id"] for c in collections}

        for folder_name, series_ids in folder_groups.items():
            print(f" -> 同步: '{folder_name}' ({len(series_ids)} 本)")
            cid = existing_map.get(folder_name, 0)
            update_collection_for_series(token, cid, folder_name, series_ids)
        print("\n✅ 所有作業已完成！")
    except:
        pass

if __name__ == "__main__":
    main()