#!/usr/bin/env python3
"""交接單工具（分支協作流程的檢查器）。

用法：
    python tools/handoff.py new m10a-lora-ui --milestone M10a
    python tools/handoff.py list
    python tools/handoff.py check [分支名|all]
    python tools/handoff.py brief <分支名>

規則見 docs/dev/BRANCH-WORKFLOW.md。
本工具除了 new 之外皆為唯讀，不會修改你的程式碼或 git 狀態。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
HANDOFF_DIR = ROOT / "docs" / "handoff"
TEMPLATE = HANDOFF_DIR / "_TEMPLATE.md"
PLAN = ROOT / "PLAN.md"

REQUIRED_SECTIONS = [
    "做了什麼",
    "動到的檔案",
    "介面變更",
    "未完成／已知限制",
    "驗證方式",
    "是否動到契約（data-model.md / contracts.md）",
]
REQUIRED_KEYS = ["branch", "milestone", "status", "opened", "files"]
VALID_STATUS = ("wip", "ready", "merged")


# ── 解析 ────────────────────────────────────────────────────
def parse_handoff(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    data = {"path": path, "branch": path.stem, "meta": {}, "raw": text, "sections": {}}

    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    body = text
    if m:
        fm, body = m.group(1), m.group(2)
        for line in fm.splitlines():
            line = line.rstrip()
            if not line or line.lstrip().startswith("#") or ":" not in line:
                continue
            key, _, val = line.partition(":")
            val = val.split(" #")[0].strip()  # 去掉行尾註解
            data["meta"][key.strip()] = val
        if data["meta"].get("branch"):
            data["branch"] = data["meta"]["branch"]

    files = data["meta"].get("files", "")
    if files.startswith("["):
        try:
            data["meta"]["files_list"] = json.loads(files)
        except json.JSONDecodeError:
            data["meta"]["files_list"] = []
    else:
        data["meta"]["files_list"] = []

    # 切 ## 段
    cur = None
    buf = []
    for line in body.splitlines():
        if line.startswith("## "):
            if cur:
                data["sections"][cur] = "\n".join(buf).strip()
            cur = line[3:].strip()
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur:
        data["sections"][cur] = "\n".join(buf).strip()

    # 「動到的檔案」段落內的清單也算數
    sec_files = []
    for line in data["sections"].get("動到的檔案", "").splitlines():
        for hit in re.findall(r"`([^`]+)`", line):
            sec_files.append(hit.strip())
    data["section_files"] = sec_files
    return data


def all_handoffs() -> list[Path]:
    if not HANDOFF_DIR.exists():
        return []
    return sorted(p for p in HANDOFF_DIR.glob("*.md") if not p.name.startswith("_"))


# ── 指令 ────────────────────────────────────────────────────
def cmd_new(args: list[str]) -> int:
    if not args:
        print("用法：python tools/handoff.py new <分支名> [--milestone M10a]")
        return 2
    branch = args[0]
    milestone = ""
    if "--milestone" in args:
        milestone = args[args.index("--milestone") + 1]
    target = HANDOFF_DIR / f"{branch}.md"
    if target.exists():
        print(f"已存在：{target.relative_to(ROOT)}（不覆寫）")
        return 1
    if not TEMPLATE.exists():
        print(f"找不到模板：{TEMPLATE}")
        return 1
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("<分支名，例：m10a-lora-ui>", branch)
    text = text.replace("# 交接單：<分支名>", f"# 交接單：{branch}")
    text = text.replace("branch: <分支名，例：m10a-lora-ui>", f"branch: {branch}")
    if milestone:
        text = re.sub(r"^milestone: .*$", f"milestone: {milestone}", text, flags=re.M)
    text = re.sub(r"^opened: .*$", f"opened: {now}（台北）", text, flags=re.M)
    target.write_text(text, encoding="utf-8")
    print(f"已建立：{target.relative_to(ROOT)}")
    return 0


def cmd_list(_args: list[str]) -> int:
    paths = all_handoffs()
    if not paths:
        print("docs/handoff/ 目前沒有交接單。用 new 建一張。")
        return 0
    print("狀態\t里程碑\t分支\t開啟時間")
    print("-" * 78)
    for p in paths:
        d = parse_handoff(p)
        meta = d["meta"]
        print(
            f"{meta.get('status','?')}\t{meta.get('milestone','?')}\t"
            f"{d['branch']}\t{meta.get('opened','?')}"
        )
    ready = [parse_handoff(p) for p in paths]
    n_ready = sum(1 for d in ready if d["meta"].get("status") == "ready")
    print("-" * 78)
    print(f"共 {len(paths)} 張，其中待合併（ready）{n_ready} 張。")
    return 0


def check_one(d: dict) -> list[str]:
    """回傳問題清單（空 = 通過）。"""
    problems = []
    meta = d["meta"]
    if not meta:
        problems.append("缺少 front matter（--- ... --- 區塊）")
    for key in REQUIRED_KEYS:
        if key not in meta or not str(meta.get(key, "")).strip():
            problems.append(f"front matter 缺 `{key}`")

    status = meta.get("status", "")
    if status and status not in VALID_STATUS:
        problems.append(f"status `{status}` 不合法（只能是 {'/'.join(VALID_STATUS)}）")

    for sec in REQUIRED_SECTIONS:
        if sec not in d["sections"]:
            problems.append(f"缺少段落「## {sec}」")
        elif not d["sections"][sec].strip():
            problems.append(f"段落「## {sec}」是空的")

    # 模板佔位符殘留（排除 HTML 標籤：含空白／=／斜線／引號，或常見 tag 名）
    if "<" in d["raw"] and ">" in d["raw"]:
        HTML_TAGS = {
            "head", "body", "script", "style", "div", "span", "img", "a", "p", "br",
            "td", "tr", "table", "html", "button", "input", "section", "link", "meta",
        }
        cands = re.findall(r"<([^<>\n]{2,40})>", d["raw"])
        leftovers = [
            f"<{c}>"
            for c in cands
            if not any(ch in c for ch in " =/\"'")
            and c.split()[0].lower() not in HTML_TAGS
        ]
        if leftovers:
            problems.append(f"還有模板佔位符未填：{leftovers[:3]}")

    # 檔案清單
    files = meta.get("files_list") or d["section_files"]
    if not files:
        problems.append("沒有任何檔案清單（front matter files 或「動到的檔案」段）")
    for f in files:
        if f.startswith("`") or f.startswith("<"):
            continue
        if not (ROOT / f).exists():
            problems.append(f"列出的檔案不存在：{f}")

    # PLAN.md 對應里程碑
    if PLAN.exists():
        plan_text = PLAN.read_text(encoding="utf-8")
        ms = meta.get("milestone", "")
        if ms and ms not in plan_text:
            problems.append(f"PLAN.md 找不到里程碑「{ms}」")

    # 契約檔保護
    contract_sec = d["sections"].get("是否動到契約（data-model.md / contracts.md）", "")
    touched = [f for f in files if f in ("docs/spec/data-model.md", "docs/spec/contracts.md")]
    if touched and "主對話" not in contract_sec and "核准" not in contract_sec:
        problems.append(f"動到契約檔 {touched} 但未註明主對話核准")
    return problems


def git_branch_diff(branch: str) -> list[str] | None:
    """若分支存在，回傳 git diff --name-only main...<branch>；否則 None。"""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return None
        r = subprocess.run(
            ["git", "diff", "--name-only", f"main...{branch}"],
            cwd=ROOT, capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0:
            return None
        return [l.strip() for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        return None


def cmd_check(args: list[str]) -> int:
    target = args[0] if args else "all"
    paths = all_handoffs()
    if target != "all":
        paths = [p for p in paths if p.stem == target or parse_handoff(p)["branch"] == target]
        if not paths:
            print(f"找不到交接單：{target}")
            return 1
    if not paths:
        print("docs/handoff/ 目前沒有交接單，沒有東西要檢查。")
        return 0

    total_problems = 0
    for p in paths:
        d = parse_handoff(p)
        problems = check_one(d)
        name = d["branch"]
        print(f"\n=== {name} ({d['meta'].get('status','?')}) ===")
        if not problems:
            print("  ✅ 格式通過")
        else:
            total_problems += len(problems)
            for prob in problems:
                print(f"  ❌ {prob}")

        # git 對帳：宣稱的檔案 vs 實際改的檔案
        claimed = set(d["meta"].get("files_list") or d["section_files"])
        # 交接單本身也是分支產物，不算「宣稱沒改卻偷改」
        try:
            claimed.add(p.relative_to(ROOT).as_posix())
        except ValueError:
            pass
        actual = git_branch_diff(name)
        if actual is None:
            print("  ℹ 找不到同名 git 分支，跳過 diff 對帳")
        else:
            unclaimed = [f for f in actual if f not in claimed]
            phantom = [f for f in claimed if f not in actual]
            if unclaimed:
                print(f"  ⚠ 實際改了但交接單沒列：{unclaimed}")
            if phantom:
                print(f"  ⚠ 交接單列了但實際沒改：{phantom}")
            if not unclaimed and not phantom:
                print(f"  ✅ git 對帳相符（{len(actual)} 檔）")

    print(f"\n{'─'*60}")
    if total_problems:
        print(f"共 {total_problems} 個格式問題，修好再合併。")
        return 1
    print("格式全部通過。")
    return 0


def cmd_brief(args: list[str]) -> int:
    if not args:
        print("用法：python tools/handoff.py brief <分支名>")
        return 2
    target = args[0]
    for p in all_handoffs():
        d = parse_handoff(p)
        if p.stem != target and d["branch"] != target:
            continue
        meta = d["meta"]
        print(f"# 交接摘要：{d['branch']}")
        print(f"里程碑：{meta.get('milestone','?')}　狀態：{meta.get('status','?')}　開啟：{meta.get('opened','?')}")
        for sec in REQUIRED_SECTIONS:
            body = d["sections"].get(sec, "（缺）").strip()
            print(f"\n## {sec}\n{body}")
        return 0
    print(f"找不到交接單：{target}")
    return 1


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    handlers = {"new": cmd_new, "list": cmd_list, "check": cmd_check, "brief": cmd_brief}
    if cmd not in handlers:
        print(f"未知指令：{cmd}\n")
        print(__doc__)
        return 2
    return handlers[cmd](rest)


if __name__ == "__main__":
    sys.exit(main())
