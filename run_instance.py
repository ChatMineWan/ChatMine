"""实例启动器——由 server_manager 子进程调用"""
import os
import sys
from bot_instance import AIInstance, run_instance

inst_id = os.environ["INSTANCE_ID"]
port = int(os.environ["INSTANCE_PORT"])
name = os.environ["INSTANCE_NAME"]

# 加载人格
prompt_path = os.environ["INSTANCE_PROMPT"]
with open(prompt_path, "r", encoding="utf-8") as f:
    persona_prompt = f.read()

instance = AIInstance(inst_id, port, persona_prompt, {"name": name})
print(f"[{name}] 启动在端口 {port}")
run_instance(instance)
