---
branch: m12a-shot-script-linkage
milestone: M12a
status: ready            # wip | ready | merged
opened: 2026-11-21 12:12（台北）
files: ["app/storage.py", "app/h3compose.py", "app/deploy.py", "static/app.js", "static/style.css"]
---

# 交接單：m12a-shot-script-linkage

## 做了什麼

鏡頭與劇本改用「句子 id」連動：鏡頭新增 `dialogBeatIds`（存該場次的台詞 beat id），舊資料讀檔時自動由 `dialogLineRefs` 換算，劇本頁插／刪／搬句後引用不再錯位。鏡頭卡多一張「引用劇本台詞」勾選清單＋唯讀「本鏡台詞」，劇本頁每句台詞標出「← 被 sh01、sh02 引用」或「（尚無鏡頭引用）」。後端 `compile_prompt`／`validate`／一鍵佈署逐步落地全部改讀 `dialogBeatIds`（沒有才退回舊欄位），`dialogLineRefs` 仍與新欄位同步寫入。

## 動到的檔案

- `app/storage.py`：新增 `episode_scene_map`／`shot_dialog_beat_ids`／`dialog_beat_index`／`migrate_dialog_linkage`；`fill_project_defaults`（讀檔正規化）與 `save_project`（驗證前）都跑遷移；`validate_project` 加驗 `dialogBeatIds` 必須是所屬場次的台詞 id。
- `app/h3compose.py`：新增 `shot_dialog_lines`（beat id 優先、退回序號，回 (句子, 來源, 錯誤)）；`compile_prompt` 第 ⑥ 段與 `validate_project` 第 ① 節改用它。
- `app/deploy.py`：`gen_shots_for_scene` 產出 `dialogBeatIds`；`_land_script` 回傳「預覽 beat id → 實際 beat id」對照；`_land_shots` 用實際寫入後的 beat id 寫 `dialogBeatIds` 並重算 `dialogLineRefs`；`apply_preview` 同步寫入。
- `static/app.js`：新增共用 `esDialogueBeats`／`shotDialogBeatIds`／`beatRefMap`／`syncShotDialogRefs`；鏡頭卡新增「引用劇本台詞（dialogBeatIds）」subcard 與唯讀「本鏡台詞（依引用順序）」；劇本頁 `beatRow` 加反向引用標註；`removeBeat` 會清掉被刪句的鏡頭引用；「由場次拆鏡頭草稿」改為寫 `dialogBeatIds`。
- `static/style.css`：新增 `.dialog-ref-*`、`.beat-refs` 樣式（`none` 為紅字警示）。

## 介面變更

- `shots[]` 新欄位 `dialogBeatIds: string[]`（該場次台詞 beat id；純動作鏡為 `[]`）。`dialogLineRefs` 保留並由新欄位同步重算。
- `GET /api/projects/slug` 的回應多 `project._dialogMigration = {applied: [shotId...], notes: [訊息...]}`（只是讀取時附帶，`PUT` 時會被丟掉、不寫回檔）。
- `POST /api/projects/slug/validate` 的 error 訊息改用 `dialogBeatIds` 措辭（重複引用、引用所屬場次沒有的台詞、該場台詞沒被覆蓋）。
- Python 函式：`storage.episode_scene_map(data)`、`storage.shot_dialog_beat_ids(shot, data)`、`storage.dialog_beat_index(ids, dialogue)`、`storage.migrate_dialog_linkage(data) -> list[str]`、`h3compose.shot_dialog_lines(shot, ctx, warnings=None) -> (list[tuple[int, dict]], str, list[str])`；`deploy._land_script` 回傳多第三項 `beat_id_map`，`deploy._land_shots(..., beat_id_map=None)`。

## 未完成／已知限制

- `dialogue`（自由陣列）照舊保留、UI 標成「本鏡臨時對白（不寫進劇本）」，但**沒有任何地方再讀它**（`compile_prompt` 與驗證都只看 `dialogBeatIds`），等於純備忘。
- 鏡頭卡上每個勾選框各自呼叫 `beatRefMap(es)` 重掃一次全部鏡頭；20 鏡以下的專案無感，規模大時可先算一次再共用。
- 遷移只做「序號 → id」單向換算；一個都換算不到時刻意保留原 `dialogLineRefs` 並在 notes 回報（不 400、不清空），這種鏡頭在 UI 上會顯示無引用，需人工重勾。
- 驗證只在 worktree 用臨時專案（2 場／每場 3 句＋2 動作、2～4 鏡）走過 API 與瀏覽器；**未**跑真實 LLM 一鍵佈署（本輪規定不呼模型），`deploy` 的 beat id 對照只在 `_land_script`／`_land_shots` 的資料流層驗過，不是實跑一輪佈署的證據。
- `dialogLineRefs` 的舊驗證（序號範圍／重複）仍留在 `storage.validate_project`，新舊資料混用時兩個欄位不一致會被擋；目前由前後端同步寫入保證一致，但**直接手改 JSON** 就可能兩邊打架。

## 驗證方式

```bash
cd "C:/Users/user/Desktop/hermes work/1117-wt-linkage"
"C:/Users/user/Desktop/hermes work/1117/.venv/Scripts/python.exe" -m uvicorn app.server:app --host 127.0.0.1 --port 8790
curl -s -X POST 127.0.0.1:8790/api/projects -H "Content-Type: application/json" --data-binary '{"name":"M12a Linkage Test"}'
```

建好專案後（把專案的實際 slug 帶進指令）用 API 驗三件事：`GET /api/projects/實際slug` 看舊鏡頭的 `dialogBeatIds` 是否已由 `dialogLineRefs` 換算；`POST /api/projects/實際slug/validate` 看 `counts.error`；在場景 beats 中間插一句後再 `GET`，確認原鏡頭的 `dialogBeatIds` 仍指向同一句、`dialogLineRefs` 序號已自動往後移。測完 `curl -s -X DELETE 127.0.0.1:8790/api/projects/實際slug` 並刪掉整個 `projects/`（worktree 沒有這個資料夾）。

## 是否動到契約（data-model.md / contracts.md）

有動到 `docs/data-model.md` 描述的鏡頭結構：新增 `shots[].dialogBeatIds`（string[]，該場次台詞 beat id），並保留 `shots[].dialogLineRefs` 為相容欄位（由新欄位同步重算）。這是 PLAN.md 的 M12a 條目明文要求（「舊 dialogLineRefs 自動遷移＋兩頁同一份真相」），主代理已在派工規格中核准；`docs/data-model.md` 本輪**未**修改，留給主代理補一節說明。
