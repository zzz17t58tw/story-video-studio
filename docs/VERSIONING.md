# 版本管理與一鍵發佈（VERSIONING）

適用：Story Video Studio（1117）。想改版本號、發佈新版、回滾，或之後推上 GitHub 之前，先讀這份。

## 一、三個位置，各管什麼

| 位置 | 角色 | 誰在用 | 規則 |
|---|---|---|---|
| **專案根目錄** `1117/` | 源碼與文件（`app/`、`static/`、`templates/`、`tools/`、`docs/`、`PLAN.md`） | 開發時用 `start.bat`／uvicorn 跑；**git 版控只收這裡** | `projects/`（開發主資料）、`.venv/`、`build/` 都不進 git |
| **`dist/StoryVideoStudio/`** | **你日常實際在用的 exe**（雙擊這個） | 使用者本人 | exe 與 `_internal/` 會被發佈腳本覆寫；**`projects/` 是你的資料，絕不會被清空或覆寫** |
| **`release/StoryVideoStudio/`**＋zip | **乾淨發佈包**（給別人或帶去別台電腦） | 交件、存檔 | 只放一個成果專案《雨夜的便利商店》＋`使用說明.txt`；每次發佈整個重建 |
| **`memory/`** | 程式自己的設定狀態（`gpu-settings.json`，`app/gpu.py` 寫入） | 程式 | 不進 git（機器相關） |

一句話記法：**根目錄＝源碼、dist＝我在用的、release＝交出去的。**

> 資料分離原則：`dist/StoryVideoStudio/projects/` 是使用者資料，
> `release/.../projects/` 只放品質基準專案。兩邊都**不受 git 管**，備份靠整資料夾複製。

## 二、一鍵更新流程

```bash
cd "C:\Users\user\Desktop\hermes work\1117"
python tools/release.py
```

它會依序做（任何一步失敗就停下來並印出原因，不會留下半套）：

1. 讀 `VERSION`、檢查 git 狀態（有不乾淨的變更會警告，但仍繼續；`--allow-dirty` 可省掉警告）
2. `./.venv/Scripts/pyinstaller.exe --noconfirm --distpath build/release-work StoryVideoStudio.spec` → `build/release-work/StoryVideoStudio/`
   （**一定要 `--distpath` 導到暫存**：spec 預設輸出到 `dist/StoryVideoStudio/`，而 `--noconfirm` 會先把整個目標資料夾刪掉重寫，
   直接打包會把使用端的 `projects/` 一起帶走。腳本已固定導到 `build/release-work`，不要手動改成打 dist。）
3. **同步使用端**：先把舊 exe 備份成 `StoryVideoStudio.exe.bak-<版本>`，再覆寫 exe 與整包 `_internal/`；
   `dist/StoryVideoStudio/projects/` 完全不碰，發佈前後會比對專案數，數量不同就直接中止
4. **重建乾淨版**：清空 `release/StoryVideoStudio/`，放入 exe＋`_internal/`＋`projects/project-20261118-022129`（雨夜便利商店）＋`使用說明.txt`
5. 壓 `release/StoryVideoStudio-v<VERSION>-乾淨版.zip`
6. 寫 `docs/RELEASE-NOTES-v<VERSION>.md`（版號、日期、build 時間、exe/zip 的 sha256 與大小、資料夾結構、本版變更摘要、驗證結果）
7. `git add -A && git commit -m "release: v<VERSION> …"`

常用開關：

| 參數 | 用途 |
|---|---|
| `--skip-build` | 不重跑 PyInstaller，用 `build/release-work` 上一次的產出只重建使用端／乾淨版／zip（改說明檔或只改文件時）；>24 小時的舊產物會被拒絕 |
| `--version 0.9.1` | 本次標記成 0.9.1，但**不改** `VERSION` 檔（臨時標記用，建議正式發佈前還是改 `VERSION`） |
| `--no-zip` | 跳過壓 zip（快速驗證） |
| `--no-commit` | 最後不做 git commit（自己看過再提交） |
| `--allow-dirty` | git 工作區有變更時不加警告 |

發佈前若要開著程式：**先關掉 StoryVideoStudio.exe**，否則 exe 正被鎖住，腳本會直接停下來告訴你。

發佈後建議抽查：解壓 zip 到臨時目錄 → 跑 `python tools/verify_release.py <zip路徑> <臨時目錄>`
（驗 health 200、只有 1 個專案、`final.mp4` 200 且 bytes 相符、關窗進程消失）。

## 三、版號規則

格式 `主.次.修`（SemVer），寫在 `VERSION` 一行、版號不對齊就發佈腳本會拒絕跑。

- **主**（1.x.x）：資料格式或 API 不相容、使用者需要搬移資料的重大改版
- **次**（x.1.x）：新增功能（例如新的生成引擎、新頁面）
- **修**（x.x.1）：修 bug、調整文案與參數，資料格式不變
- **0.x.y 一律視為預發版**：目前就是 0.9.0，功能面已到 MVP 完成、乾淨包可交付，
  但仍有 CHANGELOG 列出的已知限制，所以還不到 1.0.0。

發佈新版的標準動作：**改 `VERSION` → 在 `CHANGELOG.md` 最上面加一節
`## [x.y.z] - YYYY-MM-DD`（`新增`／`修正`／`已知限制` 三段）→ 跑 `python tools/release.py`**。
腳本會把那一節抓進 `docs/RELEASE-NOTES-v<x.y.z>.md`，所以 CHANGELOG 沒寫就會在發佈紀錄裡看到「尚無段落」的提醒。

## 四、怎麼回滾

### 只回到上一版 exe（最常見）

```bash
cd "C:\Users\user\Desktop\hermes work\1117\dist\StoryVideoStudio"
mv StoryVideoStudio.exe.bak-0.9.0 StoryVideoStudio.exe   # 直接覆蓋回舊版
```

`_internal/` 若也被新版覆寫而導致開不起來，最保險是從 `git` 取出舊版源碼重打包，
或直接把上一輪的乾淨版資料夾（`release/StoryVideoStudio/`）複製出來用。
你自己的 `projects/` 不在任何一步被動過，所以回滾不會影響資料。

### 回到先前的源碼

```bash
git log --oneline                 # 找要回去的 commit（tag 或 release: 開頭那筆）
git checkout <commit> -- app static tools launcher.py   # 只取回程式碼
python tools/release.py           # 重新打包＋同步＋重建包
```

### 已經發佈出去的乾淨版

下載者手上是 zip；回滾＝把舊 zip（`release/StoryVideoStudio-v<舊版>-乾淨版.zip`）重新發給他，
或直接 `git checkout <舊commit>` 重建。舊 zip 若被覆寫，靠 git 的源碼 commit＋`docs/RELEASE-NOTES-*.md` 的雜湊比對確認版本。

## 五、之後上 GitHub 的策略（下一版才會做）

- **源碼進 repo**：這個根目錄就是 repo（`.gitignore` 已排除 `.venv/`、`build/`、`dist/`、`release/`、`projects/`、`memory/`、日誌、暫存）。
  文件與 `docs/shots/` 截圖（`docs/` 共約 10MB）一併進 repo，作為驗收證據。
- **exe 與 zip 不進 git 歷史**：每個 90MB 的 zip 進歷史會讓 repo 迅速膨脹且無法刪除。
  做法是 GitHub **Release 附件**：每個 tag（`v0.9.0`）附上 `StoryVideoStudio-v0.9.0-乾淨版.zip`，
  正文貼 `docs/RELEASE-NOTES-v0.9.0.md`。
- 建議的後續流程：`git tag -a v0.9.0 -m "…"` → push 分支與 tag → `gh release create v0.9.0 <zip> --notes-file docs/RELEASE-NOTES-v0.9.0.md`。
- 乾淨版要給別人，`projects/` 內的使用者資料**永不**進 repo（已在 `.gitignore` 固定排除，不是靠人記）。

## 六、本目錄相關檔案

| 檔案 | 說明 |
|---|---|
| `VERSION` | 目前版號（一行） |
| `CHANGELOG.md` | Keep a Changelog 格式，繁中；發佈紀錄的內容來源 |
| `tools/release.py` | 一鍵同步發佈（打包→同步 dist→重建 release→zip→紀錄→commit） |
| `tools/verify_release.py` | 乾淨版解壓實測腳本 |
| `docs/RELEASE-NOTES-v*.md` | 每次發佈的自動紀錄（雜湊、大小、結構、變更摘要） |
| `docs/使用說明.txt` | 打進乾淨版的使用說明（給使用者看的） |
