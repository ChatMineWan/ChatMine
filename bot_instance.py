"""
单个 AI 实例 — 独立端口、独立记忆、独立人格
"""
import os
import sys
import json
import sqlite3
import asyncio
import hashlib
import datetime
import random
import re
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
import uvicorn


class AIInstance:
    """一个 AI 聊天实例"""

    def __init__(self, instance_id: str, port: int, persona_prompt: str, context: dict = None):
        self.instance_id = instance_id
        self.port = port
        self.persona_prompt = persona_prompt
        self.context = context or {}
        self.name = self.context.get("name", "AI")
        self.db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instances", instance_id, "memory.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL, content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.commit()

    def add_memory(self, role: str, content: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO messages (role, content) VALUES (?, ?)", (role, content))
            conn.commit()
            # Trim old messages
            count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            if count > 300:
                conn.execute("DELETE FROM messages WHERE id NOT IN (SELECT id FROM messages ORDER BY id DESC LIMIT 200)")
                conn.commit()

    def get_history(self, limit: int = 30) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    def clear_memory(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM messages")
            conn.commit()

    def get_stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        return {"instance_id": self.instance_id, "name": self.name, "port": self.port, "total_msgs": total}


def create_bot_app(instance: AIInstance) -> FastAPI:
    """为一个 AI 实例创建 FastAPI 应用"""
    app = FastAPI(title=instance.name)

    # API Key 从环境变量
    DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    @app.get("/")
    async def chat_page():
        return HTMLResponse(CHAT_HTML.replace("{{NAME}}", instance.name).replace("{{PORT}}", str(instance.port)))

    @app.post("/api/chat")
    async def chat(request: Request):
        try:
            body = await request.json()
            user_msg = body.get("message", "").strip()
            if not user_msg:
                return JSONResponse({"reply": "说点什么呗"}, status_code=400)
        except:
            return JSONResponse({"reply": "消息格式不对"}, status_code=400)

        history = instance.get_history(30)
        reply = await _call_deepseek(instance.persona_prompt, history, user_msg, DEEPSEEK_KEY, DEEPSEEK_MODEL)
        instance.add_memory("user", user_msg)
        instance.add_memory("assistant", reply)
        return JSONResponse({"reply": reply})

    @app.post("/api/clear")
    async def clear():
        instance.clear_memory()
        return JSONResponse({"ok": True})

    @app.get("/api/stats")
    async def stats():
        return JSONResponse(instance.get_stats())

    return app


async def _call_deepseek(system_prompt: str, history: list[dict], user_msg: str, api_key: str, model: str) -> str:
    if not api_key:
        return "唔… API Key 没配，说不了话"

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_msg})

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "max_tokens": 400, "temperature": 0.85, "messages": messages},
            )
            data = resp.json()
            if "choices" in data and data["choices"]:
                return data["choices"][0]["message"]["content"].strip()
            return "脑子卡了 再说一遍呗"
    except Exception as e:
        return f"出错了 {str(e)[:30]}"


def run_instance(instance: AIInstance):
    """启动一个 AI 实例"""
    app = create_bot_app(instance)
    uvicorn.run(app, host="0.0.0.0", port=instance.port, log_level="warning")


# 网页聊天界面
CHAT_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{{NAME}} - AI Chat</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,Microsoft YaHei,sans-serif;background:#111;color:#eee;height:100vh;display:flex;flex-direction:column}
.header{background:#1a1a2e;padding:12px 20px;text-align:center;font-size:18px;border-bottom:1px solid #333}
.messages{flex:1;overflow-y:auto;padding:16px}
.msg{margin-bottom:14px;max-width:85%;animation:fadein .3s}
.msg.user{margin-left:auto;text-align:right}
.msg.user .bubble{background:#2d5a3d;border-radius:16px 4px 16px 16px}
.msg.assistant .bubble{background:#2a2a3e;border-radius:4px 16px 16px 16px}
.bubble{padding:10px 14px;display:inline-block;line-height:1.6;font-size:15px;word-break:break-word}
.time{font-size:11px;color:#666;margin-top:3px}
.input-area{display:flex;padding:12px;gap:8px;background:#1a1a2e;border-top:1px solid #333}
.input-area input{flex:1;padding:12px;border:none;border-radius:20px;background:#2a2a3e;color:#eee;font-size:15px;outline:none}
.input-area button{padding:10px 22px;border:none;border-radius:20px;background:#2d5a3d;color:#eee;font-size:15px;cursor:pointer}
.input-area button:hover{background:#3a7048}
@keyframes fadein{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
</style>
</head>
<body>
<div class="header">{{NAME}}</div>
<div class="messages" id="msgs"></div>
<div class="input-area">
  <input id="inp" placeholder="输入消息..." autofocus onkeydown="if(event.key==='Enter')send()">
  <button onclick="send()">发送</button>
</div>
<script>
async function send(){
  const inp=document.getElementById('inp'),msg=inp.value.trim();
  if(!msg)return;
  addMsg('user',msg);inp.value='';inp.focus();
  const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg})});
  const d=await r.json();addMsg('assistant',d.reply);
}
function addMsg(role,text){
  const d=document.getElementById('msgs');
  const el=document.createElement('div');el.className='msg '+role;
  el.innerHTML=`<div class="bubble">${text.replace(/\\n/g,'<br>')}</div><div class="time">${new Date().toLocaleTimeString()}</div>`;
  d.appendChild(el);d.scrollTop=d.scrollHeight;
}
</script>
</body>
</html>"""
