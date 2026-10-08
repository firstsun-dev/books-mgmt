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
    *   **檔名安全化**：收藏名稱、系列名稱、章節標題皆為 Kavita 中的自由文字，可能包含 `/`、`\`、控制字元等。上傳前會先經過 `sanitize_path_component()` 正規化：
        - `/` 會轉成全角斜線 `／` (U+FF0F)、`\` 轉成全角反斜線 `＼` (U+FF3C)，避免被誤判為路徑分隔符（例如書名 `80/20法則` 會匯出成單一檔案 `80／20法則.txt`，而不是建立出一個多餘的 `80` 資料夾）。
        - 控制字元（包含 NUL）會被移除；單純的 `.` / `..` 或空白名稱會被取代為 `_untitled_`，避免路徑穿越或產生空路徑。
        - 刻意不對取代後的字串做 NFKC 正規化，否則全角斜線會被還原成半角 `/`，重新引入原本的問題。
        - **命名碰撞**：若兩個不同章節正規化後產生相同檔名，只有本次執行中「第一個遇到」的保留原名，之後的會自動加上 `(章節ID)` 後綴並印出警告，不會互相覆蓋、也不會更動正常（未碰撞）書名。這只在單次執行內追蹤，跨執行的碰撞（例如遠端已存在同名檔案）仍以「已存在則跳過」處理，不會自動改名或刪除 Drive 上既有的檔案。
    *   **下載與轉換驗證**：下載後會檢查檔案是否存在、非空，並比對實際的檔案 magic header 是否符合宣告格式（PDF 應為 `%PDF-`、EPUB 應為 zip 的 `PK`），避免把錯誤/空白回應誤判為正常書籍。EPUB 的 zip 損毀、PDF 的結構損毀（如找不到 Root object）、PDF 需要但缺少的 AES 解密依賴（`cryptography`）、純圖片掃描 PDF（擷取文字過短）都會被歸類成明確錯誤並跳過該書，不會上傳空白或錯誤內容，也不會中斷其他書籍的同步。僅會嘗試以空白密碼解密 PDF（常見於僅限制列印/編輯但未設密碼的檔案），不會嘗試破解或繞過真正有密碼保護的 PDF。
    *   **失敗統計與退出碼**：執行結束時會印出成功/跳過/失敗數量與失敗清單；只要有任何書籍失敗，程式會以非 0 狀態碼結束，讓 GitHub Actions 能正確回報失敗（過去即使有書籍失敗，workflow 仍顯示成功）。

## 🛠 核心技術

- **語言**：Python 3.14+
- **套件管理**：[uv](https://github.com/astral-sh/uv)
- **API 互動**：Kavita REST API (使用 `requests`)
- **雲端同步**：[rclone](https://rclone.org/)
- **EPUB 處理**：`EbookLib` + `BeautifulSoup4`
- **PDF 處理**：`pypdf` (+ `cryptography`，用於解密使用 AES 加密的 PDF)
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
- `tests/test_gdrive_sync.py`: 檔名安全化、下載驗證、格式判斷等離線單元測試（`uv run python -m unittest discover -s tests`，不需要網路）。
- `.github/workflows/`: 定義自動化任務。
- `pyproject.toml`: 專案依賴與設定。
