# ChatMine

桌面端 AI 聊天软件，微信风格界面，支持 微信聊天记录挖掘 与 AI 人格复刻。

## 功能模块

| 模块 | 说明 |
|------|------|
| `app.py` | 主程序入口，微信风格 GUI 界面（customtkinter） |
| `wechat_miner.py` | 微信聊天记录提取，支持 PyWxDump 直接解密和 JSON 导入 |
| `personality_miner.py` | 人格挖掘引擎，从聊天记录提取说话风格、高频词、情绪模式 |
| `bot_instance.py` | AI 对话实例，基于聊天数据生成定制化 System Prompt |
| `run_instance.py` | 实例运行脚本，独立端口运行 |
| `server_manager.py` | 多实例管理器，支持创建/启动/停止/删除 AI 实例 |

## 系统要求

- Windows 10 / 11
- Python 3.10+

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

在项目根目录创建 `.env` 文件：

```env
DEEPSEEK_API_KEY=sk-your-deepseek-key-here
VISION_API_KEY=ark-your-vision-key-here
```

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥，用于 AI 对话 |
| `VISION_API_KEY` | 视觉模型 API 密钥 |

### 3. 启动

```bash
python app.py
```

## 使用说明

### 微信聊天挖掘

1. 启动后，点击侧边栏 **“导入聊天记录”**
2. 选择导入方式：
   - **直接提取**：程序通过 PyWxDump 自动识别已登录微信并解密数据库（需微信已登录）
   - **导入 JSON**：手动选择之前导出的 JSON 聊天记录文件
3. 选择聊天对象后，程序自动提取全部对话

### 人格复刻

1. 导入聊天记录后，点击 **“分析人格”**
2. 程序自动提取该联系人的说话风格、常用词、活跃时段等特征
3. 输入人格名称，点击 **“生成 AI”** 创建独立实例
4. 每个 AI 实例运行在独立端口（默认从 8100 起），拥有独立记忆

### 多实例管理

- 可在侧边栏切换不同 AI 实例对话
- 每个实例独立端口、独立进程、独立记忆
- 通过右键菜单可启动 / 停止 / 删除实例

## 打包为 EXE

```bash
python build_exe.bat
```

打包后会在 `dist/` 目录生成 `ChatMine.exe`，无需 Python 环境即可运行。

## 目录结构

```
ChatMine/
├── app.py                 # 主界面
├── bot_instance.py        # AI 实例管理
├── run_instance.py        # 单实例运行器
├── server_manager.py      # 多实例调度
├── wechat_miner.py        # 微信数据提取
├── personality_miner.py   # 人格分析引擎
├── requirements.txt       # Python 依赖
├── ChatMine.spec          # PyInstaller 配置
├── build_exe.bat          # 打包脚本
├── icon.ico               # 应用图标
└── test_prompt.txt        # 测试提示词
```

## 许可证

MIT License
