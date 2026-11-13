<div align="center">

<img src="insight/static/logo.svg" alt="Nginx Insight Logo" width="88" />

# Nginx Insight · 春天熊の观测站

**从访问日志，看见站点的每一次连接。**

一个轻量的 Nginx 日志观测工具，将访问记录整理为地域分布、流量趋势与可检索的运行线索。

![Python](https://img.shields.io/badge/Python-3.11%2B-3776ab?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-4479a1?logo=mysql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-dc382d?logo=redis&logoColor=white)
![ECharts](https://img.shields.io/badge/ECharts-5.6-aa344d)

[功能一览](#功能一览) · [快速开始](#快速开始) · [日志接入](#日志接入) · [生产部署](#生产部署) · [开发指南](#开发指南)

</div>

---

## 为什么是观测站

站点的访问日志里，藏着访客从哪里来、哪些页面被频繁打开，以及请求在何处遇到了问题。观测站把这些分散的记录放进同一个界面，让日常查看和排查有据可循。

- **一眼看见访问**：在地图、趋势与站点统计中了解访问情况。
- **公开与管理分开**：访客可以浏览汇总数据，管理员登录后查看原始日志与 IP 信息。
- **持续采集**：记录文件读取进度，支持日志轮转、截断检测与压缩归档导入。
- **保留原始时间**：日志打印几点，就按几点存储与展示，不转换时区。

## 功能一览

| 功能 | 可以做什么 |
| :--- | :--- |
| 🗺️ **访客地域** | 展示中国省份访问地图、地域排行，区分境内、境外及未知来源 |
| 📈 **访问概览** | 查看请求量、独立 IP、响应耗时、流量、状态码分布与访问趋势 |
| 🌐 **站点筛选** | 按日志文件识别站点，切换站点与最近 1 小时、24 小时、7 天、30 天的统计 |
| 🔎 **日志检索** | 管理员分页检索 access、error、非 200、慢请求与解析异常日志，复制原始记录 |
| 📍 **定位队列** | 查看 IP 定位状态，重新调度单个失败 IP 或全部失败任务 |
| 🛠️ **运行概况** | 查看文件读取进度、解析异常数量、定位任务与当前运行配置 |

“独立 IP”按所选时间范围内的不同来源 IP 计数；慢请求指耗时至少 1 秒的 access 请求。页面成功率按非 5xx 请求占比计算，错误请求数按 HTTP 状态码至少 400 计算。

## 快速开始

准备 Linux、Python 3.11+、MySQL 与 Redis，并确保运行账号能读取 Nginx 日志目录。当前代码使用 `datetime.UTC`，因此实际运行需要 Python 3.11+，尽管包元数据声明的最低版本为 3.10。

### 1. 安装与配置

在项目根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
```

先在 MySQL 中创建数据库，再将连接地址与账号写入 `.env`。应用会创建表，但不会创建数据库或数据库用户。

```sql
CREATE DATABASE nginx_insight CHARACTER SET utf8mb4;
```

最少需要调整以下配置：

```dotenv
DATABASE_URL=mysql+pymysql://your-username:your-password@127.0.0.1:3306/nginx_insight?charset=utf8mb4
REDIS_URL=redis://127.0.0.1:6379/0
NGINX_LOG_DIR=/var/log/nginx
BAIDU_MAP_AK=your-baidu-server-ak
```

百度 AK 需要开通「普通 IP 定位」，并配置与服务器出口 IP 匹配的白名单。未配置 AK 时，仍可采集日志和查看统计，但不能启动定位进程。

### 2. 初始化并启动

```bash
nginx-insight init-db
```

在三个终端中分别激活虚拟环境，从项目根目录启动各进程：

```bash
# 终端一：持续采集活动日志
nginx-insight collect
```

```bash
# 终端二：处理 IP 定位任务
nginx-insight geo
```

```bash
# 终端三：启动本地 Web 服务
nginx-insight serve --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/>。需要导入已有轮转日志时，先停止 `collect`，执行 `nginx-insight import`，完成后再启动持续采集。同一数据库在同一主机上只允许一个采集或导入进程。

### 3. 启用管理员登录

在 `.env` 中配置以下三项，重启 Web 服务：

```dotenv
DASHBOARD_USER=your-admin-name
DASHBOARD_PASSWORD=your-admin-password
SESSION_SECRET=your-independent-random-secret
```

使用以下命令生成独立随机密钥，并将结果填入 `SESSION_SECRET`：

```bash
python -c 'import secrets; print(secrets.token_hex(32))'
```

三项完整配置后才启用登录。管理员会话有效期为 8 小时；修改账号、密码或会话密钥会使原有会话失效。HTTPS 部署应设 `SESSION_COOKIE_SECURE=true`，本地 HTTP 调试保持 `false`。

## 日志接入

### 文件命名与格式

采集器扫描指定目录的直接子文件，按文件名识别类型与站点：

| 文件名示例 | 识别结果 |
| :--- | :--- |
| `access.log` / `error.log` | 默认站点 `default` |
| `lab.springxiong.cn.access.log` | 站点 `lab.springxiong.cn` 的访问日志 |
| `lab.springxiong.cn.error.log` | 站点 `lab.springxiong.cn` 的错误日志 |
| `lab.springxiong.cn.access.log.1` / `.2.gz` | 轮转或压缩归档，使用 `import` 导入 |

参考 [nginx.conf](nginx.conf)，将 `log_format main` 合并到现有 Nginx 的 `http` 块中，并在对应站点配置日志路径：

```nginx
access_log /var/log/nginx/lab.springxiong.cn.access.log main buffer=32k flush=3s;
error_log  /var/log/nginx/lab.springxiong.cn.error.log warn;
```

仓库中的 `nginx.conf` 是配置片段，包含 `http` 块，不是完整的系统配置文件。合并后执行 `nginx -t`，通过检查再重新加载 Nginx。

access 日志需要使用示例中的 `main` 格式，包含末尾的 `rt=$request_time`：

```text
123.88.43.99 - - [13/Nov/2026:06:49:44 +0800] "GET / HTTP/2.0" 200 123 "-" "Mozilla/5.0" rt=0.125
```

标准 Nginx 的 error 日志采用固定文本格式，不能使用 `log_format` 自定义。解析器按以下结构读取，连接编号与客户端上下文可以缺省：

```text
2026/11/12 07:40:09 [error] 166554#166554: *15538 open() "/var/www/index.html" failed (2: No such file or directory), client: 123.88.43.99, server: lab.springxiong.cn, request: "GET / HTTP/2.0", host: "lab.springxiong.cn"
```

### 时间与采集规则

- access 中的 `06:49:44 +0800` 入库为 `06:49:44`，error 时间同样按原文保留；统计窗口使用应用服务器本地时间，应用与 Nginx 应使用相同的本地时钟与时区。
- access 日志级别为 `access`；error 日志保留原有级别，接口对空级别默认返回 `access`。
- 完整换行后才读取一条记录；解析失败仍保留原文和异常原因，时间回退为采集时的服务器时间。
- 文件进度与日志记录在同一事务中提交，利用文件标识、代次和偏移避免重复写入；不要在不同主机上同时采集同一数据库。
- `NGINX_IP_WHITELIST` 支持逗号或空白分隔的 IP/CIDR。命中后跳过日志及新定位任务入库，但继续推进文件偏移，不删除已有数据。

## 配置与数据

完整配置及注释见 [.env.example](.env.example)。各进程启动时读取 `.env`，修改后需重启对应进程；从 `.env` 读取的 IP 豁免规则会在下次采集扫描时重新加载，独立环境变量覆盖的规则需重启进程。生产环境建议固定工作目录。

| 配置 | 用途 / 默认值 |
| :--- | :--- |
| `DATABASE_URL` | MySQL 连接地址，保存日志、文件游标与 IP 定位状态 |
| `REDIS_URL` | Redis 连接地址，缓存 IP 定位结果 |
| `BAIDU_MAP_AK` | 百度服务端 IP 定位 AK |
| `GEO_QPS` | 单个定位进程的请求速率，默认每秒 1 次，支持小数 |
| `GEO_COOLDOWN_SECONDS` / `GEO_MAX_COOLDOWN_SECONDS` | 限流冷却初始值 / 最大值，默认 60 / 900 秒 |
| `POLL_INTERVAL` | 活动日志扫描间隔，默认 1 秒 |
| `NGINX_LOG_DIR` / `NGINX_IP_WHITELIST` | 日志目录 / 跳过采集的 IP 或网段 |
| `URL_PREFIX` | Web 路径前缀，默认为空，支持 `/nginx-insight` 等路径 |
| `DASHBOARD_USER` / `DASHBOARD_PASSWORD` / `SESSION_SECRET` | 管理员账号、密码与共享会话密钥 |
| `SESSION_COOKIE_SECURE` | HTTPS Cookie 开关，默认 `false` |
| `LOG_LEVEL` / `LOG_FILE` | 服务日志级别 / 输出文件；文件留空则仅输出 stderr |

MySQL 中的 `file_cursors`、`log_events`、`ip_locations` 分别保存读取进度、日志记录与定位结果。Redis 定位缓存有效期为 30 天，定位成功后约 30 天重新查询；内网或保留地址直接分类，不请求百度。Redis 故障时会回退到百度请求，限流与失败任务会延后重试。定位任务的调度时间使用 UTC，与原始日志时间分开处理。

公开统计 API 不返回来源 IP、请求 URL 或原始日志，详细信息仅对管理员开放。备份时应同时保留数据库与原始日志；项目目前未提供自动清理、备份恢复或数据库结构迁移命令，`init-db` 仅创建缺失的表。

## 生产部署

使用 Gunicorn 运行 Web 服务，采集与定位各作为独立常驻进程运行，可交由 systemd 或其他进程管理器管理。所有进程应使用同一份连接配置、固定工作目录与虚拟环境，所有 Web worker 使用相同的 `SESSION_SECRET`。

```bash
.venv/bin/gunicorn --workers 2 --bind 127.0.0.1:8000 'insight.web:create_app()'
```

### Nginx 子路径代理

在 `.env` 中设置：

```dotenv
URL_PREFIX=/nginx-insight
SESSION_COOKIE_SECURE=true
```

在 HTTPS 站点的 `server` 块中添加代理规则，保留请求路径前缀：

```nginx
location = /nginx-insight {
    return 302 /nginx-insight/;
}

location /nginx-insight/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

`proxy_pass` 地址末尾不要添加 `/`，否则此前缀会被替换。根路径部署则保持 `URL_PREFIX` 为空，并代理 `location /`。修改前缀后重启 Web 服务；HTTPS 开关应与浏览器实际访问协议一致。

### 发布打包

```bash
python scripts/build_release.py
```

生成 `dist/nginx-insight-v1.0.0.tar.gz`，包含运行源码、页面、静态资源与配置示例，不包含 `.env`、虚拟环境和测试文件。同名发布包已存在时会拒绝覆盖，需更新版本号或通过 `--output` 指定其他目录。当前打包脚本不包含本文档与 Nginx 配置片段，部署时请另外保留。

## 开发指南

### 技术栈

Python · Flask · SQLAlchemy · PyMySQL · Redis · Requests · Gunicorn · 原生 JavaScript · ECharts

### 目录结构

```text
nginx-insight/
├── insight/
│   ├── cli.py               # 命令行入口与进程循环
│   ├── collector.py         # 增量采集、文件进度与轮转处理
│   ├── parser.py            # access / error 日志解析
│   ├── db.py                # 数据模型与数据库连接
│   ├── geo.py               # 百度 IP 定位、缓存与限流重试
│   ├── auth.py              # 管理员会话与 CSRF 校验
│   ├── web.py               # 页面入口与统计、管理 API
│   ├── logging_config.py    # 服务日志、轮转与敏感信息脱敏
│   ├── templates/           # 页面模板
│   └── static/              # 页面脚本、样式、地图与 ECharts
├── scripts/                 # 静态资源获取与发布打包
├── tests/                   # Python 回归测试与前端冒烟测试
├── .env.example             # 环境变量说明
├── nginx.conf               # Nginx 日志配置片段
└── pyproject.toml           # 依赖、包元数据与命令入口
```

### 常用命令

| 命令 | 用途 |
| :--- | :--- |
| `nginx-insight init-db` | 创建缺失的数据表 |
| `nginx-insight collect` | 持续采集活动日志 |
| `nginx-insight import` | 导入活动日志和轮转归档，读取完成后退出 |
| `nginx-insight geo` | 持续处理定位任务 |
| `nginx-insight geo --once` | 处理一个定位批次，最多选择 50 个 IP |
| `nginx-insight geo --ip 8.8.8.8` | 绕过 Redis 诊断公网 IP 定位，不修改 MySQL |
| `nginx-insight serve --port 8000` | 启动本地开发 Web 服务 |
| `python -m pytest -q` | 运行 Python 测试 |
| `node tests/frontend_smoke.mjs` | 运行前端逻辑与地图布局冒烟测试 |
| `python scripts/fetch_assets.py` | 检查已有前端资源，下载缺失或无效资源 |
| `python scripts/build_release.py` | 生成生产源码发布包 |

采集与导入可通过 `--directory /path/to/logs` 覆盖日志目录；各命令支持 `--log-level DEBUG` 等日志级别参数。前端测试需要 Node.js，无需安装 npm 依赖；Python 测试使用临时 SQLite 和模拟外部服务，无需连接生产数据库。

ECharts 与地图资源已放在 `insight/static/`，页面运行时从本站加载。需要重新准备资源时，下载脚本支持 `--force`、重试与镜像源；离线环境可执行 `python scripts/fetch_assets.py --source-dir /path/to/assets` 导入 ECharts 脚本、地图 JSON 与许可证。

### 参与改进

欢迎通过 Issue 反馈问题或提出想法，也欢迎提交 Pull Request。提交时请说明行为变化与验证方式；修改采集或解析逻辑时提供日志样例，涉及页面变化时附上截图。

## 许可

本项目属于 [moon-weave · 织月](../README.md)，许可范围以仓库根目录及子项目的许可声明为准。第三方资源遵循各自许可；ECharts 的 Apache 2.0 许可证见 [ECHARTS-LICENSE.txt](insight/static/ECHARTS-LICENSE.txt)，地图数据来源见 [资源获取脚本](scripts/fetch_assets.py)。

---

<div align="center">

**让每一次连接，留下清晰的脉络。**

</div>
