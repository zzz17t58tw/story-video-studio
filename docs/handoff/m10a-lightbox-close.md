---
branch: feat/m10a-lightbox-close
milestone: M10a
status: merged            # wip | ready | merged
opened: 2026-11-19 11:47（台北）
files: ["static/app.js"]  # 精確路徑清單，例：["app/h3gen.py", "static/app.js"]
---

# 交接單：m10a-lightbox-close

## 做了什麼

修好燈箱關不掉：原本 `bindLightboxEvents` 是 app.js 載入當下立即執行的 IIFE，而 `<script src="/app.js">` 在 `<head>` 尾端、`#lightbox` 在 `<body>` 下方尚未解析，`if (!box) return;` 直接把事件綁定跳過。改為掛在 **document** 上的事件委派（click + keydown），且每次事件時才 `getElementById("lightbox")`，因此節點何時出現都不影響。順手把 `openLightbox` 的 `lightboxEl` 快取改成「取到且 `isConnected` 才快取，否則每次重試」，消除同類 DOM 未就緒問題（先前若快取到 null 會永久 fallback 到 `window.open` 開新分頁）。

## 動到的檔案

- `static/app.js`（`bindLightbox` / `openLightbox` 的快取邏輯、檔尾燈箱事件改 document 委派）

## 介面變更

無。未動任何 API 路徑、JSON 欄位或後端模組；`openLightbox(src, caption)` 對外行為不變，`bindLightbox()` 改為回傳節點（舊呼叫端忽略回傳值仍相容）。

## 未完成／已知限制

- 無未完成項。四種關閉路徑實測全通過。
- 已知限制（非本卡範圍，未動）：`SVSConfirm` 內 `document.removeEventListener("keydown", onKey)` 寫在 `const onKey` 宣告之前（TDZ），該行實為死碼。不影響功能（監聽確實有掛上、並在 `done()` 時移除），若之後要清可另開卡。
- 聊天抽屜的 Esc 原本就有「燈箱開著時不關抽屜」的守門（`.lightbox:not(.hidden)`），本次改動後依然成立，已一併驗過。

## 驗證方式

```bash
cd "C:/Users/user/Desktop/hermes work/1117-wt-m10a"
node --check static/app.js
python -m uvicorn app.server:app --host 127.0.0.1 --port 8790
# 瀏覽器開 http://127.0.0.1:8790 → 建專案 → 上傳一張圖到 characters → PUT 寫進 characters[0].refImages
# → 人物頁點縮圖：
#   (a) #lightbox class 變 "lightbox"
#   (b) 點 .lb-close → 回到 "lightbox hidden"
#   (c) 再開、按 Esc → "lightbox hidden"
#   (d) 再開、點背景 → "lightbox hidden"
```

實測輸出（tmp 專案 `zz-m10a-lightbox-test`、64×64 PNG；測完 DELETE，worktree 內 `projects/` 已移除）：

```
(a) after click thumb: lightbox | img src: /files/zz-m10a-lightbox-test/media/characters/20261119-114835_m10a_ref.png
(b) after click .lb-close: lightbox hidden
(c) after Esc: lightbox hidden
(d) after click background: lightbox hidden
node --check static/app.js → NODE_CHECK_OK
```

## 是否動到契約（data-model.md / contracts.md）

沒有。只改 `static/app.js`，未動 docs/data-model.md、docs/contracts.md、PLAN.md。
