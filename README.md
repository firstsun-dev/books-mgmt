# books-mgmt

`books-mgmt` 是一個基於 Python 的自動化工具，旨在同步 [Kavita](https://www.kavitareader.com/) 圖書伺服器的架構，並將圖書內容匯出至 Google Drive。

## 🌟 主要功能

1.  **實體目錄同步至 Kavita 收藏 (Collection Sync)**：
    *   自動掃描 Kavita 中的書籍路徑。
    *   根據書籍的父資料夾名稱，自動在 Kavita 中建立或更新對應的「收藏 (Collection)」。
    *   確保 Kavita 內的分類與您硬碟上的實體目錄結構保持一致。

2.  **Kavita 圖書匯出至 Google Drive (TXT Export)**：
    *   從 Kavita 下載 EPUB 格式書籍。
    *   自動將 EPUB 轉換為純文字 (TXT) 格式。
    *   同步上傳至 Google Drive 的指定資料夾。
    *   **高效率機制**：採用 rclone 快取技術，上傳前會先比對 GDrive，僅同步缺失檔案，避免重複下載與頻寬浪費。
    *   **完整目錄架構**：在 GDrive 上會完整還原 `{收藏}/{系列}/{章節}.txt` 的層級。

## 🛠 核心技術

- **語言**：Python 3.14+
- **套件管理**：[uv](https://github.com/astral-sh/uv)
- **API 互動**：Kavita REST API (使用 `requests`)
- **雲端同步**：[rclone](https://rclone.org/)
- **EPUB 處理**：`EbookLib` + `BeautifulSoup4`
- **自動化**：GitHub Actions (支援 Self-hosted Runner)

## 🚀 快速開始

### 本地開發設定
1.  **安裝 uv**：
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```
2.  **安裝依賴**：
    ```bash
    uv sync
    ```
3.  **配置環境變數**：
    將 `.env.example` 複製為 `.env` 並填入您的 Kavita 資訊。

### GitHub Actions 自動化設定
請在 GitHub Repository 的 **Settings -> Secrets and variables -> Actions** 中設定以下內容：

#### Secrets (機密資訊)
- `KAVITA_API_KEY`: 您的 Kavita API Key。
- `GDRIVE_CLIENT_ID`: Google Drive API Client ID。
- `GDRIVE_CLIENT_SECRET`: Google Drive API Client Secret。
- `GDRIVE_TOKEN`: rclone 授權後產生的 JSON Token。
- `GDRIVE_FOLDER_ID`: Google Drive 上存放圖書的目標資料夾 ID。

#### Variables (變數)
- `KAVITA_URL`: 您的 Kavita 伺服器網址 (例如 `https://books.example.com`)。

## 📅 工作流說明

- **Kavita Collection Sync** (`sync.yml`):
  - 每天凌晨 0 點執行。
  - 負責維持 Kavita 收藏與實體目錄的一致性。
- **Kavita to GDrive TXT Export** (`gdrive_sync.yml`):
  - 每天凌晨 2 點執行。
  - 負責下載書籍、轉換格式並同步至 Google Drive。
  - 預設使用 `self-hosted` runner。

## 📂 檔案結構
- `kavita_manager.py`: 處理 Kavita 收藏同步的邏輯。
- `gdrive_sync.py`: 處理下載、轉換與 GDrive 同步的核心腳本。
- `.github/workflows/`: 定義自動化任務。
- `pyproject.toml`: 專案依賴與設定。
