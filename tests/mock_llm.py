"""開發用：30 秒的假 OpenAI 相容伺服器（驗證 M3a 成功路徑用，不是成果的一部分）。

- GET  /v1/models            → {"data":[{"id":"mock-strata-7b"},{"id":"mock-strata-70b"}]}
- POST /v1/chat/completions  → 依 system prompt 內的【本次任務】回不同樣板文字，
                               並把 system 長度放進 usage，方便確認後端真的有組 prompt。
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

MODELS = ["mock-strata-7b", "mock-strata-70b"]

_TASK_MARK = {
    "story_outline": "擴寫成完整故事大綱",
    "character_look": "依姓名與定位生成人物外型",
    "scene_expand": "擴寫場景描述",
}

REPLIES = {
    "story_outline": (
        "霧季第三天的清晨，小紅帽提著裝滿麵包的籃子，離開村口唯一一條通往森林的路。"
        "她從小就聽大人說，霧裡住著「替人保管秘密」的東西——只要說出一個真心話，牠就替你記住，"
        "代價是那句話會從你腦海裡消失。\n"
        "奶奶的木屋在森林深處，病了三週。小紅帽要送的是麵包，也是村裡人交代她「順便問奶奶當年為什麼搬進森林」。\n"
        "第四天，她在霧裡遇到一頭灰狼。灰狼不怕她，反而叫她「阿紅」——那是只有奶奶才會叫的小名。"
        "灰狼說牠保管著奶奶的秘密，只要小紅帽說出自己最怕的事，牠就把秘密還給奶奶。\n"
        "她選擇說出恐懼，換回奶奶的名字。奶奶醒來，卻不認得她。"
        "原來秘密被取走的人，會忘記自己為何要記得任何事。\n"
        "小紅帽回到霧裡，這次不說話，只是坐下來，和那頭灰狼一起等霧散。結尾，霧第一次真的散了。\n"
        "主題：記住的代價，與陪伴勝過交換。"
    ),
    "character_look": (
        "五官清秀，鼻樑略挺、鼻尖微翹，眼睛偏大且瞳色淺褐，左眉尾有一道細疤。"
        "黑色及腰直髮、齊眉瀏海，常綁成低馬尾。身形纖瘦、肩膀窄，行走時略微前傾。"
        "辨識特徵：永遠披著一件洗到褪色的紅斗篷，斗篷內側縫著奶奶的手繡線。\n"
        "promptEn: 17-year-old girl, East Asian, fair skin with light freckles on cheeks, "
        "large pale-brown eyes, slightly upturned nose, thin scar at the end of left eyebrow, "
        "long straight black hair with blunt bangs tied in a low ponytail, slim narrow-shouldered build, "
        "wearing a faded crimson hooded cloak with hand-embroidered lining, soft overcast forest light, cinematic portrait"
    ),
    "scene_expand": (
        "清晨六點的霧中森林，霧貼著地面漫過蕨類與倒木，能見度不到五步。"
        "光從樹冠縫隙斜斜落下，成為一條條淡金色的光柱，落在長滿青苔的樹根上。"
        "空氣濕冷，露水順著松針滴落，腳下是鬆軟的腐葉層與碎枝。"
        "遠處有溪水的低鳴與啄木鳥的敲擊，偶爾一聲枯枝斷裂，安靜得讓那聲音顯得更近。"
    ),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in MODELS]})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        sys_prompt = ""
        for m in body.get("messages", []):
            if m.get("role") == "system":
                sys_prompt = m.get("content") or ""
        task = ""
        for key in REPLIES:
            if _TASK_MARK[key] in sys_prompt:
                task = key
                break
        reply = REPLIES.get(task) or (
            f"（mock 回覆）收到 {len(body.get('messages', []))} 則訊息，system 長度 {len(sys_prompt)} 字。"
            "這台是 8999 的假伺服器，只驗證成功路徑。"
        )
        self._json(200, {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "model": body.get("model") or MODELS[0],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": len(sys_prompt),
                "completion_tokens": len(reply),
                "total_tokens": len(sys_prompt) + len(reply),
                "mock_task": task or "generic",
            },
        })


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8999
    srv = HTTPServer(("127.0.0.1", port), Handler)
    print(f"mock OpenAI server on http://127.0.0.1:{port}/v1", flush=True)
    srv.timeout = 30
    srv.handle_request() if False else srv.serve_forever()
