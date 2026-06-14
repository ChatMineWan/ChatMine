"""
微信聊天记录提取器 — 使用 PyWxDump API
支持直接提取 + JSON 导入
"""
import os, json, glob, re, sqlite3


def find_wechat_accounts() -> list[dict]:
    """扫描微信账号，优先用 PyWxDump API"""
    # 尝试 PyWxDump
    try:
        from pywxdump import get_wx_info
        wx_info = get_wx_info()
        if wx_info and "data" in wx_info:
            accounts = []
            for item in wx_info.get("data", []):
                accounts.append({
                    "wxid": item.get("wxid", ""),
                    "name": item.get("name", item.get("wxid", "")),
                    "key": item.get("key", ""),
                    "db_path": item.get("db_path", ""),
                })
            if accounts:
                return accounts
    except Exception:
        pass

    # 降级：扫描文件夹
    bases = [
        os.path.expandvars(r"%USERPROFILE%\Documents\xwechat_files"),
        os.path.expandvars(r"%USERPROFILE%\Documents\WeChat Files"),
    ]
    accounts = []
    for base in bases:
        if not os.path.exists(base): continue
        for folder in os.listdir(base):
            full = os.path.join(base, folder)
            if not os.path.isdir(full): continue
            for sub in ["db_storage", "Msg"]:
                cand = os.path.join(full, sub)
                if os.path.isdir(cand):
                    accounts.append({"wxid": folder, "name": folder, "key": "", "db_path": cand, "base_path": full})
                    break
    return accounts


def extract_chatlogs(wxid: str, contact_name: str = None) -> list[dict]:
    """提取聊天记录 — 优先 PyWxDump 解密，降级直接读 DB / JSON"""
    # 方式1：PyWxDump API
    try:
        from pywxdump import get_wx_info, batch_decrypt
        import tempfile
        wx_info = get_wx_info()
        if wx_info and "data" in wx_info:
            for item in wx_info["data"]:
                if item.get("wxid") == wxid:
                    key = item.get("key", "")
                    db_path = item.get("db_path", "")
                    if key and db_path and os.path.exists(db_path):
                        with tempfile.TemporaryDirectory() as tmp:
                            batch_decrypt(key, db_path, tmp)
                            # 找解密后的消息数据库
                            for f in glob.glob(os.path.join(tmp, "**", "*MSG*"), recursive=True):
                                msgs = _read_db(f, contact_name)
                                if msgs:
                                    return msgs
    except Exception:
        pass

    # 方式2：直接读已解密的 DB
    accs = find_wechat_accounts()
    for acc in accs:
        if acc["wxid"] == wxid:
            db = acc.get("db_path", "")
            if db:
                for f in glob.glob(os.path.join(db, "*MSG*")):
                    try:
                        msgs = _read_db(f, contact_name)
                        if msgs: return msgs
                    except:
                        pass

    # 方式3：同目录 JSON
    for jf in glob.glob("*.json"):
        try:
            return import_json(jf)
        except:
            pass

    return []


def _read_db(db_path: str, contact_name: str = None) -> list[dict]:
    """读取 SQLite 消息数据库"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    msg_tables = [t for t in tables if "msg" in t.lower() and "dels" not in t.lower()]
    if not msg_tables:
        conn.close()
        return []

    msgs = []
    for tbl in msg_tables[:2]:
        try:
            rows = cur.execute(f"SELECT * FROM {tbl} ORDER BY CreateTime DESC LIMIT 10000").fetchall()
            cols = [d[0] for d in cur.description]
            for row in rows:
                d = dict(zip(cols, row))
                content = str(d.get("StrContent", d.get("Content", ""))).strip()
                if not content or content.startswith("<"):
                    continue
                is_send = d.get("IsSender", 0)
                sender = "我" if is_send == 1 else (contact_name or "对方")
                msgs.append({
                    "sender": sender,
                    "content": content,
                    "time": str(d.get("CreateTime", "")),
                    "is_send": is_send,
                })
        except:
            pass
    conn.close()
    return msgs


def import_json(filepath: str) -> list[dict]:
    """从 JSON 文件导入聊天记录，自动适配常见格式"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    msgs = []
    if isinstance(data, dict) and "messages" in data:
        msgs = data["messages"]
    elif isinstance(data, list):
        msgs = data
    else:
        return []

    # 字段映射：统一转为 sender / content / time
    result = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        content = (m.get("content") or m.get("StrContent") or "").strip()
        if not content:
            continue
        # 只保留文本消息
        mtype = m.get("type", m.get("MsgType", ""))
        if "文本" not in str(mtype) and "text" not in str(mtype).lower() and mtype:
            continue
        result.append({
            "sender": m.get("sender") or m.get("senderDisplayName") or m.get("talker", ""),
            "content": content,
            "time": str(m.get("time") or m.get("formattedTime") or m.get("createTime") or ""),
            "is_send": m.get("is_send") if "is_send" in m else m.get("isSend", -1),
        })
    return result
