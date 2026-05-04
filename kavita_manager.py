import os
import requests
import json
from pathlib import Path

# --- 除錯與載入環境變數 ---
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

def authenticate():
    auth_url = f"{KAVITA_URL}/api/Plugin/authenticate"
    response = requests.post(
        auth_url, 
        params={"apiKey": API_KEY, "pluginName": "AutoReadingListScript"}
    )
    response.raise_for_status()
    token = response.json().get("token")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

def get_all_series(headers):
    url = f"{KAVITA_URL}/api/Series/all-v2"
    payload = {"statements": [], "combination": 0, "sortOptions": {"sortField": 1, "isAscending": True}, "limitTo": 0}
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()

def get_reading_lists(headers):
    url = f"{KAVITA_URL}/api/ReadingList/lists"
    params = {"PageNumber": 1, "PageSize": 1000}
    response = requests.post(url, headers=headers, json={}, params=params)
    response.raise_for_status()
    data = response.json()
    return data["items"] if isinstance(data, dict) and "items" in data else data

def delete_reading_list(headers, list_id):
    url = f"{KAVITA_URL}/api/ReadingList"
    response = requests.delete(url, headers=headers, params={"readingListId": list_id})
    response.raise_for_status()

def get_collections(headers):
    url = f"{KAVITA_URL}/api/Collection"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()

def update_collection_for_series(headers, collection_id, collection_title, series_ids):
    url = f"{KAVITA_URL}/api/Collection/update-for-series"
    payload = {
        "collectionTagId": collection_id,
        "collectionTagTitle": collection_title,
        "seriesIds": series_ids
    }
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()

def main():
    if not API_KEY:
        print("錯誤：找不到有效的 API Key！請檢查 .env")
        return

    headers = authenticate()
    
    # 1. 清理書單
    print("正在清理之前建立的書單 (Reading Lists)...")
    try:
        reading_lists = get_reading_lists(headers)
        target_names = ['資訊技術', 'others', '良好習慣', 'TCP研究', '00. 考試相關資料', 
                        '心靈成長', '財經', '天界之舟', '生活品味', '修行金句', 'tianyao_books', '未分類']
        for lst in reading_lists:
            if lst["title"] in target_names:
                print(f" -> 刪除書單: {lst['title']}")
                delete_reading_list(headers, lst["id"])
    except Exception as e:
        print(f"清理書單時發生錯誤: {e}")

    # 2. 取得書籍並分類 (第一層資料夾)
    print("\n正在從 Kavita 取得所有的書籍/系列...")
    series_list = get_all_series(headers)
    print(f"共找到 {len(series_list)} 個系列。")

    folder_groups = {}
    print("正在分析第一層資料夾結構...")
    for series in series_list:
        folder_path = series.get("folderPath")
        if not folder_path: continue
        
        parts = Path(folder_path).parts
        if len(parts) > 2:
            category = parts[2]
        elif len(parts) == 2:
            category = "未分類"
        else:
            continue
            
        if category in ["tianyao_books", "/", ""]: continue

        if category not in folder_groups: folder_groups[category] = []
        folder_groups[category].append(series["id"])

    if not folder_groups:
        print("找不到任何資料夾資訊。")
        return

    # 3. 同步到收藏
    print("正在取得現有的收藏 (Collections)...")
    collections = get_collections(headers)
    existing_collections_map = {c["title"]: c["id"] for c in collections}

    for folder_name, series_ids in folder_groups.items():
        print(f"\n處理資料夾群組: '{folder_name}' (共 {len(series_ids)} 本書)")
        collection_id = existing_collections_map.get(folder_name, 0)
        try:
            update_collection_for_series(headers, collection_id, folder_name, series_ids)
            print(f" -> ✅ 成功同步收藏 '{folder_name}'")
        except Exception as e:
            print(f" -> ❌ 無法更新收藏 '{folder_name}': {e}")
        
    print("\n✅ 所有作業已完成！")

if __name__ == "__main__":
    main()