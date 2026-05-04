import os
import subprocess
import json
import tempfile
from pathlib import Path
import warnings
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import requests

# Suppress ebooklib warnings
warnings.filterwarnings('ignore', category=UserWarning)
warnings.filterwarnings('ignore', category=FutureWarning)

# --- Kavita Configuration ---
KAVITA_URL = os.environ.get("KAVITA_URL", "").rstrip("/")
API_KEY = os.environ.get("KAVITA_API_KEY")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# --- GDrive Configuration (rclone) ---
GDRIVE_REMOTE = os.environ.get("GDRIVE_REMOTE", "gdrive")

# Global cache for existing files in GDrive to speed up checks
gdrive_files_cache = set()

def call_api(method, path, params=None, json_data=None, auth_token=None, stream=False):
    final_url = f"{KAVITA_URL}{path}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json"
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    
    response = requests.request(method, final_url, params=params, json=json_data, headers=headers, stream=stream)
    response.raise_for_status()
    
    if stream:
        return response
    return response.json() if response.content else None

def authenticate():
    params = {"apiKey": API_KEY, "pluginName": "GdriveSyncScript"}
    data = call_api("POST", "/api/Plugin/authenticate", params=params)
    return data.get("token") if data else None

def get_collections(token):
    return call_api("GET", "/api/Collection", auth_token=token) or []

def get_series_in_collection(token, collection_id):
    params = {"collectionId": collection_id, "PageNumber": 1, "PageSize": 1000}
    data = call_api("GET", "/api/Series/series-by-collection", params=params, auth_token=token)
    if isinstance(data, dict) and "items" in data: return data["items"]
    return data or []

def get_series_volumes(token, series_id):
    return call_api("GET", f"/api/Series/volumes", params={"seriesId": series_id}, auth_token=token) or []

def download_chapter(token, chapter_id, dest_path):
    response = call_api("GET", "/api/Download/chapter", params={"chapterId": chapter_id}, auth_token=token, stream=True)
    with open(dest_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

def epub_to_txt(epub_path, txt_path):
    book = epub.read_epub(epub_path)
    text_content = []
    
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            soup = BeautifulSoup(item.get_content(), 'html.parser')
            text = soup.get_text(separator='\n')
            text_content.append(text)
    
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write('\n\n'.join(text_content))

def init_gdrive_cache():
    """Build a global cache of all existing TXT files in GDrive for fast lookups."""
    print("🔍 Scanning Google Drive for existing files (this may take a moment)...")
    cmd = ["rclone", "lsf", "-R", "--files-only", f"{GDRIVE_REMOTE}:"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                gdrive_files_cache.add(line.strip())
            print(f"✅ Found {len(gdrive_files_cache)} files in GDrive.")
        else:
            print("⚠️ Could not initialize GDrive cache. Will perform live checks.")
    except Exception as e:
        print(f"⚠️ Error initializing GDrive cache: {e}")

def check_gdrive_file_exists(remote_path):
    """Check if file exists using the pre-built cache or a live check if cache is empty."""
    if remote_path in gdrive_files_cache:
        return True
    
    # Fallback to live check if cache was not initialized or for newly uploaded files
    parent = str(Path(remote_path).parent)
    target_name = Path(remote_path).name
    cmd = ["rclone", "lsjson", f"{GDRIVE_REMOTE}:{parent}", "--files-only"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0: return False
        files = json.loads(result.stdout)
        return any(f['name'] == target_name for f in files)
    except Exception:
        return False

def upload_to_gdrive(local_path, remote_path):
    """Upload file to GDrive using rclone."""
    cmd = ["rclone", "copyto", str(local_path), f"{GDRIVE_REMOTE}:{remote_path}"]
    subprocess.run(cmd, check=True, capture_output=True)
    # Update cache
    gdrive_files_cache.add(remote_path)

def main():
    if not API_KEY:
        print("❌ Error: KAVITA_API_KEY not found.")
        return
    
    token = authenticate()
    if not token:
        print("❌ Authentication failed.")
        return
    print("✅ Kavita authenticated.")

    init_gdrive_cache()

    collections = get_collections(token)
    total_collections = len(collections)
    print(f"📂 Found {total_collections} collections in Kavita.")

    for c_idx, col in enumerate(collections, 1):
        col_id = col['id']
        col_title = col['title']
        
        series_in_col = get_series_in_collection(token, col_id)
        total_series = len(series_in_col)
        
        print(f"\n[{c_idx}/{total_collections}] 📂 Collection: {col_title} ({total_series} series)")
        
        for s_idx, series in enumerate(series_in_col, 1):
            series_id = series['id']
            series_name = series['name']
            
            print(f"  [{s_idx}/{total_series}] Processing Series: {series_name}")
            
            volumes = get_series_volumes(token, series_id)
            for volume in volumes:
                chapters = volume.get('chapters', [])
                total_chapters = len(chapters)
                
                for ch_idx, chapter in enumerate(chapters, 1):
                    chapter_id = chapter['id']
                    chapter_name = chapter['title']
                    
                    # Hierarchy: Collection / Series / Chapter.txt
                    remote_path = f"{col_title}/{series_name}/{chapter_name}.txt"
                    
                    # Inline progress
                    print(f"    ({ch_idx}/{total_chapters}) Checking: {chapter_name}", end="\r", flush=True)
                    
                    if check_gdrive_file_exists(remote_path):
                        continue
                    
                    print(f"\n    ({ch_idx}/{total_chapters}) 🚀 Syncing: {chapter_name}")
                    
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        tmp_dir_path = Path(tmp_dir)
                        epub_path = tmp_dir_path / "book.epub"
                        txt_path = tmp_dir_path / "book.txt"
                        
                        try:
                            download_chapter(token, chapter_id, epub_path)
                            epub_to_txt(epub_path, txt_path)
                            upload_to_gdrive(txt_path, remote_path)
                        except Exception as e:
                            print(f"\n    ❌ Error processing {chapter_name}: {e}")

    print("\n✨ All synchronization tasks completed!")

if __name__ == "__main__":
    main()
