"""GPU 資源切換層（純系統層：VRAM 量測、ComfyUI 釋放、llama.cpp 啟停）。

為什麼要有這一層：
  本機同時跑 ComfyUI（生圖，:8000）與 llama.cpp（本機 LLM，:8080），兩邊都吃同一張 16GB 卡。
  顯卡被佔滿時 llama.cpp 的層會落到 CPU，速度會從 15-25 t/s 掉到不到 1 t/s。
  這層只做「量」與「切換」，不改任何模型參數（ctx / 量化 / MTP 一律不動）。

對外一律回中文訊息；唯讀函式不拋例外，動作函式拋 GpuError（API 層轉成 200+ok:false 或 4xx）。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── 端點與啟動參數（原樣沿用使用者現行的 llama-server 指令）────────────
COMFY_BASE = "http://127.0.0.1:8000"
LLAMA_BASE = "http://127.0.0.1:8080"
LLAMA_HOST = "127.0.0.1"
LLAMA_PORT = 8080

LLAMA_CWD = Path(r"C:\llama.cpp\llama.cpp")
LLAMA_EXE = LLAMA_CWD / "build" / "bin" / "llama-server.exe"
# 模型可換：使用者另外還有 Q3_K_P；預設跟桌面啟動腳本 llama-star-Qwen3.8-v6.bat 一致（Q2_K_P），
# 因為「按鈕啟的」和「他雙擊 bat 啟的」必須是同一個模型，否則／v1/models 的 id 會對不上設定頁。
MODELS_DIR = LLAMA_CWD / "models"
DEFAULT_MODEL_FILE = "Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-Q2_K_P.gguf"
LLAMA_TEMPLATE = LLAMA_CWD / "models" / "templates" / "chat_template.jinja"
LLAMA_LOG = Path(r"C:\llama.cpp\llama-server.log")


def list_models() -> list[dict]:
    """列出 models 目錄裡可直接啟動的 gguf（給 UI 下拉用）。"""
    out = []
    try:
        for p in sorted(MODELS_DIR.glob("*.gguf")):
            if p.name.startswith("ggml-") or p.name.startswith("mmproj-"):
                continue
            out.append({"file": p.name, "sizeGB": round(p.stat().st_size / 1024 ** 3, 2),
                        "default": p.name == DEFAULT_MODEL_FILE})
    except OSError:
        pass
    return out


def model_path(file_name: str | None = None) -> Path:
    """把使用者給的檔名收斂成 models 目錄內的 gguf（擋路徑穿越與不存在的檔）。"""
    name = (file_name or DEFAULT_MODEL_FILE).strip() or DEFAULT_MODEL_FILE
    name = Path(name).name  # 只取檔名，丟掉任何目錄部分
    p = MODELS_DIR / name
    if p.suffix.lower() != ".gguf":
        raise GpuError(f"模型檔必須是 .gguf：{name}", 400)
    return p


def llama_args(model_file: str | None = None) -> list[str]:
    """組出完整的 llama-server 啟動參數（參數值原樣沿用使用者的 bat，一個都不改）。"""
    model = model_path(model_file)
    return [
        str(LLAMA_EXE),
        "-m", str(model),
        "-ngl", "999",
        "--flash-attn", "on",
        "--jinja",
        "--chat-template-file", str(LLAMA_TEMPLATE),
        "-c", "32768",
        "-b", "1024",
        "-ub", "256",
        "-t", "6",
        "--temp", "1.0",
        "--top-p", "0.95",
        "--top-k", "20",
        "--min-p", "0.0",
        "--repeat-penalty", "1.0",
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q4_0",
        "--spec-type", "draft-mtp",
        "--spec-draft-n-max", "3",
        "--parallel", "1",
        "--log-file", str(LLAMA_LOG),
        "--host", LLAMA_HOST,
        "--port", str(LLAMA_PORT),
    ]

START_TIMEOUT_S = 120.0
PROBE_TIMEOUT_S = 2.5
# 這台機器對「未監聽的埠」回 TCP 拒絕要 ~2 秒（防火牆丟包而非 RST），
# 而設定頁每 15 秒會輪詢 status()：不快取就會反覆卡住、也讓 llm/status 重複探測同一埠。
_PROBE_TTL_S = 5.0
_probe_cache: dict[str, tuple[float, dict]] = {}


def clear_probe_cache() -> None:
    """動作（釋放／啟停模型）之後呼叫，讓下次 status() 重新探測而不是回舊值。"""
    _probe_cache.clear()


def _cached_probe(key: str, fn, ttl: float = _PROBE_TTL_S):
    """同一輪頁面刷新只探一次（僅限唯讀探測；動作路徑一律直接呼叫）。"""
    now = time.time()
    hit = _probe_cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _probe_cache[key] = (time.time(), val)
    return val

# 本機 LLM 要「有餘裕」至少留這麼多 VRAM，否則層會落到 CPU
LLAMA_NEED_FREE_MB = 2048


class GpuError(Exception):
    """可預期的 GPU 操作錯誤 → API 層轉成中文訊息（帶 status）。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ── 1. VRAM（nvidia-smi）───────────────────────────────────────────
def vram() -> dict:
    """回 {usedMB,totalMB,freeMB,source}；拿不到 nvidia-smi 時 source 說明原因。"""
    exe = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        return {"usedMB": None, "totalMB": None, "freeMB": None, "source": "找不到 nvidia-smi（顯卡或驅動？）"}
    except subprocess.TimeoutExpired:
        return {"usedMB": None, "totalMB": None, "freeMB": None, "source": "nvidia-smi 逾時"}
    if out.returncode != 0:
        return {"usedMB": None, "totalMB": None, "freeMB": None,
                "source": f"nvidia-smi 失敗：{(out.stderr or out.stdout or '').strip()[:200]}"}
    line = (out.stdout or "").strip().splitlines()
    if not line:
        return {"usedMB": None, "totalMB": None, "freeMB": None, "source": "nvidia-smi 沒有輸出"}
    try:
        used_s, total_s = [x.strip() for x in line[0].split(",")[:2]]
        used, total = int(float(used_s)), int(float(total_s))
    except Exception:  # noqa: BLE001
        return {"usedMB": None, "totalMB": None, "freeMB": None, "source": f"nvidia-smi 輸出看不懂：{line[0][:120]}"}
    return {"usedMB": used, "totalMB": total, "freeMB": max(total - used, 0), "source": "nvidia-smi"}


# ── 2. HTTP 小工具（只用標準庫，跟 llm.py 一致）──────────────────────
def _get_json(url: str, timeout: float = PROBE_TIMEOUT_S):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _post_json(url: str, payload: dict, timeout: float = 10.0):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:  # noqa: BLE001
        return {"raw": raw[:200]}


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.6):
            return True
    except OSError:
        return False


# ── 3. ComfyUI 狀態 ────────────────────────────────────────────────
def comfy_status() -> dict:
    """{running, vramFreeMB, vramTotalMB, version, error}"""
    try:
        stat = _get_json(f"{COMFY_BASE}/system_stats")
    except urllib.error.URLError as e:
        return {"running": False, "vramFreeMB": None, "vramTotalMB": None, "version": "", "error": _short(e)}
    except Exception as e:  # noqa: BLE001
        return {"running": False, "vramFreeMB": None, "vramTotalMB": None, "version": "", "error": _short(e)}
    devices = stat.get("devices") or [{}]
    dev = devices[0] if isinstance(devices, list) and devices else {}
    free = dev.get("vram_free")
    total = dev.get("vram_total")
    return {
        "running": True,
        "vramFreeMB": round(free / 1024 / 1024) if isinstance(free, (int, float)) else None,
        "vramTotalMB": round(total / 1024 / 1024) if isinstance(total, (int, float)) else None,
        "version": (stat.get("system") or {}).get("comfyui_version") or "",
        "error": "",
    }


# ── 4. llama.cpp 狀態 ──────────────────────────────────────────────
def _llama_model_name() -> tuple[bool, str, str]:
    """回 (running, model, error)。model 取 /v1/models 第一個 id。"""
    try:
        data = _get_json(f"{LLAMA_BASE}/v1/models")
    except Exception as e:  # noqa: BLE001
        return False, "", _short(e)
    models = data.get("data") or []
    name = ""
    if models and isinstance(models, list):
        name = (models[0] or {}).get("id") or ""
    return True, name or "(未回報模型名)", ""


def _llama_slots() -> dict:
    """回 /slots 的整理：{ok, isProcessing, busySlots, nSlots, error}。"""
    try:
        slots = _get_json(f"{LLAMA_BASE}/slots")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "isProcessing": None, "busySlots": 0, "nSlots": 0, "error": _short(e)}
    if not isinstance(slots, list):
        return {"ok": False, "isProcessing": None, "busySlots": 0, "nSlots": 0, "error": "/slots 回應格式不是陣列"}
    busy = 0
    for s in slots:
        if not isinstance(s, dict):
            continue
        # llama.cpp 新版用 is_processing；舊版沒有這個欄位就看 n_decoded / state
        flag = s.get("is_processing")
        if flag is None:
            flag = (s.get("state") == "processing") or bool(s.get("n_decoded"))
        if flag:
            busy += 1
    return {"ok": True, "isProcessing": busy > 0, "busySlots": busy, "nSlots": len(slots), "error": ""}


def llama_status() -> dict:
    running, model, err = _llama_model_name()
    out = {"running": running, "model": model, "isProcessing": None, "port": LLAMA_PORT, "error": err}
    if running:
        sl = _llama_slots()
        out["isProcessing"] = sl["isProcessing"]
        out["slots"] = sl
    return out


# ── 5. 釋放 ComfyUI 模型（只打 /free，不啟動/關閉 ComfyUI）───────────
def release_comfy() -> dict:
    before = vram()
    try:
        _post_json(f"{COMFY_BASE}/free", {"unload_models": True, "free_memory": True}, timeout=15.0)
    except Exception as e:  # noqa: BLE001
        raise GpuError(f"呼叫 ComfyUI /free 失敗：{_short(e)}。ComfyUI 可能沒開（{COMFY_BASE}）。", 502) from e
    # ComfyUI 卸載是背景執行緒，給它一點時間真的把 VRAM 交回去
    after = None
    for _ in range(12):  # 最多等 6 秒，一旦看到下降就再穩 1 秒
        time.sleep(0.5)
        cur = vram()
        if after is None:
            after = cur
        after = cur  # 取最後一次
        if cur["freeMB"] is not None and before["freeMB"] is not None and \
                cur["freeMB"] - before["freeMB"] >= 500:
            time.sleep(1.0)
            after = vram()
            break
    after = after or vram()
    freed = None
    if before["freeMB"] is not None and after["freeMB"] is not None:
        freed = after["freeMB"] - before["freeMB"]
    if freed is None:
        msg = "已送出釋放指令，但拿不到 VRAM 數字（nvidia-smi 不可用）。"
    elif freed <= 0:
        msg = f"已送出釋放指令，但 VRAM 沒有明顯變化（釋放 {freed} MB）——ComfyUI 可能本來就沒有載入模型。"
    else:
        msg = f"已釋放 ComfyUI 模型，回收約 {freed} MB VRAM。"
    return {"before": before, "after": after, "freedMB": freed, "message": msg}


# ── 6. 停止本機 LLM（破壞性：先確認沒在算）────────────────────────
def _find_listen_pid(port: int) -> tuple[int | None, str]:
    """用 Get-NetTCPConnection 找本機 :<port> LISTENING 的 PID。回 (pid, 說明)。

    為什麼不用 netstat：本機 netstat -ano 的輸出欄位順序不固定，靠 split + 欄位索引
    對不到（實測看到 :8080 明明 LISTENING 卻解析失敗）。PowerShell 的
    Get-NetTCPConnection 直接給 OwningProcess，穩定且沒有欄位對齊問題。
    """
    ps = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue "
         f"| Select-Object -First 1 -ExpandProperty OwningProcess)"],
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    val = (ps.stdout or "").strip().splitlines()
    if ps.returncode == 0 and val:
        try:
            return int(val[0].strip()), f"127.0.0.1:{port}"
        except ValueError:
            pass
    # 備援：netstat，改成找「最後一個欄位是純數字且狀態欄是 LISTENING」的那行
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                             timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in (out.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3].upper() == "LISTENING" \
                    and parts[-1].isdigit():
                return int(parts[-1]), parts[1]
    except Exception:  # noqa: BLE001
        pass
    return None, f"找不到 {port} 埠的 LISTENING 程序"


def stop_llama() -> dict:
    st = llama_status()
    if not st["running"]:
        # 服務沒回應但仍可能有程序卡住 → 再從埠判斷一次
        pid, where = _find_listen_pid(LLAMA_PORT)
        if pid is None:
            raise GpuError(f"本機 LLM 沒有在跑（{LLAMA_BASE} 連不上，{LLAMA_PORT} 埠也沒有程序在聽）。", 409)
    sl = st.get("slots") or {}
    if st.get("isProcessing") is True:
        raise GpuError(f"模型正在運算中（{sl.get('busySlots', 0)} 個 slot 忙碌），不要停止。請等這次回覆結束再按。", 409)
    if st.get("isProcessing") is None and st["running"]:
        # 拿不到 /slots 就保守停止（服務在跑、但無法確認是否運算）
        pass

    before = vram()
    pid, where = _find_listen_pid(LLAMA_PORT)
    if pid is None:
        raise GpuError(f"找不到佔用 {LLAMA_PORT} 埠的程序（{where}），可能已經結束。", 409)
    ps = subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force"],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if ps.returncode != 0:
        raise GpuError(f"停止 PID {pid} 失敗：{(ps.stderr or ps.stdout or '').strip()[:300]}", 500)
    # 等埠真的關掉（最多 15 秒）
    t0 = time.time()
    while time.time() - t0 < 15:
        if not _port_open(LLAMA_HOST, LLAMA_PORT):
            break
        time.sleep(0.5)
    after = vram()
    freed = None
    if before["freeMB"] is not None and after["freeMB"] is not None:
        freed = after["freeMB"] - before["freeMB"]
    return {
        "pid": pid,
        "before": before,
        "after": after,
        "freedMB": freed,
        "waitedS": round(time.time() - t0, 1),
        "message": f"已停止本機 LLM（PID {pid}）" + (f"，VRAM 回收約 {freed} MB。" if freed is not None else "。"),
    }


# ── 7. 啟動本機 LLM（背景起，輪詢到就緒）──────────────────────────
def start_llama(model_file: str | None = None) -> dict:
    st = llama_status()
    if st["running"]:
        return {"already": True, "model": st["model"], "waitedS": 0,
                "message": f"本機 LLM 已經在跑（模型：{st['model']}），不需要再啟動。"}
    if not LLAMA_EXE.is_file():
        raise GpuError(f"找不到 llama-server 執行檔：{LLAMA_EXE}", 400)
    model = model_path(model_file)
    if not model.is_file():
        raise GpuError(f"找不到模型檔：{model}（可用：{[m['file'] for m in list_models()]}）", 400)
    before = vram()
    log_path = str(LLAMA_LOG)
    try:
        with open(log_path, "ab") as logf:
            logf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start by story-video-studio (pid unknown) =====\n".encode())
            logf.flush()
            proc = subprocess.Popen(
                llama_args(model.name), cwd=str(LLAMA_CWD),
                stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                # DETACHED_PROCESS：讓 llama-server 完全脫離 App 的行程樹，
                # 這樣重啟/關閉 App（8788）不會把使用者正在用的模型一起殺掉。
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
    except Exception as e:  # noqa: BLE001
        raise GpuError(f"啟動 llama-server 失敗：{type(e).__name__}: {e}", 500) from e

    t0 = time.time()
    last_err = ""
    while time.time() - t0 < START_TIMEOUT_S:
        if proc.poll() is not None:
            raise GpuError(f"llama-server 啟動後立刻結束（離開碼 {proc.returncode}）。"
                           f"請看日誌 {log_path}。", 500)
        running, model, err = _llama_model_name()
        if running:
            after = vram()
            return {
                "pid": proc.pid, "model": model, "waitedS": round(time.time() - t0, 1),
                "before": before, "after": after,
                "message": f"本機 LLM 已啟動（PID {proc.pid}，耗時 {time.time() - t0:.1f} 秒），模型：{model}。",
            }
        last_err = err
        time.sleep(1.0)
    raise GpuError(f"等了 {int(START_TIMEOUT_S)} 秒仍未就緒（最後錯誤：{last_err}）。"
                   f"程序可能還在載入模型，請看日誌 {log_path}。", 504)


# ── 8. 彙整狀態 ────────────────────────────────────────────────────
def status() -> dict:
    v = _cached_probe("vram", vram)
    c = _cached_probe("comfy", comfy_status)
    l = _cached_probe("llama", llama_status)
    warn = ""
    if v["freeMB"] is not None and v["freeMB"] < LLAMA_NEED_FREE_MB:
        warn = (f"GPU 只剩 {v['freeMB'] / 1024:.1f} GB 可用；本機 LLM 可能落到 CPU（會很慢）。"
                f"要生圖先按「停止本機 LLM」，要用 LLM 就先按「釋放 ComfyUI 模型」。")
    return {
        "vram": v,
        "autoSwitch": auto_switch_enabled(),
        "comfy": {"running": c["running"], "vramFreeMB": c["vramFreeMB"], "vramTotalMB": c["vramTotalMB"],
                  "version": c["version"], "error": c["error"]},
        "llama": {"running": l["running"], "model": l["model"], "isProcessing": l["isProcessing"],
                  "port": l["port"], "slots": l.get("slots"), "error": l["error"]},
        "llamaNeedFreeMB": LLAMA_NEED_FREE_MB,
        "models": list_models(),
        "defaultModel": DEFAULT_MODEL_FILE,
        "warning": warn,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ══════════════════════════════════════════════════════════════════
# 9. 生圖／生影片前自動騰 GPU，完成後自動還原（settings.gpu.autoSwitch）
#
# 為什麼要這一層：本機 LLM 佔 11.9-13 GB，ComfyUI 生圖要 ~12 GB、H3 生影片要 ~15 GB。
# 兩邊同時開著會溢出到系統 RAM，速度從 33 t/s 掉到 0.6 t/s。手動按按鈕很容易忘，
# 所以生圖／生影片的入口在動工前呼叫 ensure_comfy_gpu()、在 finally 呼叫 restore_llm_after()。
#
# 兩條硬規則：
#   1. LLM 有任務在運算中就不殺（回 busy，讓使用者自己決定）——不硬斷別人正在等的回覆。
#   2. 只要「本來是開著的」，finally 一定要把它啟回來；啟不回來也要把原因吐出來並寫 log。
# ══════════════════════════════════════════════════════════════════
AUTO_SWITCH_DEFAULT = True
# 等 VRAM 真的放下來的最長時間（stop 之後 nvidia-smi 的數字會有幾百毫秒延遲）
FREE_WAIT_S = 20.0
FREE_POLL_S = 0.5
_SETTINGS_FILE = Path(__file__).resolve().parent.parent / "memory" / "gpu-settings.json"


def auto_switch_enabled() -> bool:
    """讀 settings.gpu.autoSwitch（全域設定檔，沒有就用預設 true）。讀不到不拋例外。"""
    try:
        raw = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        val = (raw or {}).get("autoSwitch")
        return AUTO_SWITCH_DEFAULT if val is None else bool(val)
    except (OSError, ValueError):
        return AUTO_SWITCH_DEFAULT


def set_auto_switch(enabled: bool) -> dict:
    """寫 settings.gpu.autoSwitch（原子寫入，只動這一個鍵）。"""
    raw: dict = {}
    try:
        loaded = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
    except (OSError, ValueError):
        raw = {}
    raw["autoSwitch"] = bool(enabled)
    try:
        _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, _SETTINGS_FILE)
    except OSError as e:
        raise GpuError(f"寫入自動切換設定失敗：{e}", 500) from e
    return {"autoSwitch": bool(enabled)}


def _wait_free(target_mb: int, timeout_s: float = FREE_WAIT_S) -> dict:
    """poll nvidia-smi 到 freeMB >= target_mb 或逾時。回最後一次 vram()（多帶 waitedS）。"""
    t0 = time.time()
    cur = vram()
    while time.time() - t0 < timeout_s:
        if cur["freeMB"] is not None and cur["freeMB"] >= target_mb:
            break
        time.sleep(FREE_POLL_S)
        cur = vram()
    cur = dict(cur)
    cur["waitedS"] = round(time.time() - t0, 1)
    return cur


def ensure_comfy_gpu(need_mb: int = 12000, auto: bool | None = None) -> dict:
    """生圖／生影片前確保 ComfyUI 有足夠 VRAM（不夠就把閒置的本機 LLM 停掉）。

    回 {switched, stoppedLlama, freedMB, wasRunning, wasModel, busy, note, vramBefore, vramAfter}
      switched     : 這次有沒有真的做切換（false＝本來就夠，或自動切換被關掉）
      stoppedLlama : 有沒有停掉 llama（restore_llm_after 只看這個旗標）
      wasRunning   : 本來 llama 在不在跑（給還原判斷）
      busy         : true＋GpuError(409) → LLM 正在運算，這一輪不要送
    """
    auto = auto_switch_enabled() if auto is None else bool(auto)
    before = vram()
    out = {"switched": False, "stoppedLlama": False, "freedMB": None, "wasRunning": False,
           "wasModel": "", "busy": False, "note": "", "vramBefore": before, "vramAfter": before}
    if before["freeMB"] is None:
        out["note"] = "讀不到 VRAM（nvidia-smi 不可用），不自動切換，直接生。"
        return out
    if before["freeMB"] >= need_mb:
        out["note"] = f"VRAM 已足夠（空閒 {before['freeMB']} MB ≥ 需要 {need_mb} MB），不需要停本機 LLM。"
        return out
    if not auto:
        out["note"] = (f"空閒 VRAM 只有 {before['freeMB']} MB（需要 {need_mb} MB），"
                       "但「自動停止本機 LLM」已關閉，照原樣送件（可能很慢或 OOM）。")
        return out

    st = llama_status()
    out["wasRunning"] = bool(st["running"])
    out["wasModel"] = st.get("model") or ""
    if not st["running"]:
        out["note"] = (f"空閒 VRAM 只有 {before['freeMB']} MB（需要 {need_mb} MB），"
                       "但本機 LLM 沒在跑，沒有東西可以停。")
        return out
    sl = st.get("slots") or {}
    if st.get("isProcessing") is True:
        out["busy"] = True
        raise GpuError(
            f"本機 LLM 正在運算中（{sl.get('busySlots', 1)} 個 slot 忙碌），請等它完成再生成"
            f"（現在只有 {before['freeMB']} MB 空閒 VRAM，直接生會很慢或爆記憶體）。",
            409,
        )

    try:
        stopped = stop_llama()
    except GpuError as e:
        raise GpuError(f"要生圖但停不掉本機 LLM：{e}", e.status) from e
    out["stoppedLlama"] = True
    out["switched"] = True
    after = _wait_free(need_mb)
    out["vramAfter"] = after
    out["freedMB"] = stopped.get("freedMB")
    if out["freedMB"] is None and before["freeMB"] is not None and after["freeMB"] is not None:
        out["freedMB"] = after["freeMB"] - before["freeMB"]
    enough = after["freeMB"] is not None and after["freeMB"] >= need_mb
    freed_txt = f"{out['freedMB'] / 1024:.1f} GB" if out["freedMB"] is not None else "?"
    out["note"] = (f"已自動停止本機 LLM（PID {stopped.get('pid')}，釋放 {freed_txt}）"
                   + ("。" if enough else f"，等 {after.get('waitedS')} 秒後空閒仍有 {after['freeMB']} MB（不足 {need_mb} MB，可能很慢）。"))
    return out


def restore_llm_after(flag: dict | None, model_file: str | None = None) -> dict:
    """生圖／生影片結束後還原：只有「本來開著、而且是被我們停掉的」才啟回。

    回 {restarted, pid, waitedS, error, note}；啟不回來不拋例外（生圖結果不能被還原失敗吃掉），
    但 error 一定非空，呼叫端要把它寫進 pipeline.logs。
    """
    flag = flag or {}
    out = {"restarted": False, "pid": None, "waitedS": 0.0, "error": "", "note": ""}
    if not flag.get("stoppedLlama"):
        out["note"] = "本來就沒停本機 LLM，不需要還原。"
        return out
    if llama_status()["running"]:
        out["note"] = "本機 LLM 已經在跑（可能已被使用者手動啟動），不重複啟動。"
        return out
    want = model_file or flag.get("wasModel") or None
    # wasModel 是 llama 回報的完整路徑；start_llama 只要檔名
    if want:
        want = Path(str(want)).name
        if not (MODELS_DIR / want).is_file():
            want = None  # 檔名怪就退回預設模型，總比不啟好
    try:
        # 重要：ComfyUI 生完圖後仍把模型掛在 VRAM（實測 MOSS 約 11.5GB），
        # 若不先釋放就啟回 LLM，LLM 會因為 VRAM 不夠而把層丟到 CPU（掉到 0.6-4 t/s）。
        try:
            release_comfy()
        except Exception:  # noqa: BLE001
            pass
        res = start_llama(want)
    except GpuError as e:
        out["error"] = str(e)
        out["note"] = f"本機 LLM 還原失敗：{e}"
        return out
    except Exception as e:  # noqa: BLE001 — 還原失敗不能讓整批炸掉
        out["error"] = f"{type(e).__name__}: {e}"
        out["note"] = f"本機 LLM 還原失敗：{out['error']}"
        return out
    out["restarted"] = True
    out["pid"] = res.get("pid")
    out["waitedS"] = float(res.get("waitedS") or 0.0)
    out["note"] = f"已重新啟動本機 LLM（PID {out['pid']}，耗時 {out['waitedS']:.1f}s）。"
    return out


def _short(e: Exception) -> str:
    if isinstance(e, urllib.error.URLError):
        reason = getattr(e, "reason", e)
        if isinstance(reason, ConnectionRefusedError) or "refused" in str(reason).lower():
            return "連線被拒（伺服器沒開）"
        if isinstance(reason, socket.timeout):
            return "連線逾時"
        return f"連線失敗：{reason}"
    if isinstance(e, socket.timeout):
        return "連線逾時"
    return f"{type(e).__name__}: {e}"
