# 文件索引（Story Video Studio / 1117）

> **定位**：這是全部文件的唯一入口。任何文件的**位置與用途**都在這裡登記；新增、移動、刪除文件時必須同步更新本表。
>
> **分層原則**（2026-11-25 定案）：程式歸程式、開發工程歸開發工程、測試歸測試、技能歸技能、代理人歸代理人。
> 目標是「功能獨立，又能全盤了解」——每一層可以各自讀，不必翻整包。

---

## 一、分層對照表

| 層 | 位置 | 放什麼 | 不放什麼 |
|---|---|---|---|
| **程式** | `app/` `static/` `templates/` `launcher.py` | 程式本體、前端、工作流模板 | 文件、測試、截圖 |
| **工具** | `tools/` | 開發與發佈會呼叫的工具（發版、交接單、圖示、agent 文件自檢） | 測試案例 |
| **測試** | `tests/` | 測試腳本、夜間巡檢實作、mock 服務 | 正式功能程式 |
| **規格** | `docs/spec/` | 程式的權威規格（資料模型、下游契約、分鏡契約、連動契約） | 開發流程、歷史紀錄 |
| **開發工程** | `docs/dev/` | 版本規則、分支規矩、桌面化筆記、開發歷程、外部調查、待辦 | 程式規格、驗收截圖 |
| **代理人** | `docs/agent/` ＋ 根 `AGENTS.md` | 給外部 AI 的操作手冊、機器可讀 manifest | 對人的說明 |
| **測試證據** | `docs/testing/` | 夜間巡檢報告、驗收截圖、對照影片 | 程式碼 |
| **版本紀錄** | `docs/release/` | 每版 RELEASE-NOTES、Release body | 規格變更的解釋（那是 spec 的事） |
| **交接** | `docs/handoff/` | 分支交接單（一分支一張） | 長期規格 |
| **使用者文件** | `docs/usage/` | 使用說明（會被打包進乾淨版） | 開發者文件 |

**技能（skill）不在本 repo**：操作知識、教訓、驗證手法沉澱在 Hermes 的 `story-video-studio` skill 與其 references；repo 只放程式需要的規格與使用者說明（見 README 說明）。

---

## 二、根目錄（四個入口，各司其職）

| 檔案 | 給誰看 | 用途 |
|---|---|---|
| `README.md` | 一般人／使用者 | 專案是什麼、怎麼跑、用了哪些模型（含授權與出處） |
| `AGENTS.md` | 外部 AI 代理人 | 三分鐘上手：三個入口 API、三條硬限制、寫資料的唯一正確方式 |
| `PLAN.md` | 開發者（含我） | 北極星、里程碑、進度表、防偏線規則——**唯一主線** |
| `CHANGELOG.md` | 開發者 | 每個版本改了什麼（keep-a-changelog 格式） |
| `VERSION` | 程式 | 版號單一真相（`app/version.py` 讀它） |

---

## 三、`docs/spec/` — 程式規格（動程式前必讀）

| 檔案 | 用途 |
|---|---|
| `data-model.md` | `project.json` 完整 schema 與不變式（含 v0.2 追加欄位） |
| `contracts.md` | 下游 ComfyUI 契約：H3 送件影格網格、Qwen payload、ffmpeg 合併參數 |
| `storyboard-contract.md` | 分鏡契約 v1：三層單一真相、`beatIds` 主鍵、V1–V5 驗證、H3 長度衝突解法 |
| `shots-live-linkage.md` | 鏡頭頁即時連動契約 v1.1：真相／衍生分界、補洞排序鐵律、T1–T13 驗收 |

## 四、`docs/dev/` — 開發工程

| 檔案 | 用途 |
|---|---|
| `VERSIONING.md` | 版號規則與發版流程（`tools/release.py` 的依據） |
| `BRANCH-WORKFLOW.md` | 多分支並行規矩：檔案歸屬表、高衝突檔、開分支模板、合併閘 |
| `DESKTOP.md` | 桌面化（pywebview）四個致命坑與解法 |
| `DEVLOG-M1-M3.md` | M1–M3 開發歷程（從 README 移出的歷史紀錄） |
| `research-existing-tools.md` | 外部 14 個開源專案調查：可借模組、授權紅燈、不 fork 的理由 |
| `TODO-backlog.md` | 偏線發現一律丟這裡，不准順手做 |

## 五、`docs/agent/` — 代理人

| 檔案 | 用途 |
|---|---|
| `AGENT-GUIDE.md` | 給外部 AI 的 Markdown 操作手冊匯出檔 |
| `agent-manifest.json` | 機器可讀 manifest 匯出檔 |

> 兩者是 `app/agentdoc.py` 的匯出物（單一真相在程式裡）；改了 `agentdoc.py` 要重新匯出，並跑 `python tools/check_agent_doc.py`。

## 六、`docs/handoff/` — 交接單

一個分支一張（`_TEMPLATE.md` 是模板）。五段必填，`python tools/handoff.py check` 綠了才合併。

## 七、`docs/release/` — 版本紀錄

- `RELEASE-NOTES-v*.md`：每版產物紀錄（含 exe／zip 的 sha256，由 `tools/release.py` 自動產生）
- `release-body-v*.md`：GitHub Release 對外說明稿

## 八、`docs/testing/` — 測試與證據

| 位置 | 用途 |
|---|---|
| `nightly-audit/` | 夜間巡檢：`latest.json`＋每日 `<日期>.json`（不進版控）＋報告 md |
| `evidence/` | 驗收截圖與對照影片（各里程碑 `mXX-*.png`、`*-sheet.jpg`、比對 `.mp4`） |

## 九、`docs/usage/` — 使用者文件

| 檔案 | 用途 |
|---|---|
| `使用說明.txt` | 打包進乾淨版的使用說明（**唯一來源**，`tools/release.py` 會複製並驗證版號） |
| `使用說明-archive-20261118.txt` | 舊版歸檔 |

---

## 十、工具與測試清單

**`tools/`（開發與發佈）**

| 檔案 | 用途 |
|---|---|
| `release.py` | 一鍵發版：build → 覆寫 dist exe → 重建乾淨版 → zip → 寫 RELEASE-NOTES → commit |
| `verify_release.py` | 發佈包驗證 |
| `handoff.py` | 交接單 new／list／check／brief |
| `check_agent_doc.py` | 檢查每支 API 都有語意說明（0 = 通過） |
| `make_icon.py` | 產生 app.ico |

**`tests/`（測試）**

| 檔案 | 用途 |
|---|---|
| `test_live_linkage.py` | 鏡頭頁即時連動 T1–T13 驗收（只在複本上跑） |
| `check_deploy_scopes.py` | 一鍵佈署三種範圍只動自己那層（免模型、複本資料） |
| `mock_llm.py` | 離線假模型伺服器（測試用，不得當「功能可用」證據） |
| `nightly_audit.py` | 夜間巡檢實作（cron 經 shim `hermes/scripts/svs_nightly_audit.py` 呼叫） |

---

## 十一、歷史文件的路徑說明

`CHANGELOG.md`、`docs/release/*`、`docs/handoff/*` 是**當時的歷史紀錄**，內文可能出現搬遷前的舊路徑（如 `docs/contracts.md`）。對照：

| 舊路徑 | 新路徑 |
|---|---|
| `docs/contracts.md` / `data-model.md` / `storyboard-contract.md` / `shots-live-linkage.md` | `docs/spec/…` |
| `docs/BRANCH-WORKFLOW.md` / `VERSIONING.md` / `DESKTOP.md` / `DEVLOG-M1-M3.md` / `research-existing-tools.md` / `TODO-backlog.md` | `docs/dev/…` |
| `docs/AGENT-GUIDE.md` / `agent-manifest.json` | `docs/agent/…` |
| `docs/RELEASE-NOTES-*.md` / `release-body-*.md` | `docs/release/…` |
| `docs/shots/` | `docs/testing/evidence/` |
| `docs/nightly-audit/` | `docs/testing/nightly-audit/` |
| `docs/使用說明.txt` | `docs/usage/使用說明.txt` |
| `tools/mock_llm.py` / `tools/nightly_audit.py` | `tests/…` |
| `scripts/test_live_linkage.py` / `scripts/check_deploy_scopes.py` | `tests/…` |

活文件（README、AGENTS.md、PLAN.md、docs/spec、docs/dev、docs/agent、程式註解）已全部更新為新路徑。

---

## 十二、新增文件時該放哪（決策樹）

1. 是**程式行為的規格**、而且改程式前該讀？→ `docs/spec/`
2. 是**開發流程／規則／歷程／調查**？→ `docs/dev/`
3. 是**給 AI 代理人的操作說明**？→ `docs/agent/`（並確認來源在 `app/agentdoc.py`）
4. 是**某個分支的交接紀錄**？→ `docs/handoff/`
5. 是**某一版的產物紀錄**？→ `docs/release/`
6. 是**驗收證據或巡檢報告**？→ `docs/testing/`
7. 是**給使用者的說明**？→ `docs/usage/`
8. 是**測試程式**？→ `tests/`
9. 是**發版或維護腳本**？→ `tools/`
10. 都不屬於以上，而且是長期知識／教訓？→ **不進 repo，寫進 skill**
