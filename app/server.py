"""Story Video Studio 後端（M1：骨架 + 專案 CRUD）。

啟動：python -m uvicorn app.server:app --host 127.0.0.1 --port 8788
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, Optional

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import comfy, deploy, h3compose, h3gen, imagegen, llm, prompts, storygen, promptrules
from . import gpu
from . import assemble as asm
from . import paths
from . import storage
from . import version
from .storage import StorageError

log = logging.getLogger("svs")

app = FastAPI(title="Story Video Studio", version=version.get())


def _fail(status: int, message: str):
    return JSONResponse(status_code=status, content={"ok": False, "error": message})


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """任何未預期例外都回中文 JSON，不吐 traceback（模型服務／前端都靠 error 字串顯示）。"""
    log.exception("未處理的例外：%s %s", request.method, request.url.path)
    return _fail(500, f"伺服器內部錯誤：{type(exc).__name__}: {exc}")


# ── API ─────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    ver = version.get()
    return {
        "ok": True,
        "app": "story-video-studio",
        "version": ver,
        "milestone": f"v{ver}",
        "pid": os.getpid(),
        "dataDir": str(storage.PROJECTS_DIR),
        "time": storage.now_iso(),
        "paths": paths.describe(),
    }


@app.get("/api/projects")
def api_list_projects():
    try:
        return {"ok": True, "projects": storage.list_projects()}
    except StorageError as e:
        return _fail(400, str(e))


class CreateProjectBody(BaseModel):
    name: str
    aspectRatio: str = "9:16"
    slug: Optional[str] = None


@app.post("/api/projects")
def api_create_project(body: CreateProjectBody):
    try:
        data = storage.create_project(body.name, body.aspectRatio, body.slug)
    except StorageError as e:
        return _fail(400, str(e))
    meta = data["meta"]
    return {
        "ok": True,
        "slug": meta["slug"],
        "name": meta["name"],
        "project": data,
    }


@app.get("/api/projects/{slug}")
def api_get_project(slug: str):
    try:
        return {"ok": True, "project": storage.load_project(slug)}
    except StorageError as e:
        return _fail(404, str(e))


@app.put("/api/projects/{slug}")
async def api_put_project(slug: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        return _fail(400, "請求內容不是合法 JSON")
    try:
        data = storage.save_project(slug, body)
    except StorageError as e:
        msg = str(e)
        status = 404 if msg.startswith("找不到專案") else 400
        return _fail(status, msg)
    return {
        "ok": True,
        "updatedAt": data["meta"]["updatedAt"],
        # M2a：順便回下一個可用 id（前端多分頁同時新增時可再核對）
        "nextIds": {
            "characters": storage.next_id(data["characters"], "c"),
            "scenes": storage.next_id(data["scenes"], "sc"),
        },
    }


@app.delete("/api/projects/{slug}")
def api_delete_project(slug: str):
    try:
        trash = storage.delete_project(slug)
    except StorageError as e:
        return _fail(404, str(e))
    return {"ok": True, "movedTo": f"projects/_trash/{trash}"}


@app.post("/api/projects/{slug}/upload")
async def api_upload(slug: str, kind: str = Query(...), file: UploadFile = File(...)):
    try:
        payload = await file.read()
        rel = storage.save_upload(slug, kind, file.filename or "", payload)
    except StorageError as e:
        return _fail(400, str(e))
    return {"ok": True, "path": rel, "url": f"/files/{slug}/{rel}"}


# ── M3a：模型對話層 ─────────────────────────────────────────────
# 原則：/chat 只回文字（不寫檔）；要進專案一定走 /chat/apply（使用者按「套用」）。
class TargetBody(BaseModel):
    kind: str
    id: Optional[str] = None
    # M3b：beat id 只在場次內唯一 → 帶所屬場次（episodeScene id）定位
    parentId: Optional[str] = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatBody(BaseModel):
    messages: list[ChatMessage]
    task: Optional[str] = None
    target: Optional[TargetBody] = None
    projectSlug: Optional[str] = None
    temperature: Optional[float] = None
    maxTokens: Optional[int] = None
    model: Optional[str] = None


class ApplyBody(BaseModel):
    target: TargetBody
    path: str
    value: str
    mode: str = "replace"


class ApplyItemBody(BaseModel):
    """M13a：一項套用。value 可以是字串，也可以是整份陣列（chapters／beats／dialogue／outfits）。"""
    path: str
    value: Any = ""
    mode: str = "replace"


class ApplyBatchBody(BaseModel):
    target: TargetBody
    items: list[ApplyItemBody]


class LlmSettingsBody(BaseModel):
    """M3b：設定頁寫 settings.llm（provider/baseUrl/model/temperature）。

    apiKey 只接受「本次使用」的 key：寫入 project.json 前先剝掉，只回 hasApiKey 狀態。
    """
    provider: Optional[str] = None
    baseUrl: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    apiKey: Optional[str] = None


_LLM_DEFAULT_BASE = {
    "local": "http://127.0.0.1:8080/v1",
    "online": llm.ONLINE_BASE_URL,
    "custom": "",
}
_LLM_DEFAULT_MODEL = {"local": "auto", "online": llm.ONLINE_MODEL, "custom": ""}

_APPLY_LABELS = {
    "story": "故事",
    "character": "人物",
    "scene": "場景",
    "episodeScene": "劇本場次",
    "beat": "台詞節拍",
    "shot": "鏡頭",
}


@app.get("/api/llm/status")
def api_llm_status(slug: Optional[str] = Query(None), baseUrl: Optional[str] = Query(None),
                   model: Optional[str] = Query(None), provider: Optional[str] = Query(None)):
    """模型服務狀態：{ok, provider, baseUrl, model, models[], hasApiKey, apiKeyMasked}。

    連不上也是 200 + ok:false（前端畫紅點＋橫幅）。永遠不回完整 API key。
    baseUrl/model/provider 可由設定頁傳入（讓使用者先試再存）。
    """
    cfg = dict(storage.llm_settings(slug))
    if baseUrl is not None:
        cfg["baseUrl"] = baseUrl
    if model is not None:
        cfg["model"] = model
    if provider is not None:
        cfg["provider"] = provider
    return llm.status(cfg)


@app.put("/api/projects/{slug}/llm/settings")
def api_put_llm_settings(slug: str, body: LlmSettingsBody):
    """M3b：設定頁存供應商設定。apiKey 不會寫進 project.json（只反映在 hasApiKey）。"""
    try:
        with storage._lock:
            data = storage.load_project(slug)
            llm_cfg = (data.setdefault("settings", {})).setdefault("llm", {})
            for key in ("provider", "baseUrl", "model", "temperature"):
                val = getattr(body, key)
                if val is not None:
                    llm_cfg[key] = val
            if body.provider in ("local", "online", "custom") and body.baseUrl is None:
                # 只切 provider 沒給 baseUrl → 套該供應商預設（避免 online 還打 127.0.0.1／local 還留著線上模型）
                llm_cfg["baseUrl"] = _LLM_DEFAULT_BASE.get(body.provider, llm_cfg.get("baseUrl"))
                llm_cfg["model"] = _LLM_DEFAULT_MODEL.get(body.provider, llm_cfg.get("model"))
            req_key = (body.apiKey or "").strip()
            storage.strip_llm_api_key(data)
            data["meta"]["updatedAt"] = storage.now_iso()
            errors = storage.validate_project(data)
            if errors:
                raise StorageError("；".join(errors))
            storage._write_json_atomic(storage.project_file(slug), data)
    except StorageError as e:
        msg = str(e)
        return _fail(404 if msg.startswith("找不到專案") else 400, msg)
    # 回狀態時：若這次帶了 key，就用它試連一次（不回 key 本體）
    cfg = dict(storage.llm_settings(slug))
    if req_key:
        cfg["apiKey"] = req_key
    info = llm.status(cfg)
    info["apiKeySaved"] = bool(req_key)
    info["note"] = "API key 只供本次連線使用，未寫入 project.json；長期請用環境變數或 hermes .env"
    return info


@app.post("/api/llm/test")
def api_llm_test(body: LlmSettingsBody, slug: Optional[str] = Query(None)):
    """M3b：設定頁「重新檢查」——用畫面上的值（含尚未儲存的 key）測連線並取模型列表。"""
    cfg = dict(storage.llm_settings(slug))
    for key in ("provider", "baseUrl", "model"):
        val = getattr(body, key)
        if val is not None:
            cfg[key] = val
    if (body.apiKey or "").strip():
        cfg["apiKey"] = body.apiKey.strip()
    return llm.status(cfg)


@app.post("/api/projects/{slug}/chat")
def api_chat(slug: str, body: ChatBody):
    """組 system prompt（專案 meta＋目標卡片現況＋任務規則）→ 呼叫模型 → 回 {reply, model, usage}。"""
    try:
        project = storage.load_project(slug)
    except StorageError as e:
        return _fail(404, str(e))
    messages = [m.model_dump() for m in body.messages if (m.content or "").strip()]
    if not messages:
        return _fail(400, "messages 不可為空（至少一句使用者訊息）")
    if messages[-1].get("role") != "user":
        messages.append({"role": "user", "content": "請根據上面的內容繼續。"})

    target_obj = None
    if body.target:
        kind = body.target.kind
        if kind in ("episodeScene", "beat", "shot"):
            # M3b：劇本／鏡頭類別先確認存在（beat 用所屬場次定位），再展開成人看得懂的資料
            try:
                if kind == "beat" and body.target.parentId:
                    pool = []
                    for ep in (project.get("script") or {}).get("episodes") or []:
                        pool += [es for es in (ep.get("scenes") or []) if isinstance(es, dict)]
                    if not any(
                        es.get("id") == body.target.parentId
                        and any(isinstance(b, dict) and b.get("id") == body.target.id for b in es.get("beats") or [])
                        for es in pool
                    ):
                        return _fail(400, f"找不到 beat：{body.target.parentId}/{body.target.id}")
                else:
                    storage.find_target(project, kind, body.target.id)
                target_obj = prompts.build_target(project, kind, body.target.id)
            except StorageError as e:
                return _fail(400, str(e))
        else:
            try:
                target_obj = storage.find_target(project, body.target.kind, body.target.id)
            except StorageError as e:
                return _fail(400, str(e))

    cfg = storage.llm_settings(slug)
    system = prompts.build_system(
        project=project,
        kind=(body.target.kind if body.target else None),
        target=target_obj,
        task=body.task,
    )
    full = [{"role": "system", "content": system}, *messages]
    temp = body.temperature if body.temperature is not None else cfg.get("temperature")
    try:
        result = llm.chat(
            full,
            llm_cfg=cfg,
            model=body.model or cfg.get("model"),
            temperature=temp,
            max_tokens=body.maxTokens,
        )
    except llm.LLMError as e:
        return JSONResponse(status_code=e.status, content={
            "ok": False, "error": str(e), "baseUrl": cfg.get("baseUrl"), "provider": cfg.get("provider"),
        })
    return {
        "ok": True,
        "reply": result["reply"],
        "model": result["model"],
        "usage": result["usage"],
        "finishReason": result.get("finish_reason"),
        "task": body.task or "",
        "target": (body.target.model_dump() if body.target else None),
        "provider": llm.resolve_config(cfg)["provider"],
        "systemChars": len(system),
    }


@app.post("/api/projects/{slug}/chat/apply")
def api_chat_apply(slug: str, body: ApplyBody):
    """把一段文字寫進專案某張卡的某個欄位（使用者按「套用」才會打到這裡）。

    M3b：欄位走白名單；beat 用 parentId（所屬場次）定位；shot 直接寫 promptZh/promptEn/spec/dialogue。
    M13a：單欄介面也是走 apply_to_target（與 apply-batch 同一套驗證）；要一次多欄請用 apply-batch。
    """
    if not (body.value or "").strip():
        return _fail(400, "要套用的內容是空的")
    try:
        result = storage.apply_to_target(
            slug, body.target.kind, body.target.id, body.path, body.value, body.mode,
            parent_id=body.target.parentId,
        )
    except StorageError as e:
        msg = str(e)
        status = 404 if msg.startswith("找不到專案") else 400
        return _fail(status, msg)
    out = {"ok": True, **result}
    tid = result["target"].get("id")
    label = _APPLY_LABELS.get(body.target.kind, body.target.kind)
    if body.target.kind == "shot":
        # 鏡頭用「第 N 鏡（shXX）」比較好認
        sh = result.get("targetAfter") or {}
        label = f"第 {sh.get('no', '?')} 鏡（{tid}）" if isinstance(sh, dict) else f"鏡頭 {tid}"
    elif tid and body.target.kind not in ("story",):
        label = f"{label} {tid}"
    out["label"] = label
    return out


def _apply_label(kind: str, target_after: dict, tid: str | None) -> str:
    label = _APPLY_LABELS.get(kind, kind)
    if kind == "shot":
        return f"第 {target_after.get('no', '?')} 鏡（{tid}）" if isinstance(target_after, dict) else f"鏡頭 {tid}"
    if tid and kind != "story":
        return f"{label} {tid}"
    return label


@app.post("/api/projects/{slug}/chat/apply-batch")
def api_chat_apply_batch(slug: str, body: ApplyBatchBody):
    """M13a：一次套用多個欄位（含整份章回／beats／dialogue／outfits）。

    一次讀檔 → 逐項寫在記憶體 → 單次 validate_project → 原子寫檔一次；
    任何一項不合法就整批不寫（400，錯誤訊息指出是第幾項）。
    """
    if not body.items:
        return _fail(400, "items 是空的（至少要勾一個要套用的欄位）")
    try:
        result = storage.apply_to_target_batch(
            slug, body.target.kind, body.target.id,
            [it.model_dump() for it in body.items],
            parent_id=body.target.parentId,
        )
    except StorageError as e:
        msg = str(e)
        status = 404 if msg.startswith("找不到專案") else 400
        return _fail(status, msg)
    return {"ok": True, **result,
            "label": _apply_label(body.target.kind, result.get("targetAfter") or {},
                                  (result.get("target") or {}).get("id"))}


# ── M3c：結構化一次生成（dryRun 只回預覽，不寫檔）──────────────────
class GenScriptBody(BaseModel):
    episodeTitle: Optional[str] = None
    sceneCount: int = 3
    targetMinutes: float = 1.5
    dryRun: bool = True


class GenShotsBody(BaseModel):
    episodeSceneId: str
    count: int = 3
    dryRun: bool = True


class BulkBody(BaseModel):
    kind: str                      # "episode" | "shots"
    data: object
    mode: str = "append"           # "append" | "replace"


def _gen_error(e: Exception, cfg: dict | None = None):
    """生成失敗 → 中文錯誤（結構問題 400、供應商連線問題沿用 llm 的 status）。"""
    if isinstance(e, llm.LLMError):
        return JSONResponse(status_code=e.status, content={
            "ok": False, "error": str(e),
            "baseUrl": (cfg or {}).get("baseUrl"), "provider": (cfg or {}).get("provider")})
    if isinstance(e, storygen.GenError):
        return JSONResponse(status_code=e.status, content={
            "ok": False, "error": str(e), "rawSnippet": (e.raw or "")[:500]})
    raise e


@app.post("/api/projects/{slug}/generate/script")
def api_generate_script(slug: str, body: GenScriptBody):
    """故事 → 整集劇本（episode + scenes + beats）。dryRun=true 時只回預覽。"""
    try:
        project = storage.load_project(slug)
    except StorageError as e:
        return _fail(404, str(e))
    if not (project.get("characters") and project.get("scenes")):
        return _fail(400, "生成劇本前請先建好至少一張人物卡與一張場景卡（模型只能用真實 id）")
    cfg = storage.llm_settings(slug)
    try:
        result = storygen.gen_script(project, cfg, body.episodeTitle or "",
                                     body.sceneCount, body.targetMinutes)
    except Exception as e:  # noqa: BLE001 — 一律轉中文 JSON（不是 500）
        return _gen_error(e, cfg)
    created = None
    if not body.dryRun:
        try:
            created = storage.apply_bulk(slug, "episode", result["preview"], "append")
        except StorageError as e:
            return _fail(400, str(e))
    return {"ok": True, "dryRun": bool(body.dryRun), "model": result["model"],
            "usage": result["usage"], "preview": result["preview"], "warnings": result["warnings"],
            "created": created,
            "params": {"episodeTitle": body.episodeTitle or "", "sceneCount": body.sceneCount,
                       "targetMinutes": body.targetMinutes}}


@app.post("/api/projects/{slug}/generate/shots")
def api_generate_shots(slug: str, body: GenShotsBody):
    """某場次 → 多個鏡頭（spec/beats/dialogLineRefs/promptZh/promptEn）。dryRun=true 只回預覽。"""
    try:
        project = storage.load_project(slug)
    except StorageError as e:
        return _fail(404, str(e))
    cfg = storage.llm_settings(slug)
    try:
        result = storygen.gen_shots(project, cfg, body.episodeSceneId, body.count)
    except Exception as e:  # noqa: BLE001
        return _gen_error(e, cfg)
    created = None
    if not body.dryRun:
        try:
            created = storage.apply_bulk(slug, "shots", result["preview"], "append")
        except StorageError as e:
            return _fail(400, str(e))
    return {"ok": True, "dryRun": bool(body.dryRun), "model": result["model"],
            "usage": result["usage"], "preview": result["preview"], "warnings": result["warnings"],
            "created": created}


@app.post("/api/projects/{slug}/apply/bulk")
def api_apply_bulk(slug: str, body: BulkBody):
    """把（使用者確認過的）預覽 JSON 一次寫進專案；append 會重新編 id/no。"""
    try:
        result = storage.apply_bulk(slug, body.kind, body.data, body.mode)
    except StorageError as e:
        msg = str(e)
        return _fail(404 if msg.startswith("找不到專案") else 400, msg)
    return {"ok": True, **result}


# ── M3d：referencePlan / 確定性 prompt 編譯 / 全專案機械校驗 ────────
# 全部純邏輯，不呼叫任何模型；結果會寫回 project.json（compile 寫 promptEn）。
def _load_for_m3d(slug: str):
    """回 (data, None) 或 (None, 錯誤回應)。找不到專案 → 404 中文。"""
    try:
        data = storage.load_project(slug)
    except StorageError as e:
        msg = str(e)
        return None, _fail(404 if msg.startswith("找不到專案") else 400, msg)
    return storage.fill_project_defaults(data), None


def _get_shot(data: dict, shot_id: str):
    return next((s for s in (data.get("shots") or []) if s.get("id") == shot_id), None)


@app.post("/api/projects/{slug}/shots/{shot_id}/build-reference-plan")
def api_build_reference_plan(slug: str, shot_id: str):
    data, err = _load_for_m3d(slug)
    if err:
        return err
    shot = _get_shot(data, shot_id)
    if shot is None:
        return _fail(404, f"找不到鏡頭 {shot_id}")
    try:
        plan, warnings = h3compose.build_reference_plan(data, shot)
    except Exception as e:  # 純邏輯出錯也回中文，不 500
        log.exception("build-reference-plan 失敗")
        return _fail(400, f"建參考圖槽位失敗：{type(e).__name__}: {e}")
    shot["referencePlan"] = plan
    try:
        storage.save_project(slug, data)
    except StorageError as e:
        return _fail(400, f"寫回專案失敗：{e}")
    return {"ok": True, "shotId": shot_id, "referencePlan": plan, "warnings": warnings,
            "count": len(plan["slots"])}


@app.post("/api/projects/{slug}/shots/{shot_id}/compile-prompt")
def api_compile_prompt(slug: str, shot_id: str):
    data, err = _load_for_m3d(slug)
    if err:
        return err
    shot = _get_shot(data, shot_id)
    if shot is None:
        return _fail(404, f"找不到鏡頭 {shot_id}")
    try:
        prompt, warnings, meta = h3compose.compile_prompt(data, shot)
    except Exception as e:
        log.exception("compile-prompt 失敗")
        return _fail(400, f"編譯 prompt 失敗：{type(e).__name__}: {e}")
    if not prompt.strip():
        return _fail(400, "編譯結果是空的（鏡頭沒有角色、場景、動作可用）")
    shot["promptEn"] = prompt
    try:
        storage.save_project(slug, data)
    except StorageError as e:
        return _fail(400, f"寫回專案失敗：{e}")
    return {"ok": True, "shotId": shot_id, "promptEn": prompt, "warnings": warnings, "meta": meta}


class ValidateBody(BaseModel):
    """留白即可；日後若要只驗部分範圍再用。"""


# ── M4a：單鏡生圖（本機 ComfyUI · Qwen 2.1 i2i/t2i）────────────────
class GenerateImageBody(BaseModel):
    """mode=ref：帶參考圖做 i2i 編輯（預設）；mode=t2i：純文字生圖。其餘欄位留白用預設。

    engine：qwen（預設）｜moss（MOSS Image SFT，純文字 caption、不吃參考圖）
    """
    mode: str = "ref"
    engine: Optional[str] = None
    resolution: Optional[int] = None
    steps: Optional[int] = None
    cfg: Optional[float] = None
    seed: Optional[int] = None
    aspect: Optional[str] = None
    timeout: Optional[int] = None
    sizeTier: Optional[int] = None


class CharacterSheetBody(BaseModel):
    """為人物生正面全身立繪。ids 不給＝全部人物；count>0 再截前 N 位。"""
    engine: Optional[str] = None
    ids: Optional[list[str]] = None
    count: Optional[int] = None
    seed: Optional[int] = None
    steps: Optional[int] = None
    cfg: Optional[float] = None
    timeout: Optional[int] = None
    sizeTier: Optional[int] = None


class ImageEngineBody(BaseModel):
    """設定頁的「生圖引擎」下拉（settings.imageEngine）。"""
    engine: str


class SheetBody(BaseModel):
    """第二層：H3 多角度設定表。views 不給＝預設五視角；crop 預設 True（拼版可切就切）。"""
    views: Optional[list[str]] = None
    style: Optional[str] = None
    crop: Optional[bool] = True
    bodyHeight: Optional[int] = None
    megapixels: Optional[int] = None
    seed: Optional[int] = None
    timeout: Optional[int] = None


@app.get("/api/comfy/status")
def api_comfy_status(slug: Optional[str] = Query(None), baseUrl: Optional[str] = Query(None)):
    """ComfyUI 狀態：{ok, baseUrl, version, device, vramFreeMB, message}。連不上也是 200。"""
    url = baseUrl
    if url is None and slug:
        try:
            url = ((storage.load_project(slug).get("settings") or {}).get("comfy") or {}).get("baseUrl")
        except StorageError:
            url = None
    ok, message, info = comfy.comfy_available(url)
    return {"ok": ok, "message": message, **info}


@app.post("/api/projects/{slug}/shots/{shot_id}/generate/image")
def api_generate_shot_image(slug: str, shot_id: str, body: Optional[GenerateImageBody] = None):
    """同步生一張首幀（最多等到 timeout，預設 180 秒），完成後寫回 shots[].images.first。

    失敗一律 200 + {ok:false, error:中文原因}（前端直接顯示），不讓整個請求變 500。
    生圖前會自動停掉閒置的本機 LLM 騰 VRAM，結束後一定啟回；結果在 gpu 欄位。
    """
    body = body or GenerateImageBody()
    data, err = _load_for_m3d(slug)
    if err:
        return err
    if _get_shot(data, shot_id) is None:
        return _fail(404, f"找不到鏡頭 {shot_id}")
    opts = body.model_dump(exclude_none=True)
    try:
        result = imagegen.generate_shot_image(data, shot_id, opts)
    except imagegen.ImageGenError as e:
        busy = bool(getattr(e, "busy", False))
        content = {"ok": False, "error": str(e), "shotId": shot_id,
                   "baseUrl": imagegen._comfy_base(data)}
        if busy:
            content["gpu"] = {"busy": True, "error": str(e), "switched": False, "stoppedLlama": False,
                              "restored": False, "restartWaitedS": 0.0, "freedMB": None}
        return JSONResponse(status_code=200, content=content)
    except Exception as e:  # noqa: BLE001 — 一律中文，不吐 traceback
        log.exception("生圖未預期失敗")
        return JSONResponse(status_code=200, content={
            "ok": False, "error": f"生圖時發生未預期錯誤：{type(e).__name__}: {e}", "shotId": shot_id})
    try:
        storage.save_project(slug, data)
    except StorageError as e:
        # 圖已生成但寫不回專案：回半成功，明確講清楚圖在哪
        return JSONResponse(status_code=200, content={
            "ok": False, "shotId": shot_id,
            "error": f"圖已生成並存到 {result['path']}，但寫回 project.json 失敗：{e}（請手動把首幀路徑貼上）",
            **{k: result[k] for k in ("pid", "elapsed", "path")}})
    result["shot"] = {"id": shot_id, "images": _get_shot(data, shot_id).get("images")}
    result["url"] = f"/files/{slug}/{result['path']}"
    return result


# ── M6：生圖引擎（settings.imageEngine）＋角色立繪端點 ──────────────
_ENGINE_LABELS = {"qwen": "Qwen Image 2.1（i2i／t2i）", "moss": "MOSS Image SFT（t2i，純文字）",
                  "h3sheet": "H3 Character Sheet（多角度，i2i，很慢）"}


@app.get("/api/projects/{slug}/image/engine")
def api_get_image_engine(slug: str):
    """目前生圖引擎＋可選項＋(MOSS) 本機三件套是否都在 ComfyUI 的清單裡。"""
    data, err = _load_for_m3d(slug)
    if err:
        return err
    cur = (data.get("settings") or {}).get("imageEngine") or "qwen"
    info = {"ok": True, "slug": slug, "engine": cur, "label": _ENGINE_LABELS.get(cur, cur),
            "options": [{"value": k, "label": v} for k, v in _ENGINE_LABELS.items()],
            "loaders": imagegen.read_back_loaders(imagegen._comfy_base(data))}
    return info


@app.put("/api/projects/{slug}/image/engine")
def api_put_image_engine(slug: str, body: ImageEngineBody):
    """寫 settings.imageEngine（預設保留 qwen）。"""
    if body.engine not in imagegen.ENGINES:
        return _fail(400, f"不支援的生圖引擎：{body.engine}（合法值：{'／'.join(imagegen.ENGINES)}）")
    try:
        with storage._lock:
            data = storage.load_project(slug)
            data.setdefault("settings", {})["imageEngine"] = body.engine
            data["meta"]["updatedAt"] = storage.now_iso()
            errors = storage.validate_project(data)
            if errors:
                raise StorageError("；".join(errors))
            storage._write_json_atomic(storage.project_file(slug), data)
    except StorageError as e:
        return _fail(404 if str(e).startswith("找不到專案") else 400, str(e))
    return {"ok": True, "slug": slug, "engine": body.engine, "label": _ENGINE_LABELS.get(body.engine, "")}


@app.get("/api/projects/{slug}/shots/{shot_id}/caption/moss")
def api_preview_moss_caption(slug: str, shot_id: str):
    """預覽 MOSS 會收到的 caption（純字串組裝、不碰 GPU、不寫檔）。"""
    data, err = _load_for_m3d(slug)
    if err:
        return err
    shot = _get_shot(data, shot_id)
    if shot is None:
        return _fail(404, f"找不到鏡頭 {shot_id}")
    try:
        caption, warnings = imagegen.build_moss_caption(data, shot)
    except Exception as e:  # noqa: BLE001
        return _fail(400, f"組 caption 失敗：{type(e).__name__}: {e}")
    return {"ok": True, "slug": slug, "shotId": shot_id, "caption": caption,
            "chars": len(caption), "warnings": warnings}


@app.get("/api/prompt-rules")
def api_prompt_rules():
    """M12b：所有生成引擎的官方提示詞規則卡（純讀；前端顯示「規則：<label>」與規則條列）。"""
    return {"ok": True, "rules": promptrules.describe(), "engines": list(promptrules.RULES)}


@app.get("/api/projects/{slug}/shots/{shot_id}/prompt-preview")
def api_prompt_preview(slug: str, shot_id: str, engine: str = Query("h3_video")):
    """M12b：預覽「這次真的會送出的提示詞」＋規則驗證結果（純讀、不碰 GPU、不寫檔）。

    engine：h3_video（H3 影片六段式）｜moss_t2i（MOSS caption）｜qwen_image（Qwen 官方槽位）。
    h3_video 依 h3gen.build_timeline 的同一套規則選稿（promptEn 合格 → promptEn；否則模板編譯），
    所以這裡看到的就是按「生影片」時模型會收到的字。
    """
    data, err = _load_for_m3d(slug)
    if err:
        return err
    shot = _get_shot(data, shot_id)
    if shot is None:
        return _fail(404, f"找不到鏡頭 {shot_id}")
    eng = promptrules._norm(engine)
    rule = promptrules.rule_of(eng)
    if rule is None:
        return _fail(400, f"不認得的引擎 {engine}（合法值：{'／'.join(promptrules.RULES)}）")
    warnings: list[str] = []
    problems: list[str] = []
    notes: list[str] = []
    source = "promptEn"
    # 預覽一律要能看到「真的會送出去的字」，所以任何一段失敗都要退回組稿而不是回空字串。
    if eng == "h3_video":
        ref_problems: list[str] = []
        try:
            refs, w, ref_notes = h3gen.collect_refs(data, shot)
            warnings.extend(w)
            sendable = h3gen._sendable_ordinals(refs, ref_notes)
        except Exception as e:  # noqa: BLE001 — 通常是「一個可用的參考圖都沒有」
            ref_problems.append(str(e))
            sendable = [s["ordinal"] for s in (shot.get("referencePlan") or {}).get("slots") or []
                        if isinstance(s, dict) and s.get("ordinal")]
        prompt, pw, source, problems = h3gen._pick_prompt(data, shot, sendable)
        warnings.extend(pw)
        problems = ref_problems + list(problems)
        ok = not problems
    elif eng == "moss_t2i":
        prompt, warnings = imagegen.build_moss_caption(data, shot)
        ok, problems, notes = promptrules.check(eng, prompt, promptrules.ctx_for(eng, data, shot))
    else:
        refs, w = imagegen.collect_refs(data, shot)
        warnings.extend(w)
        prompt, w2 = imagegen.build_prompt(data, shot, refs)
        warnings.extend(w2)
        ok, problems, notes = promptrules.check(eng, prompt, promptrules.ctx_for(eng, data, shot, refs=refs))
        source = "build_prompt"
    return {"ok": ok, "engine": eng, "ruleId": rule["id"], "label": rule["label"],
            "source": rule["source"], "rulesZh": rule["rulesZh"], "prompt": prompt,
            "promptSource": source, "warnings": warnings, "problems": problems, "notes": notes,
            "chars": len(prompt)}


@app.get("/api/projects/{slug}/characters/{cid}/caption/moss")
def api_preview_moss_character_caption(slug: str, cid: str):
    """預覽角色立繪會收到的 caption（純邏輯）。"""
    data, err = _load_for_m3d(slug)
    if err:
        return err
    ch = imagegen.find_character(data, cid)
    if ch is None:
        return _fail(404, f"找不到人物 {cid}")
    caption, warnings = imagegen.build_character_sheet_caption(data, ch)
    return {"ok": True, "slug": slug, "characterId": cid, "caption": caption,
            "chars": len(caption), "warnings": warnings}


@app.post("/api/projects/{slug}/generate/characters")
def api_generate_characters(slug: str, body: Optional[CharacterSheetBody] = None):
    """為每個人物生「正面全身立繪、中性淺灰背景、完整服裝、清楚臉部」。

    同步跑（一個人物一張圖）；寫回 characters[].refImages（kind=full、lockedFace=true）
    與 media/characters/<id>_sheet_<時間>.png。單一人物失敗不中斷，結果在 items[].error。
    """
    body = body or CharacterSheetBody()
    data, err = _load_for_m3d(slug)
    if err:
        return err
    opts = body.model_dump(exclude_none=True)
    try:
        result = imagegen.generate_all_character_sheets(data, body.ids, opts)
    except imagegen.ImageGenError as e:
        busy = bool(getattr(e, "busy", False))
        content = {"ok": False, "slug": slug, "error": str(e)}
        if busy:
            content["gpu"] = {"busy": True, "error": str(e), "switched": False, "stoppedLlama": False,
                              "restored": False, "restartWaitedS": 0.0, "freedMB": None}
        return JSONResponse(status_code=200, content=content)
    except Exception as e:  # noqa: BLE001
        log.exception("角色立繪未預期失敗")
        return JSONResponse(status_code=200, content={
            "ok": False, "slug": slug, "error": f"角色立繪時發生未預期錯誤：{type(e).__name__}: {e}"})
    saved_ok = True
    try:
        storage.save_project(slug, data)
    except StorageError as e:
        saved_ok = False
        result["ok"] = False
        result["error"] = f"圖已生成，但寫回 project.json 失敗：{e}（圖仍在 media/characters/）"
    for it in result.get("items") or []:
        if it.get("path"):
            it["url"] = f"/files/{slug}/{it['path']}"
    result.update({"slug": slug, "saved": saved_ok})
    return result


# ── M4b：生圖前置檢查（乾跑，不碰 GPU）／批次生圖（背景依序）──────────
class PreflightBody(BaseModel):
    """mode=ref 時檢查參考圖與 <Picture N>；t2i 只檢查提示詞。"""
    mode: str = "ref"


class BatchImageBody(BaseModel):
    """shotIds 必填；trialFirst=true（預設）只先生第一鏡給人看，確認後再放量。"""
    shotIds: list[str] = []
    mode: str = "ref"
    engine: Optional[str] = None
    resolution: Optional[int] = None
    trialFirst: bool = True


def _preflight_payload(data: dict, shot_ids: list[str], mode: str) -> dict:
    """對每個鏡頭跑 imagegen.preflight，回 {ok, items[], blockers[], ready}。"""
    items = []
    for sid in shot_ids:
        shot = _get_shot(data, sid)
        if shot is None:
            items.append({"shotId": sid, "ok": False, "blockers": [f"找不到鏡頭 {sid}"],
                          "warnings": [], "refs": []})
            continue
        try:
            refs, warnings, blockers = imagegen.preflight(data, shot, mode)
        except Exception as e:  # noqa: BLE001
            items.append({"shotId": sid, "ok": False, "blockers": [f"檢查時出錯：{type(e).__name__}: {e}"],
                          "warnings": [], "refs": []})
            continue
        items.append({
            "shotId": sid, "no": shot.get("no"), "ok": not blockers,
            "blockers": blockers, "warnings": warnings,
            "refs": [{"path": r["path"], "label": r["label"]} for r in refs],
            "mode": mode,
        })
    all_blockers = [f"{i['shotId']}（第 {i.get('no')} 鏡）：{b}" for i in items for b in i["blockers"]]
    return {"ok": not all_blockers, "items": items, "blockers": all_blockers,
            "ready": [i["shotId"] for i in items if i["ok"]]}


@app.post("/api/projects/{slug}/preflight/image")
def api_preflight_image(slug: str, body: Optional[PreflightBody] = None):
    """生圖前置檢查：缺鎖臉圖／<Picture N> 對不上／沒有提示詞 → 回清單（不送 GPU）。

    shotIds 不給時檢查全部鏡頭（前端在批次前會只問勾選的那些）。
    """
    body = body or PreflightBody()
    data, err = _load_for_m3d(slug)
    if err:
        return err
    ids = [s.get("id") for s in (data.get("shots") or []) if isinstance(s, dict) and s.get("id")]
    return {"slug": slug, **_preflight_payload(data, ids, body.mode)}


@app.post("/api/projects/{slug}/generate/images")
def api_generate_images_batch(slug: str, body: BatchImageBody):
    """批次生圖：依序送件（不並行），每鏡完成即寫回；回 jobId，前端每 2 秒 poll 狀態。"""
    data, err = _load_for_m3d(slug)
    if err:
        return err
    if body.mode not in ("ref", "t2i"):
        return _fail(400, f"不支援的生成模式：{body.mode}（合法值：ref／t2i）")
    shot_ids = [x for x in (body.shotIds or []) if str(x or "").strip()]
    if not shot_ids:
        return _fail(400, "沒有選任何鏡頭：請先勾選鏡頭，或用「本場次」範圍")
    # 送件前先全部檢查：有 blocker 的鏡頭直接標「未送」（不硬跑），其餘照樣放量；
    # 全部都沒過才整批擋下（避免使用者按了卻什麼都沒發生、也避免白跑 GPU）。
    pre = _preflight_payload(data, shot_ids, body.mode)
    if not pre["ready"]:
        return JSONResponse(status_code=200, content={
            "ok": False, "slug": slug,
            "error": f"前置檢查沒過（{len(pre['blockers'])} 項），已擋下，沒有送 GPU：\n- " + "\n- ".join(pre["blockers"]),
            "blockers": pre["blockers"], "items": pre["items"],
        })
    skipped = [{"shotId": i["shotId"], "no": i.get("no"), "blockers": i["blockers"]}
               for i in pre["items"] if not i["ok"]]
    opts = {"mode": body.mode, "trialFirst": bool(body.trialFirst)}
    if body.engine:
        opts["engine"] = body.engine
    if body.resolution is not None:
        opts["resolution"] = body.resolution
    try:
        job = imagegen.start_batch(data, slug, pre["ready"], opts)
    except imagegen.BatchError as e:
        return JSONResponse(status_code=200, content={"ok": False, "slug": slug, "error": str(e)})
    job["skipped"] = skipped
    return {"ok": True, "slug": slug, "job": job, "skipped": skipped,
            "warnings": pre["blockers"],
            "pollUrl": f"/api/projects/{slug}/generate/images/{job['jobId']}"}


@app.get("/api/projects/{slug}/generate/images/latest")
def api_generate_images_latest(slug: str):
    """最近一個批次任務（前端換頁／F5 後接回進度用）。"""
    try:
        storage.load_project(slug)
    except StorageError as e:
        return _fail(404, str(e))
    return {"ok": True, "slug": slug, "job": imagegen.latest_batch(slug)}


@app.get("/api/projects/{slug}/generate/images/{job_id}")
def api_generate_images_status(slug: str, job_id: str):
    job = imagegen.get_batch(job_id)
    if not job or job.get("slug") != slug:
        return _fail(404, f"找不到批次任務 {job_id}（伺服器重啟過的話，進度會消失，圖仍在 media/shots/）")
    return {"ok": True, "slug": slug, "job": job}


# ── 第二層：H3 多角度設定表（角色立繪 → 多視角設定表，i2i）────────────
@app.post("/api/projects/{slug}/characters/{cid}/generate/sheet")
def api_generate_character_sheet_angles(slug: str, cid: str, body: Optional[SheetBody] = None):
    """第二層：把角色卡最新的 kind=full 立繪送進 H3 Character Sheet Designer。

    H3 image 很慢（21GB UNET + 25GB text encoder）→ 一律走背景 job，回 jobId + pollUrl，
    前端每 3 秒 poll；完成後圖寫回 characters[].refImages（原圖保留）。
    """
    body = body or SheetBody()
    data, err = _load_for_m3d(slug)
    if err:
        return err
    ch = imagegen.find_character(data, cid)
    if ch is None:
        return _fail(404, f"找不到人物 {cid}")
    ref = imagegen.pick_character_full_ref(data, ch)
    if ref is None:
        return JSONResponse(status_code=200, content={
            "ok": False, "slug": slug, "characterId": cid,
            "error": f"{cid}（{ch.get('name') or cid}）沒有可用的全身立繪（kind=full）："
                     "請先在角色卡按「生成立繪」產生第一層立繪（MOSS t2i），再生成多角度設定表",
        })
    opts = body.model_dump(exclude_none=True)
    try:
        job = imagegen.start_sheet_job(slug, cid, opts)
    except imagegen.BatchError as e:
        return JSONResponse(status_code=200, content={"ok": False, "slug": slug, "error": str(e)})
    return {"ok": True, "slug": slug, "characterId": cid, "job": job, "jobId": job["jobId"],
            "sourceRef": ref["path"],
            "pollUrl": f"/api/projects/{slug}/characters/{cid}/generate/sheet/{job['jobId']}"}


@app.get("/api/projects/{slug}/characters/{cid}/generate/sheet/{job_id}")
def api_generate_character_sheet_angles_status(slug: str, cid: str, job_id: str):
    job = imagegen.get_sheet_job(job_id)
    if not job or job.get("slug") != slug:
        return _fail(404, f"找不到多角度任務 {job_id}（伺服器重啟過的話進度會消失，圖仍在 media/characters/）")
    return {"ok": True, "slug": slug, "characterId": cid, "job": job}


@app.post("/api/projects/{slug}/validate")
def api_validate(slug: str, body: Optional[ValidateBody] = None):
    data, err = _load_for_m3d(slug)
    if err:
        return err
    try:
        result = h3compose.validate_project(data)
    except Exception as e:
        log.exception("validate 失敗")
        return _fail(400, f"校驗失敗：{type(e).__name__}: {e}")
    return {"ok": True, "slug": slug, **result}


# ── 一鍵佈署：故事 → 角色 / 場景 / 劇本 / 鏡頭（只建文字，圖片全空）─────
class DeployBody(BaseModel):
    """一鍵佈署參數。預設 dryRun=false（真的寫入）、overwrite=false（append 接續編號）。"""
    characterCount: int = 3
    sceneCount: int = 2
    shotCount: int = 6
    shotSeconds: float = 5
    overwrite: bool = False
    dryRun: bool = False


@app.get("/api/projects/{slug}/deploy/info")
def api_deploy_info(slug: str):
    """彈窗要先知道「已有多少內容」才能警告 append 會接續編號。"""
    data, err = _load_for_m3d(slug)
    if err:
        return err
    story = data.get("story") or {}
    return {"ok": True, "slug": slug, "existing": deploy.existing_counts(data),
            "hasStory": bool((story.get("synopsis") or "").strip() or (story.get("premise") or "").strip()),
            "storyHint": (story.get("synopsis") or story.get("premise") or "")[:120],
            "llm": _deploy_llm_info(data),
            "defaults": {"characterCount": 3, "sceneCount": 2, "shotCount": 6, "shotSeconds": 5},
            "latestJob": deploy.latest_job(slug)}


def _deploy_llm_info(data: dict) -> dict:
    """部署彈窗上方的模型來源提示：目前 provider（local/online）＋建議。"""
    cfg = dict((data.get("settings") or {}).get("llm") or {})
    try:
        from . import llm as _llm
        provider = str(_llm.resolve_config(cfg).get("provider") or "local")
    except Exception:  # noqa: BLE001 —— 缺 key 等情況也照實顯示 provider，不要擋彈窗
        provider = str(cfg.get("provider") or "local").strip().lower() or "local"
    return {"provider": provider,
            "providerLabel": "線上" if provider == "online" else ("自訂" if provider == "custom" else "本機"),
            "model": str(cfg.get("model") or ""),
            "recommendOnline": provider != "online"}


@app.post("/api/projects/{slug}/deploy")
def api_deploy(slug: str, body: Optional[DeployBody] = None):
    """一鍵佈署。4 次模型呼叫（1–3 分鐘）→ 一律背景 job，回 jobId + pollUrl。

    dryRun=true：同步跑完四步但**不寫檔**，回 {preview, warnings}（給「先預覽」用）。
    """
    b = body or DeployBody()
    opts = {"characterCount": b.characterCount, "sceneCount": b.sceneCount, "shotCount": b.shotCount,
            "shotSeconds": b.shotSeconds, "overwrite": bool(b.overwrite), "dryRun": bool(b.dryRun)}
    try:
        storage.load_project(slug)
    except StorageError as e:
        return _fail(404, str(e))
    try:
        if opts["dryRun"]:
            t0 = time.time()
            res = deploy.preview(slug, opts)
            return {"ok": True, "slug": slug, "dryRun": True, "preview": res["preview"],
                    "warnings": res["warnings"], "warningsByStep": res["warningsByStep"],
                    "timings": res["timings"], "params": res["params"], "existing": res["existing"],
                    "elapsed": round(time.time() - t0, 1)}
        job = deploy.start(slug, opts)
    except Exception as e:  # noqa: BLE001 —— 一律中文 JSON
        return _gen_error(e, storage.llm_settings(slug))
    return {"ok": True, "slug": slug, "jobId": job["jobId"], "job": job,
            "pollUrl": f"/api/projects/{slug}/deploy/{job['jobId']}",
            "steps": list(deploy.STEPS)}


@app.get("/api/projects/{slug}/deploy/latest")
def api_deploy_latest(slug: str):
    try:
        storage.load_project(slug)
    except StorageError as e:
        return _fail(404, str(e))
    return {"ok": True, "slug": slug, "job": deploy.latest_job(slug)}


@app.get("/api/projects/{slug}/deploy/{job_id}")
def api_deploy_status(slug: str, job_id: str):
    job = deploy.get_job(job_id)
    if not job or job.get("slug") != slug:
        return _fail(404, f"找不到佈署任務 {job_id}（伺服器重啟過的話進度會消失，已寫入的資料不受影響）")
    return {"ok": True, "slug": slug, "status": job.get("status"), "step": job.get("step"),
            "progress": job.get("progress"), "result": job.get("result"), "warnings": job.get("warnings"),
            "warningsByStep": job.get("warningsByStep"), "timings": job.get("timings"),
            "error": job.get("error"), "failedStep": job.get("failedStep"), "job": job}


@app.post("/api/projects/{slug}/deploy/{job_id}/cancel")
def api_deploy_cancel(slug: str, job_id: str):
    job = deploy.cancel(job_id)
    if not job or job.get("slug") != slug:
        return _fail(404, f"找不到佈署任務 {job_id}")
    return {"ok": True, "slug": slug, "job": job}


class VideoSettingsBody(BaseModel):
    """settings.video（M9）：lora{name,strength,active} ＋ steps/cfg/shiftVideo/shiftAudio/sampler/scheduler。"""
    lora: Optional[dict] = None
    steps: Optional[int] = None
    cfg: Optional[float] = None
    shiftVideo: Optional[float] = None
    shiftAudio: Optional[float] = None
    sampler: Optional[str] = None
    scheduler: Optional[str] = None


@app.get("/api/projects/{slug}/video/settings")
def api_get_video_settings(slug: str):
    """影片生成參數（settings.video，缺的補預設）＋可選 LoRA 清單。"""
    try:
        data = storage.load_project(slug)
    except StorageError as e:
        return _fail(404, str(e))
    return {"ok": True, "slug": slug, "settings": storage.video_settings(data),
            "defaults": storage.video_settings_defaults(),
            "loraChoices": [{"name": n, "label": lb} for n, lb in h3gen.LORA_CHOICES]}


@app.put("/api/projects/{slug}/video/settings")
def api_put_video_settings(slug: str, body: Optional[VideoSettingsBody] = None):
    """存 settings.video（只寫有給的欄位；lora.name 空字串＝不掛）。"""
    try:
        data = storage.load_project(slug)
    except StorageError as e:
        return _fail(404, str(e))
    body = body or VideoSettingsBody()
    cur = storage.video_settings(data)
    got = body.model_dump(exclude_none=True)
    for k in ("steps", "cfg", "shiftVideo", "shiftAudio", "sampler", "scheduler"):
        if k in got:
            cur[k] = got[k]
    if "lora" in got:
        lora = got["lora"] if isinstance(got["lora"], dict) else {}
        name = str(lora.get("name") or "").strip()
        if not name:
            cur["lora"] = {"name": "", "strength": 1.0, "active": False}
        else:
            try:
                strength = float(lora.get("strength", cur["lora"].get("strength", 1.0)))
            except (TypeError, ValueError):
                strength = 1.0
            cur["lora"] = {"name": name, "strength": max(0.0, min(1.5, strength)),
                           "active": bool(lora.get("active", True))}
    data.setdefault("settings", {})["video"] = cur
    try:
        storage.save_project(slug, data)
    except StorageError as e:
        return _fail(400, str(e))
    return {"ok": True, "slug": slug, "settings": cur}


# ── M5a：單鏡生影片（導演台 r2v）──────────────────────────────────
class GenerateVideoBody(BaseModel):
    """單鏡生影片參數。全部可留白：預設 480x864（9:16 低清）、39 幀（1.625 秒）、steps=4。

    useFirstFrame=False 時，即使角色槽位都是占位圖也不會拿首幀當 <Picture 1>（純參考圖模式）。
    """
    width: Optional[int] = None
    height: Optional[int] = None
    frames: Optional[int] = None
    steps: Optional[int] = None
    cfg: Optional[float] = None
    seed: Optional[int] = None
    timeout: Optional[int] = None
    useFirstFrame: bool = True
    # M9：影片 LoRA 與採樣參數（留空＝用 settings.video）。lora 給 {"name","strength","active"}，
    # name 空字串＝這一鏡不掛；不傳 lora 欄位＝照專案設定。
    lora: Optional[dict] = None
    shiftVideo: Optional[float] = None
    shiftAudio: Optional[float] = None
    sampler: Optional[str] = None
    scheduler: Optional[str] = None


@app.post("/api/projects/{slug}/shots/{shot_id}/generate/video")
def api_generate_shot_video(slug: str, shot_id: str, body: Optional[GenerateVideoBody] = None):
    """背景生影片：H3 r2v 要數十秒到數分鐘，不可同步卡住 HTTP。

    立刻回 jobId + pollUrl，前端每 1~2 秒打 pollUrl 看進度。失敗一律 200 + ok:false。
    """
    body = body or GenerateVideoBody()
    data, err = _load_for_m3d(slug)
    if err:
        return err
    if _get_shot(data, shot_id) is None:
        return _fail(404, f"找不到鏡頭 {shot_id}")
    opts = body.model_dump(exclude_none=True)
    try:
        job = h3gen.start_job(data, slug, shot_id, opts)
    except h3gen.H3JobError as e:
        return JSONResponse(status_code=200, content={"ok": False, "slug": slug,
                                                      "error": str(e), "shotId": shot_id})
    except h3gen.H3GenError as e:
        return JSONResponse(status_code=200, content={"ok": False, "slug": slug,
                                                      "error": str(e), "shotId": shot_id})
    return {"ok": True, "slug": slug, "shotId": shot_id, "jobId": job["jobId"],
            "job": job, "pollUrl": f"/api/projects/{slug}/generate/video/{job['jobId']}"}


@app.get("/api/projects/{slug}/generate/video/latest")
def api_generate_video_latest(slug: str):
    """最近一個影片任務（前端 F5 後接回進度用）。"""
    try:
        storage.load_project(slug)
    except StorageError as e:
        return _fail(404, str(e))
    return {"ok": True, "slug": slug, "job": h3gen.latest_job(slug)}


@app.get("/api/projects/{slug}/generate/video/{job_id}")
def api_generate_video_status(slug: str, job_id: str):
    """任務狀態：status/phase/elapsed/pid/position/path/url/error（error 已截斷 500 字）。"""
    job = h3gen.get_job(job_id)
    if not job or job.get("slug") != slug:
        return _fail(404, f"找不到影片任務 {job_id}（伺服器重啟過的話進度會消失，已完成的影片仍在 media/clips/）")
    return {"ok": True, "slug": slug, "job": job}


# ── M5b：多鏡批次生影片 ───────────────────────────────────────────
class VideoPresetBody(BaseModel):
    """批次參數。全部可留白：預設 720x1280／121 幀（5 秒）／steps 12。"""
    width: Optional[int] = None
    height: Optional[int] = None
    frames: Optional[int] = None
    steps: Optional[int] = None
    cfg: Optional[float] = None
    seed: Optional[int] = None
    timeout: Optional[int] = None
    lora: Optional[dict] = None
    shiftVideo: Optional[float] = None
    shiftAudio: Optional[float] = None
    sampler: Optional[str] = None
    scheduler: Optional[str] = None


class GenerateVideosBody(BaseModel):
    """scope：'all'（本專案全部鏡頭）／'scene:<episodeSceneId>'；或直接給 shotIds（優先）。"""
    shotIds: Optional[list[str]] = None
    scope: Optional[str] = None
    preset: Optional[VideoPresetBody] = None
    trialCount: Optional[int] = None      # 先試 N 鏡（預設 1）；0＝全部照排
    useFirstFrame: bool = True


def _shots_for_scope(data: dict, body: GenerateVideosBody) -> tuple[list[str], str]:
    """把 shotIds／scope 解析成鏡頭 id 清單。回 (ids, 範圍說明)。"""
    ids = [str(x).strip() for x in (body.shotIds or []) if str(x or "").strip()]
    if ids:
        return ids, f"指定 {len(ids)} 鏡"
    scope = (body.scope or "").strip()
    shots = [s for s in (data.get("shots") or []) if isinstance(s, dict)]
    if scope.startswith("scene:"):
        es_id = scope.split(":", 1)[1].strip()
        picked = [s for s in shots if (s.get("episodeSceneId") or "") == es_id]
        return [s.get("id") for s in picked if s.get("id")], f"場次 {es_id}（{len(picked)} 鏡）"
    if scope in ("", "all"):
        return [s.get("id") for s in shots if s.get("id")], f"本專案全部（{len(shots)} 鏡）"
    raise h3gen.H3JobError(f"不支援的範圍：{scope}（合法值：all／scene:<場次 id>，或直接給 shotIds）")


@app.post("/api/projects/{slug}/generate/videos")
def api_generate_videos_batch(slug: str, body: Optional[GenerateVideosBody] = None):
    """批次生影片：背景依序跑（不並行），每鏡完成即寫回、失敗不中斷；回 jobId + pollUrl。

    前置檢查沒過的鏡頭會標 blocked（不送 GPU），其餘照跑；全都被擋才整批不啟動。
    """
    body = body or GenerateVideosBody()
    data, err = _load_for_m3d(slug)
    if err:
        return err
    try:
        ids, scope_label = _shots_for_scope(data, body)
    except h3gen.H3JobError as e:
        return JSONResponse(status_code=200, content={"ok": False, "slug": slug, "error": str(e)})
    if not ids:
        return JSONResponse(status_code=200, content={
            "ok": False, "slug": slug,
            "error": "這個範圍裡沒有鏡頭：請先建立鏡頭，或改用「本專案全部」以外的範圍"})
    preset = body.preset.model_dump(exclude_none=True) if body.preset else {}
    opts = {"preset": preset, "trialCount": body.trialCount if body.trialCount is not None else 1,
            "useFirstFrame": bool(body.useFirstFrame)}
    try:
        job = h3gen.start_video_batch(data, slug, ids, opts)
    except h3gen.H3JobError as e:
        return JSONResponse(status_code=200, content={"ok": False, "slug": slug, "error": str(e)})
    except h3gen.H3GenError as e:
        return JSONResponse(status_code=200, content={"ok": False, "slug": slug, "error": str(e)})
    if job.get("runnable", 0) == 0:
        return JSONResponse(status_code=200, content={
            "ok": False, "slug": slug, "scope": scope_label, "job": job,
            "error": f"前置檢查全部沒過（{len(job.get('preblocked') or [])} 鏡，範圍：{scope_label}），沒有送 GPU：\n- "
                     + "\n- ".join(f"{p.get('shotId')}：{p.get('error')}" for p in job.get("preblocked") or []),
            "preblocked": job.get("preblocked") or [],
        })
    return {"ok": True, "slug": slug, "scope": scope_label, "jobId": job["jobId"], "job": job,
            "preblocked": job.get("preblocked") or [],
            "pollUrl": f"/api/projects/{slug}/generate/videos/{job['jobId']}"}


@app.get("/api/projects/{slug}/generate/videos/latest")
def api_generate_videos_latest(slug: str):
    """最近一個批次影片任務（換頁／F5 後接回進度用）。"""
    try:
        storage.load_project(slug)
    except StorageError as e:
        return _fail(404, str(e))
    return {"ok": True, "slug": slug, "job": h3gen.latest_video_batch(slug)}


@app.get("/api/projects/{slug}/generate/videos/{job_id}")
def api_generate_videos_status(slug: str, job_id: str):
    """逐鏡狀態／耗時／佇列位置／錯誤。"""
    job = h3gen.get_video_batch(job_id)
    if not job or job.get("slug") != slug:
        return _fail(404, f"找不到批次影片任務 {job_id}（伺服器重啟過的話進度會消失，"
                          f"已完成的影片仍在 media/clips/）")
    return {"ok": True, "slug": slug, "job": job}


@app.post("/api/projects/{slug}/generate/videos/{job_id}/cancel")
def api_cancel_videos_batch(slug: str, job_id: str):
    """取消（未開始的鏡頭標「已取消」，正在跑的那鏡會跑完）。"""
    job = h3gen.get_video_batch(job_id)
    if not job or job.get("slug") != slug:
        return _fail(404, f"找不到批次影片任務 {job_id}")
    return {"ok": True, "slug": slug, "job": h3gen.cancel_video_batch(job_id)}


# ── M6：合併成長片 ────────────────────────────────────────────────
class AssembleBody(BaseModel):
    """mode：copy＝直接串（要求各段參數一致）／reencode＝統一重編碼／auto＝能 copy 就 copy。

    skipMissing：有鏡頭還沒生影片時，true＝略過它們照合併，false＝中止並列出缺片。
    """
    mode: str = "auto"
    skipMissing: bool = False


@app.get("/api/projects/{slug}/assemble/plan")
def api_assemble_plan(slug: str, skipMissing: bool = Query(False)):
    """合併前檢查（不執行 ffmpeg）：缺哪些片、參數一致不一致、建議模式、總時長。"""
    data, err = _load_for_m3d(slug)
    if err:
        return err
    try:
        return {"ok": True, "slug": slug, **asm.plan(data, skip_missing=skipMissing)}
    except asm.AssembleError as e:
        return JSONResponse(status_code=200, content={"ok": False, "slug": slug, "error": str(e)})


@app.post("/api/projects/{slug}/assemble")
def api_assemble(slug: str, body: Optional[AssembleBody] = None):
    """同步合併（幾十秒內）。成功回 final 路徑／時長／大小／幀數；失敗回中文 error。"""
    body = body or AssembleBody()
    data, err = _load_for_m3d(slug)
    if err:
        return err
    try:
        res = asm.assemble(data, slug, {"mode": body.mode, "skipMissing": bool(body.skipMissing)})
    except asm.AssembleError as e:
        return JSONResponse(status_code=200, content={"ok": False, "slug": slug, "error": str(e)})
    except Exception as e:  # noqa: BLE001
        log.exception("合併失敗")
        return JSONResponse(status_code=200, content={
            "ok": False, "slug": slug, "error": f"合併失敗（未預期錯誤）：{type(e).__name__}: {e}"})
    res["url"] = f"/files/{slug}/{res['relPath']}"
    return {"ok": True, "slug": slug, **res}


@app.get("/api/projects/{slug}/outputs")
def api_outputs(slug: str):
    """已合併過的成品清單（output/*.mp4，新到舊）。"""
    try:
        pdir = storage.project_dir(slug)
    except StorageError as e:
        return _fail(404, str(e))
    if not pdir.is_dir():
        return _fail(404, f"找不到專案：{slug}")
    outdir = pdir / "output"
    items = []
    if outdir.is_dir():
        for f in sorted(outdir.glob("final_*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
            st = f.stat()
            items.append({"path": f"output/{f.name}", "url": f"/files/{slug}/output/{f.name}",
                          "bytes": st.st_size,
                          "mtime": storage.datetime.fromtimestamp(st.st_mtime, storage.ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")})
    return {"ok": True, "slug": slug, "items": items}

# ── GPU 資源切換（app/gpu.py：VRAM 量測、釋放 ComfyUI、啟停本機 LLM）────
class GpuStartBody(BaseModel):
    model: Optional[str] = None   # gguf 檔名（不含目錄）；空＝後端預設（與桌面 bat 一致）


def _gpu_fail(e: gpu.GpuError):
    return JSONResponse(status_code=e.status, content={"ok": False, "error": str(e)})


@app.get("/api/gpu/status")
def api_gpu_status():
    return {"ok": True, **gpu.status()}


@app.post("/api/gpu/release-comfy")
def api_gpu_release_comfy():
    try:
        gpu.clear_probe_cache()
        res = gpu.release_comfy()
    except gpu.GpuError as e:
        return _gpu_fail(e)
    return {"ok": True, **res}


@app.post("/api/gpu/stop-llama")
def api_gpu_stop_llama():
    try:
        gpu.clear_probe_cache()
        res = gpu.stop_llama()
    except gpu.GpuError as e:
        return _gpu_fail(e)
    return {"ok": True, **res}


@app.post("/api/gpu/start-llama")
def api_gpu_start_llama(body: Optional[GpuStartBody] = None):
    try:
        gpu.clear_probe_cache()
        res = gpu.start_llama(body.model if body else None)
    except gpu.GpuError as e:
        return _gpu_fail(e)
    return {"ok": True, **res}


class GpuSettingsBody(BaseModel):
    """settings.gpu（目前只有 autoSwitch）。None＝不改。"""
    autoSwitch: Optional[bool] = None


@app.get("/api/gpu/settings")
def api_gpu_settings():
    return {"ok": True, "autoSwitch": gpu.auto_switch_enabled(),
            "default": gpu.AUTO_SWITCH_DEFAULT}


@app.put("/api/gpu/settings")
def api_put_gpu_settings(body: GpuSettingsBody):
    try:
        if body.autoSwitch is not None:
            res = gpu.set_auto_switch(body.autoSwitch)
        else:
            res = {"autoSwitch": gpu.auto_switch_enabled()}
    except gpu.GpuError as e:
        return _gpu_fail(e)
    return {"ok": True, **res,
            "message": ("已開啟：生圖／生影片前會自動停止本機 LLM，完成後自動啟回。"
                        if res["autoSwitch"] else
                        "已關閉：生圖／生影片不再自動切換，需要時請自己按「停止本機 LLM」。")}


# ── 靜態掛載（順序：API 先註冊，靜態最後掛 /）────────────────────
class NoCacheStaticFiles(StaticFiles):
    """開發用：靜態檔一律 no-store，改 JS/CSS 後 F5 立即生效（不必重啟 uvicorn）。

    只加在掛 / 的那一支；/files 的媒體檔仍可正常快取（圖片很大，重抓很浪費）。
    """

    def file_response(self, *args, **kwargs):  # starlette >=0.30 走這條
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        return response

    async def get_response(self, path: str, scope):  # 保險：所有回應（含 404、index）都帶標頭
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        return response


PROJECTS_DIR = storage.PROJECTS_DIR
STATIC_DIR = paths.STATIC_DIR
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=str(PROJECTS_DIR)), name="files")
app.mount("/", NoCacheStaticFiles(directory=str(STATIC_DIR), html=True), name="static")


@app.on_event("startup")
def _startup() -> None:
    # 埠可能是 launcher 動態挑的（8788→8799），用環境變數 SVS_PORT 回報實際埠
    _port = os.environ.get("SVS_PORT") or "8788"
    _msg = f"Story Video Studio 已啟動 http://127.0.0.1:{_port}"
    if sys.stdout is not None:  # --noconsole 下 stdout 是 None，print(flush=True) 會拋錯
        try:
            print(_msg, flush=True)
        except Exception:
            pass
    log.info("%s", _msg)
