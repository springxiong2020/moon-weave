<div align="center">

<img src="web/logo.svg" alt="Agent Session Hub Logo" width="88" />

# Agent Session Hub · 本地智能体会话台

**把散落在终端里的对话，整理成你的开发记忆。**

一个轻量的本地会话管理工具，将 Codex CLI 与 Claude Code 的对话、工具调用和项目线索汇集到同一个界面。

![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab?logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003b57?logo=sqlite&logoColor=white)
![JavaScript](https://img.shields.io/badge/JavaScript-原生-f7df1e?logo=javascript&logoColor=black)
![Local](https://img.shields.io/badge/运行方式-本地模式-16a085)

[功能一览](#功能一览) · [快速开始](#快速开始) · [会话接入](#会话接入) · [配置与数据](#配置与数据) · [开发指南](#开发指南)

</div>

---

## 为什么是会话台

一次排查、一个实现方案、一段值得保留的工具调用，常常留在不同终端和项目的会话文件里。ASH 把这些记录整理到一起，让你可以沿着项目、关键词和时间，找回当时的思路。

- **两种来源，一个入口**：统一查看 Codex CLI 与 Claude Code 的本地记录。
- **顺着项目找回上下文**：按工作目录筛选，搜索对话内容，展开查看工具调用。
- **留下值得保留的思考**：收藏、归档，或导出为 Markdown 与 JSON。
- **轻量地留在本机**：Python 标准库即可启动，无需 API Key、模型服务或前端构建。

## 功能一览

| 功能 | 可以做什么 |
| :--- | :--- |
| 📚 **会话概览** | 查看会话总数、关联项目与近 7 天更新数量，按文件更新时间排序 |
| 🗂️ **项目筛选** | 按工作目录聚合项目，结合 Codex、Claude Code 来源筛选 |
| 🔎 **内容搜索** | 搜索标题、工作目录及已解析消息，支持中文，按 `/` 聚焦搜索框 |
| 💬 **记录回看** | 查看对话、工具调用、来源文件、会话 ID、模型与已有 token 用量 |
| ⭐ **收藏归档** | 标记常用会话，将暂时不需要的记录收进「已归档」 |
| 📤 **导出留存** | 下载 Markdown 对话或包含会话信息的 JSON |
| 🗑️ **会话清理** | 确认后永久删除来源 JSONL，同时清理对应索引和管理状态 |
| 🔄 **增量扫描** | 启动时扫描，手动刷新时跳过未变化文件，查看数据源与扫描警告 |

搜索采用不区分大小写的子串匹配。更新时间取源文件修改时间，项目取记录中的 `cwd`；token 总量仅展示 Codex 日志中已有的数据，缺失时留空，不估算费用。

## 快速开始

准备 Python 3.10+（包含标准库 `sqlite3`）和现代浏览器。永久删除依赖 POSIX 文件系统接口，建议在 Linux、macOS 或 WSL 中运行。

### 1. 启动本地服务

从仓库根目录进入子工程，无需安装第三方运行依赖：

```bash
cd agent-session-hub
python3 server.py
```

打开 <http://127.0.0.1:8000/>。服务启动时会扫描默认会话目录，按 `Ctrl+C` 停止。

### 2. 体验示例会话

没有本地会话时，可以使用演示模式：

```bash
python3 server.py --demo
```

演示模式生成五条示例会话，只读取示例目录，使用独立数据库保存索引与管理状态。退出后去掉 `--demo` 即可切回真实会话。

### 3. 浏览与整理

在左侧选择项目，或在列表上方按来源和关键词筛选。选中会话后，可切换「对话记录」「工具调用」「会话信息」。

详情右上角提供收藏、归档、Markdown 导出和删除操作；JSON 下载入口位于「会话信息」。CLI 产生新记录后，点击「扫描会话」更新列表。

## 会话接入

### 默认目录

ASH 递归扫描来源目录中的 `*.jsonl` 文件：

| 来源 | 默认目录 | 环境变量覆盖 |
| :--- | :--- | :--- |
| Codex CLI | `~/.codex/sessions` | `$CODEX_HOME/sessions` |
| Claude Code | `~/.claude/projects` | `$CLAUDE_CONFIG_DIR/projects` |

环境变量指定的是 CLI 配置根目录；命令行参数则直接指定会话扫描目录：

```bash
python3 server.py \
  --codex-dir "$HOME/.codex/sessions" \
  --claude-dir "$HOME/.claude/projects"
```

在「数据源与设置」中可以查看实际扫描路径、目录连接状态与警告。目录不存在时仍可启动服务；修改来源目录需要调整启动参数并重启。

### 解析与扫描规则

- Codex 读取会话元信息、模型上下文、消息、工具调用及其结果；Claude Code 读取用户与助手消息中的文本及工具记录，并按消息 UUID 去重。
- 会话标题取第一条用户消息的首行，最多 100 个字符；没有用户消息时使用文件名。
- 根据文件修改时间和大小识别变化；变化文件会重新解析，未变化文件跳过。已不存在的源文件会从会话索引移除。
- 启动后通过「扫描会话」手动更新，不持续监听文件变化。无效 JSON 等解析错误会计数并跳过，在详情底部显示。
- 扫描忽略指向配置根目录外的文件符号链接。

当前适配的是代码中支持的 JSONL 事件结构，CLI 日志格式变化可能需要更新解析器。消息以纯文本展示；ASH 用于回看与整理记录，不提供继续对话或执行工具的入口。

## 配置与数据

所有配置通过启动参数传入，使用 `python3 server.py --help` 查看说明。

| 参数 | 用途 / 默认值 |
| :--- | :--- |
| `--port` | 本地 HTTP 端口，默认 `8000` |
| `--codex-dir` | Codex 会话目录，默认见上表 |
| `--claude-dir` | Claude Code 会话目录，默认见上表 |
| `--data-dir` | 本地数据目录，默认子工程下的 `.data/` |
| `--demo` | 仅加载生成的示例会话，忽略真实来源目录参数 |

例如，使用其他端口和数据目录启动：

```bash
python3 server.py --port 8080 --data-dir /tmp/ash-data
```

### 索引与管理状态

| 文件 | 保存内容 |
| :--- | :--- |
| `.data/index.sqlite` | 正常模式的消息索引、元数据、收藏与归档标记 |
| `.data/demo.sqlite` | 演示模式的索引与管理状态 |
| `.data/examples/` | 演示模式生成的 JSONL 文件 |

收藏与归档独立于原始记录保存，归档只调整 ASH 中的显示状态，不移动源文件。删除数据库后可重新扫描生成索引，但收藏与归档状态会丢失。索引包含消息正文，备份时应按原始会话数据对待；默认 `.data/` 已加入 Git 忽略规则。

### 永久删除

**永久删除会直接移除来源 JSONL，不进入回收站，无法撤销。** 需要保留内容时请先导出，并结束对应会话的 CLI 进程，避免文件继续写入或重新生成。

删除前会核对详情中的索引版本、文件修改时间和大小，并检查路径及文件类型，拒绝通过符号链接删除。文件发生变化时，需要重新扫描并确认；文件删除失败时保留索引与管理状态。ASH 不负责清理 CLI 的其他元数据或缓存。

### 本机访问

服务固定监听 `127.0.0.1`，允许通过对应端口的 `localhost` 或 `127.0.0.1` 访问。页面脚本、样式和图标均从本机加载。

服务校验 Host、Origin 与跨站请求标识，写请求需带 `X-ASH: 1`。当前没有账户与登录系统，适合个人本机使用，不提供公网或多人部署配置。

## 开发指南

### 技术栈

Python 标准库 · SQLite · ThreadingHTTPServer · 原生 HTML / CSS / JavaScript

### 目录结构

```text
agent-session-hub/
├── server.py               # 日志解析、SQLite 索引、HTTP API 与命令行入口
├── web/
│   ├── index.html          # 页面结构与操作弹窗
│   ├── app.js              # 搜索、筛选、详情与管理交互
│   ├── style.css           # 页面样式
│   ├── logo.svg            # ASH 标识
│   └── icons/              # 本地图标、来源清单与第三方许可
├── tests/
│   ├── test_service.py     # 解析、索引、HTTP、删除与静态资源测试
│   └── test_ui.mjs         # 基于模拟 DOM 的删除交互测试
└── .data/                  # 运行时生成的索引与示例文件，不纳入版本控制
```

### 常用命令

以下命令在子工程目录执行：

| 命令 | 用途 |
| :--- | :--- |
| `python3 server.py` | 扫描真实会话并启动本地服务 |
| `python3 server.py --demo` | 使用示例会话启动 |
| `python3 server.py --port 8080` | 指定服务端口 |
| `python3 server.py --help` | 查看启动参数 |
| `python3 -m unittest discover -s tests -p 'test_*.py'` | 运行 Python 测试 |
| `node --test tests/test_ui.mjs` | 运行前端交互测试 |

Python 测试使用临时目录和本机 HTTP 服务，无需读取真实会话。前端测试使用 Node.js 内置测试运行器与模拟 DOM，建议 Node.js 18+，无需安装 npm 依赖；页面布局与浏览器兼容性仍需在真实浏览器中检查。

### 参与改进

欢迎通过 Issue 反馈问题或提交 Pull Request。提交时请说明行为变化与验证方式；修改解析逻辑时提供脱敏后的 JSONL 样例，涉及页面变化时附上截图。

## 许可

本项目属于 [moon-weave · 织月](../README.md)，项目代码遵循仓库根目录的 [LICENSE](../LICENSE)。第三方图标遵循各自许可，来源与清单见 [manifest.json](web/icons/manifest.json)，许可文本见 [LICENSE-Lucide.txt](web/icons/LICENSE-Lucide.txt) 与 [LICENSE-Remix.txt](web/icons/LICENSE-Remix.txt)。

---

<div align="center">

**让每一次思考，都有迹可循。**

</div>
