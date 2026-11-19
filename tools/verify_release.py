"""乾淨版解壓實測：解壓 → 啟動 exe → 驗證 API/專案/媒體 → 關閉 → 確認消失。

用法: python verify_release.py <zip路徑> [測試目錄]
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ZIP = Path(sys.argv[1])
TEST = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(os.environ.get("TMPDIR", ".")) / "svs_clean_test"


def http(url, timeout=10, head=False):
    req = urllib.request.Request(url, method="HEAD" if head else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = b"" if head else r.read()
            return r.status, body, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, b"", dict(e.headers)
    except Exception as e:  # 連不上
        return None, str(e).encode(), {}


def pids_of(name="StoryVideoStudio.exe"):
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-Process -Name {name[:-4]} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id) -join ','"],
        capture_output=True, text=True).stdout.strip()
    return [int(x) for x in out.split(",") if x.strip()]


print(f"[1] 解壓 {ZIP.name} → {TEST}")
if TEST.exists():
    shutil.rmtree(TEST, ignore_errors=True)
TEST.mkdir(parents=True)
t0 = time.time()
with zipfile.ZipFile(ZIP) as z:
    z.extractall(TEST)
print(f"    解壓完成 {time.time()-t0:.1f}s")

base = TEST / "StoryVideoStudio"
exe = base / "StoryVideoStudio.exe"
print(f"[2] exe 存在={exe.is_file()} size={exe.stat().st_size}")
print(f"    頂層內容={sorted(p.name for p in base.iterdir())}")

before = set(pids_of())
print(f"[3] 啟動 exe（可見到程序 {len(before)} 個）")
proc = subprocess.Popen([str(exe)], cwd=str(base))

health = None
port = None
for _ in range(120):  # 最多 60 秒
    time.sleep(0.5)
    for p in (8788, 8789, 8790, 8791, 8792):
        st, body, _ = http(f"http://127.0.0.1:{p}/api/health", timeout=2)
        if st == 200:
            try:
                j = json.loads(body)
            except Exception:
                continue
            dd = j.get("dataDir", "")
            if str(TEST).lower() in dd.lower().replace("/", "\\"):
                health, port = j, p
                break
    if health:
        break

print(f"[4] health: {'200 OK' if health else 'FAILED'} port={port}")
if health:
    print(f"    {json.dumps(health, ensure_ascii=False)}")
    print(f"[5] dataDir 在測試目錄內 = {str(TEST).lower() in health['dataDir'].lower()}")

procs = pids_of()
new = [p for p in procs if p not in before]
print(f"[6] 進程存在: {procs}（新增 {new}）")

st, body, _ = http(f"http://127.0.0.1:{port}/api/projects")
projs = json.loads(body).get("projects", []) if st == 200 else []
print(f"[7] projects API {st} → {len(projs)} 個")
for p in projs:
    print(f"    {json.dumps(p, ensure_ascii=False)}")
on_disk = [d.name for d in (base / "projects").iterdir() if d.is_dir()]
print(f"    projects/ 目錄實際={on_disk}")

PJ = base / "projects" / "project-20261118-022129"
pj = json.loads((PJ / "project.json").read_text(encoding="utf-8"))
print(f"[8] 專案名稱={pj['meta']['name']} 人物={len(pj['characters'])} 場景={len(pj['scenes'])} 鏡頭={len(pj['shots'])}")
finals = sorted((PJ / "output").glob("final_*.mp4"))
print(f"    final.mp4={[f.name for f in finals]}")

st, body, _ = http(f"http://127.0.0.1:{port}/files/project-20261118-022129/project.json")
print(f"[9] /files project.json → {st} bytes={len(body)}")

ok = True
for f in finals:
    url = f"http://127.0.0.1:{port}/files/project-20261118-022129/output/{f.name}"
    st, body, hdr = http(url)
    same = len(body) == f.stat().st_size
    ok = ok and st == 200 and same
    print(f"[10] {url.split('/files/')[1]}\n     → {st} 下載={len(body)} 磁碟={f.stat().st_size} 相符={same} type={hdr.get('content-type')}")

# 抽一張角色圖與一支 clip 驗證二元檔可讀
for rel in (f"media/clips/{pj['shots'][0]['video']['path'].split('/')[-1]}",
            f"media/characters/{pj['characters'][0]['refImages'][-1]['path'].split('/')[-1]}"):
    p = PJ / rel
    st, body, hdr = http(f"http://127.0.0.1:{port}/files/project-20261118-022129/{rel}")
    print(f"[11] {rel} → {st} 下載={len(body)} 磁碟={p.stat().st_size} 相符={len(body)==p.stat().st_size if p.exists() else 'NA'}")

print(f"[12] 關閉 exe（proc.pid={proc.pid}）")
subprocess.run(["powershell", "-NoProfile", "-Command",
                "Get-Process -Name StoryVideoStudio -ErrorAction SilentlyContinue | Stop-Process -Force"])
for _ in range(40):
    time.sleep(0.5)
    if not pids_of():
        break
print(f"    關閉後進程={pids_of()}")
st, body, _ = http(f"http://127.0.0.1:{port}/api/health", timeout=3)
print(f"[13] 關閉後 health → {st}（應為 None，連不上）")
print(f"[14] 埠 {port} 釋放 = {st is None}")

print(f"[15] 刪除測試目錄 {TEST}")
shutil.rmtree(TEST, ignore_errors=True)
print(f"    已刪除 = {not TEST.exists()}")
print("RESULT_MEDIA_OK =", ok)
