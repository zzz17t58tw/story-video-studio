"""M4a：本機 ComfyUI 的最小客戶端（可用性、上傳參考圖、送件、輪詢、取圖）。

只用 httpx（已裝）。所有錯誤訊息一律中文、可直接顯示給使用者，呼叫端不需要自己翻。

契約依據 docs/contracts.md §4（Qwen 2.1 payload）與 ComfyUI 內建 HTTP API：
  GET  /system_stats          可用性與 VRAM
  POST /upload/image          multipart（image=檔案, overwrite=true）→ {"name": "...", "subfolder": "", "type": "input"}
  POST /prompt                {"prompt": {nodeId: {class_type, inputs}}} → {"prompt_id": "..."}
  GET  /history/{pid}         完成後才有該 pid；outputs.<node>.images[] = {filename, subfolder, type}
  GET  /view?filename=&subfolder=&type=output   取輸出檔位元組
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
CONNECT_TIMEOUT = 5.0


class ComfyError(Exception):
    """可預期的 ComfyUI 錯誤 → API 層轉成 200 + ok:false + 中文原因。"""


def _base(base_url: str | None) -> str:
    return (base_url or DEFAULT_BASE_URL).rstrip("/")


def _short(e: Exception) -> str:
    if isinstance(e, httpx.ConnectError):
        return "連線被拒（伺服器沒開或埠號不對）"
    if isinstance(e, httpx.TimeoutException):
        return "連線逾時"
    return f"{type(e).__name__}: {e}"


# ── 1. 可用性 ───────────────────────────────────────────────────
def comfy_available(base_url: str | None = None) -> tuple[bool, str, dict]:
    """回 (ok, 中文訊息, 摘要)。摘要含 comfyui_version 與 vram_free。

    摘要只放安全的正規化資料，不把 /system_stats 原樣丟給前端。
    """
    url = _base(base_url)
    try:
        with httpx.Client(timeout=CONNECT_TIMEOUT) as c:
            r = c.get(f"{url}/system_stats")
    except Exception as e:  # noqa: BLE001
        return False, f"連不上 ComfyUI（{url}）：{_short(e)}。請確認 ComfyUI 已啟動，或到設定頁改 comfy.baseUrl。", {}
    if r.status_code != 200:
        return False, f"ComfyUI 回了 HTTP {r.status_code}（{url}/system_stats），服務可能還在啟動中。", {}
    try:
        stat = r.json()
    except Exception:  # noqa: BLE001
        return False, "ComfyUI 的 /system_stats 回應不是 JSON，版本可能不相容。", {}
    if not isinstance(stat, dict):
        return False, "ComfyUI 的 /system_stats 回應格式不對。", {}
    sysinfo = stat.get("system") or {}
    devices = stat.get("devices") or []
    dev = devices[0] if isinstance(devices, list) and devices else {}
    total = dev.get("vram_total") or 0
    free = dev.get("vram_free") or 0
    summary = {
        "baseUrl": url,
        "version": sysinfo.get("comfyui_version") or "",
        "device": dev.get("name") or "",
        "vramFreeMB": round(free / 1024 / 1024) if isinstance(free, (int, float)) else None,
        "vramTotalMB": round(total / 1024 / 1024) if isinstance(total, (int, float)) else None,
    }
    return True, "ComfyUI 可用", summary


# ── 2. 上傳參考圖到 ComfyUI input ────────────────────────────────
def upload_image(local_path: str | Path, base_url: str | None = None) -> str:
    """把本機圖上傳到 ComfyUI 的 input 目錄，回「可直接填進 LoadImage.image 的檔名」。

    檔名一律加 m4a_<8hex>_ 前綴，避免蓋掉使用者自己在 input 的同名檔。
    """
    p = Path(local_path)
    if not p.is_file():
        raise ComfyError(f"要上傳的參考圖不存在：{p}")
    if p.stat().st_size == 0:
        raise ComfyError(f"要上傳的參考圖是空檔：{p.name}")
    url = _base(base_url)
    fname = f"m4a_{uuid.uuid4().hex[:8]}_{p.name}"
    try:
        with httpx.Client(timeout=60.0) as c:
            with open(p, "rb") as fh:
                r = c.post(
                    f"{url}/upload/image",
                    files={"image": (fname, fh, _mime(p.suffix))},
                    data={"overwrite": "true", "type": "input"},
                )
    except Exception as e:  # noqa: BLE001
        raise ComfyError(f"上傳參考圖到 ComfyUI 失敗（{p.name}）：{_short(e)}") from e
    if r.status_code != 200:
        raise ComfyError(f"上傳參考圖到 ComfyUI 失敗（{p.name}）：HTTP {r.status_code} {_body_snip(r)}")
    try:
        info = r.json()
    except Exception as e:  # noqa: BLE001
        raise ComfyError(f"上傳參考圖後，ComfyUI 回的不是 JSON（{p.name}）") from e
    name = (info or {}).get("name") or ""
    sub = (info or {}).get("subfolder") or ""
    if not name:
        raise ComfyError(f"上傳參考圖後，ComfyUI 沒有回檔名（{p.name}）")
    return f"{sub}/{name}" if sub else name


def _mime(suffix: str) -> str:
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".bmp": "image/bmp"}.get(suffix.lower(), "application/octet-stream")


def _body_snip(r: httpx.Response, n: int = 300) -> str:
    try:
        text = r.text or ""
    except Exception:  # noqa: BLE001
        return ""
    text = " ".join(text.split())
    return text[:n]


# ── 3. 送件 ─────────────────────────────────────────────────────
def submit(prompt_graph: dict, base_url: str | None = None, client_id: str | None = None) -> str:
    """POST /prompt，回 prompt_id。圖（dict）直接給，不必自己包 {"prompt": ...}。"""
    if not isinstance(prompt_graph, dict) or not prompt_graph:
        raise ComfyError("送件內容是空的（沒有節點圖）")
    url = _base(base_url)
    payload = {"prompt": prompt_graph, "client_id": client_id or f"svs-m4a-{uuid.uuid4().hex[:8]}"}
    try:
        with httpx.Client(timeout=60.0) as c:
            r = c.post(f"{url}/prompt", json=payload)
    except Exception as e:  # noqa: BLE001
        raise ComfyError(f"送件到 ComfyUI 失敗：{_short(e)}") from e
    try:
        body: Any = r.json()
    except Exception:  # noqa: BLE001
        body = None
    if r.status_code != 200:
        raise ComfyError(_explain_submit_error(r.status_code, body))
    pid = (body or {}).get("prompt_id") if isinstance(body, dict) else None
    if not pid:
        raise ComfyError(f"ComfyUI 沒有回 prompt_id（HTTP {r.status_code}）：{_body_snip(r)}")
    if isinstance(body, dict) and body.get("node_errors"):
        # 有 node_errors 還是有 prompt_id 的情況很少見；照送但把原因帶上去給上層記 log
        raise ComfyError(f"ComfyUI 回報節點錯誤：{_pretty_node_errors(body['node_errors'])}")
    return str(pid)


def _pretty_node_errors(node_errors: Any) -> str:
    if not isinstance(node_errors, dict):
        return str(node_errors)[:400]
    bits = []
    for nid, info in node_errors.items():
        if not isinstance(info, dict):
            continue
        cls = info.get("class_type") or ""
        for err in (info.get("errors") or []):
            msg = (err or {}).get("message") or (err or {}).get("details") or ""
            bits.append(f"節點 {nid}({cls})：{msg}")
    return "；".join(bits)[:600] or str(node_errors)[:400]


def _explain_submit_error(status: int, body: Any) -> str:
    base = f"ComfyUI 拒絕送件（HTTP {status}）"
    if not isinstance(body, dict):
        return base
    if body.get("error"):
        detail = body.get("error")
        tail = ""
        if isinstance(detail, dict):
            tail = f"：{detail.get('message') or detail.get('type') or ''}"
            tail += (" " + str(detail.get("details")) if detail.get("details") else "")
        else:
            tail = f"：{detail}"
        ne = body.get("node_errors")
        if ne:
            tail += "｜" + _pretty_node_errors(ne)
        return base + tail
    return base


# ── 4. 輪詢 ─────────────────────────────────────────────────────
def wait(pid: str, timeout: float = 180.0, base_url: str | None = None,
         interval: float = 1.5) -> dict:
    """輪詢 /history/{pid} 直到有結果或逾時；回該筆 history 記錄（含 outputs / status）。"""
    url = _base(base_url)
    deadline = time.time() + float(timeout)
    last_err = ""
    with httpx.Client(timeout=15.0) as c:
        while time.time() < deadline:
            try:
                r = c.get(f"{url}/history/{pid}")
            except Exception as e:  # noqa: BLE001
                last_err = _short(e)
                time.sleep(interval)
                continue
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                time.sleep(interval)
                continue
            try:
                hist = r.json()
            except Exception:  # noqa: BLE001
                last_err = "history 回應不是 JSON"
                time.sleep(interval)
                continue
            rec = (hist or {}).get(pid) if isinstance(hist, dict) else None
            if isinstance(rec, dict):
                status = rec.get("status") or {}
                if status.get("status_str") == "error":
                    raise ComfyError("ComfyUI 生成失敗：" + _history_error(status))
                if rec.get("outputs"):
                    return rec
            time.sleep(interval)
    msg = f"等 ComfyUI 生成逾時（{int(timeout)} 秒，prompt_id={pid}）。GPU 可能還在忙其他工作，可稍後重試；圖若後來完成會在 ComfyUI 的 output 目錄。"
    if last_err:
        msg += f"（最後一次輪詢錯誤：{last_err}）"
    raise ComfyError(msg)


def _history_error(status: dict) -> str:
    for m in (status.get("messages") or []):
        if isinstance(m, (list, tuple)) and len(m) >= 2 and m[0] == "execution_error":
            info = m[1] or {}
            node = info.get("node_type") or info.get("node_id") or ""
            return f"節點 {node}：{info.get('exception_message') or info.get('exception_type') or '未知錯誤'}"
    return "（ComfyUI 沒有給錯誤細節）"


# ── 5. 取圖 ─────────────────────────────────────────────────────
def collect_images(record: dict, dest_dir: str | Path, filename: str | None = None,
                   base_url: str | None = None) -> list[Path]:
    """把 history 記錄內所有輸出圖下載到 dest_dir，回實際落地路徑清單（依節點/序號）。

    filename 給定時只存第一張成該檔名（其餘不存）；否則沿用 ComfyUI 的檔名。
    """
    outs = (record or {}).get("outputs") or {}
    if not isinstance(outs, dict) or not outs:
        raise ComfyError("ComfyUI 完成了，但 outputs 是空的（工作流可能沒有 SaveImage 節點）")
    images: list[dict] = []
    for node_out in outs.values():
        if not isinstance(node_out, dict):
            continue
        for img in (node_out.get("images") or []):
            if isinstance(img, dict) and img.get("filename"):
                images.append(img)
    if not images:
        raise ComfyError("ComfyUI 完成了，但沒有任何圖檔輸出（工作流可能沒有 SaveImage 節點）")

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    url = _base(base_url)
    saved: list[Path] = []
    for i, img in enumerate(images):
        sub = img.get("subfolder") or ""
        params = {"filename": img["filename"], "type": img.get("type") or "output"}
        if sub:
            params["subfolder"] = sub
        try:
            with httpx.Client(timeout=120.0) as c:
                r = c.get(f"{url}/view", params=params)
        except Exception as e:  # noqa: BLE001
            raise ComfyError(f"下載輸出圖失敗（{img['filename']}）：{_short(e)}") from e
        if r.status_code != 200 or not r.content:
            raise ComfyError(f"下載輸出圖失敗（{img['filename']}）：HTTP {r.status_code}")
        if filename:
            name = filename if i == 0 else f"{Path(filename).stem}_{i}{Path(filename).suffix}"
        else:
            name = Path(img["filename"]).name
        target = dest / name
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(r.content)
        tmp.replace(target)
        saved.append(target)
        if filename:
            break
    return saved
