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

def get_all_series(token):
    payload = {"statements": [], "combination": 0, "sortOptions": {"sortField": 1, "isAscending": True}, "limitTo": 0}
    return call_api("POST", "/api/Series/all-v2", json_data=payload, auth_token=token) or []

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

def check_gdrive_file_exists(remote_path):
    """Check if file exists in the specific GDrive path using rclone."""
    parent = str(Path(remote_path).parent)
    target_name = Path(remote_path).name
    
    # Use rclone lsjson to check the parent directory
    cmd = ["rclone", "lsjson", f"{GDRIVE_REMOTE}:{parent}", "--files-only"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return False
        
        files = json.loads(result.stdout)
        return any(f['name'] == target_name for f in files)
    except Exception:
        return False

def upload_to_gdrive(local_path, remote_path):
    """Upload file to GDrive using rclone."""
    cmd = ["rclone", "copyto", str(local_path), f"{GDRIVE_REMOTE}:{remote_path}"]
    subprocess.run(cmd, check=True, capture_output=True)

def main():
    if not API_KEY:
        print("❌ Error: KAVITA_API_KEY not found.")
        return
    
    token = authenticate()
    if not token:
        print("❌ Authentication failed.")
        return
    print("✅ Kavita authenticated.")

    series_list = get_all_series(token)
    total_series = len(series_list)
    print(f"📚 Found {total_series} series in Kavita.")

    for idx, series in enumerate(series_list, 1):
        series_id = series['id']
        series_name = series['name']
        folder_path = series.get("folderPath", "")
        
        # Determine category (folder structure)
        # Assuming path format: /path/to/category/SeriesName
        parts = Path(folder_path).parts
        category = parts[2] if len(parts) > 2 else "Uncategorized"
        if category in ["tianyao_books", "/", ""]: 
            category = "Uncategorized"
        
        print(f"\n[{idx}/{total_series}] Processing: {series_name} (Category: {category})")
        
        volumes = get_series_volumes(token, series_id)
        for volume in volumes:
            chapters = volume.get('chapters', [])
            total_chapters = len(chapters)
            
            for c_idx, chapter in enumerate(chapters, 1):
                chapter_id = chapter['id']
                chapter_name = chapter['title']
                
                # Mirror folder structure: Category / Series Name / Chapter.txt
                remote_path = f"{category}/{series_name}/{chapter_name}.txt"
                
                # Inline progress display
                print(f"  ({c_idx}/{total_chapters}) Checking: {chapter_name}", end="\r", flush=True)
                
                if check_gdrive_file_exists(remote_path):
                    continue
                
                print(f"\n  ({c_idx}/{total_chapters}) 🚀 Syncing: {chapter_name}")
                
                with tempfile.TemporaryDirectory() as tmp_dir:
                    tmp_dir_path = Path(tmp_dir)
                    epub_path = tmp_dir_path / "book.epub"
                    txt_path = tmp_dir_path / "book.txt"
                    
                    try:
                        download_chapter(token, chapter_id, epub_path)
                        epub_to_txt(epub_path, txt_path)
                        upload_to_gdrive(txt_path, remote_path)
                    except Exception as e:
                        print(f"\n  ❌ Error processing {chapter_name}: {e}")

    print("\n✨ All synchronization tasks completed!")

if __name__ == "__main__":
    main()
