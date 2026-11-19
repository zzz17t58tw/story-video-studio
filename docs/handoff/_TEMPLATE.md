---
branch: <分支名，例：m10a-lora-ui>
milestone: <例：M10a>
status: wip            # wip | ready | merged
opened: <YYYY-MM-DD HH:MM（台北）>
files: []              # 精確路徑清單，例：["app/h3gen.py", "static/app.js"]
---

# 交接單：<分支名>

## 做了什麼

<一到三句，講結果不講過程。>

## 動到的檔案

- `app/xxx.py`

## 介面變更

<新增/修改的 API 路徑、JSON 欄位、函式簽章。沒有就寫「無」。>

## 未完成／已知限制

<誠實寫。沒做完的、寫死的、只在 mock 驗過的，全部列出來。>

## 驗證方式

```bash
# 一行可重跑的指令或測試，主對話會照著跑
```

## 是否動到契約（data-model.md / contracts.md）

<正常應為「沒有」。有動的話寫出動了什麼、為什麼，並說明主對話已核准。>
