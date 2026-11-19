"""LLM 對話層：OpenAI 相容 /chat/completions client（M3a 建、M3b 加可選供應商）。

M3b 供應商規則（settings.llm.provider）：
- local  ：本機 OpenAI 相容服務，預設 http://127.0.0.1:8080/v1，model=auto（取 /models 第一個）
- online ：線上 OpenAI 相容服務，預設 https://api.deepseek.com/v1、model=deepseek-chat
- custom ：使用者自填 baseUrl / model / key（不套任何預設）

API key 解析（只在後端發生，絕不回傳前端、也不寫進 project.json）：
  project.settings.llm.apiKey → 環境變數 LLM_API_KEY →（provider=online）hermes .env 的 DEEPSEEK_API_KEY
- /api/llm/status 只回 hasApiKey / apiKeySource / apiKeyMasked（後四碼）

只用標準庫 urllib.request（零依賴、同步阻塞）。
"""

from __future__ import annotations

import json
import os
import time
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path

# ── 供應商預設值 ─────────────────────────────────────────────────
PROVIDERS = ("local", "online", "custom")
LOCAL_BASE_URL = "http://127.0.0.1:8080/v1"
ONLINE_BASE_URL = "https://api.deepseek.com/v1"
ONLINE_MODEL = "deepseek-chat"
# 帳號實際列出的模型可能不同（例如 deepseek-flash / deepseek-v4-pro）；
# 預設模型不在清單時依序改試這些，最後才退回清單第一個（見 _pick_model）。
ONLINE_FALLBACK_MODELS = ("deepseek-chat", "deepseek-flash", "deepseek-reasoner", "deepseek-v4-pro")
DEFAULT_MODEL = "auto"
DEFAULT_BASE_URL = LOCAL_BASE_URL  # 舊名相容

_PROVIDER_LABEL = {
    "local": "本機服務（local）",
    "online": "線上 DeepSeek（online）",
    "custom": "自訂服務（custom）",
}

# hermes 的 .env（M3b：provider=online 時讀 DEEPSEEK_API_KEY）
HERMES_ENV_PATH = Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")) / "hermes" / ".env"
ENV_KEYS = {"online": "DEEPSEEK_API_KEY", "custom": "LLM_API_KEY"}

# 連線（含 DNS/TCP）超時：本機服務關掉時 Windows 會重試 SYN 兩次（約 2 秒），5 秒足夠；
# 真正的等待由 socket read timeout 決定。使用者要求「本地模型絕不被中斷」→ 預設 24 小時
# （86400 秒），等於不設上限；可用環境變數 SVS_LLM_TIMEOUT（秒）覆寫。
CONNECT_TIMEOUT = 5.0
TOTAL_TIMEOUT = float(os.environ.get("SVS_LLM_TIMEOUT") or 86400)

# 本機推理模型（Qwen3.x 等）的思考內容也算進 max_tokens，長 JSON 任務常被吃到正文空白
# （finish_reason=length、content 為空）。本機端點預設關掉思考；SVS_LLM_THINKING=on 可強制開回。
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0")
NO_THINK_PAYLOAD = {"chat_template_kwargs": {"enable_thinking": False}}

# OpenAI 相容伺服器常見的「模型不存在」錯誤碼
_MODEL_MISSING_HINTS = ("model_not_found", "does not exist", "not found", "invalid model", "no model")


class LLMError(Exception):
    """可預期的模型服務錯誤 → API 層轉成 502/400 與中文訊息（不是 500）。"""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _host_port(base_url: str) -> str:
    rest = base_url.split("://", 1)[-1]
    return rest.split("/", 1)[0]


def normalize_base_url(raw: str | None, provider: str = "local") -> str:
    """保證結尾沒有斜線、且自動補 /v1（使用者常只填 http://127.0.0.1:8080）。

    M3b：以 provider 決定「空白時的預設位址」（online → api.deepseek.com）。
    """
    default = ONLINE_BASE_URL if provider == "online" else LOCAL_BASE_URL
    url = (raw or "").strip() or default
    url = url.rstrip("/")
    if not re.search(r"/v\d+$", url):
        url += "/v1"
    return url


# ── API key ────────────────────────────────────────────────────
def _read_env_file(path: Path) -> dict:
    """.env 解析：KEY=VALUE，忽略註解/空行，去掉成對引號。讀不到就回 {}（不拋）。"""
    out: dict = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.startswith("export "):
            k = k[7:].strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def resolve_api_key(llm_cfg: dict | None) -> tuple[str, str]:
    """回 (key, 來源說明)。不會拋例外。

    順序：project.settings.llm.apiKey → 環境變數 LLM_API_KEY →（online）hermes .env 的 DEEPSEEK_API_KEY
    """
    cfg = llm_cfg or {}
    provider = str(cfg.get("provider") or "local").strip().lower()

    direct = str(cfg.get("apiKey") or "").strip()
    if direct:
        return direct, "專案設定"

    env_direct = os.environ.get("LLM_API_KEY", "").strip()
    if env_direct:
        return env_direct, "環境變數 LLM_API_KEY"

    if provider == "online":
        name = ENV_KEYS["online"]
        from_env = os.environ.get(name, "").strip()
        if from_env:
            return from_env, f"環境變數 {name}"
        if HERMES_ENV_PATH.is_file():
            from_file = _read_env_file(HERMES_ENV_PATH).get(name, "").strip()
            if from_file:
                return from_file, f"hermes .env（{name}）"
    return "", ""


def mask_key(key: str) -> str:
    """只露後四碼：********a1b2。"""
    k = (key or "").strip()
    if not k:
        return ""
    if len(k) <= 4:
        return "****"
    return "*" * 8 + k[-4:]


def key_status(llm_cfg: dict | None) -> dict:
    """給 /api/llm/status 用：{hasApiKey, apiKeySource, apiKeyMasked}。永不回完整 key。"""
    key, source = resolve_api_key(llm_cfg)
    return {"hasApiKey": bool(key), "apiKeySource": source, "apiKeyMasked": mask_key(key)}


def resolve_config(llm_cfg: dict | None) -> dict:
    """把 settings.llm 正規化成 {provider, baseUrl, model, apiKey, …}。

    - provider 不認識 → 退回 local
    - online/custom 沒有 key → 拋 LLMError（中文，說清楚去哪補）
    - online 若 baseUrl 還停在 127.0.0.1（使用者只切了下拉）→ 自動改用預設線上位址
    """
    cfg = llm_cfg or {}
    provider = str(cfg.get("provider") or "local").strip().lower()
    if provider not in PROVIDERS:
        provider = "local"
    raw_base = str(cfg.get("baseUrl") or "").strip()
    if provider == "online" and (not raw_base or "127.0.0.1" in raw_base or "localhost" in raw_base):
        raw_base = ONLINE_BASE_URL
    base = normalize_base_url(raw_base, provider)
    model = str(cfg.get("model") or "").strip()
    if provider == "online" and model in ("", "auto"):
        model = ONLINE_MODEL
    if not model:
        model = DEFAULT_MODEL

    key, source = resolve_api_key(cfg)
    if provider != "local" and not key:
        if provider == "online":
            raise LLMError(
                f"線上供應商需要 API key，但目前找不到：請把 {ENV_KEYS['online']} 放進 {HERMES_ENV_PATH}"
                "（一行 DEEPSEEK_API_KEY=sk-…），或設環境變數 LLM_API_KEY，"
                "或在設定頁的「API Key」欄位填入（不會寫進 project.json）",
                status=400,
            )
        raise LLMError(
            "自訂供應商需要 API key：請設環境變數 LLM_API_KEY，或在設定頁的「API Key」欄位填入"
            "（不會寫進 project.json）",
            status=400,
        )
    return {
        "provider": provider,
        "providerLabel": _PROVIDER_LABEL[provider],
        "baseUrl": base,
        "model": model,
        "temperature": cfg.get("temperature"),
        "apiKey": key,
        "apiKeySource": source,
    }


# ── HTTP ───────────────────────────────────────────────────────
def _describe_network_error(exc: Exception, base_url: str, provider: str = "local") -> str:
    hp = _host_port(base_url)
    online = provider == "online" or "127.0.0.1" not in hp
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return f"連線模型服務逾時（{hp}）：服務可能正在載入模型，請稍後再試"
        if online:
            return f"連不上線上模型服務（{hp}）：請確認網路連線與 baseUrl 是否正確（目前：{base_url}）"
        return (
            f"連不上本機模型服務（{hp}）：請確認本機 LLM 已啟動"
            "（python -m uvicorn … --port 8080），或到「設定」把供應商改成 online"
        )
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return f"模型服務回應逾時（{hp}）：模型可能仍在載入或任務太長，請縮短內容或稍後重試"
    if isinstance(exc, ConnectionResetError):
        return f"模型服務中斷連線（{hp}）：請重試；若持續發生，換個模型或縮短內容"
    return f"呼叫模型服務失敗（{hp}）：{type(exc).__name__}: {exc}"


def _http_error_message(code: int, body: str, provider: str, base_url: str) -> str:
    """HTTP 錯誤 → 中文訊息（401/403 指向 key，404 指向路徑或模型，429 指向額度）。"""
    snippet = (body or "").strip()[:400]
    low = snippet.lower()
    online = provider != "local"
    if code in (401, 403):
        who = "線上 API key" if online else "API key（本機通常不需要，除非服務有開驗證）"
        return f"模型服務拒絕存取（HTTP {code}）：{who} 無效或沒有權限。{snippet}"
    if code == 404:
        if any(h in low for h in _MODEL_MISSING_HINTS):
            return f"模型不存在或帳號無權使用（HTTP 404）：{snippet}"
        return f"模型服務位址不對（HTTP 404）：請確認 baseUrl 是否要帶 /v1（目前：{base_url}）。{snippet}"
    if code == 429:
        return f"模型服務忙線或額度不足（HTTP 429）：請稍後再試，或檢查帳號餘額。{snippet}"
    if code >= 500:
        return f"模型服務內部錯誤（HTTP {code}）：{snippet}"
    return f"模型服務回應 HTTP {code}：{snippet or '（沒有回應內容）'}"


def _request_json(url: str, payload: dict | None = None, method: str | None = None,
                  api_key: str = "", provider: str = "local", base_url: str | None = None) -> dict:
    """同步 HTTP JSON 呼叫；網路錯誤一律轉 LLMError（中文）。"""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=TOTAL_TIMEOUT) as res:
            raw = res.read()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        msg = _http_error_message(e.code, body, provider, base_url or url)
        # key 錯是使用者可修的設定問題 → 400（前端顯示成「設定」而非「服務掛了」）
        status = 400 if e.code in (401, 403) else 502
        raise LLMError(msg, status=status) from e
    except Exception as e:  # URLError / timeout / 連線被拒
        raise LLMError(_describe_network_error(e, base_url or url, provider), status=502) from e
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception as e:
        raise LLMError(f"模型服務回應不是合法 JSON：{raw[:200]!r}", status=502) from e


def list_models(base_url: str | None = None, api_key: str = "", provider: str = "local") -> list[str]:
    """GET {baseUrl}/models → 模型 id 清單（取不到就拋 LLMError）。"""
    base = normalize_base_url(base_url, provider)
    body = _request_json(f"{base}/models", api_key=api_key, provider=provider, base_url=base)
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list) and isinstance(body, dict):
        items = body.get("models")  # 少數服務把清單放在 models
    if not isinstance(items, list):
        raise LLMError(f"模型服務的 /models 回應格式不認識：{str(body)[:200]}", status=502)
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("id"):
            out.append(str(it["id"]))
        elif isinstance(it, str):
            out.append(it)
    return out


def _pick_model(wanted: str, models: list[str], provider: str) -> tuple[str, str]:
    """決定實際模型。回 (model, note)。note 非空＝有改選（給前端提示）。

    - wanted 在清單中 → 用它
    - wanted 是 "auto"/空 → 清單第一個
    - wanted 不在清單：online 依 ONLINE_FALLBACK_MODELS 找第一個存在的；再不行取清單第一個。
      其他 provider 維持嚴格（回空 model + note，由上層報錯），避免本機默默換模型。
    """
    name = (wanted or "").strip()
    lower = {m.lower(): m for m in models}
    if not name or name.lower() == "auto":
        return (models[0] if models else ""), ""
    if not models:
        return name, ""
    if name.lower() in lower:
        return lower[name.lower()], ""
    if provider == "online":
        for cand in ONLINE_FALLBACK_MODELS:
            if cand.lower() in lower:
                return lower[cand.lower()], f"預設模型 {name} 不在帳號清單，已改用 {lower[cand.lower()]}"
        if models:
            return models[0], f"模型 {name} 不在帳號清單，已改用清單第一個 {models[0]}"
    return name, ""


def resolve_model(base_url: str | None = None, model: str | None = None,
                  api_key: str = "", provider: str = "local") -> tuple[str, list[str]]:
    """回 (實際使用的 model, 可用模型清單)。model 為空/"auto" 時取清單第一個。"""
    models = list_models(base_url, api_key=api_key, provider=provider)
    name = (model or "").strip()
    if not name or name.lower() == "auto":
        if not models:
            raise LLMError("模型服務沒有任何可用模型（/models 回空清單）：請先載入一個模型或改填 model", status=502)
        return models[0], models
    if models and name not in models:
        # 不是所有伺服器都列全部模型（例如 llama.cpp 只有一個別名）；只有在清單非空且明顯不存在時才擋
        lower = {m.lower() for m in models}
        if name.lower() not in lower:
            raise LLMError(f"模型 {name!r} 不在伺服器清單中（可用：{'、'.join(models[:8])}）", status=400)
    return name, models


# ── /models 探測快取 ────────────────────────────────────────────────
# 這台機器連「沒開的埠」要 ~2 秒才回 TCP 拒絕，而設定頁會輪詢 llm/status；
# 不快取就每次白等 2 秒（且與 gpu.status 重複探測同一埠）。只快取唯讀探測，chat 不受影響。
_MODELS_TTL_S = 5.0
_MODELS_FAIL_TTL_S = 10.0
_models_cache: dict[str, tuple[float, list[str], str, int]] = {}


def _cached_models(base_url: str, api_key: str = "", provider: str = "local") -> list[str]:
    key = f"{provider}|{base_url}"
    hit = _models_cache.get(key)
    if hit:
        ttl = _MODELS_TTL_S if not hit[2] else _MODELS_FAIL_TTL_S
        if time.time() - hit[0] < ttl:
            if hit[2]:
                raise LLMError(hit[2], status=hit[3] or 502)
            return hit[1]
    try:
        items = list_models(base_url, api_key=api_key, provider=provider)
    except LLMError as e:
        _models_cache[key] = (time.time(), [], str(e), getattr(e, "status", 502) or 502)
        raise
    _models_cache[key] = (time.time(), items, "", 0)
    return items


def status(llm_cfg: dict | None = None) -> dict:
    """給 GET /api/llm/status 用；永遠回結構，不拋例外。

    M3b：吃整包 settings.llm（含 provider）；永遠不回完整 key（只有 hasApiKey 與遮罩）。
    """
    cfg = llm_cfg or {}
    provider = str(cfg.get("provider") or "local").strip().lower()
    if provider not in PROVIDERS:
        provider = "local"
    ks = key_status(cfg)
    raw_model = str(cfg.get("model") or "").strip()
    wanted = raw_model or ("auto" if provider != "online" else ONLINE_MODEL)
    info = {
        "ok": False,
        "provider": provider,
        "providerLabel": _PROVIDER_LABEL[provider],
        "baseUrl": normalize_base_url(cfg.get("baseUrl"), provider),
        "model": wanted,
        "models": [],
        **ks,
    }
    try:
        conf = resolve_config(cfg)
    except LLMError as e:
        info["error"] = str(e)
        return info

    try:
        models = _cached_models(conf["baseUrl"], api_key=conf["apiKey"], provider=provider)
    except LLMError as e:
        info.update(baseUrl=conf["baseUrl"], error=str(e))
        return info

    name = conf["model"]
    picked, note = _pick_model(name, models, provider)
    if note:
        info["modelNote"] = note
    if picked:
        name = picked
    elif name.lower() != "auto" and models:
        info.update(model=name, models=models, baseUrl=conf["baseUrl"],
                    error=f"指定的模型 {name!r} 不在服務清單中（可用：{'、'.join(models[:8])}）")
        return info
    return {
        "ok": True,
        "provider": provider,
        "providerLabel": _PROVIDER_LABEL[provider],
        "baseUrl": conf["baseUrl"],
        "model": name,
        "models": models,
        **({"modelNote": info["modelNote"]} if info.get("modelNote") else {}),
        **ks,
    }


def _is_local_host(base_url: str) -> bool:
    """baseUrl 是否指向本機（127.0.0.1 / localhost / [::1] / 0.0.0.0）。"""
    host = (base_url or "").split("://", 1)[-1].split("/", 1)[0]
    host = host.rsplit(":", 1)[0] if host.count(":") and not host.endswith("]") else host
    return host.strip("[]").lower() in _LOCAL_HOSTS


def thinking_disabled(provider: str, base_url: str, thinking: bool | None = None) -> bool:
    """這一通要不要關掉模型思考。

    優先序：函式參數 → 環境變數 SVS_LLM_THINKING → 預設（本機端點關、遠端服務開）。
    """
    if thinking is not None:
        return not thinking
    flag = (os.environ.get("SVS_LLM_THINKING") or "").strip().lower()
    if flag in ("on", "1", "true", "yes"):
        return False
    if flag in ("off", "0", "false", "no"):
        return True
    return provider in ("local", "custom") and _is_local_host(base_url)


def _extract_reply(body: dict, used: str) -> dict:
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices:
        raise LLMError(f"模型回應格式不認識（缺 choices）：{str(body)[:300]}", status=502)
    choice = choices[0] if isinstance(choices[0], dict) else {}
    msg = choice.get("message") or {}
    reply = msg.get("content")
    if reply is None:
        reply = choice.get("text") or ""
    return {"reply": str(reply), "model": body.get("model") or used,
            "usage": body.get("usage") or {}, "finish_reason": choice.get("finish_reason")}


def chat(
    messages: list[dict],
    llm_cfg: dict | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    thinking: bool | None = None,
) -> dict:
    """POST /chat/completions（非串流）。回 {reply, model, usage, ...}；失敗拋 LLMError。

    llm_cfg = 專案的 settings.llm（provider/baseUrl/model/temperature）；apiKey 只在後端解析。
    指定/自動的模型先用 /models 解析；/models 掛掉時仍直接用指定 model 打 chat。

    thinking：False＝送 chat_template_kwargs.enable_thinking=false（本機推理模型不再把
    max_tokens 花在思考上）；None＝依 provider/端點自動判斷（見 thinking_disabled）。
    服務不認這個欄位時自動去掉重試一次，不會因此壞掉。
    """
    conf = resolve_config(llm_cfg)
    provider, base, key = conf["provider"], conf["baseUrl"], conf["apiKey"]
    wanted = (model or conf["model"] or "").strip()
    used = wanted
    if not wanted or wanted.lower() == "auto":
        try:
            used, _ = resolve_model(base, wanted, api_key=key, provider=provider)
        except LLMError:
            used = ONLINE_MODEL if provider == "online" else ""
    elif provider == "online":
        # M3b：online 帳號可能不列 deepseek-chat；用清單挑一個它認得的（清單取不到就照原樣試）
        try:
            used = _pick_model(wanted, list_models(base, api_key=key, provider=provider), provider)[0] or wanted
        except LLMError:
            used = wanted
    payload: dict = {"messages": messages, "stream": False}
    if used:
        payload["model"] = used
    temp = temperature if temperature is not None else conf.get("temperature")
    if temp is not None:
        try:
            payload["temperature"] = float(temp)
        except (TypeError, ValueError):
            pass
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    no_think = thinking_disabled(provider, base, thinking)
    if no_think:
        payload.update(NO_THINK_PAYLOAD)

    def send(p: dict) -> dict:
        try:
            return _request_json(f"{base}/chat/completions", p, api_key=key, provider=provider, base_url=base)
        except LLMError:
            if "chat_template_kwargs" not in p:
                raise
            # 服務不認這個欄位（非 llama.cpp / 嚴格驗證）→ 拿掉再送一次
            fallback = {k: v for k, v in p.items() if k != "chat_template_kwargs"}
            return _request_json(f"{base}/chat/completions", fallback, api_key=key, provider=provider, base_url=base)

    body = send(payload)
    out = _extract_reply(body, used)
    if not out["reply"].strip() and not no_think and thinking is None:
        # 沒關思考卻拿到空正文：典型是思考吃光 max_tokens → 關掉思考再試一次
        payload.update(NO_THINK_PAYLOAD)
        retry = _extract_reply(send(payload), used)
        if retry["reply"].strip():
            return retry
        out = retry
    if not out["reply"].strip():
        # 常見於模型只吐 reasoning/被截斷；把 finish_reason 帶出來方便判斷
        raise LLMError(f"模型回覆是空的（finish_reason={out['finish_reason']}）："
                       "思考內容可能吃光輸出上限，請換模型或在設定關掉思考", status=502)
    return out
