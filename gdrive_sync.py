import os
import subprocess
import json
import tempfile
from pathlib import Path
import warnings
import ebooklib
from ebooklib import epub

# Suppress ebooklib warnings
warnings.filterwarnings('ignore', category=UserWarning)
warnings.filterwarnings('ignore', category=FutureWarning)
from bs4 import BeautifulSoup
import requests

# --- Kavita Configuration ---
KAVITA_URL = os.environ.get("KAVITA_URL", "").rstrip("/")
API_KEY = os.environ.get("KAVITA_API_KEY")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# --- GDrive Configuration (rclone) ---
GDRIVE_REMOTE = os.environ.get("GDRIVE_REMOTE", "gdrive")
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "")

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
    print(f"Downloading chapter {chapter_id} to {dest_path}...")
    response = call_api("GET", "/api/Download/chapter", params={"chapterId": chapter_id}, auth_token=token, stream=True)
    with open(dest_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

def epub_to_txt(epub_path, txt_path):
    print(f"Converting {epub_path} to {txt_path}...")
    book = epub.read_epub(epub_path)
    text_content = []
    
    # Sort items to maintain order if possible
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            soup = BeautifulSoup(item.get_content(), 'html.parser')
            # Get text and clean it up
            text = soup.get_text(separator='\n')
            text_content.append(text)
    
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write('\n\n'.join(text_content))

def check_gdrive_file_exists(filename):
    """Check if file exists in the specific GDrive folder using rclone."""
    cmd = ["rclone", "lsjson", f"{GDRIVE_REMOTE}:", "--files-only"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        files = json.loads(result.stdout)
        return any(f['name'] == filename for f in files)
    except Exception as e:
        print(f"Error checking GDrive: {e}")
        return False

def upload_to_gdrive(local_path, remote_filename):
    """Upload file to GDrive using rclone."""
    print(f"Uploading {local_path} to GDrive as {remote_filename}...")
    cmd = ["rclone", "copyto", str(local_path), f"{GDRIVE_REMOTE}:{remote_filename}"]
    subprocess.run(cmd, check=True)

def main():
    if not API_KEY:
        print("Error: KAVITA_API_KEY not found.")
        return
    
    token = authenticate()
    if not token:
        print("Authentication failed.")
        return
    print("Kavita authenticated.")

    series_list = get_all_series(token)
    print(f"Found {len(series_list)} series.")

    for series in series_list:
        series_id = series['id']
        series_name = series['name']
        print(f"\nProcessing Series: {series_name}")
        
        volumes = get_series_volumes(token, series_id)
        for volume in volumes:
            chapters = volume.get('chapters', [])
            for chapter in chapters:
                chapter_id = chapter['id']
                chapter_name = chapter['title']
                filename = f"{series_name} - {chapter_name}.txt"
                
                # Check if already in GDrive
                if check_gdrive_file_exists(filename):
                    print(f" -> Skipping: {filename} already exists in GDrive.")
                    continue
                
                # Sync process
                with tempfile.TemporaryDirectory() as tmp_dir:
                    tmp_dir_path = Path(tmp_dir)
                    epub_path = tmp_dir_path / "book.epub"
                    txt_path = tmp_dir_path / "book.txt"
                    
                    try:
                        download_chapter(token, chapter_id, epub_path)
                        epub_to_txt(epub_path, txt_path)
                        upload_to_gdrive(txt_path, filename)
                        print(f" -> Successfully synced: {filename}")
                    except Exception as e:
                        print(f" -> Error processing {filename}: {e}")

if __name__ == "__main__":
    main()
