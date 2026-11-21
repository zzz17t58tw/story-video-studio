# 分鏡契約（storyboard contract）v1｜2026-11-21

本檔定義「劇本 → 分鏡 → 生成」三層之間的唯一規則。**任何新功能（規則式分鏡、一鍵佈署、對話回填、外部工作流匯入）都必須遵守本檔；與本檔衝突的舊行為以本檔為準。**

---

## 0. 為什麼需要這份契約

使用者回報（2026-11-21）：「現在的鏡頭與劇本太過簡陋，故事是要透過鏡頭與劇本演出來的，但現在顛倒了。只看生成出來的影片根本不懂在演什麼。」

實測根因（`project-20261120-172537`）：劇本 2 場 × 9 beats 完整（誰說、說什麼、做什麼都在），但 6 個鏡頭的 `dialogBeatIds` 全是 `null`、`dialog` 全空、每鏡 11–15 秒塞 2 個 beat。**鏡頭沒有承接劇本，生成端只拿到一段畫面描述，所以影片只有畫面沒有戲。**

---

## 1. 三層與單一真相

| 層 | 檔案位置 | 角色 | 是否真相 |
|---|---|---|---|
| 劇本層 | `script.episodes[].scenes[].beats[]` | 誰、說/做什麼、情緒、順序 | **唯一真相（source of truth）** |
| 分鏡層 | `shots[]` | 一次可生成的片段，引用 beats | 衍生＋決策（不可背離 beats） |
| 生成層 | `durationSec`／`promptEn`／`referencePlan` | 送進模型的內容 | 由分鏡層派生 |

**鐵則 S1：分鏡不得改寫劇本。** 分鏡只能挑選、分組、決定鏡位與長度，不能產生劇本裡沒有的台詞，也不能改台詞文字。要改台詞，回劇本層改。

---

## 2. 分鏡欄位（新增／收斂）

```jsonc
{
  "id": "sh07", "no": 7,
  "episodeSceneId": "e01s01",
  "beatIds": ["b05"],              // 新．主鍵：本鏡演出的 beats（有序、連續、不跨場次）
  "beatPart": { "beatId": "b05", "part": 1, "total": 2 },  // 僅「單一 beat 太長須拆鏡」時出現
  "role": "dialogue",              // 新．establish | dialogue | reaction | action | insert | closing
  "derivedFrom": "storyboard",     // 新．storyboard（規則式）| deploy（LLM 分鏡）| manual
  "padSec": 1.4,                   // 新．為滿足 H3 最短長度而留白的秒數（純停留／反應，不算劇情）
  "durationSec": 5.17,             // 必須落在 H3 合法格（見 §3）
  "dialogBeatIds": ["b05"],        // 既有（M12a 主鍵）→ 本輪起由 beatIds 中的 dialogue beats 自動推導
  "dialogLineRefs": [3],           // 既有 → 由 dialogBeatIds 換算，唯讀
  "spec": { }, "promptZh": "", "promptEn": "", "referencePlan": { }
}
```

**收斂（deprecation）**
- `shot.dialogue`（自由台詞陣列）→ **停用**：台詞一律由 `beatIds → 場次 beats` 取原文，避免同一句存兩份而漂移。舊資料保留不刪，讀取端不再使用。
- `shot.beats`（自由敘述陣列、`type` 常為 null）→ **停用**：改由 `beatIds` 引用；舊內容遷移時整包存進 `shot.notes`（不覆蓋既有 notes 就存 `shot.legacyBeats`），不參與生成。
- `shot.durationSec` 仍由人決定，但必須吸附 §3 的合法格，且**不得小於該鏡台詞唸完所需的時間**。

---

## 3. 長度規則（與 H3 硬限制對齊，這是最容易規則衝突的地方）

H3 的影格合法格是 `17k+5` 且訓練域 `124–362` 幀 → **單鏡片段合法長度 5.17–15.08 秒（fps=24）**。

這與「一句一鏡、每鏡 4–6 秒」的業界節奏**直接衝突**，因此規則必須是：

- **L1 估時**：`estimate_durations()` 的算式（對白 `字數/4.5 + 2.0`、動作 `max(3.0, 字數/6 + 2.0)`）作為「這一 beat 需要多少秒」。
- **L2 吸附**：估時 → `frames_for()` 吸附到 `17k+5`；若 < 124 幀（5.17s）→ 以 `padSec` 補足到 124 幀；若 > 362 幀（15.08s）→ **必須拆鏡**（`beatPart`），不得靠超長片段硬吃。
- **L3 分組**：一句台詞一鏡是預設；但當「單句估時 < 5.17 秒」時，允許與**相鄰 beat 合併成一鏡**（合併後仍須 ≤ 15.08 秒），並在 UI 標示「合併 N 句（H3 最短 5.17 秒）」。合併是物理妥協，不是設計選擇。
- **L4 不得為湊長度灌水劇情**：補足長度只能靠 `padSec`（停留、呼吸、眼神、環境）或合併相鄰 beat；**禁止**為了湊秒數新增台詞或新動作。
- **L5 成片節奏**由剪輯決定：`padSec` 與合併造成的鬆散，屬於「生成長度」而非「成片長度」。未來若有剪輯層，以 `trimSec` 記錄要剪掉的頭尾；本版先把 `padSec` 記在資料裡，不做自動剪輯。

---

## 4. 覆蓋與順序（驗證規則，全部為 error）

依既有 `validate_project()` 擴充，`shots` 對每個場次必須滿足：

- **V1 順序覆蓋**：場次內 `shots`（依 `no` 排序）的 `beatIds` 串接後，必須與該場次 `beats`（排除 `_skipped`）**完全一致**（同順序、無漏、無重複、不夾雜他場次 beat）。
- **V2 台詞覆蓋**（既有）：每個 `dialogue` beat 剛好被某一鏡引用一次。
- **V3 長度合法**：`frames_for(durationSec).legal` 必須 `124 ≤ legal ≤ 362` 且 `inRange`（現為 warn，本版改 **error**）。
- **V4 台詞唸得完**：`durationSec - padSec ≥ 該鏡台詞字數/4.5`，不足為 error（會導致配音截斷）。
- **V5 職責一致**：`role=dialogue` 必含 ≥1 個 dialogue beat；`role=reaction` 只准引用 0 個 dialogue beat 且必須有 `speakerId` 的對手在場；`role=establish` 只能是場次第一個鏡且不得含台詞（除旁白）。

---

## 5. 與其他功能的關係（避免兩套規則打架）

- **一鍵佈署（`app/deploy.py` 的 LLM 分鏡）**：`derivedFrom="deploy"`，輸出必須落在同一 schema（`beatIds`），不得再寫 `shot.dialogue`／`shot.beats`；送出前用同一支 `validate_project()` 驗，驗不過就不落檔。
- **規則式分鏡（本版新增）**：`derivedFrom="storyboard"`，純規則、零模型呼叫；不得覆蓋 `derivedFrom="manual"` 的鏡頭，除非使用者勾「覆蓋手動調整」。
- **對話回填（M13a／M13b）**：beats 被改寫後，引用它的鏡頭 `beatIds` 會失效 → 既有 `_reconcile_beat_refs()` 負責清理並回報，不自動重排。
- **H3 送件（`h3gen._pick_prompt`／`h3compose.compile_prompt`）**：
  - 提示詞採**兩層結構**：`global`（角色錨點、場景、光線基調、風格）＋`per-shot`（這一鏡唯一事件、誰對誰說哪句、起始→結束、運鏡）。此結構對照 HoloCine（CVPR 2026）的 global caption + per-shot prompt + `[shot cut]`。
  - 一鏡只准一個主事件：`role=dialogue` 的鏡頭只寫「這一句＋對手反應」，不得把整場塞進去。
  - 相鄰鏡要寫承接（上一鏡的結果）→ 因果鏈。

---

## 6. 驗收標準（本版）

1. 用 `project-20261120-172537`（18 beats）實跑規則式分鏡：產出鏡頭數 ≥ 4、每鏡長度全在 124–362 幀、`validate_project()` 的 V1–V5 **0 error**。
2. 分鏡表 UI 由上往下讀，能讀出整齣戲（鏡號／說話者／台詞逐字／動作／秒數／累計時間）。
3. 覆蓋率檢查在故意漏一句時會擋（error），不是靜默通過。
4. 舊專案（未被重建分鏡者）不受影響：讀檔不報錯、`legacyBeats` 遷移成功。
