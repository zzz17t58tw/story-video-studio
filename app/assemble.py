"""M6：把各鏡頭的影片合併成一支長片（final_<時間戳>.mp4）。

依據 docs/contracts.md §5：
  · 方案 A（優選）：各段參數已統一（同 codec/pix_fmt/fps/解析度/音訊取樣率）→ 直接 `-f concat -c copy`，
    零轉碼；參數不同卻硬 copy 會造成時間戳錯位（實測 2.52s ≠ 2.0s）。
  · 方案 B（兜底）：參數不一致 → `filter_complex` 現場統一（scale+pad+setsar+fps / aresample）再 concat。
    交錯順序必須是 [v0][a0][v1][a1]…（先寫 [v0][0:a] 會報 Media type mismatch）。

對外函式：
  ordered_segments(project)         -> [{no, shotId, path, exists, ...}]（依鏡頭 no 排序，缺片的也列出來）
  plan(project, skip_missing)       -> 檢查清單（有哪些缺片、哪些參數不一致）
  assemble(project, slug, opts)     -> dict（同步執行；回 path / duration / bytes / frames / mode）

設計紀律：
  · 不改 templates/、不改 project.json（除了 pipeline.logs 追加一筆）；
  · 缺片一律「列出來」讓呼叫端決定 skipMissing，不偷偷跳過；
  · ffmpeg 一律用系統的（shutil.which），沒裝就回中文錯誤。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from . import storage

# 統一的輸出參數（contracts §5 方案 A 的目標值）
TARGET_VCODEC = "h264"
TARGET_PIX_FMT = "yuv420p"
TARGET_FPS = 24
TARGET_ACODEC = "aac"
TARGET_AR = 48000
FFMPEG_TIMEOUT = 600          # 單次 ffmpeg 最長 10 分鐘（幾十秒的短片用不到，防呆）


class AssembleError(Exception):
    """可預期錯誤（中文，可直接顯示給使用者）。"""


def _s(v: Any) -> str:
    return str(v or "").strip()


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise AssembleError("找不到 ffmpeg（系統 PATH 裡沒有）。合併需要 ffmpeg：請確認 `ffmpeg -version` 可用")
    return exe


def _ffprobe() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise AssembleError("找不到 ffprobe（合併前要讀各段參數）。請確認 `ffprobe -version` 可用")
    return exe


def _run(cmd: list[str], timeout: int = FFMPEG_TIMEOUT) -> subprocess.CompletedProcess:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as e:
        raise AssembleError(f"ffmpeg 逾時（{timeout} 秒）被中止：{' '.join(cmd[:6])} …") from e
    except OSError as e:
        raise AssembleError(f"執行 ffmpeg 失敗：{type(e).__name__}: {e}") from e
    return p


def _tail(text: str, n: int = 1200) -> str:
    text = (text or "").strip()
    return text[-n:] if len(text) > n else text


# ── 1. 取鏡頭與影片 ────────────────────────────────────────────
def _sort_key(shot: dict, idx: int) -> tuple:
    try:
        return (0, int(shot.get("no")), idx)
    except (TypeError, ValueError):
        return (1, idx, idx)


def ordered_segments(project: dict) -> list[dict]:
    """依鏡頭 no 排序回 [{no, shotId, path, exists, title, durationSec}]。缺片也在清單裡（exists=False）。"""
    slug = _s((project.get("meta") or {}).get("slug"))
    pdir = storage.project_dir(slug) if slug else Path(".")
    shots = [s for s in (project.get("shots") or []) if isinstance(s, dict)]
    out: list[dict] = []
    for idx, sh in enumerate(shots):
        v = sh.get("video") if isinstance(sh.get("video"), dict) else {}
        rel = _s(v.get("path"))
        p = (pdir / rel) if rel else None
        exists = bool(p and p.is_file() and p.stat().st_size > 0)
        out.append({
            "no": sh.get("no"), "shotId": _s(sh.get("id")), "path": rel, "exists": exists,
            "status": _s(v.get("status")) or ("done" if exists else "none"),
            "title": _s(sh.get("title")) or _s(sh.get("titleZh")),
            "durationSec": sh.get("durationSec"),
            "_key": _sort_key(sh, idx),
        })
    out.sort(key=lambda x: x.pop("_key"))
    return out


def probe(path: str | Path) -> dict:
    """讀一支影片的關鍵參數（codec/pix_fmt/fps/解析度/音訊取樣率/聲道/時長/幀數）。"""
    exe = _ffprobe()
    p = _run([exe, "-v", "error", "-show_entries",
              "stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,"
              "pix_fmt,sample_rate,channels,nb_frames,duration",
              "-show_entries", "format=duration,size", "-of", "json", str(path)], timeout=120)
    if p.returncode != 0:
        raise AssembleError(f"ffprobe 讀不到 {Path(path).name}：{_tail(p.stderr, 300)}")
    try:
        info = json.loads(p.stdout or "{}")
    except json.JSONDecodeError as e:
        raise AssembleError(f"ffprobe 輸出不是 JSON（{Path(path).name}）") from e
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), {})
    fmt = info.get("format") or {}

    def _fps(raw: str) -> float | None:
        try:
            num, den = _s(raw).split("/")
            d = float(den or 1)
            return round(float(num) / d, 3) if d else None
        except Exception:  # noqa: BLE001
            return None

    try:
        dur = round(float(fmt.get("duration") or v.get("duration") or 0), 3)
    except (TypeError, ValueError):
        dur = None
    return {
        "vcodec": _s(v.get("codec_name")), "acodec": _s(a.get("codec_name")),
        "width": v.get("width"), "height": v.get("height"), "pix_fmt": _s(v.get("pix_fmt")),
        "fps": _fps(v.get("r_frame_rate")), "avgFps": _fps(v.get("avg_frame_rate")),
        "sampleRate": int(a["sample_rate"]) if _s(a.get("sample_rate")).isdigit() else None,
        "channels": a.get("channels"), "hasAudio": bool(a),
        "duration": dur, "bytes": int(fmt["size"]) if _s(fmt.get("size")).isdigit() else None,
        "frames": v.get("nb_frames"),
    }


# ── 2. 檢查能不能直接 copy ─────────────────────────────────────
def _uniform(probes: list[dict]) -> tuple[bool, list[str]]:
    """各段參數是否一致到可以 `-c copy`。回 (bool, 不一致的原因清單)。"""
    if not probes:
        return False, ["沒有可用的影片"]
    reasons: list[str] = []
    first = probes[0]
    keys = [
        ("width", "解析度寬"), ("height", "解析度高"), ("vcodec", "影片編碼"),
        ("acodec", "音訊編碼"), ("sampleRate", "音訊取樣率"), ("channels", "音訊聲道"),
        ("hasAudio", "有無音軌"), ("pix_fmt", "像素格式"),
    ]
    for i, pr in enumerate(probes[1:], start=2):
        for k, label in keys:
            if pr.get(k) != first.get(k):
                reasons.append(f"第 {i} 段的{label}（{pr.get(k)}）與第 1 段（{first.get(k)}）不同")
        if pr.get("fps") and first.get("fps") and abs(pr["fps"] - first["fps"]) > 0.001:
            reasons.append(f"第 {i} 段的 fps（{pr['fps']}）與第 1 段（{first['fps']}）不同")
    # 就算各段互相同，也要符合目標參數才敢 copy（否則合併後播放器相容性不一）
    if first.get("vcodec") != TARGET_VCODEC:
        reasons.append(f"影片編碼是 {first.get('vcodec') or '?'}（要 {TARGET_VCODEC} 才能直接 copy）")
    if first.get("pix_fmt") and first["pix_fmt"] != TARGET_PIX_FMT:
        reasons.append(f"像素格式是 {first['pix_fmt']}（要 {TARGET_PIX_FMT}）")
    if first.get("fps") and abs(first["fps"] - TARGET_FPS) > 0.001:
        reasons.append(f"fps 是 {first['fps']}（要 {TARGET_FPS}）")
    if first.get("acodec") and first["acodec"] != TARGET_ACODEC:
        reasons.append(f"音訊編碼是 {first['acodec']}（要 {TARGET_ACODEC}）")
    if first.get("sampleRate") and first["sampleRate"] != TARGET_AR:
        reasons.append(f"音訊取樣率是 {first['sampleRate']}（要 {TARGET_AR}）")
    # 去掉重複（多段同樣原因只講一次）
    seen: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.append(r)
    return (not seen), seen


def plan(project: dict, skip_missing: bool = False) -> dict:
    """合併前檢查：缺哪些片、參數一致不一致、能不能直接 copy。不執行 ffmpeg。"""
    segs = ordered_segments(project)
    missing = [s for s in segs if not s["exists"]]
    usable = [s for s in segs if s["exists"]]
    slug = _s((project.get("meta") or {}).get("slug"))
    pdir = storage.project_dir(slug) if slug else Path(".")
    probes: list[dict] = []
    probe_errors: list[str] = []
    for s in usable:
        try:
            pr = probe(pdir / s["path"])
            s["probe"] = pr
            probes.append(pr)
        except AssembleError as e:
            probe_errors.append(f"{s['shotId']}：{e}")
    uniform, reasons = _uniform(probes) if probes else (False, ["沒有可用的影片"])
    total = round(sum((pr.get("duration") or 0) for pr in probes), 3)
    resol = ({"width": probes[0].get("width"), "height": probes[0].get("height")} if probes else {})
    return {
        "segments": segs, "missing": missing, "usable": usable,
        "uniform": uniform, "reasons": reasons, "probeErrors": probe_errors,
        "targetDuration": total, "resolution": resol,
        "suggestMode": "copy" if uniform else "reencode",
        "canAssemble": bool(usable) and (skip_missing or not missing),
    }


# ── 3. 合併 ────────────────────────────────────────────────────
def _concat_copy(files: list[Path], tmpdir: Path, out: Path) -> None:
    """方案 A：-f concat -c copy。list.txt 用絕對路徑（含單引號要轉義）。"""
    lines = []
    for f in files:
        # 一律 resolve() 成絕對路徑：concat demuxer 對相對路徑的解析基準是 list.txt 的目錄，
        # 傳相對路徑會變成「.scratch/t_asm/.scratch/t_asm/a.mp4」這種疊兩層的錯路徑（實測踩過）。
        # 檔名轉義：單引號寫成 '\''
        p = str(Path(f).resolve()).replace("'", "'\\''")
        lines.append(f"file '{p}'")
    lst = tmpdir / "concat_list.txt"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    exe = _ffmpeg()
    cmd = [exe, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "concat", "-safe", "0", "-i", str(lst),
           "-c", "copy", "-movflags", "+faststart", str(out)]
    p = _run(cmd)
    if p.returncode != 0 or not out.is_file():
        raise AssembleError(f"直接合併（copy）失敗：{_tail(p.stderr)}")
    lst.unlink(missing_ok=True)


def _concat_filter(files: list[Path], width: int, height: int, probes: list[dict], out: Path) -> None:
    """方案 B：filter_complex 現場統一（scale/pad/setsar/fps + aresample）再 concat。

    交錯順序必須 [v0][a0][v1][a1]…（contracts §5 的實測坑）。
    沒有音軌的段落用 anullsrc 補靜音軌，避免 concat 對不上。
    """
    exe = _ffmpeg()
    base: list[str] = [exe, "-y", "-hide_banner", "-loglevel", "error"]
    parts: list[str] = []
    labels: list[str] = []
    n_in = 0        # 目前已加的 -i 個數（＝下一個輸入的 index）；補出來的 lavfi 也算
    for i, (f, pr) in enumerate(zip(files, probes)):
        dur = pr.get("duration") or 1.0
        has_v = bool(pr.get("width") and pr.get("height"))

        def _add_input(args: list[str]) -> int:
            nonlocal n_in
            base.extend(args)
            n_in += 1
            return n_in - 1

        # 影像：沒有影像流（只有音軌的奇怪的檔）就補一段純黑畫面，否則 [n:v] 不存在
        if has_v:
            idx_v = _add_input(["-i", str(Path(f).resolve())])
        else:
            idx_v = _add_input(["-f", "lavfi", "-t", f"{dur:.3f}", "-i",
                                f"color=c=black:s={width}x{height}:r={TARGET_FPS}"])
        # 音訊：沒有音軌就補靜音，否則 concat 的 a=1 對不上
        if pr.get("hasAudio"):
            idx_a = _add_input(["-i", str(Path(f).resolve())]) if not has_v else idx_v
            parts.append(f"[{idx_a}:a]aresample={TARGET_AR},"
                         f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        else:
            idx_a = _add_input(["-f", "lavfi", "-t", f"{dur:.3f}", "-i",
                                f"anullsrc=r={TARGET_AR}:cl=stereo"])
            parts.append(f"[{idx_a}:a]aresample={TARGET_AR},"
                         f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        parts.append(
            f"[{idx_v}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={TARGET_FPS},format={TARGET_PIX_FMT}[v{i}]")
        labels.append(f"[v{i}][a{i}]")
    parts.append(f"{''.join(labels)}concat=n={len(files)}:v=1:a=1[v][a]")
    cmd = base + ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", TARGET_PIX_FMT, "-r", str(TARGET_FPS),
            "-c:a", TARGET_ACODEC, "-ar", str(TARGET_AR),
            "-movflags", "+faststart", str(out)]
    p = _run(cmd)
    if p.returncode != 0 or not out.is_file():
        raise AssembleError(f"重新編碼合併失敗：{_tail(p.stderr)}")


def assemble(project: dict, slug: str, opts: dict | None = None) -> dict:
    """同步合併（幾十秒內）。opts: mode='copy'|'reencode'（'auto' 也可）、skipMissing=bool。

    失敗一律拋 AssembleError（中文）。成功回 dict（path/abs/mode/duration/bytes/frames/segments…）。
    """
    opts = opts or {}
    mode = _s(opts.get("mode")) or "auto"
    if mode not in ("auto", "copy", "reencode"):
        raise AssembleError(f"不支援的合併模式：{mode}（合法值：copy／reencode／auto）")
    skip_missing = bool(opts.get("skipMissing", False))
    pl = plan(project, skip_missing=skip_missing)
    usable = pl["usable"]
    if not usable:
        raise AssembleError("這個專案沒有任何已生成的影片可合併（請先在鏡頭頁或本頁批次生影片）")
    if pl["missing"] and not skip_missing:
        names = "、".join(f"{m['shotId']}（第 {m['no']} 鏡）" for m in pl["missing"])
        raise AssembleError(f"有 {len(pl['missing'])} 個鏡頭還沒有影片：{names}。"
                            f"要略過它們直接合併，請勾「略過沒有影片的鏡頭」")
    if skip_missing and pl["missing"]:
        pass  # 已在 usable 裡排除

    pdir = storage.project_dir(slug)
    files = [pdir / s["path"] for s in usable]
    probes = [s.get("probe") or probe(f) for s, f in zip(usable, files)]

    outdir = pdir / "output"
    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / f"final_{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    tmpdir = outdir
    t0 = time.time()

    use_copy = None
    if mode == "copy":
        if not pl["uniform"]:
            raise AssembleError("各段參數不一致，不能直接 copy（會時間戳錯位、播放會抖）："
                                + "；".join(pl["reasons"][:4])
                                + "。要直接串請先讓各段參數一致（用「統一重編碼」跑過一次），或用「自動／重新編碼」模式")
        use_copy = True
    elif mode == "reencode":
        use_copy = False
    else:
        use_copy = pl["uniform"]

    first = probes[0]
    width = int(first.get("width") or 720)
    height = int(first.get("height") or 1280)
    if use_copy:
        _concat_copy(files, tmpdir, target)
        mode_used = "copy"
    else:
        _concat_filter(files, width, height, probes, target)
        mode_used = "reencode"

    elapsed = round(time.time() - t0, 1)
    pr = probe(target)
    expected = pl["targetDuration"]
    got = pr.get("duration") or 0
    drift = round(got - expected, 3)
    res = {
        "relPath": f"output/{target.name}", "abs": str(target), "bytes": target.stat().st_size,
        "mode": mode_used, "duration": got, "expectedDuration": expected, "driftSec": drift,
        "width": pr.get("width"), "height": pr.get("height"), "fps": pr.get("fps"),
        "frames": pr.get("frames"), "vcodec": pr.get("vcodec"), "acodec": pr.get("acodec"),
        "hasAudio": pr.get("hasAudio"), "elapsed": elapsed,
        "segments": [{"no": s["no"], "shotId": s["shotId"], "path": s["path"],
                      "duration": (s.get("probe") or {}).get("duration")} for s in usable],
        "skipped": [{"no": m["no"], "shotId": m["shotId"], "reason": "沒有影片"} for m in pl["missing"]],
        "syncedOk": abs(drift) <= max(0.3, 0.02 * max(expected, 1.0)),
    }
    # 只在 pipeline.logs 追加一筆（其餘欄位不動）
    try:
        fresh = storage.load_project(slug)
        logs = fresh.setdefault("pipeline", {}).setdefault("logs", [])
        logs.append({
            "at": storage.now_iso(), "step": "assemble",
            "msg": (f"合併成長片：{res['relPath']}｜{mode_used}｜{len(usable)} 段｜"
                    f"{got} 秒（各段合計 {expected} 秒）｜{res['width']}x{res['height']}｜"
                    f"{res['bytes']} bytes｜{elapsed} 秒完成"),
            "path": res["relPath"], "mode": mode_used, "duration": got,
            "segments": [s["shotId"] for s in usable], "elapsed": elapsed,
        })
        if len(logs) > 200:
            del logs[:-200]
        storage.save_project(slug, fresh)
    except (storage.StorageError, OSError):
        pass          # 合併本身成功就是成功，log 寫不進去不該報失敗
    return res
