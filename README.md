# 即梦视频队列 (Jimeng Queue)

一个用于**即梦 (Dreamina) AI 视频生成**的队列管理工具，提供 Web 界面来排队、管理和跟踪即梦视频生成任务。

## 功能

- **任务队列** — 添加 prompt 生成任务，自动排队依次提交到即梦 CLI
- **参考文件上传** — 支持上传图片/视频/音频作为参考素材（需配置腾讯云 COS）
- **拖拽排序** — 拖拽调整队列中任务的优先级
- **实时状态** — 自动轮询即梦 API，追踪生成进度、耗时
- **积分查询** — 实时显示即梦账号剩余积分
- **CLI 健康检查** — 自动检测 dreamina CLI 安装和登录状态
- **密码保护** — JWT 认证，默认密码 `admin123`
- **暂停/恢复** — 随时暂停和恢复队列处理

## 前置条件

| 依赖 | 说明 |
|------|------|
| Python 3.11+ | 后端运行环境 |
| Node.js 20+ | 前端构建和开发 |
| dreamina CLI | 即梦官方命令行工具，需单独安装并登录 |

## 快速开始 (5 分钟)

### 1. 克隆项目

```bash
git clone <repo-url>
cd jimeng_auto
```

### 2. 安装 dreamina CLI 并登录

```bash
# 安装即梦命令行工具（参考即梦官方文档）
dreamina login
```

### 3. 一键启动

```bash
# 复制环境变量配置（默认值即可直接使用）
cp .env.example .env

# 安装 Python 依赖
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 安装前端依赖
cd frontend && npm install && cd ..

# 启动后端（会自动建数据库、启动队列 worker）
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

然后**新开一个终端**启动前端开发服务器：

```bash
cd frontend
npm run dev
```

浏览器打开 **http://localhost:5173**，用默认密码 **`admin123`** 登录即可使用。

> **不需要改任何配置**，`.env.example` 里的默认值就是可用的。想改密码或配置 COS 上传功能时再编辑 `.env`。

### 4. 生产模式（单进程，无需前端开发服务器）

```bash
cd frontend && npm run build && cd ..
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

后端会自动托管前端静态文件，浏览器打开 `http://127.0.0.1:8000` 即可。

## 环境变量

所有变量都有默认值，**不用 `.env` 文件也能启动**。需要自定义时才创建 `.env`。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PASSWORD` | `admin123` | Web 登录密码 |
| `JWT_SECRET` | 内置 fallback | JWT 签名密钥 |
| `POLL_INTERVAL` | `30` | 任务状态轮询间隔（秒） |
| `COS_SECRET_ID` | — | 腾讯云 COS SecretId（可选，开启后支持参考文件上传） |
| `COS_SECRET_KEY` | — | 腾讯云 COS SecretKey（可选） |
| `COS_REGION` | `ap-chongqing` | COS 存储桶地域（可选） |
| `COS_CUSTOM_DOMAIN` | — | COS 自定义域名（可选） |

> **注意：** 不配置 COS 也不影响文生视频核心功能，只是无法上传参考图片/视频/音频。

## 使用指南

### 文生视频

1. 在底部输入框输入 prompt
2. 选择时长 (4-15秒)、比例和模型版本
3. 点击发送，任务进入队列
4. Worker 自动逐个提交到即梦，完成后显示结果

### 参考文件生成（图/视频/音频生视频）

1. 先配置好 COS 环境变量
2. 拖拽或点击上传参考文件（图片/视频/音频）
3. 输入 prompt 并发送
4. Worker 从 COS 下载参考文件 → 提交给即梦 CLI

### 队列管理

- **拖拽排序** — 长按或拖拽队列中的任务卡片调整优先级
- **暂停/恢复** — 点击"暂停队列"可临时停止提交新任务
- **删除任务** — 只能删除尚未开始生成的 pending 任务
- **查看历史** — 顶部区域展示最近完成/失败的任务，点击"查看更多"查看全部

## 项目结构

```
jimeng_auto/
├── app/                      # FastAPI 后端
│   ├── main.py               # 应用入口，中间件，路由挂载
│   ├── auth.py               # JWT 认证 + 密码哈希
│   ├── database.py           # SQLite 数据库初始化
│   ├── dreamina.py           # dreamina CLI 封装 (提交/查询/健康检查)
│   ├── cos.py                # 腾讯云 COS 上传/下载
│   ├── worker.py             # 队列 worker (提交任务 + 轮询状态)
│   ├── models.py             # Pydantic 数据模型
│   └── router/
│       ├── auth.py           # POST /api/auth/login
│       ├── tasks.py          # CRUD /api/tasks
│       ├── queue.py          # GET /api/queue/status, /pause, /resume
│       └── upload.py         # POST /api/upload/presign, /proxy
├── frontend/                 # React 前端
│   ├── src/
│   │   ├── api.ts            # API 客户端 + 类型定义
│   │   ├── pages/
│   │   │   ├── LoginPage.tsx # 登录页
│   │   │   └── MainPage.tsx  # 主界面 (队列/提交/历史)
│   │   └── styles/
│   │       └── global.css    # 全局样式
│   ├── vite.config.ts        # Vite 配置 (含 API 代理)
│   └── package.json
├── deploy/                   # 部署参考文件
│   ├── jimeng-queue.service  # systemd 服务单元
│   └── nginx.conf            # nginx 反向代理配置
├── .env.example              # 环境变量模板（复制为 .env 直接用）
└── requirements.txt          # Python 依赖
```

## API 概览

| 方法 | 路径 | 说明 | 需要认证 |
|------|------|------|----------|
| POST | `/api/auth/login` | 密码登录 | 否 |
| GET | `/api/auth/check` | 验证 token 是否有效 | 是 |
| GET | `/api/system/health` | CLI 健康检查 | 否 |
| GET | `/api/tasks` | 任务列表 | 是 |
| POST | `/api/tasks` | 创建任务 | 是 |
| GET | `/api/tasks/:id` | 任务详情 | 是 |
| PATCH | `/api/tasks/:id` | 编辑任务 | 是 |
| DELETE | `/api/tasks/:id` | 删除任务 | 是 |
| PATCH | `/api/tasks/:id/reorder` | 调整队列顺序 | 是 |
| GET | `/api/queue/status` | 队列状态 | 是 |
| POST | `/api/queue/pause` | 暂停队列 | 是 |
| POST | `/api/queue/resume` | 恢复队列 | 是 |
| GET | `/api/queue/credit` | 即梦积分 | 是 |
| POST | `/api/upload/presign` | 获取 COS 预签名上传 URL | 是 |
| POST | `/api/upload/proxy` | 通过后端代理上传到 COS | 是 |

## 部署到服务器

参考 `deploy/` 目录中的 `jimeng-queue.service` 和 `nginx.conf`。

```bash
# 1. 将项目放到服务器上
scp -r jimeng_auto/ user@your-server:/opt/jimeng-queue

# 2. 在服务器上配置 .env（改掉默认密码）
cp .env.example .env
vim .env

# 3. 安装依赖 + 构建前端
cd /opt/jimeng-queue
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..

# 4. 配置 systemd 服务 (编辑 deploy/jimeng-queue.service 中的路径和密码)
sudo cp deploy/jimeng-queue.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jimeng-queue

# 5. 配置 nginx 反向代理 (编辑 deploy/nginx.conf 中的域名)
sudo cp deploy/nginx.conf /etc/nginx/conf.d/jimeng-queue.conf
sudo systemctl reload nginx
```

## License

MIT
