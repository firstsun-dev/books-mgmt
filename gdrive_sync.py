import os
import re
import subprocess
import sys
import json
import tempfile
from pathlib import Path
import warnings
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
from pypdf import PdfReader
from pypdf.errors import DependencyError, PyPdfError
import requests

# --- Load Environment Variables ---
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

# --- Kavita Configuration ---
KAVITA_URL = os.environ.get("KAVITA_URL", "").rstrip("/")
API_KEY = os.environ.get("KAVITA_API_KEY")
USER_AGENT = "KavitaSyncScript/1.0"

# --- GDrive Configuration (rclone) ---
GDRIVE_REMOTE = os.environ.get("GDRIVE_REMOTE", "gdrive")
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID")

# 建立 rclone 的基礎路徑。如果提供了 Folder ID，則將路徑鎖定在該資料夾內。
if GDRIVE_FOLDER_ID:
    GDRIVE_ROOT = f"{GDRIVE_REMOTE},root_folder_id={GDRIVE_FOLDER_ID}:"
else:
    GDRIVE_ROOT = f"{GDRIVE_REMOTE}:"

# Global cache for existing files in GDrive to speed up checks
gdrive_files_cache = set()

# Tracks remote_path -> owning chapter_id for this run, so two different
# chapters that sanitize to the same name don't silently overwrite each other.
_remote_path_owners = {}


class BookProcessingError(Exception):
    """A single book failed to download/convert/upload for a reportable reason.

    Raising this (instead of letting a raw exception propagate) lets main()
    skip the offending book, keep processing the rest, and still surface the
    failure in the final summary and exit code.
    """


# --- Path sanitization ---
#
# Kavita metadata (collection titles, series names, chapter titles) is
# user-controlled free text and is used verbatim to build rclone remote
# paths. A literal '/' or '\\' in a title is indistinguishable from an
# intentional path separator, and control characters (including NUL) crash
# subprocess/path APIs outright. sanitize_path_component() must be applied to
# every such value BEFORE it is combined into a remote_path.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
FULLWIDTH_SOLIDUS = "／"  # U+FF0F, replaces ASCII '/'
FULLWIDTH_BACKSLASH = "＼"  # U+FF3C, replaces ASCII '\'


def sanitize_path_component(name, fallback="_untitled_"):
    """Make `name` safe to use as a single rclone remote path component.

    - Strips control characters (incl. NUL) that crash filesystem/subprocess APIs.
    - Replaces '/' and '\\' with their fullwidth lookalikes so a literal slash
      in a title can never be mistaken for a path separator.
    - Collapses '.', '..', and empty/whitespace-only names to `fallback`,
      since those would otherwise reference the current/parent directory.

    Deliberately does NOT apply unicode normalization (e.g. NFKC) afterwards:
    NFKC folds the fullwidth slash/backslash back into their ASCII originals,
    which would silently reintroduce the exact bug this function exists to fix.
    Applying this function twice to its own output is a no-op (idempotent).
    """
    text = "" if name is None else str(name)
    text = _CONTROL_CHARS_RE.sub("", text)
    text = text.replace("/", FULLWIDTH_SOLIDUS).replace("\\", FULLWIDTH_BACKSLASH)
    text = text.strip()
    if text in ("", ".", ".."):
        return fallback
    return text


def build_file_name(series_name, chapter_name):
    """Compose the export filename from (already-sanitized) series/chapter names."""
    if series_name == chapter_name:
        return f"{series_name}.txt"
    return f"{series_name} - {chapter_name}.txt"


def resolve_remote_path(col_title, file_name, chapter_id):
    """Sanitize col_title/file_name into a remote_path and guard against collisions.

    Two different Kavita chapters can sanitize to the same remote path (e.g.
    one title uses '/' and another already used the fullwidth '／'). Rather
    than letting the second silently overwrite the first on upload, the
    second occurrence is deterministically suffixed with its chapter_id. This
    only tracks collisions within a single run - it's a last-resort guard
    against distinct source titles colliding, not a rename of the original.
    """
    safe_col = sanitize_path_component(col_title)
    safe_file = sanitize_path_component(file_name)
    remote_path = f"{safe_col}/{safe_file}"

    owner = _remote_path_owners.get(remote_path)
    if owner is not None and owner != chapter_id:
        stem, ext = os.path.splitext(safe_file)
        disambiguated = f"{stem} ({chapter_id}){ext}"
        remote_path = f"{safe_col}/{disambiguated}"
        print(
            f"⚠️ Filename collision: '{file_name}' in collection '{col_title}' "
            f"normalizes to a name already used by another chapter. "
            f"Disambiguating to: {remote_path}"
        )

    _remote_path_owners[remote_path] = chapter_id
    return remote_path


# --- Downloaded content validation ---
#
# Kavita's download endpoint can return a non-2xx body, an empty body, or
# (per chapter_format) bytes that are simply not the format we expect. These
# must be caught by inspecting the actual bytes rather than assuming the
# declared chapter_format is correct, so a corrupt source file is reported
# distinctly from a real code bug.
PDF_MAGIC = b"%PDF-"
EPUB_ZIP_MAGIC = b"PK"  # EPUB is a zip container


def validate_downloaded_file(path, chapter_format):
    """Confirm the downloaded file exists, is non-empty, and has a plausible
    magic header for the declared Kavita format (3=Epub, 4=Pdf)."""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        raise BookProcessingError("downloaded file is missing or empty (Kavita download failed)")

    with open(path, "rb") as f:
        header = f.read(8)

    if chapter_format == 4:
        if not header.startswith(PDF_MAGIC):
            raise BookProcessingError(f"not a valid PDF file (unexpected header {header!r})")
    elif chapter_format == 3:
        if not header.startswith(EPUB_ZIP_MAGIC):
            raise BookProcessingError(f"not a valid EPUB/zip archive (unexpected header {header!r})")


def validate_text_output(txt_path, min_chars=20):
    """Reject an export that is empty or near-empty (e.g. a scan-only PDF with
    no extractable text layer), instead of silently uploading a blank file."""
    text = Path(txt_path).read_text(encoding="utf-8", errors="ignore")
    if len(text.strip()) < min_chars:
        raise BookProcessingError(
            f"extracted text too short ({len(text.strip())} chars) - "
            "likely a scan-only/image PDF with no text layer (unsupported, OCR not implemented)"
        )

def call_api(method, path, params=None, json_data=None, auth_token=None, download_path=None):
    final_url = f"{KAVITA_URL}{path}"
    if params:
        from urllib.parse import urlencode
        final_url += f"?{urlencode(params)}"
    
    # 使用 -i 包含 Header，且不要用 text=True 以免二進位檔案損壞
    cmd = ["curl", "-i", "-s", "-L", "-X", method, final_url]
    cmd += ["-H", f"User-Agent: {USER_AGENT}"]
    cmd += ["-H", "Accept: application/json, text/plain, */*"]
    
    if auth_token: cmd += ["-H", f"Authorization: Bearer {auth_token}"]
    
    if json_data:
        cmd += ["-H", "Content-Type: application/json"]
        cmd += ["-d", json.dumps(json_data)]
    
    try:
        # 重要：使用 capture_output=True 但不要 text=True
        result = subprocess.run(cmd, capture_output=True, check=True)
        raw_output = result.stdout
        
        # 拆分 Header 和 Body (二進位處理)
        if b"\r\n\r\n" in raw_output:
            parts = raw_output.rsplit(b"\r\n\r\n", 1)
        elif b"\n\n" in raw_output:
            parts = raw_output.rsplit(b"\n\n", 1)
        else:
            parts = [raw_output, b""]
            
        headers_last = parts[0].decode('utf-8', errors='ignore')
        body = parts[1]
        
        # 提取最後一個狀態碼
        status_line = "Unknown Status"
        for line in reversed(headers_last.splitlines()):
            if line.startswith("HTTP/"):
                status_line = line
                break
        
        if b"Just a moment" in body or "403 Forbidden" in status_line:
            print(f"DEBUG: API blocked by Cloudflare or Forbidden at {path}. Status: {status_line}")
            if body: print(f"DEBUG: Response body: {body.decode('utf-8', errors='ignore')[:500]}")
            return None

        if download_path:
            if "200" not in status_line:
                print(f"DEBUG: Download failed for {path}. Status: {status_line}")
                if body: print(f"DEBUG: Response body: {body.decode('utf-8', errors='ignore')[:500]}")
                return False
            with open(download_path, "wb") as f:
                f.write(body)
            return True

        if not body.strip(): 
            if "200" not in status_line:
                print(f"DEBUG: API Error at {path}. Status: {status_line}")
            return None
        try:
            return json.loads(body.decode('utf-8'))
        except Exception as e:
            print(f"DEBUG: JSON decode error at {path}: {e}")
            print(f"DEBUG: Response body snippet: {body.decode('utf-8', errors='ignore')[:500]}")
            return None
    except Exception as e:
        print(f"DEBUG: subprocess error: {e}")
        return None

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
    """Returns True only if Kavita responded with a 200 and the body was written."""
    return bool(call_api("GET", "/api/Download/chapter", params={"chapterId": chapter_id}, auth_token=token, download_path=dest_path))

def epub_to_txt(epub_path, txt_path):
    try:
        book = epub.read_epub(epub_path)
    except Exception as e:
        raise BookProcessingError(f"invalid/corrupt EPUB (zip) archive: {e}") from e

    text_content = []
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            soup = BeautifulSoup(item.get_content(), 'html.parser')
            text = soup.get_text(separator='\n')
            text_content.append(text)

    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write('\n\n'.join(text_content))

def pdf_to_txt(pdf_path, txt_path):
    try:
        reader = PdfReader(pdf_path)
        if reader.is_encrypted:
            # Only ever try a blank/empty user password (e.g. PDFs that
            # restrict printing/editing but don't actually password-protect
            # content). Never attempts to guess or crack a real password.
            try:
                reader.decrypt("")
            except Exception as e:
                raise BookProcessingError(
                    f"PDF is password-protected and could not be opened with a blank password: {e}"
                ) from e
        text_content = [page.extract_text() or "" for page in reader.pages]
    except BookProcessingError:
        raise
    except DependencyError as e:
        raise BookProcessingError(f"PDF requires a missing dependency to decode: {e}") from e
    except PyPdfError as e:
        raise BookProcessingError(f"invalid/corrupt PDF structure: {e}") from e

    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write('\n\n'.join(text_content))

def init_gdrive_cache():
    """Build a global cache of all existing TXT files in GDrive for fast lookups."""
    print(f"🔍 Scanning Google Drive ({GDRIVE_ROOT}) for existing files...")
    # Hide the root_folder_id for security if possible, or just print the command
    cmd = ["rclone", "lsf", "-R", "--files-only", GDRIVE_ROOT]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            lines = result.stdout.splitlines()
            for line in lines:
                gdrive_files_cache.add(line.strip())
            print(f"✅ Found {len(gdrive_files_cache)} files in GDrive.")
        else:
            print(f"⚠️ Could not initialize GDrive cache. Return code: {result.returncode}")
            if result.stderr:
                print(f"DEBUG: rclone error: {result.stderr.strip()}")
            print("Will perform live checks.")
    except Exception as e:
        print(f"⚠️ Error initializing GDrive cache: {e}")

def check_gdrive_file_exists(remote_path):
    """Check if file exists using the pre-built cache or a live check if cache is empty."""
    if remote_path in gdrive_files_cache:
        return True
    
    # Fallback to live check if cache was not initialized or for newly uploaded files
    parent = str(Path(remote_path).parent)
    target_name = Path(remote_path).name
    cmd = ["rclone", "lsjson", f"{GDRIVE_ROOT}{parent}", "--files-only"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0: return False
        files = json.loads(result.stdout)
        return any(f['name'] == target_name for f in files)
    except Exception:
        return False

def upload_to_gdrive(local_path, remote_path):
    """Upload file to GDrive using rclone."""
    cmd = ["rclone", "copyto", str(local_path), f"{GDRIVE_ROOT}{remote_path}"]
    subprocess.run(cmd, check=True, capture_output=True)
    # Update cache
    gdrive_files_cache.add(remote_path)

def main():
    if not API_KEY:
        print("❌ Error: KAVITA_API_KEY not found.")
        sys.exit(1)

    token = authenticate()
    if not token:
        print("❌ Authentication failed.")
        sys.exit(1)
    print("✅ Kavita authenticated.")

    init_gdrive_cache()

    collections = get_collections(token)
    total_collections = len(collections)
    print(f"📂 Found {total_collections} collections in Kavita.")

    if total_collections == 0:
        print("⚠️ No collections found. Please check if Kavita has collections and if API access is correct.")

    stats = {"uploaded": 0, "skipped": 0, "failed": 0}
    failures = []  # list of (series - chapter, reason)

    for c_idx, col in enumerate(collections, 1):
        col_id = col['id']
        col_title = col['title']

        series_in_col = get_series_in_collection(token, col_id)
        total_series = len(series_in_col)

        print(f"\n[{c_idx}/{total_collections}] 📂 Collection: {col_title} (ID: {col_id}, {total_series} series)")

        if total_series == 0:
            print(f"  ⚠️ No series found in collection '{col_title}'.")
            continue

        for s_idx, series in enumerate(series_in_col, 1):
            series_id = series['id']
            series_name = series['name']

            volumes = get_series_volumes(token, series_id)
            print(f"  [{s_idx}/{total_series}] Processing Series: {series_name} ({len(volumes)} volumes)")

            for volume in volumes:
                chapters = volume.get('chapters', [])
                total_chapters = len(chapters)
                print(f"    📖 Volume: {volume.get('name', 'Unknown')} ({total_chapters} chapters)")

                for ch_idx, chapter in enumerate(chapters, 1):
                    chapter_id = chapter['id']
                    chapter_name = chapter['title']
                    chapter_format = chapter.get('format') # 3=Epub, 4=Pdf

                    # Kavita formats: 3=Epub, 4=Pdf
                    if chapter_format not in [3, 4]:
                        continue

                    # Sanitize BEFORE composing/using the name anywhere (a raw
                    # '/' or NUL byte in these titles previously broke rclone
                    # paths and crashed subprocess calls - see sanitize_path_component).
                    safe_series_name = sanitize_path_component(series_name)
                    safe_chapter_name = sanitize_path_component(chapter_name)
                    file_name = build_file_name(safe_series_name, safe_chapter_name)
                    remote_path = resolve_remote_path(col_title, file_name, chapter_id)

                    # Inline progress
                    print(f"    ({ch_idx}/{total_chapters}) Checking: {chapter_name}", end="\r", flush=True)

                    if check_gdrive_file_exists(remote_path):
                        stats["skipped"] += 1
                        continue

                    print(f"\n    ({ch_idx}/{total_chapters}) 🚀 Syncing: {chapter_name}")

                    with tempfile.TemporaryDirectory() as tmp_dir:
                        tmp_dir_path = Path(tmp_dir)
                        # 根據格式決定暫存檔名: 3=Epub, 4=Pdf
                        book_filename = "book.epub" if chapter_format == 3 else "book.pdf"
                        book_path = tmp_dir_path / book_filename
                        txt_path = tmp_dir_path / "book.txt"

                        try:
                            ok = download_chapter(token, chapter_id, book_path)
                            if not ok:
                                raise BookProcessingError("Kavita download request failed (non-200 response)")
                            validate_downloaded_file(book_path, chapter_format)
                            if chapter_format == 3:
                                epub_to_txt(book_path, txt_path)
                            else:
                                pdf_to_txt(book_path, txt_path)
                            validate_text_output(txt_path)
                            upload_to_gdrive(txt_path, remote_path)
                            stats["uploaded"] += 1
                        except BookProcessingError as e:
                            print(f"\n    ❌ Error processing {chapter_name}: {e}")
                            stats["failed"] += 1
                            failures.append((f"{series_name} - {chapter_name}", str(e)))
                        except Exception as e:
                            print(f"\n    ❌ Unexpected error processing {chapter_name}: {e}")
                            stats["failed"] += 1
                            failures.append((f"{series_name} - {chapter_name}", f"unexpected: {e}"))

    print("\n📊 Summary")
    print(f"   ✅ Uploaded: {stats['uploaded']}")
    print(f"   ⏭️  Skipped (already in GDrive): {stats['skipped']}")
    print(f"   ❌ Failed: {stats['failed']}")
    if failures:
        print("\n❌ Failed books:")
        for title, reason in failures:
            print(f"   - {title}: {reason}")

    print("\n✨ All synchronization tasks completed!")

    if stats["failed"] > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
