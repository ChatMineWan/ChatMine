"""
ChatMine — 微信聊天记录一键生成 AI（纯线程版）
"""
import os, sys, json, threading, datetime, re, time, sqlite3, asyncio, traceback
from dotenv import load_dotenv; load_dotenv()
import customtkinter as ctk
from tkinter import messagebox, filedialog, StringVar
import httpx

from wechat_miner import import_json
from personality_miner import mine_personality, generate_prompt

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("green")

# ── 调试日志（写入文件） ──
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(_BASE_DIR, "debug.log")
def _log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    except: pass
_log("===== App started =====")
_log(f"frozen={getattr(sys, 'frozen', False)}, base_dir={_BASE_DIR}")

# ── 微信风格配色 ──
BG_APP      = "#f5f5f5"
BG_SIDEBAR  = "#e8e8e8"
BG_CHAT     = "#ededed"
BG_MY_MSG   = "#95ec69"
BG_HER_MSG  = "#ffffff"
TEXT_DARK   = "#1a1a1a"
TEXT_GRAY   = "#999999"
TEXT_TIME   = "#b0b0b0"
SEP_COLOR   = "#d9d9d9"
HOVER_BG    = "#dedede"
ACTIVE_BG   = "#d2d2d2"
INPUT_BG    = "#ffffff"

# ── 字体 ──
FONT_FAMILY = "Microsoft YaHei"

def _f(size, weight="normal"):
    """创建字体，带 fallback"""
    try:
        return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)
    except:
        return ctk.CTkFont(size=size, weight=weight)


# ============================================================
# 内存存储
# ============================================================
class MemStore:
    def __init__(self, path="memory.json"):
        self.path = path
        self.data = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                _log(f"MemStore._load: {self.path} keys={list(self.data.keys())}, total_msgs={sum(len(v) for v in self.data.values())}")
            else:
                _log(f"MemStore._load: {self.path} not found")
        except Exception as e:
            _log(f"MemStore._load: failed {e}")
            self.data = {}

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
        except Exception as e:
            print(f"[MemStore save error] {e}")

    def add(self, uid, role, content):
        if uid not in self.data:
            self.data[uid] = []
        self.data[uid].append({"role": role, "content": content, "time": datetime.datetime.now().strftime("%H:%M")})
        if len(self.data[uid]) > 200:
            self.data[uid] = self.data[uid][-200:]
        self._save()
        print(f"[MemStore] saved {uid}: {len(self.data[uid])} msgs → {self.path}")

    def get(self, uid, limit=30):
        return self.data.get(uid, [])[-limit:]

    def clear(self, uid):
        self.data[uid] = []
        self._save()

    def count(self, uid):
        return len(self.data.get(uid, []))


# ============================================================
# AI 实例
# ============================================================
class AIBuddy:
    def __init__(self, name, persona_prompt, memory_store, api_key="", model="deepseek-chat",
                 vision_key="", vision_model="doubao-seed-1-8-251228"):
        self.name = name; self.persona = persona_prompt; self.mem = memory_store
        self.uid = name; self.api_key = api_key; self.model = model
        self.vkey = vision_key; self.vmodel = vision_model
        self.verbose = False
        self.description = ""

    def reply(self, msg):
        try:
            return asyncio.run(self._call(msg))
        except Exception as e:
            return f"出错了: {e}"

    async def _call(self, msg):
        cmd = self._handle_cmd(msg)
        if cmd: return cmd

        need_search = self._needs_search(msg)
        want_img = any(w in msg for w in ["图片","照片","发图","发张","找张","找图","表情包","截图","搜图","壁纸","样子","长什么样"])
        recent = self.mem.get(self.uid, 20)
        if self.vkey and not need_search and recent:
            need_search = await self._check_search(recent, msg)

        history = self.mem.get(self.uid, 30)
        messages = [{"role": "system", "content": self.persona}]
        messages.extend(history)

        max_tok = 600 if self.verbose else 400

        # 要图片 → 用豆包 Seedance 直接搜
        if need_search and want_img and self.vkey:
            cnt = self._img_count(msg)
            img_sys = self.persona + (
                f"\n用户要图片！你只需用文字简单描述图片内容即可，不要给任何图片URL。最多描述{cnt}张。"
                "系统会自动配图，你不用管。"
            )
            img_msgs = [{"role": "system", "content": img_sys}]
            img_msgs.extend(history)
            img_msgs.append({"role": "user", "content": msg})
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(
                    f"https://ark.cn-beijing.volces.com/api/v3/chat/completions",
                    headers={"Authorization": f"Bearer {self.vkey}", "Content-Type": "application/json"},
                    json={"model": self.vmodel, "max_tokens": max_tok, "messages": img_msgs})
                d = r.json()
                if "choices" in d and d["choices"]:
                    reply = d["choices"][0]["message"]["content"].strip()
                    # 删除不可靠的 Wikipedia / Wikimedia URL
                    reply = re.sub(r'https?://[^\s]*?wikimedia[^\s]*', '', reply, flags=re.I)
                    reply = re.sub(r'https?://[^\s]*?wikipedia[^\s]*', '', reply, flags=re.I)
                    reply = re.sub(r'\s+', ' ', reply).strip()
                    # 搜狗图片兜底
                    fb_urls = self._fallback_image_search(msg)
                    if fb_urls:
                        img_tags = " ".join(f"[IMG]{u}[/IMG]" for u in fb_urls[:self._img_count(msg)])
                        reply = reply + "\n" + img_tags if reply else img_tags
                    self.mem.add(self.uid, "assistant", reply)
                    return reply

        # 要搜索但不要图 → DeepSeek + 搜索结果注入
        if need_search and self.vkey and not want_img:
            sr = await self._web_search(msg)
            if sr and "失败" not in sr:
                messages.append({"role": "user", "content": f"联网搜索结果：{sr}\n\n用户的问题：{msg}\n用你的口吻简短回答。"})
            else:
                messages.append({"role": "user", "content": msg})
        else:
            messages.append({"role": "user", "content": msg})

        # DeepSeek 回复
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"model": self.model, "max_tokens": max_tok, "temperature": 0.85, "messages": messages})
            d = r.json()
            if "choices" in d and d["choices"]:
                reply = d["choices"][0]["message"]["content"].strip()
                self.mem.add(self.uid, "assistant", reply)
                return reply
        return "脑子卡了…再说一遍？"

    async def _check_search(self, recent, msg):
        """让模型判断当前对话是否需要联网搜索"""
        ctx = "\n".join(f"{m['role']}: {m['content'][:60]}" for m in recent[-10:])
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={"model": self.model, "max_tokens": 10, "temperature": 0,
                          "messages": [
                              {"role": "system", "content": "判断用户是否需要联网搜索得到实时信息。只看是否需要，不要想太多。只需回复 YES 或 NO。"},
                              {"role": "user", "content": f"最近对话：\n{ctx}\n\n用户最新消息：{msg}\n\n需要联网搜索吗？YES/NO"}
                          ]})
                d = r.json()
                if "choices" in d and d["choices"]:
                    return "YES" in d["choices"][0]["message"]["content"].upper()
        except:
            pass
        return False

    def _handle_cmd(self, msg):
        if not msg.startswith("/"): return None
        parts = msg.split(); cmd = parts[0].lower()
        if cmd in ("/帮助", "/help", "/0"):
            return "指令：/搜索 xxx | /图片 xxx | /多说 | /简短 | /状态 | /重置 | /0"
        if cmd in ("/状态", "/status"):
            return f"{self.name} | 记忆{self.mem.count(self.uid)}条 | {self.model}"
        if cmd in ("/重置", "/reset"): self.mem.clear(self.uid); return "忘了。"
        if cmd in ("/多说",): self.verbose = True; return "好，多说点"
        if cmd in ("/简短",): self.verbose = False; return "OK"
        if cmd in ("/搜索", "/图片", "/图"):
            return None
        return None

    @staticmethod
    def _img_count(msg):
        """从用户消息中解析期望图片数量：发一张→1，发两张→2，发几张→3，默认3"""
        m = re.search(r'([一二三四五六七八九十百千万两几]|[0-9]+)\s*[张张个幅]', msg)
        if m:
            num_map = {'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,'几':3}
            raw = m.group(1)
            if raw.isdigit():
                return max(1, min(10, int(raw)))
            return num_map.get(raw, 3)
        return 3

    def _needs_search(self, msg):
        if msg.startswith(("/搜索", "/图片", "/图")): return True
        kws = ["搜一下","查一下","帮我查","帮我搜","今天","现在","最近","天气",
               "新闻","最新","热点","发生","价格","上映","怎么样","评价",
               "多少","在哪","几点","什么时候","开门","关门","好吃","推荐",
               "附近","图片","照片","发图","找张","样子","长什么样","图片",
               "什么电影","什么歌","什么游戏","什么书","上映","评分","排名"]
        return any(k in msg for k in kws)

    async def _web_search(self, query):
        if not self.vkey: return "没接网线"
        want_img = any(w in query for w in ["图片","照片","发图","发张","找张","找个图","图","表情包","截图","搜图","壁纸","样子","长什么样","什么样子"])
        sys_msg = ("你是搜索助手。根据你的知识回答用户问题。"
                   "如果用户要图片，务必给出真实可访问的图片URL（https开头，以.jpg/.png/.gif结尾），至少2个。"
                   "如果不知道确切的图片URL，就说找不到，不要编造链接。") if want_img else (
                   "你是搜索助手。根据你的知识回答用户问题。尽量提供准确信息。")
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(
                    f"https://ark.cn-beijing.volces.com/api/v3/chat/completions",
                    headers={"Authorization": f"Bearer {self.vkey}", "Content-Type": "application/json"},
                    json={"model": self.vmodel, "max_tokens": 600,
                          "messages": [{"role": "system", "content": sys_msg}, {"role": "user", "content": query}]})
                d = r.json()
                err = d.get("error", {}).get("message", "") if "error" in d else ""
                has_choice = "choices" in d and d["choices"]
                print(f"[搜索] status={r.status_code} ok={has_choice} err={err[:60] if err else '-'}")
                if has_choice:
                    return d["choices"][0]["message"].get("content", "").strip()
        except Exception as e:
            print(f"[搜索] {e}")
        return "搜索失败"

    def _fallback_image_search(self, keyword):
        """去搜狗图片搜索抓取真实可访问图片URL（豆包URL不可用时兜底）"""
        import urllib.request, ssl, urllib.parse, random
        kw = re.sub(r'^(发|找|搜|给|来)(几张|张|一下|个)\s*', '', keyword).strip()
        if not kw:
            kw = keyword.strip()
        UAS = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
        ]
        try:
            q = urllib.parse.quote(kw)
            url = f"https://pic.sogou.com/pics?query={q}"
            ctx = ssl.create_default_context()
            ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers={
                "User-Agent": random.choice(UAS),
                "Referer": "https://www.sogou.com/",
            })
            html = urllib.request.urlopen(req, timeout=10, context=ctx).read().decode("utf-8", errors="ignore")
            raw_urls = re.findall(r'"picUrl"\s*:\s*"([^"]+)"', html)
            decoded = [u.replace("\\u002F", "/").replace("\\/", "/") for u in raw_urls]
            return decoded[:5] if decoded else []
        except Exception:
            return []


# ═══ 图片缓存（避免重复下载） ═══
_IMG_CACHE = {}

# ═══════════════════════════════════════════════════════════
# 微信风格聊天面板
# ═══════════════════════════════════════════════════════════
class WeChatStyleChat(ctk.CTkFrame):
    def __init__(self, parent, avatar_getter=None, self_nick="我", **kw):
        super().__init__(parent, fg_color=BG_CHAT, **kw)
        self.ai = None
        self._get_avatar = avatar_getter or (lambda n: None)
        self._self_nick = self_nick
        self._last_user_msg = ""
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 顶部标题栏
        self.title_bar = ctk.CTkFrame(self, fg_color="#f5f5f5", height=44, corner_radius=0)
        self.title_bar.grid(row=0, column=0, sticky="ew")
        self.title_bar.grid_propagate(False)
        self.title_label = ctk.CTkLabel(self.title_bar, text="", font=_f(14, "bold"),
                                        text_color=TEXT_DARK)
        self.title_label.place(relx=0.5, rely=0.5, anchor="center")
        # 底部分隔线
        ctk.CTkFrame(self.title_bar, height=1, fg_color=SEP_COLOR).pack(side="bottom", fill="x")

        self._msg_widgets = []  # 跟踪消息组件，用于清理
        self.msg_frame = ctk.CTkScrollableFrame(self, fg_color=BG_CHAT)
        self.msg_frame.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)

        # "正在输入"
        self.typing_frame = ctk.CTkFrame(self, fg_color=BG_CHAT, height=24)
        self.typing_frame.grid(row=3, column=0, sticky="ew", padx=0)
        self.typing_frame.grid_remove()
        self.typing_lbl = ctk.CTkLabel(self.typing_frame, text="", text_color=TEXT_GRAY, font=_f(10))
        self.typing_lbl.pack(side="left", padx=16)

        # 欢迎界面
        self.welcome = ctk.CTkFrame(self.msg_frame, fg_color="transparent")
        self.welcome.pack(pady=80)
        self._msg_widgets.append(self.welcome)
        ctk.CTkLabel(self.welcome, text="欢迎使用 ChatMine", text_color="#bbbbbb",
                     font=_f(16, "bold")).pack()
        ctk.CTkLabel(self.welcome, text="从左侧添加好友，开始聊天", text_color="#cccccc",
                     font=_f(11)).pack(pady=(6, 0))

        # 输入栏
        inp_bar = ctk.CTkFrame(self, fg_color="#f0f0f0", height=52, corner_radius=0)
        inp_bar.grid(row=4, column=0, sticky="ew")
        inp_bar.grid_propagate(False)
        inp_bar.grid_columnconfigure(0, weight=1)

        self.inp = ctk.CTkEntry(
            inp_bar, placeholder_text="", font=_f(13),
            fg_color=INPUT_BG, text_color=TEXT_DARK,
            border_color="#d0d0d0", border_width=1, corner_radius=6, height=36
        )
        self.inp.grid(row=0, column=0, sticky="ew", padx=(12, 8), pady=8)
        self.inp.bind("<Return>", lambda e: self._send())

        self.send_btn = ctk.CTkButton(
            inp_bar, text="发送", width=64, height=36,
            fg_color="#4caf50", hover_color="#43a047",
            font=_f(12), corner_radius=6, command=self._send
        )
        self.send_btn.grid(row=0, column=1, sticky="e", padx=(0, 12), pady=8)

    def set_self_nick(self, nick):
        self._self_nick = nick

    def set_ai(self, ai):
        _log(f"set_ai: switching to {ai.name if ai else 'None'}, prev={self.ai.name if self.ai else 'None'}")
        print(f"[set_ai] switching to: {ai.name if ai else 'None'}, previous: {self.ai.name if self.ai else 'None'}")
        self.ai = ai
        self._hide_typing()
        # 递增世代编号，使旧好友的 pending 回调全部失效
        self._gen = getattr(self, '_gen', 0) + 1
        for w in self._msg_widgets:
            try: w.destroy()
            except: pass
        self._msg_widgets = []
        if ai:
            self.title_label.configure(text=ai.name)
            self._sys(f"现在可以和 {ai.name} 聊天了")
            history = self.ai.mem.get(self.ai.uid, 30)
            _log(f"set_ai: name={ai.name}, uid={ai.uid}, history_count={len(history)}")
            # 先展平所有气泡
            bubbles = []
            for item in history:
                sender = self._self_nick if item["role"] == "user" else ai.name
                content = item["content"]
                if re.search(r'(?:^|\n)\s*\|\|\s*(?:\n|$)', content):
                    parts = [p.strip() for p in re.split(r'(?:^|\n)\s*\|\|\s*(?:\n|$)', content) if p.strip()]
                elif "||" in content:
                    parts = [p.strip() for p in content.split("||") if p.strip()]
                elif "\n\n" in content:
                    parts = [p.strip() for p in content.split("\n\n") if p.strip()]
                else:
                    parts = [content.strip()]
                for part in parts:
                    bubbles.append((sender, part, item.get("time")))
            # 分批渲染：每批 5 条，间隔 30ms，避免 UI 卡死
            self._render_history_batch(bubbles, 0, 5)
        else:
            self.title_label.configure(text="")
        _log(f"set_ai: done")

    def _render_history_batch(self, bubbles, start, batch_size):
        """分批渲染历史气泡，避免一次性创建大量 widget 卡死 UI"""
        end = min(start + batch_size, len(bubbles))
        for i in range(start, end):
            sender, text, ts = bubbles[i]
            self._bubble(sender, text, ts, defer_images=True)
        if end < len(bubbles):
            self.after(30, lambda: self._render_history_batch(bubbles, end, batch_size))

    def _send(self):
        if not self.ai: return
        msg = self.inp.get().strip()
        if not msg: return
        self.inp.delete(0, "end")
        self._bubble(self._self_nick, msg, datetime.datetime.now().strftime("%H:%M"))
        self.ai.mem.add(self.ai.uid, "user", msg)
        self._show_typing()
        threading.Thread(target=self._call, args=(msg,), daemon=True).start()

    def _show_typing(self):
        self.typing_frame.grid()
        self._typing_dots = 0
        self._typing_active = True
        self._animate_typing()

    def _hide_typing(self):
        self._typing_active = False
        self.typing_frame.grid_remove()

    def _animate_typing(self):
        if not self._typing_active: return
        dots = [".", "..", "..."][self._typing_dots % 3]
        self.typing_lbl.configure(text=f"对方正在输入{dots}")
        self._typing_dots += 1
        self.after(500, self._animate_typing)

    def _call(self, msg):
        self._last_user_msg = msg
        reply = self.ai.reply(msg)
        self._hide_typing()

        # 用户要图片 → 清理Wikipedia URL + 搜狗兜底（仅当AI层未处理时）
        want_img = any(w in msg for w in ["图片","照片","发图","发张","找张","找图","表情包","截图","搜图","壁纸","样子","长什么样"])
        if want_img:
            reply = re.sub(r'https?://[^\s]*?wikimedia[^\s]*', '', reply, flags=re.I)
            reply = re.sub(r'https?://[^\s]*?wikipedia[^\s]*', '', reply, flags=re.I)
            reply = re.sub(r'\s+', ' ', reply).strip()
            if '[IMG]' not in reply:
                fb_urls = self._fallback_image_search(msg)
                if fb_urls:
                    img_tags = " ".join(f"[IMG]{u}[/IMG]" for u in fb_urls[:AIBuddy._img_count(msg)])
                    reply = reply + "\n" + img_tags if reply else img_tags

        if re.search(r'(?:^|\n)\s*\|\|\s*(?:\n|$)', reply):
            parts = [p.strip() for p in re.split(r'(?:^|\n)\s*\|\|\s*(?:\n|$)', reply) if p.strip()]
        elif "||" in reply:
            parts = [p.strip() for p in reply.split("||") if p.strip()]
        elif "\n\n" in reply:
            parts = [p.strip() for p in reply.split("\n\n") if p.strip()]
        else:
            parts = [reply.strip()]
        self._show_parts(parts, 0)

    def _show_parts(self, parts, idx):
        if idx >= len(parts): return
        self._bubble(self.ai.name, parts[idx], datetime.datetime.now().strftime("%H:%M"))
        if idx + 1 < len(parts):
            self.after(900, lambda: self._show_parts(parts, idx + 1))

    def _bubble(self, sender, text, timestamp=None, defer_images=False):
        is_me = (sender == self._self_nick)

        row = ctk.CTkFrame(self.msg_frame, fg_color="transparent")
        row.pack(fill="x", pady=3, padx=14)
        self._msg_widgets.append(row)

        # 头像
        def _make_avatar(s, sz, side):
            f = ctk.CTkFrame(row, width=sz, height=sz, corner_radius=sz//2, fg_color="#4caf50")
            f.pack(side=side, padx=(0,6) if side=="left" else (6,0), anchor="n")
            f.pack_propagate(False)
            ctk.CTkLabel(f, text=s[0] if s else "?", text_color="white", font=_f(sz//3,"bold")).place(relx=0.5,rely=0.5,anchor="center")
            avi_path = self._get_avatar(s)
            if avi_path and os.path.exists(avi_path):
                try:
                    from PIL import Image
                    i = Image.open(avi_path).resize((sz,sz),Image.LANCZOS)
                    p = ctk.CTkImage(light_image=i,dark_image=i,size=(sz,sz))
                    l = ctk.CTkLabel(f,image=p,text=""); l.image = p
                    l.place(relx=0.5,rely=0.5,anchor="center")
                except: pass
            return f

        if not is_me:
            _make_avatar(sender, 32, "left")
            right_side = ctk.CTkFrame(row, fg_color="transparent")
            right_side.pack(side="left", fill="x", expand=True)
        else:
            _make_avatar(sender, 32, "right")
            right_side = ctk.CTkFrame(row, fg_color="transparent")
            right_side.pack(side="right", fill="x", expand=True)

        name_color = "#666666"
        ctk.CTkLabel(right_side, text=sender, text_color=name_color,
                     font=_f(10), anchor="w" if not is_me else "e"
                     ).pack(anchor="w" if not is_me else "e", padx=4, pady=(0, 1))

        bg = BG_MY_MSG if is_me else BG_HER_MSG
        txt_color = TEXT_DARK

        bub = ctk.CTkFrame(right_side, fg_color=bg, corner_radius=6)
        bub.pack(anchor="e" if is_me else "w", pady=0)

        # 微信表情 → emoji
        emoji_map = {"[OK]":"👌","[破涕为笑]":"😂","[憨笑]":"😊","[流汗]":"😅",
                     "[撇嘴]":"😕","[发呆]":"😶","[害羞]":"😳","[委屈]":"🥺",
                     "[惊讶]":"😲","[疑问]":"🤔","[大哭]":"😭","[发怒]":"😠",
                     "[Laugh]":"😂","[Grin]":"😁","[Smile]":"🙂","[Sweat]":"😅"}
        for k, v in emoji_map.items():
            text = text.replace(k, v)

        # [IMG]url[/IMG] 标记 → 图片内嵌在文本流中（微信式内嵌）
        img_tag = re.compile(r'\[IMG\]\s*(https?://[^\s\[\]]+?)\s*\[/IMG\]', re.I)
        img_keyword = self._last_user_msg if not is_me else ""
        if img_tag.search(text):
            parts = img_tag.split(text)  # [text_before, url1, text_between, url2, text_after...]
            for i, part in enumerate(parts):
                part = part.strip()
                if not part: continue
                if i % 2 == 1:  # 奇数索引 = 图片URL
                    self._add_image_preview(bub, part, keyword=img_keyword, defer=defer_images)
                else:
                    for chunk in self._split(part):
                        chunk = chunk.strip()
                        if not chunk: continue
                        ctk.CTkLabel(bub, text=chunk, text_color=txt_color, anchor="w",
                                     font=_f(14), wraplength=380, justify="left",
                                     ).pack(anchor="w", padx=14, pady=5)
        else:
            # 兜底：旧式纯URL正则匹配（兼容无标记的回复）
            imgs = re.findall(r'https?://[^\s]*?(?:img|image|pic|photo|cdn)[^\s]*?\.(?:jpg|jpeg|png|gif|webp|bmp)[^\s]*', text, re.I)
            if not imgs:
                imgs = re.findall(r'https?://[^\s]+\.(?:jpg|jpeg|png|gif|webp)(?:\?[^\s]*)?', text, re.I)
            for url in imgs[:4]:
                self._add_image_preview(bub, url, keyword=img_keyword, defer=defer_images)
            if imgs:
                text = re.sub(r'https?://[^\s]+\.(?:jpg|jpeg|png|gif|webp|bmp)(?:[^\s]*)?', '', text, flags=re.I).strip()
            for chunk in self._split(text):
                chunk = chunk.strip()
                if not chunk: continue
                ctk.CTkLabel(bub, text=chunk, text_color=txt_color, anchor="w",
                             font=_f(14), wraplength=380, justify="left",
                             ).pack(anchor="w", padx=14, pady=5)

        if timestamp is not None:
            ctk.CTkLabel(right_side, text=timestamp,
                     text_color=TEXT_TIME, font=_f(9)
                     ).pack(anchor="e" if is_me else "w", padx=4, pady=(1, 0))

        self.after(100, lambda: self.msg_frame._parent_canvas.yview_moveto(1.0))

    def _fallback_image_search(self, keyword):
        """去搜狗图片搜索抓取真实可访问图片URL（豆包URL不可用时兜底）"""
        import urllib.request, ssl, urllib.parse, random, re as _re
        # 清理关键词：去掉常见前缀
        kw = _re.sub(r'^(发|找|搜|给|来)(几张|张|一下|个)\s*', '', keyword).strip()
        if not kw:
            kw = keyword.strip()
        UAS = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
        ]
        try:
            q = urllib.parse.quote(kw)
            url = f"https://pic.sogou.com/pics?query={q}"
            ctx = ssl.create_default_context()
            ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers={
                "User-Agent": random.choice(UAS),
                "Referer": "https://www.sogou.com/",
            })
            html = urllib.request.urlopen(req, timeout=10, context=ctx).read().decode("utf-8", errors="ignore")
            raw_urls = _re.findall(r'"picUrl"\s*:\s*"([^"]+)"', html)
            # 解码转义字符 \u002F → / , \/ → /
            decoded = [u.replace("\\u002F", "/").replace("\\/", "/") for u in raw_urls]
            return decoded[:5] if decoded else []
        except Exception:
            return []

    def _add_image_preview(self, parent, url, keyword="", defer=False):
        """后台异步下载图片；defer=True 时仅显示文字占位，不下载"""
        import webbrowser, threading
        gen = getattr(self, '_gen', 0)
        # 命中缓存 → 直接显示
        if url in _IMG_CACHE:
            import tkinter as tk
            lbl = tk.Label(parent, image=_IMG_CACHE[url], bg="#ffffff", bd=0)
            lbl.image = _IMG_CACHE[url]
            lbl.pack(padx=6, pady=4)
            return

        # 延迟模式：只显示文字占位，不触发下载
        if defer:
            lbl = ctk.CTkLabel(parent, text="[图片]", text_color=TEXT_GRAY,
                               font=_f(10))
            lbl.pack(padx=6, pady=4)
            return

        placeholder = ctk.CTkButton(parent, text="加载中…", width=70, height=26,
                                    fg_color="#f0f0f0", hover_color="#e0e0e0",
                                    text_color=TEXT_GRAY, font=_f(10), corner_radius=6)
        placeholder.pack(padx=6, pady=4)

        def _download():
            import random, time as _time
            from io import BytesIO
            from PIL import Image, ImageTk
            import urllib.request, ssl
            UAS = [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            ]
            for attempt in range(3):
                try:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
                    headers = {
                        "User-Agent": random.choice(UAS),
                        "Referer": "https://www.google.com/",
                        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    }
                    req = urllib.request.Request(url, headers=headers)
                    data = urllib.request.urlopen(req, timeout=12, context=ctx).read()
                    if len(data) < 200: raise Exception("响应过短，疑似错误页面")
                    img = Image.open(BytesIO(data))
                    w, h = img.size
                    if w > 260: ratio = 260 / w; w, h = 260, int(h * ratio)
                    img = img.resize((w, h), Image.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    _IMG_CACHE[url] = photo
                    self.after(0, lambda p=photo, g=gen: _show_image(p) if self._gen == g else None)
                    return
                except Exception:
                    if attempt < 2:
                        _time.sleep(0.8)
            self.after(0, lambda g=gen: _show_link() if self._gen == g else None)

        def _show_image(photo):
            try: placeholder.destroy()
            except: pass
            import tkinter as tk
            lbl = tk.Label(parent, image=photo, bg="#ffffff", bd=0)
            lbl.image = photo
            lbl.pack(padx=6, pady=4)

        def _show_link():
            try: placeholder.destroy()
            except: pass
            if keyword:
                fb_urls = self._fallback_image_search(keyword)
                if fb_urls:
                    for fb_url in fb_urls[:3]:
                        self._add_image_preview(parent, fb_url, keyword="")
                    return
            ctk.CTkButton(parent, text="查看图片", width=90, height=26,
                          fg_color="#e3f2fd", hover_color="#bbdefb",
                          text_color="#1976d2", font=_f(10), corner_radius=6,
                          command=lambda u=url: webbrowser.open(u)).pack(padx=10, pady=4)

        threading.Thread(target=_download, daemon=True).start()

    def _sys(self, text):
        lbl = ctk.CTkLabel(self.msg_frame, text=text, text_color="#aaaaaa",
                           font=_f(11))
        lbl.pack(pady=20)
        self._msg_widgets.append(lbl)

    def _split(self, text):
        if len(text) <= 18: return [text]
        parts = re.split(r'(?<=[。！？!?，,、…])', text)
        chunks, buf = [], ""
        for p in parts:
            p = p.strip()
            if not p: continue
            if len(buf + p) > 20 and buf:
                chunks.append(buf); buf = p
            else: buf += p
        if buf.strip(): chunks.append(buf.strip())
        return chunks if chunks else [text]


# ============================================================
# 联系人列表项
# ============================================================
class ContactItem(ctk.CTkFrame):
    def __init__(self, parent, name, on_click, on_right_click=None, avatar_path=None, subtitle="", **kw):
        super().__init__(parent, fg_color=BG_SIDEBAR, height=56, **kw)
        self.grid_propagate(False)
        self.contact_name = name
        self._avatar_path = avatar_path
        self._callback = lambda: (on_click(), _log(f"ContactItem CLICK: {name}"))
        self._right_cb = on_right_click
        self._active = False

        # 绑定点击到自身及所有子控件
        self._bind_click(self)

        # 头像
        self.avatar_frame = ctk.CTkFrame(self, width=40, height=40, corner_radius=20, fg_color="#4caf50")
        self.avatar_frame.place(x=12, y=8)
        self._bind_click(self.avatar_frame)
        self.avatar_label = ctk.CTkLabel(self.avatar_frame, text=name[0] if name else "?", text_color="white",
                                          font=_f(16, "bold"))
        self.avatar_label.place(relx=0.5, rely=0.5, anchor="center")
        self._bind_click(self.avatar_label)
        if avatar_path and os.path.exists(avatar_path):
            self._set_avatar_image(avatar_path)

        # 名称和描述
        self.label = ctk.CTkLabel(self, text=name, text_color=TEXT_DARK, font=_f(13), anchor="w")
        self.label.place(x=62, y=10)
        self._bind_click(self.label)
        self.sub = ctk.CTkLabel(self, text=subtitle or "点击开始聊天", text_color=TEXT_GRAY, font=_f(10), anchor="w")
        self.sub.place(x=62, y=30)
        self._bind_click(self.sub)

    def _bind_click(self, widget):
        """给 widget 绑定左键点击、右键、悬停，递归绑定子控件"""
        widget.bind("<Button-1>", lambda e: self._callback())
        widget.bind("<Enter>", lambda e: self._on_enter())
        widget.bind("<Leave>", lambda e: self._on_leave())
        if self._right_cb:
            widget.bind("<Button-3>", lambda e: self._right_cb())
        for child in widget.winfo_children():
            self._bind_click(child)

    def _on_enter(self):
        if not self._active:
            self.configure(fg_color=HOVER_BG)

    def _on_leave(self):
        if not self._active:
            self.configure(fg_color=BG_SIDEBAR)

    def set_active(self, active):
        self._active = active
        self.configure(fg_color=ACTIVE_BG if active else BG_SIDEBAR)

    def _set_avatar_image(self, path):
        try:
            from PIL import Image
            img = Image.open(path).resize((40, 40), Image.LANCZOS)
            photo = ctk.CTkImage(light_image=img, dark_image=img, size=(40, 40))
            self.avatar_label.configure(image=photo, text="")
            self.avatar_label.image = photo
        except: pass
        # CTkLabel 设置图片后内部会创建 Canvas，需重新绑定所有子控件
        self._bind_click(self.avatar_label)
        self._bind_click(self.avatar_frame)

    def update_avatar(self, path):
        self._avatar_path = path
        self._set_avatar_image(path)


# ============================================================
# 添加好友弹窗
# ============================================================
class AddFriendDialog(ctk.CTkToplevel):
    def __init__(self, parent, store, api_key, api_model, vision_key, vision_model):
        super().__init__(parent)
        self.title("添加好友")
        self.geometry("420x520")
        self.resizable(False, False)
        self.grab_set()

        self.store = store
        self.api_key = api_key; self.api_model = api_model
        self.vision_key = vision_key; self.vision_model = vision_model

        self.raw_messages = []
        self.persona = None
        self.prompt = ""
        self.created_ai = None
        self._active = False

        self._build()
        self.after(100, lambda: self.focus())

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=0, sticky="nsew", padx=16, pady=12)
        main.grid_columnconfigure(0, weight=1)

        # ── 标题 ──
        head = ctk.CTkFrame(main, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(head, text="添加好友", font=_f(18, "bold"),
                     text_color=TEXT_DARK).pack(side="left")
        self.status_lbl = ctk.CTkLabel(head, text="", font=_f(10), text_color=TEXT_GRAY)
        self.status_lbl.pack(side="right")

        # ── 第1步：导入聊天记录 ──
        s1 = ctk.CTkFrame(main, fg_color="transparent")
        s1.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(s1, text="1. 导入聊天记录", font=_f(13, "bold"),
                     text_color=TEXT_DARK).pack(anchor="w")

        ctk.CTkButton(s1, text="选择 JSON 文件", width=140, height=32,
                      fg_color="#4caf50", hover_color="#43a047",
                      font=_f(12), command=self._import).pack(anchor="w", pady=(4, 2))

        self.import_hint = ctk.CTkLabel(s1, text="支持微信聊天记录导出的 JSON 格式",
                                       text_color=TEXT_GRAY, font=_f(10))
        self.import_hint.pack(anchor="w")

        # ── 第2步：聊天风格 ──
        s2 = ctk.CTkFrame(main, fg_color="transparent")
        s2.grid(row=2, column=0, sticky="ew", pady=(4, 8))
        ctk.CTkLabel(s2, text="2. 聊天风格", font=_f(13, "bold"),
                     text_color=TEXT_DARK).pack(anchor="w")
        self.persona_box = ctk.CTkTextbox(s2, font=_f(11), wrap="word",
                                          fg_color="#fafafa", border_color="#e0e0e0",
                                          border_width=1, height=140)
        self.persona_box.pack(fill="x", pady=(4, 0))
        self.persona_box.insert("0.0", "导入聊天记录后自动分析…")

        # ── 第3步：设置 ──
        s3 = ctk.CTkFrame(main, fg_color="transparent")
        s3.grid(row=3, column=0, sticky="ew", pady=(4, 8))
        ctk.CTkLabel(s3, text="3. 设置", font=_f(13, "bold"),
                     text_color=TEXT_DARK).pack(anchor="w")

        inp_row = ctk.CTkFrame(s3, fg_color="transparent")
        inp_row.pack(fill="x", pady=(4, 0))
        self.name_inp = ctk.CTkEntry(inp_row, placeholder_text="昵称",
                                     font=_f(12), height=32, width=120,
                                     fg_color=INPUT_BG, border_color="#d0d0d0",
                                     text_color=TEXT_DARK)
        self.name_inp.pack(side="left", padx=(0, 8))
        self.extra_inp = ctk.CTkEntry(inp_row, placeholder_text="备注（如: 河南人）",
                                      font=_f(12), height=32, width=160,
                                      fg_color=INPUT_BG, border_color="#d0d0d0",
                                      text_color=TEXT_DARK)
        self.extra_inp.pack(side="left")

        # ── 底部 ──
        bot = ctk.CTkFrame(main, fg_color="transparent")
        bot.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ctk.CTkButton(bot, text="取消", width=80, height=32,
                      fg_color="#e0e0e0", text_color=TEXT_DARK,
                      hover_color="#d0d0d0", font=_f(12),
                      command=self.destroy).pack(side="right", padx=(8, 0))
        self.add_btn = ctk.CTkButton(bot, text="添加", width=80, height=32,
                                     fg_color="#4caf50", hover_color="#43a047",
                                     font=_f(12), state="disabled",
                                     command=self._create)
        self.add_btn.pack(side="right")

    # ── 操作 ──
    def _status(self, txt):
        self.status_lbl.configure(text=txt)

    def _import(self):
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not p: return
        try:
            msgs = import_json(p)
            if not msgs:
                messagebox.showerror("错误", "JSON 格式不对，需要包含 messages 数组")
                return
            self.raw_messages = msgs
            # 检测发送者
            senders = set(m.get("sender", "") for m in msgs if m.get("sender"))
            self.target_sender = self._pick_sender(senders)
            self._status(f"导入 {len(msgs)} 条，目标={self.target_sender}")
            self._analyze()
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def _pick_sender(self, senders):
        me = {"我", "王舟亢", "WangYiHang"}
        for s in senders:
            if s not in me and not s.startswith("wxid"):
                return s
        others = [s for s in senders if s != "我"]
        return others[0] if others else "对方"

    def _analyze(self):
        self._status("分析中")
        self._active = True
        self._tick = 0
        self._animate()

        def w():
            self.persona = mine_personality(self.raw_messages, self.target_sender)
            self.after(0, self._show_persona)
        threading.Thread(target=w, daemon=True).start()

    def _animate(self):
        if not self._active: return
        dots = (self._tick % 4); self._tick += 1
        phases = ["读取消息","分析口头禅","识别语气","挖掘话题","提取语录","生成风格"]
        idx = (self._tick // 5) % len(phases)
        self._status(f"{phases[idx]}{'.' * dots}")
        self.after(300, self._animate)

    def _show_persona(self):
        self._active = False
        p = self.persona
        s = p.get("stats", {}); st = p.get("speech", {})
        ph = [x["phrase"] for x in p.get("phrases", [])[:10]]
        tp = p.get("topics", {})
        si = p.get("self_info", [])[:6]
        qt = p.get("quotes", [])[:4]

        txt = f"{p.get('name','?')} | {s.get('total',0)}条消息 | 均长{s.get('avg_len',0)}字\n"
        txt += f"语气: {st.get('tone','?')}\n\n"
        txt += f"口头禅: {', '.join(ph)}\n\n"
        txt += f"话题: {' '.join(tp.keys())}\n"
        if si:
            txt += f"\n关于自己:\n" + "\n".join(f"  · {i['content']}" for i in si)
        if qt:
            txt += f"\n\n语录:\n" + "\n".join(f"  「{q}」" for q in qt)

        self.persona_box.delete("0.0", "end")
        self.persona_box.insert("0.0", txt)

        name = self.name_inp.get().strip() or p.get('name', '好友')
        self.prompt = generate_prompt(p, self.extra_inp.get().strip(), name)
        self.add_btn.configure(state="normal")
        self._status(f"分析完成 — {s.get('total',0)}条")

    def _create(self):
        name = self.name_inp.get().strip()
        if not name:
            name = self.persona.get("name", "好友") if self.persona else "好友"
        if not self.prompt:
            messagebox.showinfo("提示", "请先导入聊天记录")
            return
        if not self.api_key:
            messagebox.showinfo("提示", "请在 .env 中设置 DEEPSEEK_API_KEY")
            return

        ai = AIBuddy(name, self.prompt, self.store, self.api_key, self.api_model,
                     self.vision_key, self.vision_model)
        self.created_ai = ai
        self._status("已添加")
        self.after(200, self.destroy)


# ============================================================
# 主窗口
# ============================================================
class ChatMineApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ChatMine")
        self.geometry("980x640")
        self.minsize(780, 480)
        self.configure(fg_color=BG_APP)

        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        self.store = MemStore(os.path.join(base, "memory.json"))
        self.config_path = os.path.join(base, "buddies.json")
        self.avatar_dir = os.path.join(base, "avatars")
        os.makedirs(self.avatar_dir, exist_ok=True)
        self.avatars = {}  # name → path
        self.self_nick = "我"
        self.self_avatar = None
        self.buddies = {}
        self._active_ai = None
        self._active_item = None

        self.api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.api_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.vision_key = os.getenv("VISION_API_KEY", "")
        self.vision_model = os.getenv("VISION_MODEL", "doubao-seed-1-8-251228")

        self._load_buddies()
        self._load_self_settings()

        self._build()

    # ── UI 构建 ──
    def _build(self):
        _log(f"_build: start, buddies={list(self.buddies.keys())}")
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ── 左侧边栏 ──
        sidebar = ctk.CTkFrame(self, width=250, fg_color=BG_SIDEBAR, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)

        # 侧边栏头部
        side_head = ctk.CTkFrame(sidebar, fg_color="transparent", height=48)
        side_head.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        side_head.grid_propagate(False)
        ctk.CTkLabel(side_head, text="消息", font=_f(18, "bold"),
                     text_color=TEXT_DARK).pack(side="left")
        ctk.CTkButton(side_head, text="＋", width=32, height=28,
                      fg_color="transparent", hover_color=HOVER_BG,
                      text_color=TEXT_DARK, font=_f(16),
                      command=self._add_friend).pack(side="right")
        ctk.CTkButton(side_head, text="⚙", width=32, height=28,
                      fg_color="transparent", hover_color=HOVER_BG,
                      text_color=TEXT_GRAY, font=_f(14),
                      command=self._edit_self).pack(side="right", padx=(0,4))
        ctk.CTkButton(side_head, text="?", width=32, height=28,
                      fg_color="transparent", hover_color=HOVER_BG,
                      text_color=TEXT_GRAY, font=_f(14, "bold"),
                      command=self._show_help).pack(side="right", padx=(0,4))

        # 分隔线
        sep1 = ctk.CTkFrame(sidebar, height=1, fg_color=SEP_COLOR)
        sep1.grid(row=1, column=0, sticky="ew", padx=0, pady=0)

        # 联系人列表（普通 frame，高度随内容）
        self.contact_list = ctk.CTkFrame(sidebar, fg_color="transparent")
        self.contact_list.grid(row=2, column=0, sticky="ew")

        # 底部导入按钮
        side_bot = ctk.CTkFrame(sidebar, fg_color="transparent", height=40)
        side_bot.grid(row=3, column=0, sticky="ew", padx=10, pady=8)
        side_bot.grid_propagate(False)
        ctk.CTkButton(side_bot, text="从聊天记录添加好友", width=180, height=30,
                      fg_color="transparent", hover_color=HOVER_BG,
                      text_color=TEXT_GRAY, font=_f(10),
                      command=self._add_friend).pack()

        self._refresh_contacts()

        # ── 右侧聊天区 ──
        self.chat = WeChatStyleChat(self, avatar_getter=self._get_avatar, self_nick=self.self_nick)
        self.chat.grid(row=0, column=1, sticky="nsew")

    # ── 联系人列表 ──
    def _refresh_contacts(self):
        _log(f"_refresh_contacts: start, buddies={list(self.buddies.keys())}, avatars={list(self.avatars.keys())}")
        self._active_item = None
        for w in self.contact_list.winfo_children():
            w.destroy()

        if not self.buddies:
            _log(f"_refresh_contacts: no buddies, showing empty state")
            empty = ctk.CTkFrame(self.contact_list, fg_color="transparent")
            empty.pack(pady=40)
            ctk.CTkLabel(empty, text="还没有好友", text_color="#cccccc",
                         font=_f(13)).pack()
            ctk.CTkLabel(empty, text="点击下方按钮添加", text_color="#dddddd",
                         font=_f(10)).pack(pady=(4, 0))
            return

        for name, ai in self.buddies.items():
            subtitle = getattr(ai, 'description', '')
            avi = self.avatars.get(name)
            _log(f"_refresh_contacts: creating ContactItem name={name}, avatar={avi}")
            item = ContactItem(self.contact_list, name,
                               on_click=lambda n=name, a=ai: self._select_contact(n, a),
                               on_right_click=lambda n=name: self._edit_contact(n),
                               avatar_path=avi,
                               subtitle=subtitle)
            item.pack(fill="x", padx=6, pady=1)
            item._ai_name = name

    def _select_contact(self, name, ai):
        _log(f"_select_contact: name={name}, active_ai={self._active_ai.name if self._active_ai else 'None'}")
        print(f"[Select] switching to {name}, active_ai={self._active_ai.name if self._active_ai else 'None'}")
        if self._active_ai is ai:
            _log(f"_select_contact: already active, skip")
            print(f"[Select] already active, skipping")
            return
        if self._active_item:
            self._active_item.set_active(False)
        found = False
        for w in self.contact_list.winfo_children():
            if hasattr(w, '_ai_name') and w._ai_name == name:
                w.set_active(True)
                self._active_item = w
                found = True
                break
        _log(f"_select_contact: found contact widget={found}, calling set_ai")
        self._active_ai = ai
        self.chat.set_ai(ai)

    # ── 添加好友 ──
    def _load_self_settings(self):
        sp = os.path.join(os.path.dirname(self.config_path), "self_settings.json")
        try:
            if os.path.exists(sp):
                with open(sp,"r",encoding="utf-8") as f: d=json.load(f)
                self.self_nick = d.get("nick","我")
                self.self_avatar = d.get("avatar")
                if self.self_avatar:
                    self.avatars[self.self_nick] = self.self_avatar
        except: pass

    def _save_self_settings(self):
        sp = os.path.join(os.path.dirname(self.config_path), "self_settings.json")
        with open(sp,"w",encoding="utf-8") as f:
            json.dump({"nick":self.self_nick,"avatar":self.self_avatar},f)

    def _show_help(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title("帮助")
        dlg.geometry("300x280")
        dlg.resizable(False, False)
        dlg.grab_set()

        ctk.CTkLabel(dlg, text="帮助", font=_f(16, "bold"), text_color=TEXT_DARK).pack(pady=(16, 8))

        # 常用指令
        cmd_frame = ctk.CTkFrame(dlg, fg_color="#fafafa", corner_radius=8)
        cmd_frame.pack(fill="x", padx=16, pady=(0, 10))

        commands = [
            ("/帮助 或 /help", "查看指令列表"),
            ("/搜索 xxx", "联网搜索"),
            ("/图片 xxx", "搜索图片"),
            ("/多说", "切换到详细回复模式"),
            ("/简短", "切换到简短回复模式"),
            ("/状态", "查看当前记忆条数"),
            ("/重置", "清空对话记忆"),
        ]
        for cmd, desc in commands:
            row = ctk.CTkFrame(cmd_frame, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=1)
            ctk.CTkLabel(row, text=cmd, font=_f(11, "bold"), text_color=TEXT_DARK,
                         width=100, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=desc, font=_f(10), text_color=TEXT_GRAY,
                         anchor="w").pack(side="left", padx=(8, 0))

        # Bug 上报
        ctk.CTkLabel(dlg, text="Bug 上报", font=_f(12, "bold"), text_color=TEXT_DARK).pack(pady=(4, 2))
        bug_frame = ctk.CTkFrame(dlg, fg_color="#fafafa", corner_radius=8)
        bug_frame.pack(fill="x", padx=16)
        ctk.CTkLabel(bug_frame, text="2691480780@qq.com", font=_f(12),
                     text_color="#1976d2").pack(pady=10)
        ctk.CTkLabel(bug_frame, text="请发送问题描述和截图", font=_f(10),
                     text_color=TEXT_GRAY).pack(pady=(0, 8))

        ctk.CTkButton(dlg, text="关闭", width=80, height=28,
                      fg_color="#e0e0e0", text_color=TEXT_DARK,
                      hover_color="#d0d0d0", font=_f(11),
                      command=dlg.destroy).pack(pady=12)

    def _edit_self(self):
        dlg = ctk.CTkToplevel(self); dlg.title("个人设置"); dlg.geometry("360x400"); dlg.grab_set()
        dlg.configure(fg_color=BG_APP)
        dlg.grid_columnconfigure(0, weight=1)

        # 标题
        ctk.CTkLabel(dlg, text="个人设置", font=_f(18, "bold"), text_color=TEXT_DARK).grid(row=0, column=0, pady=(20, 16))

        # 头像预览
        avi_size = 80
        self._self_preview_frame = ctk.CTkFrame(dlg, width=avi_size, height=avi_size, corner_radius=avi_size//2, fg_color="#4caf50")
        self._self_preview_frame.grid(row=1, column=0)
        self._self_preview_label = ctk.CTkLabel(self._self_preview_frame, text=self.self_nick[0] if self.self_nick else "?", text_color="white", font=_f(avi_size//3, "bold"))
        self._self_preview_label.place(relx=0.5, rely=0.5, anchor="center")
        self._refresh_self_avatar_preview()

        # 选择头像按钮
        ctk.CTkButton(dlg, text="更换头像", width=100, height=28,
                      fg_color="#e0e0e0", text_color=TEXT_DARK,
                      hover_color="#d0d0d0", font=_f(11),
                      command=lambda: self._do_pick_self_avatar(dlg)).grid(row=2, column=0, pady=(8, 16))

        # 昵称
        ctk.CTkLabel(dlg, text="昵称", font=_f(12), text_color=TEXT_DARK).grid(row=3, column=0, pady=(0, 4))
        nick_var = StringVar(value=self.self_nick)
        nick_entry = ctk.CTkEntry(dlg, textvariable=nick_var, font=_f(13), width=200,
                                  fg_color=INPUT_BG, border_color="#d0d0d0", text_color=TEXT_DARK)
        nick_entry.grid(row=4, column=0)

        def _on_nick_change(*args):
            new = nick_var.get().strip()
            pv = new[0] if new else "?"
            self._self_preview_label.configure(text=pv)

        nick_var.trace_add("write", _on_nick_change)

        # 按钮行
        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.grid(row=5, column=0, pady=(24, 16))
        ctk.CTkButton(btn_row, text="取消", width=90, height=34,
                      fg_color="#e0e0e0", text_color=TEXT_DARK,
                      hover_color="#d0d0d0", font=_f(12),
                      command=dlg.destroy).pack(side="left", padx=(0, 12))
        ctk.CTkButton(btn_row, text="保存", width=90, height=34,
                      fg_color="#4caf50", hover_color="#43a047",
                      font=_f(12), command=lambda: self._save_self(dlg, nick_var.get())).pack(side="left")

    def _refresh_self_avatar_preview(self):
        avi = self.self_avatar or self.avatars.get("我")
        if avi and os.path.exists(avi):
            try:
                from PIL import Image
                img = Image.open(avi).resize((80, 80), Image.LANCZOS)
                photo = ctk.CTkImage(light_image=img, dark_image=img, size=(80, 80))
                self._self_preview_label.configure(image=photo, text="")
                self._self_preview_label.image = photo
            except: pass

    def _do_pick_self_avatar(self, dlg):
        p = filedialog.askopenfilename(filetypes=[("图片", "*.jpg *.jpeg *.png")])
        if not p: return
        dst = os.path.join(self.avatar_dir, "self_avatar.png")
        from PIL import Image; Image.open(p).resize((128, 128)).save(dst)
        self.self_avatar = dst
        self.avatars[self.self_nick] = dst
        self._refresh_self_avatar_preview()

    def _save_self(self, dlg, nick_str):
        new_nick = nick_str.strip() or "我"
        old_nick = self.self_nick
        self.self_nick = new_nick
        # 迁移头像 key
        if old_nick in self.avatars:
            self.avatars[new_nick] = self.avatars.pop(old_nick)
        self._save_self_settings()
        self.chat.set_self_nick(new_nick)
        if self._active_ai:
            self._select_contact(self._active_ai.name, self._active_ai)
        dlg.destroy()

    def _edit_contact(self, name):
        ai = self.buddies.get(name)
        if not ai: return

        dlg = ctk.CTkToplevel(self)
        dlg.title(f"编辑资料 — {name}")
        dlg.geometry("340x300")
        dlg.resizable(False, False)
        dlg.grab_set()

        # 昵称
        ctk.CTkLabel(dlg, text="昵称", font=_f(12), text_color=TEXT_DARK).pack(pady=(16, 2))
        name_inp = ctk.CTkEntry(dlg, font=_f(12), width=240,
                                fg_color=INPUT_BG, border_color="#d0d0d0", text_color=TEXT_DARK)
        name_inp.pack()
        name_inp.insert(0, name)

        # 描述
        ctk.CTkLabel(dlg, text="描述", font=_f(12), text_color=TEXT_DARK).pack(pady=(10, 2))
        desc_inp = ctk.CTkEntry(dlg, font=_f(12), width=240,
                                fg_color=INPUT_BG, border_color="#d0d0d0", text_color=TEXT_DARK)
        desc_inp.pack()
        desc_inp.insert(0, getattr(ai, 'description', ''))

        # 头像
        avi_btn = ctk.CTkButton(dlg, text="更换头像", width=100, height=28,
                                fg_color="#e0e0e0", text_color=TEXT_DARK,
                                hover_color="#d0d0d0", font=_f(11),
                                command=lambda: self._pick_contact_avatar(name, dlg))
        avi_btn.pack(pady=(10, 4))

        def save():
            new_name = name_inp.get().strip()
            new_desc = desc_inp.get().strip()
            if not new_name:
                return
            ai.description = new_desc
            # 改名
            if new_name != name:
                del self.buddies[name]
                ai.name = new_name
                ai.uid = new_name
                self.buddies[new_name] = ai
                if name in self.avatars:
                    self.avatars[new_name] = self.avatars.pop(name)
            self._save_buddies()
            self._refresh_contacts()
            self._select_contact(new_name, ai)
            dlg.destroy()

        ctk.CTkButton(dlg, text="保存", width=100, height=32,
                      fg_color="#4caf50", hover_color="#43a047",
                      font=_f(12), command=save).pack(pady=10)

    def _pick_contact_avatar(self, name, dlg):
        p = filedialog.askopenfilename(filetypes=[("图片", "*.jpg *.jpeg *.png")])
        if not p: return
        dst = os.path.join(self.avatar_dir, f"{name}_avatar.png")
        from PIL import Image; Image.open(p).resize((128, 128)).save(dst)
        self.avatars[name] = dst

    def _get_avatar(self, name):
        return self.avatars.get(name) or None

    def _load_buddies(self):
        _log(f"_load_buddies: checking {self.config_path}")
        if not os.path.exists(self.config_path):
            _log(f"_load_buddies: file not found at {self.config_path}")
            print(f"[Load] buddies.json not found at {self.config_path}")
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            _log(f"_load_buddies: loaded JSON with keys={list(data.keys())}")
            print(f"[Load] loaded buddies.json: {list(data.keys())}")
        except Exception as e:
            _log(f"_load_buddies: JSON parse failed: {e}\n{traceback.format_exc()}")
            print(f"[Load] Failed to read buddies.json: {e}")
            traceback.print_exc()
            return
        for name, info in data.items():
            try:
                ai = AIBuddy(name, info["prompt"], self.store, self.api_key, self.api_model,
                             self.vision_key, self.vision_model)
                ai.description = info.get("description", "")
                self.buddies[name] = ai
                if info.get("avatar"):
                    self.avatars[name] = info["avatar"]
                _log(f"_load_buddies: restored buddy '{name}'")
                print(f"[Load] restored buddy: {name}")
            except Exception as e:
                _log(f"_load_buddies: failed to restore '{name}': {e}\n{traceback.format_exc()}")
                print(f"[Load] Failed to restore buddy '{name}': {e}")
                traceback.print_exc()
        _log(f"_load_buddies: done, buddies={list(self.buddies.keys())}")

    def _save_buddies(self):
        try:
            data = {}
            for name, ai in self.buddies.items():
                data[name] = {"prompt": ai.persona, "description": getattr(ai, 'description', '')}
                if name in self.avatars:
                    data[name]["avatar"] = self.avatars[name]
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"[Save] buddies saved to {self.config_path}: {list(data.keys())}")
        except Exception as e:
            print(f"[Save] Failed to save buddies.json: {e}")
            traceback.print_exc()

    def _add_friend(self):
        _log(f"_add_friend: opening dialog")
        dlg = AddFriendDialog(self, self.store, self.api_key, self.api_model,
                              self.vision_key, self.vision_model)
        self.wait_window(dlg)
        if dlg.created_ai:
            ai = dlg.created_ai
            _log(f"_add_friend: created buddy '{ai.name}', saving and refreshing")
            self.buddies[ai.name] = ai
            self._save_buddies()
            self._refresh_contacts()
            self._select_contact(ai.name, ai)
        else:
            _log(f"_add_friend: cancelled (no created_ai)")


if __name__ == "__main__":
    app = ChatMineApp()
    app.mainloop()
