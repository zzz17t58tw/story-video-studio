---
branch: feat/m16-live-link
milestone: M16
status: merged         # wip | ready | merged（已以 --no-ff 合入 main，v0.9.22 發佈）
opened: 2026-11-24 18:52（台北）
files: ["app/server.py", "app/h3compose.py", "app/storyboard.py", "static/app.js", "scripts/test_live_linkage.py", "docs/handoff/m16-live-link.md"]
---

# 交接單：m16-live-link

## 做了什麼

鏡頭頁即時連動 v1 的後續修補：手改的 `promptEn` 不再在存檔時被 `refresh_derived` 覆蓋
（含舊版前端沒打 `promptEnManual` 旗標的稿子，改為採認保全），並收回 V1 順序降級與
V5 establish 兩處不必要的規則放寬，驗收腳本補到 T1–T13。
上一輪曝光的 `backfill_shots()` 排序漏洞已依 `docs/shots-live-linkage.md` §8.2 修掉
（commit `f632f3d`），T3 由 FAIL → PASS。

本輪（獨立驗收找到的 P1–P4）：
- **P1（阻塞）**：`_load_for_m3d()` 只填預設值、沒重算 `derived`，`GET /storyboard` 的
  `orphanShotIds` 在乾淨複本上恆為 `[]` → 前端「移除本場未連動的鏡頭（N）」永遠不出現。
- **P2**：`derived.adoptedManual` 只存活一次存檔（第二輪 `promptEnManual` 已為 true → 走①分支
  → `adopted=False` 被 `pop`）→ 第二輪顯示成「已同步」。
- **P3**：按「模板編譯」後 `promptEnManual` 沒清 → 之後改 beats 永遠不更新該鏡。
- **P4**：T8 第一次跑 FAIL、第二次才過（測試自身的槽位前置不穩）。

## 動到的檔案

- `app/server.py`
  - `_load_for_m3d()`：回傳前對**記憶體資料**跑 `h3compose.refresh_derived(data)`（P1）。
    **不寫檔**；要寫的端點自己 `save_project`，而 `save_project` 存檔前也會再算一次同一組衍生值，
    所以 `api_compile_prompt` 寫回的字不會被存檔端改掉（T13 實測：編譯稿 2596 字存兩次都 2596）。
  - `api_compile_prompt()`（P3）：寫回 `promptEn` 後清掉 `promptEnManual`／`promptAdoptedManual`，
    回應新增 `promptManualCleared`（布林）與 `promptSource`（存檔後重讀的 `derived.promptSource`）。
- `app/h3compose.py`（P2）：採認紀錄改存在**鏡頭本體** `shot.promptAdoptedManual=true`；
  `derived.adoptedManual` 讀它（走①分支時），走③分支（重算成編譯稿）時清掉本體旗標。
- `static/app.js`：模板編譯成功後同步刪掉本機 `sh.promptAdoptedManual`、`promptSource` 改用回應值。
- `scripts/test_live_linkage.py`
  - T8（P4）：先逐鏡 `POST /shots/{id}/build-reference-plan` 重建槽位，前置不成立就**斷言失敗**
    （不放寬），不再依賴瘦身 PUT 的不確定重算。
  - T9：改成**連存三次**穩定（手改鏡每輪字數/旗標/adoptedManual 都對、sh10 三次同字、sh07 三次都不被標）。
  - 新增 T12（P1）：乾淨複本（磁碟無 `derived`）打 `/storyboard`，e01s01 的 `orphanShotIds`
    == {sh25,sh26,sh27,sh28}、其餘場次為空，且與 `GET /api/projects/{slug}` 判定一致。
  - 新增 T13（P3）：編譯前 sh10 是手寫稿 → `compile-prompt` → 兩次存檔都不得翻回 manual。
- `app/storyboard.py`：`backfill_shots()` 的 `_key()` 分組鍵改成
  `(場次序, 0=有有效 beatIds／1=沒有, pos, orig_order, …)`——沒有 `beatIds` 的既有鏡
  （手動／重複／孤兒）排該場次**所有有 beatIds 的鏡之後**，組內依 `orig_order` 維持原相對順序；
  「有效」定義為「指到本場次真的存在的 beat」。`no` 產生規則、V5 定義、`_group_character_ids` 未動。
- `app/h3compose.py`：另含 `_validate_storyboard_contract` 移除 V1 的 `exact_cover` 降級
  （連帶移除 `unshot_line_issue` 收回邏輯與 `chain_shots`）與 V5 的 `fillReason==backfill` 分支。

## 介面變更

- `shots[].derived.adoptedManual`（選用布林）：這份 `promptEn` 是「舊版無旗標手改稿，被採認保全」。
- `shots[].promptAdoptedManual`（選用布林，**真相側**）：採認紀錄本體，持久化用；使用者按
  「模板編譯」後由 `compile-prompt` 清掉。
- `POST /shots/{id}/compile-prompt` 回應新增 `promptManualCleared`（布林）與 `promptSource`。
- 無新增／刪除 API；無欄位改名。`refresh_derived` 簽章不變。
- 效能：每個 m3d 端點多一次 `refresh_derived`（28 鏡實測約 16ms，可接受）。

## 未完成／已知限制

1. 瘦身 PUT（`slim_project`）只帶分鏡決策欄位，某些鏡頭的編譯稿在 PUT 後會改變 → 存檔端判成
   手寫稿保全原值。這是正確行為；T9／T13 因此改用裸 `storage.save_project` 驗。
2. 前端只驗到 T6（28 張卡摘要字數與 `GET` 一致、console 錯誤 0）；`adoptedManual` 文案、
   「移除本場未連動的鏡頭」按鈕沒有做視覺驗收（T12 已驗它依賴的 `orphanShotIds`）。
3. `PLAN.md` 的 M16 列未動，由主代理補。
4. 未 push、未發 Release、未動 `dist/StoryVideoStudio/projects`（全程只寫複本，母本
   `project.json` mtime 前後一致）。

## 驗證方式

```bash
cd "C:/Users/user/Desktop/hermes work/1117-wt-m16-live"
"../1117/.venv/Scripts/python.exe" scripts/test_live_linkage.py            # 全部（含 T6 前端）
"../1117/.venv/Scripts/python.exe" scripts/test_live_linkage.py --no-ui    # 略過 T6
SVS_TEST_PORT=8795 "../1117/.venv/Scripts/python.exe" scripts/test_live_linkage.py   # 換埠
```

實測：**T1–T13 連跑三次 13/13 PASS**（8794、8795、8794；含 T6 前端）。
- P1 實測（同一份乾淨複本）：`/storyboard` 各場次 `orphanShotIds` 【修前】`[]`×6 場
  →【修後】`e01s01=['sh25','sh26','sh27','sh28']`、其餘 5 場 `[]`；與 `GET /api/projects` 一致。
- P2／A1／A2 實測（連存三次）：sh10 = 1881 字三次、`promptEnManual=True` 恆真、
  `derived.adoptedManual=True` 三次；sh07 三次都不被標 manual、`adoptedManual` 三次缺席。
- P3 實測：`POST /shots/sh10/compile-prompt` → `promptManualCleared=True`、`promptSource=compiled`；
  之後存兩次檔 `promptEnManual`／`promptAdoptedManual` 都不再出現、`promptSource=compiled`、
  字數維持 2596。
- T8：槽位前置 28/28 重建成功，precheck 28/28 放行、預覽與送件 28/28 逐字相等（連跑三次同結果）。
- 另：`python tools/check_agent_doc.py` rc=0、`python -m compileall app tools` 與
  `node --check static/app.js` 全過。

## 是否動到契約（data-model.md / contracts.md）

沒有動 `docs/data-model.md` 與 `docs/contracts.md` 的既有條文。V1／V5 的**行為**回到
`docs/storyboard-contract.md` 原本的敘述（順序不符＝error、establish 只能第一鏡＝error），
屬收回先前的放寬，不是新增契約；`derived.adoptedManual`／`shot.promptAdoptedManual` 是選用欄位。
