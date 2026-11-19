"""Story Video Studio 桌面啟動器。

用法：
    pythonw launcher.py                  # 開發模式（.venv）
    pythonw launcher.py --port 8788      # 指定起始埠（被占會往後試）
    pythonw launcher.py --data-dir D:\\SVS\\projects
    StoryVideoStudio.exe                 # 打包版（同樣吃上面兩個參數）

流程：
    1. 選埠（127.0.0.1:8788 → 8789…8799 第一個空的）
    2. 背景 thread 起 uvicorn（app.server:app）
    3. 輪詢 /api/health 直到 200（最多 30 秒）
    4. pywebview 開原生視窗（Edge WebView2）→ 使用者關窗即結束
    5. 關窗後 uvicorn should_exit + 護底 os._exit，不殘留 python 進程
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

APP_TITLE = "Story Video Studio"
DEFAULT_PORT = 8788
PORT_RANGE_END = 8799
HEALTH_TIMEOUT_S = 30.0
WINDOW_SIZE = (1280, 880)
MIN_SIZE = (1024, 700)

HERE = Path(__file__).resolve().parent

# ── 日誌（沒有 console 時寫到資料夾，方便回報問題）───────────────
def _log_path() -> Path:
    env = (os.environ.get("SVS_DATA_DIR") or "").strip()
    base = Path(env) if env else (
        Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else HERE
    )
    if base.name.lower() == "projects":
        base = base.parent
    return base / "launcher.log"


def _setup_logging() -> Path:
    path = _log_path()
    handlers: list[logging.Handler] = []
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    except Exception:
        pass
    # pythonw / --noconsole 下 sys.stderr 是 None，加 StreamHandler 會炸掉整個 basicConfig
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers or [logging.NullHandler()],
        force=True,
    )
    return path


log = logging.getLogger("svs.launcher")


# ── 參數 ────────────────────────────────────────────────────────
def _parse_args(argv: list[str]) -> tuple[int, Path | None]:
    port = DEFAULT_PORT
    data_dir: Path | None = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1]); i += 2; continue
        if a.startswith("--port="):
            port = int(a.split("=", 1)[1]); i += 1; continue
        if a == "--data-dir" and i + 1 < len(argv):
            data_dir = Path(argv[i + 1]); i += 2; continue
        if a.startswith("--data-dir="):
            data_dir = Path(a.split("=", 1)[1]); i += 1; continue
        i += 1
    return port, data_dir


def _default_data_dir() -> Path:
    """exe 旁（dev 時＝專案根）的 projects/。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "projects"
    return HERE / "projects"


def _resolve_data_dir(cli_value: Path | None) -> Path:
    if cli_value is not None:
        d = cli_value.expanduser()
        d = (d if d.is_absolute() else (Path.cwd() / d))
        return d.resolve()
    return _default_data_dir()


# ── 埠 ──────────────────────────────────────────────────────────
def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _health(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _same_process(port: int) -> bool:
    """該埠的伺服器是不是「本進程」起的（pid 相同）——只有這種才叫 self。"""
    h = _health(port)
    return bool(h) and h.get("pid") == os.getpid()


def _pick_port(host: str, start: int) -> tuple[int, bool]:
    """回 (port, reused)。只看「本進程自己的」伺服器，別人的絕不重用。"""
    mine = os.getpid()
    for port in range(start, PORT_RANGE_END + 1):
        if _port_free(host, port):
            return port, False
        # 埠被占：只有當它是別的程式（pid 不同）→ 往後找下一個
        h = _health(port)
        if h and h.get("app") == "story-video-studio" and h.get("pid") == mine:
            log.info("埠 %s 已是本程式起的伺服器，重用", port)
            return port, True
        log.info("埠 %s 被其他程式占用（%s），往後試", port, (h or {}).get("app") or "unknown")
    raise RuntimeError(
        f"埠 {start}–{PORT_RANGE_END} 全部被占用，請先關掉其他程式，或用 --port 指定別的埠。"
    )


# ── 伺服器 ──────────────────────────────────────────────────────
class _NullStream:
    """pythonw / --noconsole 下 sys.stdout/stderr 是 None；uvicorn 的 logging 會呼叫 .isatty()，
    直接讓它炸（AttributeError: 'NoneType' object has no attribute 'isatty'）。這裡補一個無害的流。"""

    def write(self, s):  # noqa: D401
        return len(s) if isinstance(s, str) else 0

    def flush(self):
        return None

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return "utf-8"


def _patch_std_streams() -> None:
    """沒有 console 的執行環境（pythonw、PyInstaller --noconsole、--windowed exe）把 None 換掉。"""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, _NullStream())
            except Exception:
                pass


def _serve(host: str, port: int, data_dir: Path, ready: threading.Event, box: dict) -> None:
    try:
        _patch_std_streams()
        os.environ["SVS_DATA_DIR"] = str(data_dir)
        os.environ["SVS_PORT"] = str(port)
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))  # dev：讓 app 套件可 import；frozen 時已內嵌
        import uvicorn

        config = uvicorn.Config(
            "app.server:app",
            host=host,
            port=port,
            log_level="info",
            access_log=False,
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # 訊號由視窗關閉處理，不吃 Ctrl 訊號
        box["server"] = server
        box["ready"] = ready
        ready.set()  # 至少讓主執行緒可以開始等 health
        server.run()
    except Exception:
        box["error"] = traceback.format_exc()
        ready.set()
        log.exception("伺服器啟動失敗")


def _wait_health(port: int, deadline_s: float = HEALTH_TIMEOUT_S) -> bool:
    url = f"http://127.0.0.1:{port}/api/health"
    t0 = time.time()
    while time.time() - t0 < deadline_s:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            pass
        time.sleep(0.35)
    return False


def _shutdown(box: dict, hard_deadline_s: float = 6.0) -> None:
    """視窗關閉時呼叫：請 uvicorn 收工，最多等 hard_deadline_s 秒後強制離開。"""
    log.info("視窗關閉，正在停止伺服器…")

    def _force() -> None:
        time.sleep(hard_deadline_s)
        log.warning("伺服器未在 %.0f 秒內結束，強制退出", hard_deadline_s)
        try:
            sys.stdout.flush()
        except Exception:
            pass
        os._exit(0)

    threading.Thread(target=_force, name="svs-force-exit", daemon=True).start()
    server = box.get("server")
    try:
        if server is not None:
            server.should_exit = True
    except Exception:
        log.exception("should_exit 設定失敗")

    # 等 uvicorn 那條 thread 真的收工（最多硬上限），收乾淨就立刻退出
    t = box.get("thread")
    if t is not None and t.is_alive():
        t.join(timeout=hard_deadline_s)
    log.info("伺服器已停止")
    try:
        sys.stdout.flush()
    except Exception:
        pass
    os._exit(0)


# ── 視窗 ────────────────────────────────────────────────────────
def _icon_path() -> str | None:
    roots = []
    if getattr(sys, "frozen", False):
        mp = getattr(sys, "_MEIPASS", None)
        roots += [Path(mp)] if mp else []
        roots.append(Path(sys.executable).resolve().parent)
    else:
        roots.append(HERE)
    for r in roots:
        for rel in ("assets/app.ico", "app.ico", "_internal/assets/app.ico"):
            p = r / rel
            if p.is_file():
                return str(p)
    return None


def _open_window(url: str, box: dict) -> None:
    import webview

    close_flag = {"closed": False}

    def _on_closed() -> None:
        if close_flag["closed"]:
            return
        close_flag["closed"] = True
        _shutdown(box)

    win = webview.create_window(
        APP_TITLE,
        url,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        resizable=True,
        min_size=MIN_SIZE,
        text_select=True,
    )
    win.events.closed += _on_closed
    # 注意：不要在背景下用 win.on_top 之類操作視窗（會卡住 webview 的 UI 執行緒，
    # 連帶整個 GIL/伺服器都停擺）。要置前請用 Win32 SetForegroundWindow（見 _bring_to_front）。

    icon = _icon_path()
    kwargs = {"private_mode": False, "debug": False}
    if icon:
        kwargs["icon"] = icon
    # 視窗真的畫出來後，用 Win32 把它置前（不經過 pywebview API，避免卡 UI 執行緒）
    def _front() -> None:
        try:
            import ctypes
            time.sleep(1.2)
            hwnd = ctypes.windll.user32.FindWindowW(None, APP_TITLE)
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            log.debug("置前失敗（不影響使用）", exc_info=True)
    threading.Thread(target=_front, name="svs-front", daemon=True).start()
    try:
        webview.start(**kwargs)
        log.info("webview 主迴圈結束（視窗已關閉）")
    except Exception:
        log.exception("webview.start 失敗")
        raise
    finally:
        _on_closed()


def _msgbox(text: str, title: str = APP_TITLE) -> None:
    """無 console 時用 Windows 原生對話框顯示錯誤。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)  # MB_ICONERROR
    except Exception:
        print(text, file=sys.stderr)


def _seed_projects_if_empty(data_dir: Path) -> None:
    """（已停用）舊行為：projects/ 空著就從打包機的專案根複製全部專案過來。

    為什麼停用：打包成發佈包後，exe 旁 ../.. 會指回**打包的那台電腦**的開發資料夾，
    使用者把 release 解壓到哪裡都可能忽然長出八個測試專案，違反「乾淨版」定義。
    發佈包改成直接把成果專案放進 projects/ 一起出貨，程式不再自己搬資料。
    保留函式簽名與 SVS_SEED_PROJECTS 逃生口（給需要預填資料的人手動開）。
    """
    proj = data_dir / "projects" if data_dir.name.lower() != "projects" else data_dir
    if not proj.is_dir() or any(p.is_dir() and not p.name.startswith("_") for p in proj.iterdir()):
        return  # 已有專案，不動

    candidates: list[Path] = []
    env = (os.environ.get("SVS_SEED_PROJECTS") or "").strip()
    if not env:
        log.info("專案預填已停用（乾淨版），只在有 SVS_SEED_PROJECTS 時才複製：%s", proj)
        return
    candidates.append(Path(env))
    exe_dir = Path(sys.executable).resolve().parent
    candidates += [
        exe_dir.parent.parent / "projects",  # dist/StoryVideoStudio/ → 專案根/projects
        exe_dir.parent / "projects",
        exe_dir / "projects",
    ]
    src = next((c for c in candidates if c.is_dir() and c.resolve() != proj.resolve()), None)
    if src is None:
        log.info("沒有可複製的既有專案，建立空白 projects/：%s", proj)
        return

    import shutil

    copied, skipped = 0, 0
    for d in sorted(src.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or d.name.lower() == "projects":
            continue  # 跳過 _trash 與巢狀的 projects/
        dst = proj / d.name
        if dst.exists():
            skipped += 1
            continue
        try:
            shutil.copytree(d, dst)
            copied += 1
        except Exception:
            log.exception("複製專案失敗：%s", d)
    log.info("首次啟動已複製 %s 個專案（略過 %s 個已存在）← %s", copied, skipped, src)


def main() -> int:
    _patch_std_streams()
    logfile = _setup_logging()
    log.info("啟動：argv=%s frozen=%s exe=%s", sys.argv, getattr(sys, "frozen", False), sys.executable)

    start_port, cli_data_dir = _parse_args(sys.argv[1:])
    data_dir = _resolve_data_dir(cli_data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["SVS_DATA_DIR"] = str(data_dir)
    log.info("資料夾：%s（日誌 %s）", data_dir, logfile)
    try:
        _seed_projects_if_empty(data_dir)
    except Exception:
        log.exception("首次啟動複製專案失敗（不影響啟動）")

    host = "127.0.0.1"
    try:
        port, reused = _pick_port(host, start_port)
    except RuntimeError as e:
        log.error("%s", e)
        _msgbox(f"{e}\n\n日誌：{logfile}")
        return 1
    log.info("使用埠 %s（%s）", port, "重用既有" if reused else "新啟動")

    box: dict = {}
    if not reused:
        ready = threading.Event()
        t_srv = threading.Thread(
            target=_serve, args=(host, port, data_dir, ready, box), name="svs-uvicorn", daemon=True
        )
        box["thread"] = t_srv
        t_srv.start()
        ready.wait(60)
        if box.get("error"):
            err = box["error"]
            log.error("伺服器例外：\n%s", err)
            _msgbox(f"後端伺服器啟動失敗：\n{err.splitlines()[-1]}\n\n完整錯誤見日誌：\n{logfile}")
            return 2
        if not _wait_health(port):
            log.error("等待 /api/health 逾時")
            _msgbox(f"後端在 30 秒內沒有回應（埠 {port}）。\n日誌：{logfile}")
            return 3

    url = f"http://127.0.0.1:{port}/"
    log.info("開啟視窗：%s", url)
    try:
        _open_window(url, box)
    except Exception:
        err = traceback.format_exc()
        log.error("pywebview 視窗開不起來：\n%s", err)
        # 備援：退回預設瀏覽器（不是主要路徑）
        import webbrowser
        webbrowser.open(url)
        _msgbox(
            "無法建立桌面視窗（WebView2 可能未安裝）。\n"
            f"已改用預設瀏覽器開啟：{url}\n\n錯誤：{err.splitlines()[-1]}\n日誌：{logfile}"
        )
        # 瀏覽器模式下不要馬上退出：等使用者自己關掉本進程（工作管理員）——
        # 但仍提供 30 分鐘上限，避免永遠留著。
        try:
            time.sleep(1800)
        except KeyboardInterrupt:
            pass
        _shutdown(box, hard_deadline_s=3.0)
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        log.exception("啟動器未預期例外")
        _msgbox(traceback.format_exc()[-1200:])
        code = 9
    os._exit(code if code else 0)
