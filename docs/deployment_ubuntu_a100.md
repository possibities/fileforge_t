# Ubuntu + A100 部署指南

从零起,把 fileforge 项目跑到"OCR 管线 + Web 后台 + PG 入库"全功能可用。目标环境:Ubuntu 22.04 LTS,NVIDIA A100 80GB,CUDA 12.x,Miniforge + Mamba。

> 当前文档中的命令面向真实 Ubuntu/GPU/数据库可执行环境。若当前会话是非可执行环境,只能做静态核对和文档更新,不要把下列命令写成已执行结果。

---

## 0 时间预算

| 阶段 | 大致用时 |
| --- | --- |
| 系统/驱动确认 | 10 分钟(假设已有 NVIDIA driver) |
| 拉项目 + conda env + 装依赖 | 15–30 分钟 |
| 下载 Qwen3-32B-AWQ 模型 | 10–40 分钟(看带宽) |
| PostgreSQL 启动 + 迁移 | 10 分钟 |
| 启动 vLLM(首次包含 CUDA graph 编译) | 3–5 分钟 |
| 初始化账号 + 跑一遍 demo 数据 | 10 分钟 |
| **合计** | **约 1–2 小时** |

---

## 1 硬件与系统要求

- **GPU**:NVIDIA A100 80GB(或 ≥ 24GB 显存的 RTX 4090/3090 等,跑 AWQ 4bit 量化的 Qwen3-32B)
- **CPU**:≥ 8 core
- **内存**:≥ 32GB(PaddleOCR + vLLM 主机内存)
- **磁盘**:≥ 100GB SSD(模型 ~19GB,Paddle 模型 ~1GB,OCR 临时文件)
- **OS**:Ubuntu 22.04 LTS(20.04 也可)
- **驱动**:NVIDIA driver ≥ 535,CUDA 12.1+

确认:

```bash
nvidia-smi              # 应显示 A100 + Driver Version 535+
python3 --version       # 系统 Python 3.10+ 即可(实际跑用 conda env)
```

---

## 2 Miniforge + Mamba

```bash
# 装 Miniforge(已装可跳)
curl -L -O https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
bash Miniforge3-Linux-x86_64.sh -b -p $HOME/miniforge3
source $HOME/miniforge3/bin/activate

# 升级 mamba
conda install -n base -c conda-forge mamba -y
```

---

## 3 拉项目 + 创建 conda env

```bash
# 拉项目(假设你把代码 clone 到 ~/work/fileforge)
mkdir -p ~/work && cd ~/work
git clone <your-repo-url> fileforge
cd fileforge

# 创建独立 env
mamba create -n fileforge python=3.12 -y
mamba activate fileforge
```

---

## 4 安装项目依赖

依赖分层:`base.txt`(管线核心) → `nvi.txt`(GPU + 微调) → `db.txt`(PostgreSQL) → `web.txt`(Web 后台)。

A100 服务器装全套:

```bash
cd ~/work/fileforge
pip install -r requirements/nvi.txt
pip install -r requirements/web.txt    # web 内 -r db.txt,会一并装上
pip install vllm>=0.6.3                # vLLM 单独装(项目 requirements 不含,因为它是外置服务)
```

验证关键包:

```bash
python -c "import paddle; print('paddle:', paddle.__version__); print('GPU:', paddle.is_compiled_with_cuda())"
python -c "import torch; print('torch:', torch.__version__); print('CUDA:', torch.cuda.is_available())"
python -c "import vllm; print('vllm:', vllm.__version__)"
python -c "import fastapi, sqlalchemy, alembic; print('web ok')"
```

期望 `paddle GPU: True`、`torch CUDA: True`。

---

## 5 下载 Qwen3-32B-AWQ 模型

```bash
# 用 huggingface-hub CLI(已经被 transformers 顺带装上)
huggingface-cli download Qwen/Qwen3-32B-AWQ --local-dir ~/.cache/huggingface/hub/Qwen3-32B-AWQ
```

国内访问慢可设 mirror:

```bash
export HF_ENDPOINT="https://hf-mirror.com"
huggingface-cli download Qwen/Qwen3-32B-AWQ --local-dir ~/.cache/huggingface/hub/Qwen3-32B-AWQ
```

下载完确认目录有 `config.json` 与 `model*.safetensors`。

---

## 6 启动 vLLM 服务

建议放 tmux / screen 里常驻:

```bash
tmux new -s vllm
mamba activate fileforge

vllm serve ~/.cache/huggingface/hub/Qwen3-32B-AWQ \
  --served-model-name qwen3-32b-awq \
  --host 127.0.0.1 \
  --port 8000 \
  --quantization awq_marlin \
  --dtype auto \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.90
```

Detach:`Ctrl-b d`。

健康检查(等启动完成,日志看到 `Uvicorn running on ...` 后):

```bash
curl -s http://localhost:8000/v1/models | python -m json.tool

curl -s http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-32b-awq",
    "messages": [{"role":"user","content":"只输出 JSON: {\"ok\":true}"}],
    "response_format": {"type": "json_object"},
    "max_tokens": 32,
    "chat_template_kwargs": {"enable_thinking": false}
  }'
```

返回应含 `"content":"{\"ok\":true}"`。常见问题与多卡/显存优化见 `docs/vllm_server.md`。

---

## 7 PostgreSQL 准备

### 7.1 安装

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql
```

### 7.2 建库和用户

```bash
sudo -u postgres psql <<'SQL'
CREATE USER fileforge WITH PASSWORD 'change-this-strong-password';
CREATE DATABASE fileforge_current OWNER fileforge;
GRANT ALL PRIVILEGES ON DATABASE fileforge_current TO fileforge;
SQL
```

### 7.3 客户端连接串

```bash
export DATABASE_URL="postgresql+psycopg://fileforge:change-this-strong-password@127.0.0.1:5432/fileforge_current"
```

当前项目如果要和大改前旧库隔离,推荐使用新库名 `fileforge_current`。数据库名不写死在代码里,只由 `DATABASE_URL` 决定。

如果 PostgreSQL 跑在 Docker 容器里,并且容器名为 `fileforge-pg`,可这样创建新库:

```bash
docker exec -it fileforge-pg psql -U fileforge -d postgres -c \
"CREATE DATABASE fileforge_current OWNER fileforge;"
```

长期固定配置建议放到 `/etc/fileforge/fileforge.env`,见 §10.2。

### 7.4 跑 Alembic 迁移

```bash
cd ~/work/fileforge
alembic upgrade head
```

应该完成到 `0005_rebuild_upload_online_processing`,建出上传与在线跑批所需的当前 schema。

验证:

```bash
psql "$DATABASE_URL" -c "\dt"
```

应看到 `organizations`、`app_users`、`web_sessions`、`projects`、`upload_batches`、`uploaded_files`、`processing_batches`、`processing_jobs`、`processing_events`、`archive_records`、`archive_pages`、`llm_traces`、`sequence_counters`、`export_files`、`metadata_revisions`、`audit_logs` 等表。

---

## 8 初始化账号、单位、项目

```bash
cd ~/work/fileforge

# 兼容命令:当前角色权限由 app_users.role + 代码映射提供,不会写旧 RBAC 表
python -m utils.user_admin roles init

# 创建平台管理员(后续登录 Web 后台用)
python -m utils.user_admin users create \
  --username admin \
  --password 'change-this-strong-password' \
  --display-name '系统管理员' \
  --role platform_admin

# 创建单位
python -m utils.user_admin orgs create --name '档案室'
# 记下返回的 organization_id(假设为 1)

# 创建单位操作员(可选,用于演示组织作用域隔离)
python -m utils.user_admin users create \
  --username operator01 \
  --password 'change-this-strong-password' \
  --display-name '档案操作员' \
  --organization-id 1 \
  --role org_operator
```

也可以登录 Web 后台后用页面创建,见第 11 节。

---

## 9 跑分类管线(`python main.py`)

### 9.1 准备输入数据

```bash
mkdir -p input_documents

# 每个子目录 = 一个档案,目录下放该档案的扫描图片(.jpg/.png/.tiff 等)
# 例:
#   input_documents/
#     202508_dangwei_meeting_01/
#       page_001.jpg
#       page_002.jpg
#     202508_xianzhang_speech/
#       page_001.jpg
```

### 9.2 配置环境变量

```bash
# 必填(DB 写入)
export DATABASE_URL="postgresql+psycopg://fileforge:change-this-strong-password@127.0.0.1:5432/fileforge_current"
export PROJECT_KEY='demo_2025'         # 项目稳定标识,序号在项目内连续
export PROJECT_NAME='2025 演示项目'    # 可选,用于显示
export BATCH_KEY="$(date +%Y%m%d_%H%M%S)_run01"   # 批次唯一标识

# LLM 客户端
export LLM_BASE_URL='http://127.0.0.1:8000/v1'
export LLM_MODEL_NAME='qwen3-32b-awq'
export LLM_ENABLE_THINKING='false'

# OCR
export OCR_USE_GPU='true'

# 可选:rerun 策略,默认 skip-success
# export DB_RERUN_POLICY='rerun-all'
```

### 9.3 启动

```bash
cd ~/work/fileforge
python main.py
```

期望输出:

```
[1/4] Initializing...
...
[2/4] Resolving paths...
...
[3/4] Processing archives...
...
[4/4] Exporting results...
Processing completed
  JSON summary: output_results/archive_results_<ts>.json
  CSV summary:  output_results/archive_results_<ts>.csv
  Per-archive files: output_results/*_result.json
Statistics:
  Total archives: N
  Success: M (XX%)
```

文件输出在 `output_results/`,数据库写入由 BatchRecorder 完成,失败的写入不影响 JSON/CSV 交付。

### 9.4 注意:必须先在 PG 里创建项目

`PROJECT_KEY=demo_2025` 在表 `projects` 里需要先存在。两种方式:

**方式 A**:让 main.py 自动创建 — `infrastructure/db/repositories.get_or_create_project` 会在第一次见到新 key 时自动建项目,但 `organization_id` 留空。可以跑完后用 Web 后台或 CLI 把项目绑到单位。

**方式 B**(推荐,因为 Web 后台要求项目绑单位):先用 Web 或 SQL 显式创建项目并绑定到刚才的 `档案室`(`organization_id=1`):

```bash
# CLI 暂未提供 projects create(只 Web 有),可用直接 SQL:
psql "$DATABASE_URL" <<'SQL'
INSERT INTO projects (project_key, project_name, organization_id, status, preserve_existing_numbers_on_rerun)
VALUES ('demo_2025', '2025 演示项目', 1, 'active', true);
SQL
```

或登录 Web 后台后到 `/admin/projects/new` 创建。

---

## 10 启动 Web 后台

```bash
tmux new -s webadmin
mamba activate fileforge

cd ~/work/fileforge
export DATABASE_URL="postgresql+psycopg://fileforge:change-this-strong-password@127.0.0.1:5432/fileforge_current"

# 单机访问
uvicorn web_admin.app:create_app --factory --host 127.0.0.1 --port 8080

# 跨机访问(注意配合防火墙)
# uvicorn web_admin.app:create_app --factory --host 0.0.0.0 --port 8080
```

可选 Web 配置环境变量(都有默认值):

```bash
export WEB_SESSION_COOKIE_NAME='fileforge_session'
export WEB_SESSION_TTL_SECONDS='28800'    # session 有效期 8 小时
export WEB_COOKIE_SECURE='false'          # 生产 HTTPS 后改 true
export WEB_CSRF_ENABLED='true'
export WEB_UPLOAD_STORAGE_ROOT='input_documents/web_uploads'
export WEB_PROCESSING_OUTPUT_ROOT='output_results/web_runs'
export WEB_MAX_UPLOAD_BYTES='209715200'   # 200 MiB
export WEB_MAX_UPLOAD_FILES='2000'
```

浏览器访问 `http://<server-ip>:8080/login`,用第 8 节创建的 admin / `change-this-strong-password` 登录。

可用页面(权限决定可见性):

| 路径 | 用途 | 权限 |
| --- | --- | --- |
| `/admin/users` | 用户管理 | `user:manage` |
| `/admin/organizations` | 单位管理 | `organization:manage`(只 `platform_admin`) |
| `/admin/projects` | 项目管理 | `project:manage` |
| `/uploads` | 上传图片/zip 并启动在线跑批 | `batch:manage` |
| `/processing/batches/{id}` | 在线跑批进度、任务、事件 | `batch:manage` |
| `/batches?project_key=demo_2025` | 批次列表 | `archive:view` |
| `/batches/{id}` | 批次详情 | `archive:view` |
| `/batches/{id}/archives` | 档案列表(12 字段过滤) | `archive:view` |
| `/archives/{id}` | 档案详情(三快照、页面图片路径) | `archive:view` |
| `/archives/{id}/edit` | 元数据人工修正(4 字段) | `archive:correct` |
| `/archives/{id}/revisions` | 修订记录 | `archive:view` |
| `/archives/{id}/audit` | 审计记录 | `audit:view` |

### 10.1 浏览器上传在线跑批

Web 在线跑批依赖第 6 节的 vLLM 服务和 OCR 运行环境。推荐演示流程:

```text
登录 /login
→ /admin/projects 创建或确认项目
→ /uploads 上传散图或 zip
→ 点击“开始处理”
→ /processing/batches/{id} 查看进度
→ /batches/{id}/archives 查看档案结果
```

zip 上传约定:一级目录表示一份档案;散图上传会被归入同一份档案。在线跑批会复用 `ArchiveClassifier + BatchProcessor + BatchRecorder`,不是单独的一套抽取逻辑。

如果浏览器上传已完成,也可以从命令行处理该上传批次:

```bash
python -m utils.processing_runner --upload-batch-id 1
```

该入口适合演示补跑或后续接入独立 worker。它创建的处理批次和任务与 Web “开始处理”按钮一致。

### 10.2 长期固定环境变量

服务器长期运行时,把当前项目的数据库连接、LLM、OCR 和 Web 配置固定到系统 env 文件,不要改代码:

```bash
sudo mkdir -p /etc/fileforge
sudo nano /etc/fileforge/fileforge.env
```

示例内容:

```bash
DATABASE_URL=postgresql+psycopg://fileforge:change-this-strong-password@127.0.0.1:5432/fileforge_current

LLM_BASE_URL=http://127.0.0.1:8000/v1
LLM_MODEL_NAME=qwen3-32b-awq
LLM_API_KEY=EMPTY
LLM_ENABLE_THINKING=false

OCR_USE_GPU=true

WEB_SESSION_COOKIE_NAME=fileforge_session
WEB_SESSION_TTL_SECONDS=28800
WEB_COOKIE_SECURE=false
WEB_CSRF_ENABLED=true
WEB_UPLOAD_STORAGE_ROOT=input_documents/web_uploads
WEB_PROCESSING_OUTPUT_ROOT=output_results/web_runs
WEB_MAX_UPLOAD_BYTES=209715200
WEB_MAX_UPLOAD_FILES=2000
```

保护权限:

```bash
sudo chmod 600 /etc/fileforge/fileforge.env
```

手动运行迁移、初始化账号、`main.py` 或 CLI 前加载:

```bash
cd ~/work/fileforge
mamba activate fileforge
set -a
source /etc/fileforge/fileforge.env
set +a
```

如果数据库密码包含 `@`、`:`、`/`、`#` 等字符,需要在 `DATABASE_URL` 中 URL 编码。

### 10.3 systemd 启动 Web

创建服务:

```bash
sudo nano /etc/systemd/system/fileforge-web.service
```

示例内容,按实际路径替换 `WorkingDirectory` 和 `ExecStart`:

```ini
[Unit]
Description=FileForge Web Admin
After=network.target docker.service

[Service]
WorkingDirectory=/home/ubuntu/work/fileforge
EnvironmentFile=/etc/fileforge/fileforge.env
ExecStart=/home/ubuntu/miniforge3/envs/fileforge/bin/uvicorn web_admin.app:create_app --factory --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启用:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fileforge-web
sudo systemctl status fileforge-web
```

查看日志:

```bash
journalctl -u fileforge-web -f
```

---

## 11 验证检查清单

按顺序通一遍:

- [ ] `nvidia-smi` 看到 GPU 进程占用(vLLM 进程)
- [ ] `curl http://localhost:8000/v1/models` 返回 `qwen3-32b-awq`
- [ ] `psql "$DATABASE_URL" -c "\dt"` 列出全部表
- [ ] `python -m unittest discover -s tests -p "test_*.py"` 在目标环境通过
- [ ] `python main.py` 跑通至少 1 个 demo 档案,`output_results/` 出现 JSON/CSV
- [ ] `psql "$DATABASE_URL" -c "SELECT count(*) FROM archive_records"` 返回 ≥ 1
- [ ] Web 后台 `http://<host>:8080/login` 能登录
- [ ] `/uploads` 能上传 demo 图片或 zip,并能进入 `/processing/batches/{id}`
- [ ] 看到批次详情、档案详情、修订记录、审计记录页面
- [ ] 用 `org_operator` 账号登录验证组织隔离(只看到本单位档案)
- [ ] 在 `/archives/{id}/edit` 改 4 字段,看到 `correction_status` 变成 `corrected`,`metadata_revisions` 表新增行

---

## 12 常见问题

**vLLM OOM**:降 `--max-model-len 4096` 或 `--gpu-memory-utilization 0.85`,见 `docs/vllm_server.md` §5。

**LLM 响应里夹 `<think>` 前缀**:`LLM_ENABLE_THINKING` 没设为 false。检查 env 与 vLLM `--chat-template` 默认行为。

**`paddleocr` 初始化报 GPU 找不到**:`paddlepaddle-gpu` 版本与 CUDA 不匹配,或 `LD_LIBRARY_PATH` 缺 cuDNN。试 `pip install paddlepaddle-gpu==2.6.2 -i https://www.paddlepaddle.org.cn/packages/stable/cu120/`(选对应 CUDA 版本)。降级方案:`OCR_USE_GPU=false`(慢 5–10 倍但能跑)。

**Alembic 迁移报"relation already exists"**:数据库不干净。`DROP DATABASE fileforge_current` 后重建。

**main.py 报 `RuntimeError: DATABASE_URL 已设置,但未指定 PROJECT_KEY`**:`PROJECT_KEY` / `BATCH_KEY` 必须都设。或者全部不设(纯文件路径),`DATABASE_URL` 留空。

**Web 后台登录后 403 / 跨域 cookie 失效**:`WEB_COOKIE_SECURE=true` 但访问的是 HTTP 而非 HTTPS。开发期间设 `WEB_COOKIE_SECURE=false`。

**Web 后台跨机访问浏览器一直转圈**:`uvicorn` 起来时绑了 `127.0.0.1`,需要 `--host 0.0.0.0` + 服务器防火墙开 8080(`sudo ufw allow 8080/tcp`)。

**测试在 Miniforge env 里大量 skip**:`paddleocr` / `vllm` / `fastapi` 任一缺失会让对应 TestCase 走 `@unittest.skipUnless`。装齐 `requirements/nvi.txt` + `requirements/web.txt` 后重跑。

---

## 13 关停清单

正常停服:

```bash
# Web 后台:在 tmux 里 Ctrl-C
tmux attach -t webadmin
# Ctrl-C
# Ctrl-b d

# vLLM:同样 tmux 里 Ctrl-C
tmux attach -t vllm
# Ctrl-C

# 或粗暴杀:
sudo pkill -f vllm
sudo pkill -f "uvicorn web_admin"

# PostgreSQL:保留运行(下次开机自启)
# sudo systemctl stop postgresql   # 如需停
```

---

## 14 相关文档

| 主题 | 文档 |
| --- | --- |
| 数据契约 / DB schema | `docs/postgresql_data_contract_design.md` |
| DB CLI 工具集 | `docs/postgresql_basic_admin_runtime.md` |
| Web 后台运行细节 | `docs/web_admin.md` |
| vLLM 服务参数 | `docs/vllm_server.md` |
| 项目架构 | `docs/postgresql_integration_architecture.md` |
