"""範圍化一鍵佈署的離線驗證（不呼叫任何模型、不碰使用者資料）。

用法：SVS_DATA_DIR=<複本 projects> python scripts/check_deploy_scopes.py
驗證四件事（全部斷言，任何一項失敗就非 0 結束）：
A. scope=shots：只清/重建最新一集的鏡頭，劇本、角色、場景不動
B. scope=script：只多一集劇本（角色／場景／既有鏡頭不動）
C. scope=cast_locations：只多角色與場景（劇本／鏡頭不動）
D. 前置條件：空專案的 script/shots 要給中文錯誤
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import deploy, storage  # noqa: E402


def pick_project() -> str:
    projects = sorted([p for p in storage.PROJECTS_DIR.iterdir() if p.is_dir() and (p / "project.json").exists()])
    if not projects:
        raise SystemExit("沒有專案可測：請用 SVS_DATA_DIR 指到一份複本")
    # 選「有角色、場景、劇本、鏡頭」的那一支
    for p in projects:
        d = json.loads((p / "project.json").read_text(encoding="utf-8"))
        eps = ((d.get("script") or {}).get("episodes") or [])
        if d.get("characters") and d.get("scenes") and eps:
            return p.name
    return projects[0].name


def snap(slug: str) -> dict:
    p = storage.load_project(slug)
    eps = ((p.get("script") or {}).get("episodes") or [])
    return {
        "chars": [c["id"] for c in p.get("characters") or []],
        "scenes": [s["id"] for s in p.get("scenes") or []],
        "episodes": [e["id"] for e in eps],
        "es_last": [es["id"] for es in (eps[-1].get("scenes") or [])] if eps else [],
        "shots": [s["id"] for s in p.get("shots") or []],
    }


def run_scope(slug: str, scope: str, overwrite: bool) -> dict:
    job = deploy.start(slug, {"scope": scope, "overwrite": overwrite,
                              "characterCount": 2, "sceneCount": 2, "shotCount": 4, "shotSeconds": 5})
    import time
    for _ in range(120):
        time.sleep(0.2)
        j = deploy.get_job(job["jobId"])
        if j.get("status") in ("done", "failed", "cancelled"):
            return j
    raise SystemExit(f"job 沒有結束：{job['jobId']}")


def fake_gens(project: dict):
    """把四個生成函式換成假資料（沿用既有卡片內容，只換 id），用來驗落地邏輯。"""
    ch = copy.deepcopy((project.get("characters") or [{}])[0])
    sc = copy.deepcopy((project.get("scenes") or [{}])[0])
    ep = copy.deepcopy(((project.get("script") or {}).get("episodes") or [{}])[0])

    def gen_cast(_p, _cfg, _opts, first_seq=1):
        c = copy.deepcopy(ch)
        c["id"] = f"c{first_seq:02d}"
        c["name"] = "測試角色" + c["id"]
        return {"preview": {"characters": [c]}, "warnings": []}

    def gen_locations(_p, _cfg, _opts, _chars, first_seq=1):
        s = copy.deepcopy(sc)
        s["id"] = f"sc{first_seq:02d}"
        s["name"] = "測試場景" + s["id"]
        return {"preview": {"scenes": [s]}, "warnings": []}

    def gen_script(_p, _cfg, _opts, chars, scenes, episode_no=1):
        es = (ep.get("scenes") or [{}])[0]
        out = {
            "title": f"測試第 {episode_no} 集",
            "logline": "logline",
            "sourceChapterId": None,
            "scenes": [{
                "id": "tmp1",
                "title": "場次一",
                "sceneId": scenes[0]["id"],
                "characterIds": [chars[0]["id"]],
                "summary": "摘要",
                "beats": [{"id": "b1", "type": "dialogue", "characterId": chars[0]["id"],
                           "line": "測試台詞一句", "emotion": "平靜", "notes": ""}],
            }],
        }
        return {"preview": out, "warnings": []}

    def gen_shots(_p, _cfg, chars, scenes, es, count, sec, fps, _ch_map):
        return {"preview": {"shots": [{
            "durationSec": sec,
            "spec": {"shotType": "medium", "angle": {"h": "front", "v": "eye_level", "s": "medium"},
                     "cameraMove": "static", "composition": "中景", "lighting": "自然光"},
            "promptZh": "測試鏡頭",
            "promptEn": "1. Subject: x\n2. Action: y\n3. Camera: z\n4. Lighting: l\n5. Atmosphere: a\n6. Audio: s",
            "dialogLineRefs": [1],
            "characterIds": list(es.get("characterIds") or []),
        } for _ in range(count)]}, "warnings": []}

    deploy.gen_cast = gen_cast
    deploy.gen_locations = gen_locations
    deploy.gen_script = gen_script
    deploy.gen_shots_for_scene = gen_shots


def main() -> int:
    slug = pick_project()
    print(f"測試專案：{slug}")
    before = snap(slug)
    print("初始：", before)
    project = storage.load_project(slug)
    fake_gens(project)

    # A. 分鏡
    j = run_scope(slug, "shots", True)
    assert j["status"] == "done", f"shots 失敗：{j.get('error')}"
    after = snap(slug)
    assert after["episodes"] == before["episodes"], "分鏡範圍不該動劇本"
    assert after["chars"] == before["chars"] and after["scenes"] == before["scenes"], "分鏡範圍不該動角色/場景"
    assert len(after["shots"]) >= len(after["es_last"]), "最新一集每場至少要有一鏡"
    old_last = [s for s in before["shots"]]
    kept = [s for s in after["shots"] if s in old_last]
    print(f"A 分鏡：鏡頭 {len(old_last)} → {len(after['shots'])}（最新一集重拆、其他保留 {len(kept)}）")

    # B. 劇本（append）
    j = run_scope(slug, "script", False)
    assert j["status"] == "done", f"script 失敗：{j.get('error')}"
    after2 = snap(slug)
    assert len(after2["episodes"]) == len(after["episodes"]) + 1, "劇本範圍應該只多一集"
    assert after2["chars"] == after["chars"] and after2["scenes"] == after["scenes"], "劇本範圍不該動角色/場景"
    assert after2["shots"] == after["shots"], "劇本範圍不該動既有鏡頭"
    print(f"B 劇本：集數 {len(after['episodes'])} → {len(after2['episodes'])}，鏡頭不動")

    # C. 角色與場景（append）
    j = run_scope(slug, "cast_locations", False)
    assert j["status"] == "done", f"cast_locations 失敗：{j.get('error')}"
    after3 = snap(slug)
    assert len(after3["chars"]) == len(after2["chars"]) + 1, "應該多一位角色"
    assert len(after3["scenes"]) == len(after2["scenes"]) + 1, "應該多一個場景"
    assert after3["episodes"] == after2["episodes"] and after3["shots"] == after2["shots"], "角色/場景範圍不該動劇本/鏡頭"
    print(f"C 角色與場景：角色 {len(after2['chars'])} → {len(after3['chars'])}、場景 {len(after2['scenes'])} → {len(after3['scenes'])}")

    # D. 前置條件
    errs = []
    empty = {"story": {"premise": "x"}, "characters": [], "scenes": [], "script": {"episodes": []}, "shots": []}
    for scope in ("script", "shots"):
        try:
            deploy._check_prereq(empty, scope)
            errs.append(f"{scope} 沒有擋下來")
        except deploy.DeployError as e:
            print(f"D 前置（{scope}）：{e}")
    assert not errs, errs
    print("全部通過 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
