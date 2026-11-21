"""檢查 Agent 對接文件是否完整（新增 API 卻忘了寫說明 → 這裡會失敗）。

用法：
    python tools/check_agent_doc.py
    python tools/check_agent_doc.py --json   # 機器可讀

離開碼：0 = 全部有說明；1 = 有缺（列出 method/path）。
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv: list[str]) -> int:
    from app import agentdoc, version  # noqa: IMP001（要先設好 sys.path）
    from app.server import app

    cov = agentdoc.coverage(app)
    payload = {
        "version": version.get(),
        "agentDocVersion": agentdoc.AGENT_DOC_VERSION,
        "total": cov["total"],
        "documented": cov["documented"],
        "missing": cov["missingList"],
        "guideChars": len(agentdoc.guide(app, version=version.get())),
        "manifestRoutes": len(agentdoc.manifest(app, version=version.get())["routes"]),
    }
    if "--json" in argv:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"版本 {payload['version']}｜端點 {payload['total']} 支，已說明 {payload['documented']} 支")
        print(f"操作手冊 {payload['guideChars']} 字｜manifest {payload['manifestRoutes']} 支端點")
        if cov["missingList"]:
            print("\n❌ 下列端點沒有語意說明（請補進 app/agentdoc.py 的 ROUTE_NOTES 或寫 docstring）：")
            for m in cov["missingList"]:
                print(f"   {m['method']:6} {m['path']}")
    return 0 if not cov["missingList"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
