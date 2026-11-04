# 🔐 Blitz Credential Manager (`blitz-cred-manager`)

基于 **Blitz.cloud** 部署的轻量级凭据管理器与 **Remote MCP Server**，支持邀请码注册、环境变量凭据管理、凭据文件（`cookies.txt`、`config.json` 等）托管，并通过 **Remote MCP 临时令牌 (`ctk_...`) + `cred-cli` 命令行工具** 为 AI Agent 提供安全、零上下文泄露的凭据拉取能力。

---

## ✨ 核心特性

1. **账号与权限体系**
   - **内置管理员账户**：部署自动初始化 `admin` 账号与初始管理员密码。
   - **邀请码注册**：凭邀请码 `snh48ssr` 即可自助注册新账户。
   - **密码修改**：登录后随时修改登录密码，采用 `PBKDF2-HMAC-SHA256` 加盐哈希持久化于 `/app/data/cred_manager.db`。

2. **双模态凭据托管**
   - **环境变量凭据 (`Env Vars`)**：支持单条增删改查、一键显示/复制，以及 `.env` 格式批量解析导入。
   - **凭据文件托管 (`Credential Files`)**：支持拖拽/选择上传任意凭据文件（如 `cookies.txt`、`config.json`、SSH Key 等），或直接在浏览器内在线编写/编辑文件内容。

3. **Remote MCP Server (面向 AI Agents)**
   - 同时支持 **Streamable HTTP** (`/mcp`、`/mcp?key=<mcp_key>`) 与 **HTTP+SSE** (`/sse`、`/messages`) 两种标准 MCP 传输协议。
   - **强制前置引导**：MCP `InitializeResult.instructions` 与所有 Tool 描述均明确提示 AI Agent 在拉取凭据前**必须先安装 `cred-cli`**。
   - **MCP 工具列表**：
     - `get_cli_install_guide`：获取 `cred-cli` 的跨平台安装命令与二进制下载地址。
     - `list_credentials`：列出当前可用的环境变量 Key 列表与凭据文件名列表（不暴露敏感明文）。
     - `get_temp_token`：签发短效临时令牌（`ctk_...`，内嵌服务端地址路由信息），并返回可直接执行的 `cred-cli` 命令。

4. **跨平台 `cred-cli` 命令行工具**
   - 提供 **Go 静态编译二进制**（Linux `amd64/arm64`、Windows `amd64`、macOS `amd64/arm64`）与 **零依赖 Python 3 脚本 (`cred-cli.py`)**。
   - 同时托管于 **Blitz 应用前端 (`/cli/...`)** 与 **公开 GitHub 仓库**。

---

## 🚀 快速安装 `cred-cli`

### 方式一：从 Blitz 应用前端直接安装
```bash
# Linux / macOS
curl -fsSL https://cred-manager.blitz48.blitz.cloud/cli/install.sh | sh

# Windows PowerShell
irm https://cred-manager.blitz48.blitz.cloud/cli/install.ps1 | iex
```

### 方式二：从 GitHub 公开仓库安装
```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/NannaOlympicBroadcast/blitz-cred-manager/main/cli/install.sh | sh

# Windows PowerShell
irm https://raw.githubusercontent.com/NannaOlympicBroadcast/blitz-cred-manager/main/cli/install.ps1 | iex
```

### 方式三：零依赖 Python 脚本直接使用
```bash
curl -fsSL https://raw.githubusercontent.com/NannaOlympicBroadcast/blitz-cred-manager/main/cli/cred-cli.py -o cred-cli.py
python3 cred-cli.py --help
```

---

## 🛠️ AI Agent 工作流 (`MCP` + `cred-cli`)

1. **连接 Remote MCP**：
   ```json
   {
     "mcpServers": {
       "blitz-cred-manager": {
         "url": "https://cred-manager.blitz48.blitz.cloud/mcp?key=<YOUR_MCP_KEY>"
       }
     }
   }
   ```
2. **通过 MCP 获取临时 Token**：
   AI Agent 调用 `get_temp_token` 工具，获得形如 `ctk_...` 的临时令牌（有效期默认 15 分钟）。
3. **通过 `cred-cli` 结合临时 Token 拉取凭据或文件**：
   ```bash
   # 查看所有可用环境变量 Key 与凭据文件列表
   cred-cli list --token <TEMP_TOKEN>

   # 拉取所有环境变量并保存为 .env
   cred-cli env --token <TEMP_TOKEN> -o .env

   # 拉取单个环境变量的值
   cred-cli env --token <TEMP_TOKEN> --key OPENAI_API_KEY

   # 拉取指定凭据文件 (如 cookies.txt, config.json)
   cred-cli file cookies.txt --token <TEMP_TOKEN> -o ./cookies.txt
   cred-cli file config.json --token <TEMP_TOKEN> -o ./config.json

   # 一键拉取所有环境变量与凭据文件到指定目录
   cred-cli pull-all --token <TEMP_TOKEN> --dir ./credentials

   # 将环境变量凭据注入子进程并执行命令
   cred-cli run --token <TEMP_TOKEN> -- python main.py
   ```

---

## 💓 容器内防休眠保活机制 (In-Container Keep-Alive) 与配额评估

Blitz.cloud 免费版默认在 **15 分钟无公网访问**后将容器置为休眠状态（`sleepsAfterMinutes: 15`）。本项目在**容器内部直接运行独立保活守护脚本** `/app/scripts/keepalive.py`（不使用 GitHub CI），且**不会耗尽月度请求次数或运行机时**：

### 1. 配额与机时安全说明
- **Blitz.cloud 配额模型**：仅限制并发应用数（`5` 个）、并发内存池（`512 MB`，本应用仅占 `128 MB`）、持久化存储（`10 GB`）及代码构建时长（`buildMinutes`: `300 分钟/月`）。**Blitz 不限制每月 HTTP 请求次数，也不限制容器每月在线运行时长**。
- **零外部 CI 消耗**：保活脚本直接在 Blitz 容器内以独立后台进程运行，不占用任何 GitHub Actions CI 时间。

### 2. 容器内保活运行方式
- **`Dockerfile` 启动命令**：
  ```dockerfile
  CMD ["sh", "-c", "python /app/scripts/keepalive.py --url ${KEEPALIVE_URL} --interval ${KEEPALIVE_INTERVAL} & exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
  ```
- **工作原理**：容器启动后，`/app/scripts/keepalive.py` 在容器内作为守护进程运行，每 **10 分钟（600 秒）** 向公网地址 `https://cred-manager.blitz48.blitz.cloud/api/health` 发起一次 HTTPS 请求，经由 Blitz 边缘网关回环访问自身以刷新 `lastTrafficAt`。
- **状态查询接口**：访问 `GET https://cred-manager.blitz48.blitz.cloud/api/keepalive/status` 可实时查看容器内保活脚本的累计 Ping 次数（`ping_count`）与最近一次触发时间（`last_ping_at`）。

---

## 📂 目录结构

```text
blitz-cred-manager/
├── app/
│   ├── main.py            # FastAPI 后端 + Remote MCP Server + 保活状态监控
│   └── static/
│       └── index.html     # Web 管理控制台前端 (登录/注册/改密/环境变量/凭据文件/MCP配置)
├── cli/
│   ├── main.go            # Go 原生 CLI 源码
│   ├── cred-cli.py        # Python 3 零依赖通用 CLI 脚本
│   ├── install.sh         # Linux / macOS 一键安装脚本
│   ├── install.ps1        # Windows PowerShell 一键安装脚本
│   └── bin/               # 预编译跨平台二进制 (Linux/Windows/macOS)
├── scripts/
│   ├── keepalive.py       # 容器内直接运行的 Python 防休眠保活脚本
│   ├── keepalive.ps1      # Windows PowerShell 保活脚本
│   └── keepalive.sh       # Linux / macOS Shell 保活脚本
├── Dockerfile             # Blitz.cloud 容器构建文件 (内置启动 keepalive.py + 持久化 /app/data)
├── requirements.txt       # Python 依赖
└── README.md              # 项目文档
```
