---
branch: m16-live-link
milestone: M16
status: wip            # wip | ready | merged
opened: 2026-11-24 18:52（台北）
files: ["app/storyboard.py", "app/h3compose.py", "static/app.js", "scripts/test_live_linkage.py", "docs/handoff/m16-live-link.md"]
---

# 交接單：m16-live-link

## 做了什麼

鏡頭頁即時連動 v1 的後續修補：手改的 `promptEn` 不再在存檔時被 `refresh_derived` 覆蓋
（含舊版前端沒打 `promptEnManual` 旗標的稿子，改為採認保全），並收回 V1 順序降級與
V5 establish 兩處不必要的規則放寬，驗收腳本補到 T1–T11。
上一輪曝光的 `backfill_shots()` 排序漏洞已依 `docs/shots-live-linkage.md` §8.2 修掉
（commit `f632f3d`），T3 由 FAIL → PASS，**T1–T11 全線 PASS**。

## 動到的檔案

- `app/storyboard.py`：`backfill_shots()` 的 `_key()` 分組鍵改成
  `(場次序, 0=有有效 beatIds／1=沒有, pos, orig_order, …)`——沒有 `beatIds` 的既有鏡
  （手動／重複／孤兒）排該場次**所有有 beatIds 的鏡之後**，組內依 `orig_order` 維持原相對順序；
  「有效」定義為「指到本場次真的存在的 beat」。`no` 產生規則、V5 定義、`_group_character_ids` 未動。
- `app/h3compose.py`：`refresh_derived` 的 D2 三段判定＋`derived.adoptedManual`；
  `_validate_storyboard_contract` 移除 V1 的 `exact_cover` 降級（連帶移除 `unshot_line_issue`
  收回邏輯與 `chain_shots`）與 V5 的 `fillReason==backfill` 分支。
- `static/app.js`：鏡頭卡摘要支援 `adoptedManual`（「⚠ 舊版手改稿已保留（按「模板編譯」可更新）」）；
  「⌘ 模板編譯 prompt」寫回後清掉 `promptEnManual` 並同步 `derived`。
- `scripts/test_live_linkage.py`：新增 `disk_shots()`、T9、T10、T11；T1 的測試對象改成「把 c01
  規則式鏡頭明確設回非手動稿」（母本舊手改稿依新規則被採認為手寫稿）；`ALL` 加 T9／T10／T11。

## 介面變更

- `GET /api/projects/{slug}` 的 `shots[].derived` 新增選用欄位 `adoptedManual`（布林）：
  為真代表「這份 `promptEn` 是舊版無旗標手改稿，存檔時被採認為手寫稿並保全」。
  缺欄位＝不是這種情況，前端不需改就能跑。
- 無新增／刪除 API；無欄位改名。`refresh_derived` 簽章不變。

## 未完成／已知限制

1. ~~`T3` FAIL 的 `backfill_shots()` 排序漏洞~~ **已修（`f632f3d`）**：`_key()` 原本讓沒有
   `beatIds` 的既有鏡吃 `pos=-1`（排場次最前面），母本 e01s01 的 sh25–sh28 因此從 no=5…8
   跳到 no=1…4、`sh01(establish)` 變第 5 鏡 → V5 error → `POST /storyboard/build` 回 400。
   現在無有效 beatIds 的鏡整組排在該場次有 beatIds 的鏡之後、組內依 `orig_order`。
   實測：`backfill_shots(d, ['e01s03'])` → created=`['sh29']`（插在 sh10 之後、no=15）、
   28 支既有鏡 index 序列同序、e01s01 sh01–sh04 維持 no=1…4、sh25–sh28 維持 no=5…8；
   `validate` error 5 → 3（消掉 e01s03 V1 順序與 sh01 establish V5 兩條），
   剩下 wide 取景／sh10 reaction 含台詞／sh06 V4 三條皆為母本既有資料問題。
   `python tools/handoff.py check m16-live-link` 只剩「PLAN.md 找不到里程碑『M16』」，
   PLAN.md 的 M16 列依指示由主代理補，補完即 0 個問題。
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

目前結果：T1–T11 全線 PASS（含 T6 前端）。T3 的數字：步驟① 補 b05 → created=`['sh29']`；
步驟② e01s03 插 b99 → `mode=backfill`、created=`['sh30']`、`uncoveredBeats` 清空、
既有 29 鏡 `beatIds` 一字不動、無重複引用、無 V5 降級；T11：28 支既有鏡同序、
sh25–sh28 全在有 beatIds 的 sh01–sh04 之後、sh01 仍是 e01s01 第一支有 beatIds 的鏡。
另：`python tools/check_agent_doc.py` rc=0、`python -m compileall app tools` 與
`node --check static/app.js` 全過。

## 是否動到契約（data-model.md / contracts.md）

沒有動 `docs/data-model.md` 與 `docs/contracts.md` 的既有條文。V1／V5 的**行為**回到
`docs/storyboard-contract.md` 原本的敘述（順序不符＝error、establish 只能第一鏡＝error），
屬收回先前的放寬，不是新增契約；`derived.adoptedManual` 是選用欄位（見「介面變更」）。
是否需要把 D2 三段判定寫進 `docs/shots-live-linkage.md` §1 由主代理決定。
