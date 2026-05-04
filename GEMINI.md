# books-mgmt

`books-mgmt` is a Python-based utility designed to synchronize physical folder structures with collections in a [Kavita](https://www.kavitareader.com/) library server. It ensures that series are automatically grouped into collections matching their parent directory names.

## Project Overview

- **Purpose:** Mirror physical library categorization into Kavita Collections.
- **Main Technologies:** 
    - Python 3.14+ (managed via `uv`)
    - [Kavita API](https://api.kavitareader.com/index.html)
    - GitHub Actions for automation
- **Architecture:** 
    - Scans all series in Kavita to retrieve their `folderPath`.
    - Parses the category (parent folder) from the path.
    - Updates or creates Kavita Collections to match the identified categories.

## Building and Running

### Prerequisites
- Python 3.14 or higher.
- [uv](https://github.com/astral-sh/uv) recommended for dependency management.

### Setup
1.  **Install dependencies:**
    ```bash
    uv sync
    ```
2.  **Configuration:**
    Copy `.env.example` to `.env` and fill in your Kavita server details:
    ```bash
    cp .env.example .env
    ```
    - `KAVITA_URL`: The full URL of your Kavita instance (e.g., `http://192.168.1.100:5000`).
    - `KAVITA_API_KEY`: Found in Kavita under `Settings -> 3rd Party Clients -> Auth Key`.

### Running
To manually trigger the synchronization:
```bash
uv run kavita_manager.py
```

## Development Conventions

- **API Interaction:** The project uses `curl` via `subprocess` in `kavita_manager.py` to perform API calls. This approach is used to better handle HTTP/2 and bypass certain Cloudflare Managed Challenges that standard `requests` might trigger.
- **Environment Variables:** All sensitive configuration is handled via `.env` files using `python-dotenv`.
- **Automation:** A GitHub Action workflow (`.github/workflows/sync.yml`) is configured to run the synchronization daily.

## Key Files
- `kavita_manager.py`: Contains the main synchronization logic for Kavita Collections.
- `gdrive_sync.py`: Orchestrates EPUB download, TXT conversion, and Google Drive synchronization.
- `main.py`: Placeholder entry point.
- `pyproject.toml`: Project metadata and dependency definitions.
- `.github/workflows/sync.yml`: CI/CD pipeline for scheduled collection syncs.
- `.github/workflows/gdrive_sync.yml`: CI/CD pipeline for scheduled Google Drive TXT syncs.

## Google Drive Synchronization (TXT)

This feature downloads books from Kavita, converts them to plain text, and mirrors them to a specific Google Drive folder.

### Setup
1. **GitHub Secrets**: Ensure the following secrets are configured in your repository (matching the `my-apple-health` setup):
    - `GDRIVE_CLIENT_ID`
    - `GDRIVE_CLIENT_SECRET`
    - `GDRIVE_TOKEN`
    - `GDRIVE_FOLDER_ID` (The target folder for TXT files)
    - `KAVITA_API_KEY`
2. **GitHub Variables**:
    - `KAVITA_URL`

### Efficiency Mechanism
The script uses `rclone` to list files in the target Google Drive folder before processing. If a file named `{Series} - {Chapter}.txt` already exists, the script will skip the download and conversion steps for that book, significantly reducing API calls and bandwidth usage.

### Conversion Logic
EPUB files are parsed using `EbookLib`, and text is extracted from HTML documents using `BeautifulSoup4`. This ensures a lightweight, pure-Python conversion process suitable for GitHub Actions.
