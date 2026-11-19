"""版本號的單一真相：讀專案根目錄的 VERSION 檔。

為什麼要有這個模組：
- 之前 `server.py` 的 `/api/health` 與 `FastAPI(version=...)` 各自寫死里程碑字串
  （例如 "M5b+M6"），發版後就與 `VERSION` 對不上，驗收時無法用 health 判斷版本。
- 之後任何要顯示版本的地方一律 `from . import version` 後用 `version.get()`。

路徑規則：
- dev    : <repo>/VERSION
- frozen : _internal/VERSION（StoryVideoStudio.spec 的 datas 已收錄）
"""

from __future__ import annotations

from functools import lru_cache

from . import paths

FALLBACK = "0.0.0"


@lru_cache(maxsize=1)
def get() -> str:
    """回傳版本號字串（讀不到檔案時回 FALLBACK，不拋錯）。"""
    try:
        text = (paths.BUNDLE_DIR / "VERSION").read_text(encoding="utf-8")
    except OSError:
        return FALLBACK
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return FALLBACK
