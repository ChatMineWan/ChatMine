"""
多实例管理器 — 创建/启动/停止/删除 AI 实例
每个实例独立端口、独立进程、独立记忆
"""
import os
import sys
import json
import time
import signal
import subprocess
import socket


INSTANCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instances")
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
START_PORT = 8100


def _load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"instances": {}, "next_port": START_PORT}


def _save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _port_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("0.0.0.0", port))
            return True
    except OSError:
        return False


def _find_available_port(start: int) -> int:
    port = start
    while not _port_available(port) and port < start + 100:
        port += 1
    return port


def create_instance(name: str, persona_prompt: str, context: dict = None) -> dict:
    """创建新的 AI 实例"""
    cfg = _load_config()
    instance_id = _sanitize_id(name)

    if instance_id in cfg["instances"]:
        i = 2
        while f"{instance_id}_{i}" in cfg["instances"]:
            i += 1
        instance_id = f"{instance_id}_{i}"

    port = _find_available_port(cfg.get("next_port", START_PORT))
    cfg["next_port"] = port + 1

    # 创建实例目录
    inst_dir = os.path.join(INSTANCES_DIR, instance_id)
    os.makedirs(inst_dir, exist_ok=True)

    # 保存人格文件
    prompt_path = os.path.join(inst_dir, "persona.txt")
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(persona_prompt)

    # 保存上下文
    ctx_path = os.path.join(inst_dir, "context.json")
    with open(ctx_path, "w", encoding="utf-8") as f:
        json.dump(context or {}, f, ensure_ascii=False, indent=2)

    # 记录到配置
    cfg["instances"][instance_id] = {
        "id": instance_id,
        "name": name,
        "port": port,
        "status": "stopped",
        "pid": None,
        "prompt_path": prompt_path,
        "ctx_path": ctx_path,
        "context": context or {},
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_config(cfg)
    return cfg["instances"][instance_id]


def start_instance(instance_id: str) -> bool:
    """启动指定实例"""
    cfg = _load_config()
    if instance_id not in cfg["instances"]:
        return False

    inst = cfg["instances"][instance_id]
    if inst["status"] == "running" and inst.get("pid"):
        try:
            os.kill(inst["pid"], 0)
            return True  # 已经在运行
        except OSError:
            pass

    # 启动子进程
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_instance.py")
    env = os.environ.copy()
    env["INSTANCE_ID"] = instance_id
    env["INSTANCE_PORT"] = str(inst["port"])
    env["INSTANCE_NAME"] = inst["name"]
    env["INSTANCE_PROMPT"] = inst["prompt_path"]

    proc = subprocess.Popen(
        [sys.executable, runner],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    inst["status"] = "running"
    inst["pid"] = proc.pid
    cfg["instances"][instance_id] = inst
    _save_config(cfg)

    time.sleep(1)
    return True


def stop_instance(instance_id: str):
    """停止指定实例"""
    cfg = _load_config()
    if instance_id not in cfg["instances"]:
        return
    inst = cfg["instances"][instance_id]
    if inst.get("pid"):
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(inst["pid"])], capture_output=True)
            else:
                os.kill(inst["pid"], signal.SIGTERM)
        except:
            pass
    inst["status"] = "stopped"
    inst["pid"] = None
    _save_config(cfg)


def delete_instance(instance_id: str):
    """删除实例及其所有数据"""
    stop_instance(instance_id)
    cfg = _load_config()
    if instance_id in cfg["instances"]:
        del cfg["instances"][instance_id]
    _save_config(cfg)
    # 删除文件
    import shutil
    inst_dir = os.path.join(INSTANCES_DIR, instance_id)
    if os.path.exists(inst_dir):
        shutil.rmtree(inst_dir)


def list_instances() -> list[dict]:
    cfg = _load_config()
    return list(cfg["instances"].values())


def get_instance(instance_id: str) -> dict | None:
    cfg = _load_config()
    return cfg["instances"].get(instance_id)


def _sanitize_id(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in "._-")[:20] or "ai"


def stop_all():
    for inst in list_instances():
        if inst["status"] == "running":
            stop_instance(inst["id"])
