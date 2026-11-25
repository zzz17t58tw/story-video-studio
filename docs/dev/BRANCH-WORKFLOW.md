# 分支協作規矩（BRANCH-WORKFLOW）

記錄時間：2026-11-19 12:10（台北）
適用：Story Video Studio（1117）。**開新分支對話前先讀這份。**

---

## 0. 一句話原則

> **對話是工作區，檔案才是真相。**
> 分支對話可以關掉、忘掉、被壓縮；只要契約與交接單留在檔案裡，合併就不會出事。

主對話（整合者）：管契約、管 `PLAN.md`、管合併。
分支對話（實作者）：只碰自己那塊檔案，收工交一張交接單。

---

## 1. 四個真相檔（只有主對話能改前三項）

| 檔案 | 角色 | 誰能改 | 衝突怎麼判 |
|---|---|---|---|
| `PLAN.md` | 任務板：里程碑、狀態、進度紀錄 | **只有主對話** | 分支不准改，只能回報 |
| `docs/spec/data-model.md` | 資料契約：`project.json` schema、ID 規則 | **只有主對話** | 要加欄位 → 停工回報 |
| `docs/spec/contracts.md` | 外部契約：ComfyUI 導演台／Qwen payload／ffmpeg | **只有主對話** | 同上 |
| `docs/dev/TODO-backlog.md` | 發現的雜事停車場 | 任何分支都可追加 | 只准 append，不准刪別人的 |

**分支碰到的介面問題，一律「停工 → 回報 → 主對話改契約 → 重新分派」，不准在分支裡私自改介面。**
這是合併爆掉的第一名原因：私自改簽章，會讓其他所有分支變成孤兒。

---

## 2. 分支命名與生命週期

```
m<N><字母>-<短說明>       例：m10a-lora-ui、m11-batch-gen
```

- **一個分支 = 一個里程碑切片**，對應 `PLAN.md` 的一列。
- 一個分支只做**一件事**。做不完就在交接單寫「未完成」交回去，不硬撐。
- 分支收工 = 交接單 `status: ready` ＋ 推上 remote（或留在本地，主對話拉得到即可）。
- 主對話合併後改 `status: merged`，並在 `PLAN.md` 進度紀錄補一列。分支才可刪。

---

## 3. 檔案歸屬表（開工前先看這裡）

| 檔案 | 職責 | 衝突等級 | 規則 |
|---|---|---|---|
| `app/server.py` | 全部 HTTP 路由 | 🔴 高 | 只在自己的路由區段加端點；不准重排、不准全域改名 |
| `static/app.js` | 全部前端邏輯（5383 行） | 🔴 高 | 只在自己的函式區塊內改；不准動別人的函式、不准改共用工具函式 |
| `app/storage.py` | 專案資料讀寫 | 🟠 中 | schema 變更一律先改 `data-model.md` |
| `app/llm.py` / `prompts.py` / `storygen.py` / `deploy.py` | 模型生成層 | 🟡 中低 | 各自獨立，可平行 |
| `app/imagegen.py` / `comfy.py` | 生圖 | 🟡 中低 | 送件 payload 以 `contracts.md` 為準 |
| `app/h3gen.py` / `h3compose.py` / `assemble.py` | 生影片／合併 | 🟡 中低 | 同上 |
| `static/index.html` / `style.css` | 版面與樣式 | 🟠 中 | CSS 只加新 class，不改既有 class 定義 |
| `templates/` | 作者工作流副本 | 🔴 高 | **禁止改原檔**，要改另存新檔名 |
| `docs/` 下的契約檔 | 契約 | ⛔ | 分支不准動 |
| `projects/` `dist/` `release/` | 資料與產物 | ⛔ | 不進 git、分支不准碰 |

**🔴 高衝突檔的硬規則**：`server.py` 與 `app.js` 是單體大檔，兩個分支同時改必撞。
所以每個高衝突檔的分支，開工時就宣告「我要用的區段」（例如「只加 `/api/lora/*` 這組路由」），
主對話據此確保同一時間**只有一個分支在動同一個高衝突檔**。

> 長期解：`app/server.py` 拆成 `app/routes/*.py`、`app.js` 拆成模組，是排進 backlog 的技術債（見 `TODO-backlog.md`）。拆之前用上面的區段宣告法撐住。

---

## 4. 開分支對話時，第一則訊息就貼這段

```
里程碑：M10a（PLAN.md 對應列）
這次只做：<一句話>
可以改：app/xxx.py, static/app.js（只加 loraUi 區塊）
不准改：data-model.md、contracts.md、PLAN.md、其他分支的函式
收工要交：docs/handoff/m10a-lora-ui.md（status: ready）
```

五行定生死：不知道邊界的分支，一定會越界。

---

## 5. 收工交接單（`docs/handoff/`）

用 `python tools/handoff.py new <分支名>` 產生，五個必填段：

1. **做了什麼** — 一到三句，講結果不講過程
2. **動到的檔案** — 精確路徑清單（工具會拿去跟 git diff 對帳）
3. **介面變更** — 新增/修改的 API 路徑、JSON 欄位、函式簽章；沒有就寫「無」
4. **未完成／已知限制** — 誠實寫，這比假裝完成有價値
5. **驗證方式** — 一行可重跑的指令或一個測試，主對話會照著跑

外加 front matter 的 `status`（`wip` / `ready` / `merged`）與 `milestone`。

**沒有交接單的分支，視為未完成，不合併。**

---

## 6. 主對話的合併檢查清單

合一個分支時，照這個順序做，一步都不能跳：

```bash
python tools/handoff.py check            # 1. 格式與檔案清單對帳
python tools/handoff.py brief m10a-lora-ui   # 2. 讀交接單摘要
git diff main...m10a-lora-ui --stat      # 3. 看實際改了哪些檔，對照「不准改」清單
# 4. 照交接單的驗證方式實跑一次
git merge --no-ff m10a-lora-ui           # 5. 合併
# 6. PLAN.md 補一列進度紀錄；交接單 status 改 merged
```

**紅線**：如果 `git diff --stat` 出現交接單沒列的檔案 → 先問分支，不合。
如果動到契約檔 → 直接退回，叫分支改契約流程重來。

**小步合併**：一個里程碑合一次。累積三個以上分支才合，衝突成本是平方成長的。

---

## 7. 常見失敗模式（都是實際會發生的）

| 症狀 | 根因 | 處理 |
|---|---|---|
| 合併時 `app.js` 大段衝突 | 兩個分支改同一段 | 回去用區段宣告法，重排順序一次只給一個分支 |
| 分支做的功能「主對話不知道」 | 沒交接單／交接單在對話裡不在檔案裡 | 硬規則：沒有檔案就當沒做 |
| 合完後某個舊功能壞了 | 分支私自改了共用函式簽章 | 契約變更一律走主對話 |
| 分支越做越大，三個功能攪在一起 | 沒有一分支一事件 | 停手，切回里程碑邊界重開 |
| 主對話 context 塞爆、忘記前面決定 | 想要「記在腦子裡」 | 決定一律寫進 PLAN 或契約檔，對話只做臨時運算 |

---

## 8. 工具

```bash
python tools/handoff.py new m10a-lora-ui    # 從模板建交接單
python tools/handoff.py list                # 所有交接單與狀態
python tools/handoff.py check               # 檢查必填段、檔案是否存在、PLAN 有無對應列
python tools/handoff.py brief m10a-lora-ui  # 印出待合併摘要（貼給主對話用）
```

工具只讀不寫（除了 `new`），不會動你的程式碼。
