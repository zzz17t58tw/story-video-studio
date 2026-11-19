# Story Video Studio — 桌面版（獨立程式，不開瀏覽器）

## 兩種啟動方式

| 方式 | 指令 | 用途 |
|---|---|---|
| 開發模式 | 雙擊 **`啟動 Story Video Studio.bat`** | 用 `.venv` 跑，改完程式碼直接重開即可 |
| 打包 exe | 雙擊 **`dist\StoryVideoStudio\StoryVideoStudio.exe`** | 給非技術使用者，免裝 Python |
| 瀏覽器備援 | 雙擊 **`start.bat`** | 舊模式（開 http://127.0.0.1:8788） |

三者共用同一套 FastAPI 後端（`app/server.py`），只是外殼不同：桌面版用 pywebview（Edge WebView2）開**原生視窗**。

## 視窗與關閉

- 標題「Story Video Studio」、1280×880、可調整、最小 1024×700。
- 埠自動挑：**8788 → 8789 … 8799** 第一個空的；若該埠已有本程式在跑就直接重用（不會開第二份）。
- **關掉視窗＝完全退出**：uvicorn 收工，6 秒沒收乾淨會強制 `os._exit`，不會留 python 程序在背景。

## 資料夾（很重要）

| 內容 | 位置 | 會不會被打包 |
|---|---|---|
| 程式資源 `static/`、`templates/` | exe 內（`_internal/`） | 會 |
| **專案資料 `projects/`** | **exe 旁**（`dist\StoryVideoStudio\projects\`） | **不會** |

exe 第一次啟動時，若 exe 旁沒有 `projects/` 會自動建立；
**若它是空的，會自動從打包時的專案根 `projects/` 複製一份既有專案過來**（略過 `_trash`）。

> 注意：既有專案含大量圖／影片，複製後 `dist\StoryVideoStudio` 會從 ~41MB 漲到 ~169MB。
> 想省空間又共用同一批資料，改用 `--data-dir` 指回專案根，就不會複製。

想改指向別的資料夾（例如直接沿用開發用的專案）：

```
StoryVideoStudio.exe --data-dir "C:\Users\user\Desktop\hermes work\1117\projects"
```

`--data-dir` 給的就是 **projects 本身**（若給它的父層，程式會自動補 `/projects`）。

其他參數：`--port 8790` 指定起始埠。

## 日誌

`launcher.log` 寫在資料夾的父層：
- exe：`dist\StoryVideoStudio\launcher.log`
- 開發：專案根 `launcher.log`

打不開時先看這支；錯誤也會用 Windows 對話框跳出來。

## 重建 exe

```bash
.venv\Scripts\python.exe -m venv .venv            # 已存在就跳過
.venv\Scripts\python -m pip install fastapi uvicorn httpx pywebview pyinstaller python-multipart tzdata
.venv\Scripts\pyinstaller --noconfirm --clean StoryVideoStudio.spec
# 產出 dist\StoryVideoStudio\StoryVideoStudio.exe
```

> 依賴備註：獨立 Python 環境缺 `python-multipart`（上傳檔案會 500）與 `tzdata`（`Asia/Taipei` 找不到會 500），兩者都要裝；Hermes 的 venv 本來就有，所以開發時容易漏。
> icon 由 `tools/make_icon.py` 用標準庫產生（無需 Pillow）。

## 路徑解析（給改程式的人）

所有靜態路徑集中在 `app/paths.py`：
- `BUNDLE_DIR`：唯讀資源根（dev＝專案根；frozen＝`sys._MEIPASS`）
- `PROJECTS_DIR`：可寫資料（`SVS_DATA_DIR` / `--data-dir` / exe 旁 `projects/`）
- 不要在別的模組寫 `Path(__file__).parent.parent`，打包後會指到 `_internal/app/`。
