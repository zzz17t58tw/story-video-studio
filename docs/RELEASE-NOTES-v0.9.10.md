# 發佈紀錄 v0.9.10

| 項目 | 值 |
|---|---|
| 版本 | **0.9.10**（預發版） |
| 日期 | 2026-11-22 |
| 發佈時間 | 2026-11-22 13:42:40（台北） |
| build 時間 | 2026-11-22 13:41:56 |
| git HEAD（發佈前） | 4d79190 fix: 爆點＝抓注意力、鉤子＝想看下一段，前3秒正名為開場爆點（v0.9.10） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.7 MB | `8e4e1b864bb33259f51992bad141b25ec2dbc7dbb0ce987c97d71ac14ab3996e` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.10-乾淨版.zip` | 128.7 MB（134,907,075 B） | `9a7e94833382685569758741fdf3a1a6ce81d2ffccebaa71cb1d1c305d4bfb20` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.10`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.10  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.10-乾淨版.zip
```

## 本版變更摘要

### 修正：爆點與鉤子的定義寫對——爆點抓當下注意力，鉤子勾下一段

**為什麼**：使用者指出 0.9.9 把兩者寫成「情節形式」而不是「功能」，故事的發展因此還是錯的。原稿把爆點定義成「反轉／打臉／收割」三選一、把鉤子定義成「未完成的動作」，模型照著寫會得到「結構完整但抓不住人」的段落。

- **爆點＝抓住觀眾注意力的那一下**：必須具體到一句台詞或一個畫面（一巴掌、刀抵住脖子、血濺到鏡頭、身世當眾揭露、當眾羞辱）。常見形式是反轉／打臉／衝擊畫面，但真正的標準是「正在滑手機的人，這一下會不會停住」，不准用「衝突升級」「氣氛緊張」這種抽象詞交差。**位置可以在一回的中段**。
- **鉤子＝勾起「想看下一段」的懸念**：一個未解的問題或未完成的危機（門後面是誰、他為什麼有那把刀、那句話是什麼意思）。標準是「在這裡關掉，會不會覺得沒看到答案」，**位置一定在結尾**。
- **前 3 秒正名為「開場爆點」**：原本誤標成「鉤子」。開場要的是抓住注意力（畫面＋一句台詞），不是留懸念。
- 大綱三段改名「開場爆點／主線／爆點與鉤子表」，章回四段改為「開場爆點→二選一→中段爆點→結尾鉤子」，並明確寫出兩者位置不同。只改提示詞（`prompts.py` 與 `static/app.js` 兩處同步），沒有新增任何機械檢查。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
