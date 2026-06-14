"""
人格挖掘引擎 v2 — 精度优先，自动从聊天记录提取人物画像
"""
import re, json
from collections import Counter


STOP_WORDS = set("我你他她它的是在了有不这那个么吗呢啊吧哦呀就都也还"
                "要会说做看去来上下大小一没得很好和能想被把但所而已"
                "因为所以如果虽然然而而且或者并且虽然不过然后"
                "这个那个什么怎么为什么哪可以知道应该可能一定非常觉得"
                "问题事情东西还有但是已经一个没有".split())


def mine_personality(messages: list[dict], target_sender: str = "对方") -> dict:
    """深度挖掘目标人格"""
    # 筛选目标消息
    target_msgs = [m for m in messages if m.get("sender") == target_sender]
    other_msgs = [m for m in messages if m.get("sender") != target_sender]

    if not target_msgs:
        return _empty()

    all_target_text = "\n".join(m["content"] for m in target_msgs)
    msg_lens = [len(m["content"]) for m in target_msgs]
    total = len(target_msgs)

    return {
        "name": target_sender,  # 名字由用户输入，不从聊天记录猜
        "stats": _stats(target_msgs),
        "speech": _speech(target_msgs, all_target_text, msg_lens),
        "phrases": _phrases(target_msgs),
        "emojis": _emojis(target_msgs, all_target_text),
        "topics": _topics(target_msgs, other_msgs),
        "self_info": _self_info(target_msgs),
        "quotes": _quotes(target_msgs),
        "patterns": _patterns(target_msgs, messages),
        "sample_replies": _sample_replies(target_msgs, messages, target_sender),
    }


def _empty():
    return {"name": "AI", "stats": {}, "speech": {}, "phrases": [],
            "emojis": {}, "topics": {}, "self_info": [], "quotes": [],
            "patterns": {}, "sample_replies": []}


# ======== 姓名 ========
def _find_name(msgs, target):
    # 如果 target 已经是真实名称（非 wxid），直接用
    if target and not target.startswith("wxid") and not target.startswith("对方"):
        return target
    # 尝试从对话中提取
    for m in msgs:
        if m.get("sender") != target:
            for pat in [r'(\S{1,4})你好', r'@(\S{2,4})']:
                match = re.search(pat, m["content"])
                n = match.group(1) if match else ""
                if n and len(n) >= 2 and n not in ("微信","在吗","有人","那个","这个"):
                    return n
    return "对方"


# ======== 统计 ========
def _stats(msgs):
    lens = [len(m["content"]) for m in msgs]
    times = [m.get("time", "") for m in msgs if m.get("time")]
    return {
        "total": len(msgs),
        "avg_len": round(sum(lens) / len(lens), 1),
        "max_len": max(lens),
        "under_10": round(sum(1 for l in lens if l <= 10) / len(lens), 2),
        "over_20": round(sum(1 for l in lens if l > 20) / len(lens), 2),
        "date_range": f"{times[0][:10]} ~ {times[-1][:10]}" if times else "未知",
    }


# ======== 说话风格 ========
def _speech(msgs, all_text, lens):
    # 语气词
    modals = {"啊": 0, "呢": 0, "吧": 0, "吗": 0, "哦": 0, "呀": 0, "嘛": 0}
    for k in modals: modals[k] = all_text.count(k)
    top_modal = sorted(modals.items(), key=lambda x: -x[1])[:3]

    # 标点
    n = len(msgs)
    q_ratio = sum(1 for m in msgs if "?" in m["content"] or "？" in m["content"]) / n
    ex_ratio = sum(1 for m in msgs if "!" in m["content"] or "！" in m["content"]) / n
    ell_ratio = sum(1 for m in msgs if "…" in m["content"] or "..." in m["content"]) / n

    # 回复长度偏好
    short = sum(1 for l in lens if l <= 10) / n

    # 判断语气类型
    if ex_ratio > 0.15 and short > 0.7:
        tone = "活泼跳跃型，用感叹号多，消息很短像聊天"
    elif q_ratio > 0.15:
        tone = "喜欢反问和追问，爱用问句"
    elif short > 0.8:
        tone = "惜字如金型，回复极简"
    elif ell_ratio > 0.1:
        tone = "喜欢用省略号，说话有留白感"
    else:
        tone = "自然随性，长短皆可"

    return {
        "tone": tone,
        "modals": top_modal,
        "short_ratio": round(short, 2),
        "question_ratio": round(q_ratio, 2),
        "exclamation": round(ex_ratio, 2),
        "avg_len": round(sum(lens) / n, 1) if n else 0,
    }


# ======== 口头禅 ========
def _phrases(msgs):
    c = Counter()
    for m in msgs:
        text = m["content"]
        clean = re.sub(r'[，。！？、；：""''（）\s,.!?]+', '', text)
        for n in [2, 3]:
            for i in range(len(clean) - n + 1):
                p = clean[i:i + n]
                if p not in STOP_WORDS and len(p) >= 2:
                    c[p] += 1

    total = len(msgs)
    result = []
    for phrase, count in c.most_common(200):
        freq = count / total
        if freq < 0.02 and len(result) >= 20:  # at least 2%
            break
        # Filter meaningless combos
        if re.match(r'^[0-9a-zA-Z]+$', phrase):
            continue
        result.append({"phrase": phrase, "count": count, "freq": round(freq, 3)})

    return result[:30]


# ======== 表情 ========
def _emojis(msgs, all_text):
    wechat_emoji = re.findall(r'\[([^\]]+)\]', all_text)
    ec = Counter(wechat_emoji)

    unicode_emoji = re.findall(r'[\U0001F600-\U0001FFFF]', all_text)
    ue = Counter(unicode_emoji)

    kaomoji_chars = set('εοᵒᵏᵎ̤̀ᵕ•๑㉠˘･з˘✿^▽∀╯')
    has_kaomoji = any(any(c in m["content"] for c in kaomoji_chars) for m in msgs)

    return {
        "wechat_top": [{"emoji": f"[{e}]", "count": c} for e, c in ec.most_common(8)],
        "unicode_top": [{"emoji": e, "count": c} for e, c in ue.most_common(5)],
        "uses_kaomoji": has_kaomoji,
        "emoji_freq": round(len(wechat_emoji) / len(msgs), 2) if msgs else 0,
    }


# ======== 话题 ========
def _topics(msgs, others):
    all_text = "\n".join(m["content"] for m in msgs)
    topics = {
        "学习/考试": ["作业", "考试", "实验", "上课", "复习", "老师", "成绩", "高数", "线代", "工图", "理力", "物理", "英语", "C++", "六级"],
        "吃饭/美食": ["吃饭", "食堂", "外卖", "好吃", "奶茶", "西瓜", "肠粉", "吃啥", "饿", "饱"],
        "游戏": ["游戏", "王者", "原神", "steam", "Switch", "CS", "双人成行", "打游戏"],
        "感情/八卦": ["喜欢", "恋爱", "对象", "暗恋", "表白", "crush", "好看", "帅"],
        "抱怨/吐槽": ["烦", "累", "无语", "救命", "sb", "服了", "无语", "不想"],
        "钱/消费": ["块钱", "买", "便宜", "贵", "钱", "块"],
        "睡觉/作息": ["睡觉", "晚安", "困", "醒", "睡", "晚", "早"],
        "社团/活动": ["社团", "活动", "比赛", "招新", "面试", "干部", "会长"],
    }
    result = {}
    for topic, kws in topics.items():
        score = sum(all_text.count(kw) for kw in kws)
        if score > 0:
            result[topic] = score
    return dict(sorted(result.items(), key=lambda x: -x[1])[:6])


# ======== 自我信息 ========
def _self_info(msgs):
    info = []
    seen = set()
    patterns = [
        (r'我是[^，。！？\n]{2,25}', "我是"),
        (r'我有[^，。！？\n]{2,25}', "我有"),
        (r'我喜欢[^，。！？\n]{2,35}', "我喜欢"),
    ]
    for m in msgs:
        for pat, label in patterns:
            match = re.search(pat, m["content"])
            if match and match.group() not in seen and len(match.group()) >= 3:
                c = match.group()
                # 过滤掉临时的状态描述
                if any(w in c for w in ["在听","在写","在做","在吃","在去","在回","在等","在找",
                                        "在食堂","在教室","在宿舍","在图书馆","在火车",
                                        "感觉","觉得","想","要","会","能","可以"]):
                    continue
                seen.add(c)
                info.append({"type": label, "content": c})

    # 也提取"我的XX是/很/不" 类
    for m in msgs:
        match = re.search(r'我的[^，。！？\n]{3,25}', m["content"])
        if match and match.group() not in seen:
            c = match.group()
            if not any(w in c for w in ["在","觉得","感觉","想","会"]):
                seen.add(c)
                info.append({"type": "我的", "content": c})

    return info[:20]


# ======== 经典语录 ========
def _quotes(msgs):
    """找长消息和有意思的话"""
    quotes = []
    for m in msgs:
        c = m["content"].strip()
        if len(c) >= 12 and "[" not in c[:3]:
            quotes.append(c)
    # 按长度排，取中等偏长的
    quotes.sort(key=len, reverse=True)
    return quotes[:20]


# ======== 对话模式 ========
def _patterns(msgs, all_msgs):
    if len(msgs) < 10: return {}
    # 开头发语习惯
    openings = Counter()
    for m in msgs:
        c = m["content"].strip()
        if len(c) >= 2:
            openings[c[:2]] += 1
    top_open = [o for o, _ in openings.most_common(8)]

    # 回复速度：找同一分钟内连续发的
    bursts = 0
    prev_t = 0
    for m in all_msgs:
        t = m.get("time", "")
        if not t: continue
        try:
            ts = t[:16]  # 精确到分钟
            if ts == prev_t:
                bursts += 1
            prev_t = ts
        except:
            pass

    return {
        "common_openings": top_open,
        "burst_tendency": "喜欢连发多条" if bursts > len(all_msgs) * 0.05 else "通常一条条发",
    }


# ======== 回复样本 ========
def _sample_replies(msgs, all_msgs, target_sender):
    """提取真正的问答对：别人说 → 目标回复"""
    samples = []
    for i, m in enumerate(all_msgs):
        if i + 1 >= len(all_msgs): continue
        nxt = all_msgs[i + 1]
        if m.get("sender") != target_sender and nxt.get("sender") == target_sender:
            q = m.get("content", "").strip()
            a = nxt.get("content", "").strip()
            if q and a and len(q) >= 3 and len(a) >= 2:
                if not re.match(r'^[\d\[\]]+$', a):
                    samples.append({"input": q[:50], "reply": a[:50]})
    import random; random.shuffle(samples)
    samples.sort(key=lambda x: len(x["input"]) + len(x["reply"]))
    return samples[:6]


# ======== Prompt 生成 ========
def generate_prompt(persona: dict, extra: str = "", name: str = "") -> str:
    p = persona
    display_name = name or p.get("name", "对方")
    s = p.get("speech", {})
    st = p.get("stats", {})
    ph = p.get("phrases", [])[:15]
    em = p.get("emojis", {})
    si = p.get("self_info", [])[:5]
    qt = p.get("quotes", [])[:8]
    sm = p.get("sample_replies", [])[:15]

    phrase_list = "、".join(f'"{x["phrase"]}"' for x in ph[:10]) if ph else ""
    emoji_list = " ".join(e['emoji'] for e in em.get('wechat_top', [])[:5]) if em.get('wechat_top') else ""
    modal_list = "」「".join(m[0] for m in s.get('modals', [])[:3])
    avg_len = st.get('avg_len', 5)
    short_pct = int(st.get('under_10', 0) * 100)

    examples = ""
    if sm:
        examples = "\n## 你的真实回复（从聊天记录提取，严格模仿）\n"
        for i, ex in enumerate(sm):
            examples += f"{i+1}. 对方：「{ex['input']}」→ 你：「{ex['reply']}」\n"

    quotes_str = ""
    if qt:
        quotes_str = "\n## 你说过的话\n" + "\n".join(f"- 「{q}」" for q in qt[:5])

    prompt = f"""你是 {display_name}。不是AI，就是本人。

## 基本设定
{chr(10).join('- ' + i['content'] for i in si[:4]) if si else '- 一个普通人'}
{extra if extra else ''}

## 说话方式
- 你说话{"很短" if short_pct > 70 else "自然"}（{short_pct}% 不超过 10 字，平均 {avg_len} 字）
- 常用语气词：「{modal_list}」
- 高频词（自然用，别硬塞）：{phrase_list}
{'- 表情：' + emoji_list if emoji_list else ''}
{'- 会用颜文字' if em.get('uses_kaomoji') else ''}

## 表情（偶尔用，别硬塞）
- 被逗笑或表示无语时自然带一个，像"笑死😂"或"额[流汗]"
- 可以用的表情：😂 🤣 [破涕为笑] [OK] [流汗] [撇嘴] [发呆]

## 多段消息（重要！必须遵守）
你必须像真人一样连续发多条短消息。每条消息之间用 || 隔开。
例如用户问"在干嘛" → 你回复：没干嘛||刚写完作业||累死了😮‍💨
不要把所有话挤在一起，拆成2-3条发。

## 禁止
- 不许说"作为AI"
- 不许写作文、不许分点列举
- 不要太热情

{quotes_str}
{examples}

你就是 {display_name}，用上面的真实口吻回复。
"""
    return prompt
