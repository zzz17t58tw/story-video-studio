---
branch: m16-live-link
milestone: M16
status: wip            # wip | ready | merged
opened: 2026-11-24 18:52（台北）
files: ["app/h3compose.py", "static/app.js", "scripts/test_live_linkage.py", "docs/handoff/m16-live-link.md"]
---

# 交接單：m16-live-link

## 做了什麼

鏡頭頁即時連動 v1 的後續修補：手改的 `promptEn` 不再在存檔時被 `refresh_derived` 覆蓋
（含舊版前端沒打 `promptEnManual` 旗標的稿子，改為採認保全），並收回 V1 順序降級與
V5 establish 兩處不必要的規則放寬，驗收腳本補到 T1–T10。
過程中曝光一個既有排序漏洞（見「未完成」），**未放寬、留給主代理決定**。

## 動到的檔案

- `app/h3compose.py`：`refresh_derived` 的 D2 三段判定＋`derived.adoptedManual`；
  `_validate_storyboard_contract` 移除 V1 的 `exact_cover` 降級（連帶移除 `unshot_line_issue`
  收回邏輯與 `chain_shots`）與 V5 的 `fillReason==backfill` 分支。
- `static/app.js`：鏡頭卡摘要支援 `adoptedManual`（「⚠ 舊版手改稿已保留（按「模板編譯」可更新）」）；
  「⌘ 模板編譯 prompt」寫回後清掉 `promptEnManual` 並同步 `derived`。
- `scripts/test_live_linkage.py`：新增 `disk_shots()`、T9、T10；T1 的測試對象改成「把 c01
  規則式鏡頭明確設回非手動稿」（母本舊手改稿依新規則被採認為手寫稿）；`ALL` 加 T9／T10。

## 介面變更

- `GET /api/projects/{slug}` 的 `shots[].derived` 新增選用欄位 `adoptedManual`（布林）：
  為真代表「這份 `promptEn` 是舊版無旗標手改稿，存檔時被採認為手寫稿並保全」。
  缺欄位＝不是這種情況，前端不需改就能跑。
- 無新增／刪除 API；無欄位改名。`refresh_derived` 簽章不變。

## 未完成／已知限制

1. **（需主代理裁示，本輪刻意未放寬）`T3` 現在 FAIL**：`POST /storyboard/build`（e01s03）
   回 400「補鏡結果新增了契約錯誤，未寫檔：第 5 鏡：role=establish 只能用在場次第一鏡（合約 V5）」。
   根因不是補洞鏡：`storyboard.backfill_shots` 開頭會把整份 `shots` 重排（依場次、再依
   「第一個 beat 在劇本的位置」，沒有 beatIds 的排場次最前面），母本 e01s01 的未連動手動鏡
   sh25–sh28 原本排在場次**最後**，重排後移到場次**最前面**，sh01 的 establish 因此變成第 5 鏡。
   `chain == want` 不受影響（e01s01 仍是 b01..b04）。F2 之前這條被降級成 warn 蓋住了。
   兩個可能方向（未動）：(a) 重排時保留「沒有 beatIds 的既有鏡」在原相對位置，或把它們
   排到場次最後；(b) V5 的第一鏡認定改成「第一支有 beatIds 的鏡」（校驗與排序共用同一條定義）。
   **`python tools/handoff.py check m16-live-link` 目前 rc=1，唯一問題是
   「PLAN.md 找不到里程碑『M16』」——PLAN.md 的 M16 列依指示由主代理補，補完即 0 個問題。**
2. 瘦身 PUT（`scripts/test_live_linkage.py` 的 `slim_project`）只帶分鏡決策欄位，某些鏡頭的
   編譯稿在 PUT 後會改變，於是存檔端把它們判成手寫稿並保全原值——這是「無法證明等於編譯稿
   就保全」的正確行為，不是 bug；但 T9 因此改用裸 `storage.save_project` 驗，不走 PUT。
3. 前端只驗到 T6（28 張卡的摘要字數與 `GET` 一致、console 錯誤 0）；`adoptedManual` 文案沒有
   另做視覺驗收（母本複本存檔後才有 adoptedManual=true 的卡）。
4. `PLAN.md` 的 M16 列未動，由主代理補。
5. 未 push、未發 Release、未動 `dist/StoryVideoStudio/projects`（測試全程只寫複本，母本
   `project.json` mtime 前後一致）。

## 驗證方式

```bash
cd "C:/Users/user/Desktop/hermes work/1117-wt-m16-live"
"../1117/.venv/Scripts/python.exe" scripts/test_live_linkage.py            # 全部（含 T6 前端）
"../1117/.venv/Scripts/python.exe" scripts/test_live_linkage.py --no-ui    # 略過 T6
```

目前結果：T1、T2、T4–T10 PASS；T3 FAIL（原因見「未完成 1」）。
另：`python tools/check_agent_doc.py` rc=0、`python -m compileall app tools` 與
`node --check static/app.js` 全過。

## 是否動到契約（data-model.md / contracts.md）

沒有動 `docs/data-model.md` 與 `docs/contracts.md` 的既有條文。V1／V5 的**行為**回到
`docs/storyboard-contract.md` 原本的敘述（順序不符＝error、establish 只能第一鏡＝error），
屬收回先前的放寬，不是新增契約；`derived.adoptedManual` 是選用欄位（見「介面變更」）。
是否需要把 D2 三段判定寫進 `docs/shots-live-linkage.md` §1 由主代理決定。
