# 資料模型（v0.1 草案，主代理定規格，子代理照此實作）

記錄時間：2026-11-17 00:40（台北）
單一真相檔：`projects/<slug>/project.json`（UTF-8、縮排 2、鍵名 camelCase）
媒體檔：`projects/<slug>/media/**`，project.json 內一律存「相對專案根的路徑」如 `media/shots/s01_first.png`
ID 規則：`c01`,`sc01`,`e01`,`sh01`（前綴 + 兩位流水號，建立後不可改）
時間欄位：ISO8601 帶時區（`2026-11-17T00:40:00+08:00`）

```jsonc
{
  "meta": {
    "slug": "demo", "name": "示範專案", "version": 1,
    "createdAt": "...", "updatedAt": "...",
    "aspectRatio": "9:16",        // 導演的合法值：9:16/3:4/4:3/1:1/16:9/21:9
    "fps": 24
  },

  // ── L1 故事 ────────────────────────────────
  "story": {
    "premise": "一句話概念（使用者原始輸入）",
    "synopsis": "完整故事大綱（模型可擴寫）",
    "style": { "genre": "", "tone": "", "visualStyle": "", "negative": "" },
    "chapters": [ { "id": "ch01", "title": "", "summary": "", "content": "章回正文" } ]
  },

  // ── L2 人物 ────────────────────────────────
  "characters": [ {
    "id": "c01", "name": "", "aliases": [], "role": "主角|配角",
    "appearance": {                     // 五官外型，鎖臉用
      "age": "", "gender": "", "skin": "", "face": "", "hair": "",
      "body": "", "features": ["辨識特徵，如：左眉尾有疤"],
      "promptEn": "英文外型描述（餵生圖用，模型可代寫）"
    },
    "outfits": [ { "id": "o01", "name": "日常", "desc": "", "promptEn": "" } ],
    "background": { "birth": "", "personality": "", "goal": "", "secret": "", "relations": "" },
    "voice": { "desc": "", "refAudio": "media/voices/c01.wav", "engine": "fish_s2|indextts2", "note": "" },
    "refImages": [ { "path": "media/characters/c01_face_01.png", "kind": "face|full|outfit", "locked": true } ],
    "lockedFace": true                  // 有鎖臉參考圖
  } ],

  // ── L3 場景 ────────────────────────────────
  "scenes": [ {
    "id": "sc01", "name": "", "desc": "",
    "timeOfDay": "", "lighting": "", "mood": "", "promptEn": "",
    "refImages": [ { "path": "media/scenes/sc01_01.png", "kind": "wide|detail" } ],
    "ambience": { "refAudio": "", "note": "" }
  } ],

  // ── L4 劇本（對話）─────────────────────────
  "script": {
    "episodes": [ {
      "id": "e01", "title": "",
      "scenes": [ {                       // 劇本場次，對映 scenes[] + characters[]
        "id": "e01s01", "title": "", "sceneId": "sc01", "characterIds": ["c01"],
        "summary": "",
        "beats": [ { "id": "b01", "type": "dialogue|action",
                     "characterId": "c01", "line": "", "emotion": "", "notes": "" } ]
      } ]
    } ]
  },

  // ── L5 鏡頭（核心，對映導演台 segments / asset_groups）──
  "shots": [ {
    "id": "sh01", "no": 1, "episodeSceneId": "e01s01", "sceneId": "sc01",
    "characterIds": ["c01"],
    "spec": {                            // 畫面與運鏡
      "shotType": "特寫|中景|全景|過肩|POV",
      "angle": "", "cameraMove": "", "composition": "", "lighting": ""
    },
    "durationSec": 5,
    "promptZh": "繁體中文描述（給人看/給模型擴寫）",
    "promptEn": "英文 prompt（H3 用，六段式見 h3-prompt-writing skill）",
    "dialogue": [ { "characterId": "c01", "line": "" } ],
    "images": { "first": "", "last": "" },     // 首尾幀
    "video": { "path": "", "status": "none|queued|running|done|failed",
               "taskType": "r2v|i2v|fl2v", "seed": 0, "note": "" },
    "voice": { "path": "", "engine": "" },
    "durationFrames": 0,                 // 實際送件時算出的 H3 合法影格數
    "updatedAt": ""
  } ],

  // ── 設定與執行紀錄 ─────────────────────────
  "settings": {
    "llm": { "baseUrl": "http://127.0.0.1:8080/v1", "model": "auto",
             "temperature": 0.8, "provider": "local|online" },
    "comfy": { "baseUrl": "http://127.0.0.1:8000" },
    "tts": { "engine": "fish_s2", "baseUrl": "" }
  },
  "pipeline": { "stage": "story", "logs": [ { "at": "", "step": "", "msg": "" } ] }
}
```

## 不變式（實作時要驗）

1. 任何引用（sceneId / characterId / episodeSceneId）必須存在，刪除被引用對象前先提示或改成 null。
2. `shots[].no` 在同專案內唯一、可重排，重排不影響 `id`。
3. 影片生成只吃 `promptEn`；`promptZh` 是給人看的（與 H3 skill 的六段式對齊）。
4. 所有寫入都要更新 `meta.updatedAt`；每次送出生成前把 `video.status` 從 `none` 改 `queued`。
5. 前端不得直接寫檔，一律走後端 API；後端寫檔採「讀-改-寫 + 原子替換（.tmp 再 rename）」。

---

# v0.2 補充（2026-11-17 23:20，依 `docs/dev/research-existing-tools.md` 結論）

> 全部為**向後相容**：舊專案讀進來時，缺的欄位由後端補預設值（不寫回除非使用者動到），不得因缺欄位而 400。

## 新增頂層

```jsonc
"voiceMap": {                       // 角色 id → 實際配音設定（第一次分配後固定）
  "c01": { "engine": "fish_s2", "refAudio": "media/voices/c01.wav", "assignedAt": "..." }
},
"narrator": { "engine": "", "refAudio": "", "note": "旁白專屬聲線，永不從角色池抽" }
```

## characters[] 追加

```jsonc
"identityAnchors": {                // 只讀錨點：生成 promptEn 時「逐字貼上」，不准改寫
  "faceShape": "", "facialFeatures": "", "uniqueMarks": "",
  "colorAnchors": { "hair": "#1A0A00", "eyes": "#3D2B1F", "skin": "#F5DEB3", "primaryOutfit": "#2B2B2B" },
  "skinTexture": "", "hairStyle": ""
},
"stages": [                         // 分階段造型：只有服裝/身上物件「真的變了」才新增
  { "id": "s1", "label": "入學", "fromChapterId": "ch01",
    "characterSheet": "media/characters/c01_stage1.png", "outfitId": "o01", "note": "" }
]
```

## shots[] 追加／強化

> ⚠️ **分鏡的權威規則見 `docs/spec/storyboard-contract.md`**（2026-11-21）。本節只列欄位；「誰可以寫什麼、長度怎麼算、覆蓋怎麼驗」一律以該檔為準。

```jsonc
"spec": {
  "shotType": "close_up|medium|wide|over_shoulder|pov",   // 顯示層仍可翻中文
  "angle": { "h": "front|front_left|left|back_left|back|back_right|right|front_right",
             "v": "worm|low|eye_level|high",
             "s": "close_up|medium|wide" },
  "cameraMove": "static|push_in|pull_out|pan_left|pan_right|tracking|handheld",
  "composition": "", "lighting": "", "framingRule": "has_person→medium|mcu|cu（禁 wide）"
},
"beatIds": [ "b05" ],                // **主鍵（2026-11-21 起）**：本鏡演出的 beats，有序、連續、不跨場次
"beatPart": { "beatId": "b05", "part": 1, "total": 2 },  // 單一 beat 超過 15.08 秒時拆鏡用
"role": "establish|dialogue|reaction|action|insert|closing",  // 本鏡的敘事職責
"derivedFrom": "storyboard|deploy|manual",   // 這個鏡頭是規則式分鏡、LLM 佈署、還是人手調的
"padSec": 0.0,                       // 為滿足 H3 最短 5.17 秒而留白（停留／反應）的秒數
"beats": [ { "start": 0.0, "end": 5.0, "cut": false, "action": "", "camera": "", "locationId": "" } ],
                                     // ⚠️ 已停用（自由敘述，type 常為 null）；舊資料遷移進 legacyBeats，不參與生成
"dialogBeatIds": [ "b02", "b04" ],   // 由 beatIds 內 dialogue beats 推導（本輪起衍生，不再手填）
"dialogLineRefs": [ 1, 3 ],          // 衍生值：由 dialogBeatIds 換算的「第幾句 dialogue」（1-based，舊資料相容欄位）
"dialogue": [ { "characterId": "c01", "line": "" } ],
                                     // ⚠️ 已停用：台詞一律由 beatIds → 場次 beats 取原文；舊資料保留不刪但不讀
"stageIds": [ "c01:s2" ],           // 本鏡要用角色的哪個 stage 參考圖
"sourceChapterId": "ch01",
"referencePlan": {                  // 送 H3 的參考圖槽位計畫（最多 9 張），由程式產生、模型不得自己寫
  "slots": [ { "ordinal": 1, "kind": "character", "id": "c01", "stage": "s2",
               "path": "media/characters/c01_stage1.png", "role": "portrait" },
             { "ordinal": 2, "kind": "scene", "id": "sc01", "path": "media/scenes/sc01_01.png" } ],
  "weakReferences": [ "g01" ],
  "promptPlaceholders": { "{{c01}}": "<Picture 1>", "{{sc01}}": "<Picture 2>" }
}
```

## 機械校驗（送生成前必跑，零 API 成本）

> 分鏡相關的 V1–V5（順序覆蓋、台詞覆蓋、長度合法、台詞唸得完、職責一致）以 `docs/spec/storyboard-contract.md` §4 為準，以下為其他項目。

1. `dialogBeatIds` 覆蓋所屬場次全部 dialogue（每個 dialogue beat 剛好被一鏡引用），無漏、無重複；`dialogLineRefs` 由它換算，兩者不一致時以 `dialogBeatIds` 為準並自動重算。
2. `promptEn` 內的 `<Picture N>` 必須與 `referencePlan.slots` 的 ordinal 一一對應且順序一致。
3. `durationSec` 換算的影格數落在 H3 合法格（17k+5，且 124–362 幀＝5.17–15.08 秒；`total_frames` 需 17k+1 對齊，見 contracts §3）。
4. 含人物的鏡頭不得為 wide（MSB framing rule）。
5. 場景參考圖 prompt 必須含「畫面內不得有人（含剪影、倒影、背影）」。

## 生成策略

- 先產 **1 鏡試片**，人工看過再放量（GPU 時間貴）。
- `promptEn` 走「模板 + 錨點逐字貼」，模型只在按「重新生成」時介入。
