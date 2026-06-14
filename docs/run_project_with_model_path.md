# FileForge 可执行环境运行说明（使用本机模型路径）

本文用于在可执行 Linux 环境中从头运行项目。假设你不使用之前已有的 vLLM 专用环境，而是在 FileForge 项目环境里直接安装并启动 vLLM；模型文件路径使用 `~/.cache/huggingface/hub/Qwen3-32B-AWQ`。

> 当前文档中的命令面向真实可执行环境。若只是在非可执行会话中阅读或维护文档，不要声称这些命令已经运行。

## 1 拉取最新代码

```bash
cd ~/document/mybishe/fileforge
git status -sb
git pull --ff-only
```

如果你的目录仍叫 `fileforge_t`，把上面的路径换成实际目录。若 `git pull` 提示本地改动会被覆盖，先处理本地改动，不要直接覆盖未确认文件。

## 2 创建项目运行环境

```bash
conda create -n fileforge python=3.12 -y
conda activate fileforge
python -m pip install --upgrade pip
```

安装项目依赖和 Web 依赖：

```bash
pip install -r requirements/nvi.txt
pip install -r requirements/web.txt
```

安装 vLLM：

```bash
pip install "vllm>=0.6.3"
```

如果 `paddleocr` 或 `paddlepaddle-gpu` 与本机 CUDA 不匹配，按实际 CUDA 版本安装对应 PaddlePaddle 包；临时只验证流程时可设置 `OCR_USE_GPU=false`。

## 3 确认模型路径

确认模型目录存在：

```bash
ls ~/.cache/huggingface/hub/Qwen3-32B-AWQ
```

目录中通常应能看到模型配置、tokenizer 和权重文件，例如 `config.json`、`tokenizer.json`、`*.safetensors` 等。

## 4 启动 vLLM 服务

建议用 `tmux` 保持服务常驻：

```bash
tmux new -s fileforge-vllm
conda activate fileforge

vllm serve ~/.cache/huggingface/hub/Qwen3-32B-AWQ \
  --served-model-name qwen3-32b-awq \
  --host 0.0.0.0 \
  --port 8000 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.90
```

不要关闭这个窗口。需要离开 tmux 时按 `Ctrl-b` 后按 `d`。

另开一个终端检查服务：

```bash
curl http://127.0.0.1:8000/v1/models
```

返回中应能看到 `qwen3-32b-awq`。项目里的 `LLM_MODEL_NAME` 必须和 `--served-model-name` 完全一致。

## 5 准备输入数据

命令行批处理读取 `input_documents/`。每个子目录是一份多页档案：

```text
input_documents/
  doc_001/
    page_001.jpg
    page_002.jpg
  doc_002/
    page_001.jpg
```

## 6 运行命令行批处理

在项目目录执行：

```bash
cd ~/document/mybishe/fileforge
conda activate fileforge

export LLM_BASE_URL="http://127.0.0.1:8000/v1"
export LLM_MODEL_NAME="qwen3-32b-awq"
export LLM_API_KEY="EMPTY"
export LLM_ENABLE_THINKING="false"
export OCR_USE_GPU="true"

python main.py
```

如果 OCR GPU 环境还没配好，可先用 CPU 跑通流程：

```bash
export OCR_USE_GPU="false"
python main.py
```

输出目录：

```text
output_results/
```

## 7 可选：准备 PostgreSQL 数据库

Web 后台和 CLI 入库都需要 PostgreSQL。当前目标环境已验证可直接使用名为 `postgres` 的 Docker 容器，它映射宿主机 `5432` 端口。

先确认容器存在：

```bash
docker ps --format 'table {{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}'
```

如果能看到名为 `postgres` 的容器，并且端口包含 `0.0.0.0:5432->5432/tcp`，可直接创建当前项目库。

创建角色和数据库：

```bash
docker exec postgres psql -U postgres -d postgres -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'fileforge') THEN CREATE ROLE fileforge LOGIN PASSWORD 'fileforge_local'; ELSE ALTER ROLE fileforge WITH LOGIN PASSWORD 'fileforge_local'; END IF; END \$\$;"

docker exec postgres psql -U postgres -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'fileforge_current';"

docker exec postgres psql -U postgres -d postgres -c "DROP DATABASE IF EXISTS fileforge_current;"

docker exec postgres psql -U postgres -d postgres -c "CREATE DATABASE fileforge_current OWNER fileforge;"

docker exec postgres psql -U postgres -d fileforge_current -c "GRANT ALL ON SCHEMA public TO fileforge;"

docker exec postgres psql -U postgres -d fileforge_current -c "ALTER SCHEMA public OWNER TO fileforge;"
```

设置项目连接串并执行迁移：

```bash
cd ~/document/mybishe/fileforge
conda activate fileforge

export DATABASE_URL="postgresql+psycopg://fileforge:fileforge_local@127.0.0.1:5432/fileforge_current"

alembic upgrade head
```

验证迁移版本和表：

```bash
docker exec postgres psql -U postgres -d fileforge_current -c "SELECT * FROM alembic_version;"

docker exec postgres psql -U postgres -d fileforge_current -c "\dt"
```

成功时 `alembic_version` 应为：

```text
0005_upload_online_processing
```

应能看到 17 张表，包括 `app_users`、`archive_records`、`processing_batches`、`processing_jobs`、`processing_events`、`upload_batches`、`uploaded_files`、`web_sessions` 等。

## 8 可选：启动 Web 后台

如果选择使用网页处理档案，不需要手动运行 `python main.py`。Web 页面点击“开始处理”后，会在后台复用同一条 OCR/LLM/规则/导出流程。

初始化账号：

```bash
cd ~/document/mybishe/fileforge
conda activate fileforge

export DATABASE_URL="postgresql+psycopg://fileforge:fileforge_local@127.0.0.1:5432/fileforge_current"

python -m utils.user_admin roles init

python -m utils.user_admin users create \
  --username admin \
  --password 'Admin@fileforge2026' \
  --display-name '系统管理员' \
  --role platform_admin
```

`roles init` 当前只是兼容命令，角色权限由 `app_users.role` 和代码映射提供。密码至少 12 位；`Admin@fileforge2026` 只适合本机演示或临时环境，正式环境应换成新的强密码。

验证账号可登录：

```bash
python -m utils.user_admin login \
  --username admin \
  --password 'Admin@fileforge2026'
```

成功时会返回：

```json
{
  "authenticated": true,
  "id": 1,
  "username": "admin",
  "display_name": "系统管理员"
}
```

网页登录账号：

```text
用户名：admin
密码：Admin@fileforge2026
```

如果创建时提示用户已存在，可重置密码：

```bash
python -m utils.user_admin users reset-password \
  --username admin \
  --password 'Admin@fileforge2026'
```

### 8.1 准备演示单位、项目和操作员

为了网页演示更完整，可以预置一个单位、一个项目和一个单位操作员账号。

创建演示单位：

```bash
python -m utils.user_admin orgs create --name "档案室"
```

如果提示单位已存在，可以直接查数据库里的 id：

```bash
docker exec postgres psql -U postgres -d fileforge_current -c "SELECT id, name, status FROM organizations ORDER BY id;"
```

下面这段 SQL 会保证存在一个单位 `档案室` 和一个项目 `demo_2026`。可以重复执行：

```bash
docker exec postgres psql -U postgres -d fileforge_current -c "INSERT INTO organizations (name, status) VALUES ('档案室', 'active') ON CONFLICT (name) DO UPDATE SET status = 'active';"

docker exec postgres psql -U postgres -d fileforge_current -c "INSERT INTO projects (project_key, project_name, organization_id, status, preserve_existing_numbers_on_rerun) SELECT 'demo_2026', '2026 演示项目', id, 'active', true FROM organizations WHERE name = '档案室' ON CONFLICT (project_key) DO UPDATE SET project_name = EXCLUDED.project_name, organization_id = EXCLUDED.organization_id, status = 'active';"

docker exec postgres psql -U postgres -d fileforge_current -c "SELECT id, name, status FROM organizations ORDER BY id;"

docker exec postgres psql -U postgres -d fileforge_current -c "SELECT id, project_key, project_name, organization_id, status FROM projects ORDER BY id;"
```

创建单位操作员。把 `--organization-id 1` 改成上一步 `档案室` 的实际 id：

```bash
python -m utils.user_admin users create \
  --username operator01 \
  --password 'Operator@fileforge2026' \
  --display-name '演示操作员' \
  --organization-id 1 \
  --role org_operator
```

如果用户已存在，重置密码：

```bash
python -m utils.user_admin users reset-password \
  --username operator01 \
  --password 'Operator@fileforge2026'
```

查看用户：

```bash
python -m utils.user_admin users list
```

演示账号：

```text
平台管理员：admin / Admin@fileforge2026
单位操作员：operator01 / Operator@fileforge2026
```

平台管理员用于创建单位、项目和用户；单位操作员用于演示本单位上传、跑批、查看和人工修正。

启动 Web 前设置运行环境：

```bash
export DATABASE_URL="postgresql+psycopg://fileforge:fileforge_local@127.0.0.1:5432/fileforge_current"
export LLM_BASE_URL="http://127.0.0.1:8000/v1"
export LLM_MODEL_NAME="qwen3-32b-awq"
export LLM_API_KEY="EMPTY"
export LLM_ENABLE_THINKING="false"
export OCR_USE_GPU="true"
```

启动 Web：

```bash
uvicorn web_admin.app:create_app --factory --host 0.0.0.0 --port 8080
```

浏览器访问：

```text
http://服务器IP:8080/login
```

网页登录后按这个顺序使用：

1. 进入 `/admin/projects`，创建或确认整理项目。网页创建项目时只需要填项目名称和所属单位，项目唯一标识由系统自动生成。
2. 进入 `/uploads`。
3. 选择项目，上传图片、zip 或文件夹。
4. 点击“开始处理”。
5. 进入 `/processing/batches/{id}` 查看任务进度和事件。
6. 进入批次档案列表查看处理结果。
7. 进入档案详情页查看最终字段、档号、件号、OCR/LLM/规则结果。
8. 如需人工校核，进入编辑页修改允许修正的字段，再查看修订记录和审计日志。

zip 上传建议使用一级目录区分档案：

```text
demo_upload.zip
  doc_001/
    page_001.jpg
    page_002.jpg
  doc_002/
    page_001.jpg
```

散图上传会被归为同一份档案，适合快速试跑；多份档案批量演示建议用 zip 或网页“文件夹”选择。文件夹上传时，如果选择的是批量根目录，根目录下每个子目录会作为一份档案；如果选择的是单份档案目录，该目录本身会作为一份档案。

默认单次上传上限是 200 MiB、最多 2000 个文件。上传大文件夹前，在启动 Web 前调大限制并重启 `uvicorn`：

```bash
export WEB_MAX_UPLOAD_BYTES="$((2 * 1024 * 1024 * 1024))"
export WEB_MAX_UPLOAD_FILES="10000"
```

Web 在线跑批仍要求：

- vLLM 服务正在运行，且 `LLM_BASE_URL` 指向它。
- OCR 运行环境可用；GPU 不稳定时可临时设置 `OCR_USE_GPU=false`。
- Web 进程启动时已经带上 `DATABASE_URL`。

启动 Web 前推荐显式设置：

```bash
export DATABASE_URL="postgresql+psycopg://fileforge:fileforge_local@127.0.0.1:5432/fileforge_current"
export LLM_BASE_URL="http://127.0.0.1:8000/v1"
export LLM_MODEL_NAME="qwen3-32b-awq"
export LLM_API_KEY="EMPTY"
export LLM_ENABLE_THINKING="false"
export OCR_USE_GPU="true"
```

然后启动：

```bash
uvicorn web_admin.app:create_app --factory --host 0.0.0.0 --port 8080
```

## 9 可选：CLI 入库运行

如果要让 `python main.py` 同时写入数据库，除了 LLM/OCR 配置，还要设置项目标识和批次标识。网页创建项目时 `project_key` 由系统生成；命令行入库可以使用已有项目的 `project_key`，也可以按下面方式使用预置演示项目 `demo_2026`。

```bash
export DATABASE_URL="postgresql+psycopg://fileforge:fileforge_local@127.0.0.1:5432/fileforge_current"
export PROJECT_KEY="demo_2026"
export PROJECT_NAME="演示项目"
export BATCH_KEY="$(date +%Y%m%d_%H%M%S)_run01"
```

然后运行：

```bash
python main.py
```

如果只想纯文件输出，不写数据库：

```bash
unset DATABASE_URL PROJECT_KEY PROJECT_NAME BATCH_KEY
python main.py
```

## 10 常见检查

检查 vLLM：

```bash
curl http://127.0.0.1:8000/v1/models
```

检查项目配置：

```bash
python - <<'PY'
from config.config import Config
print(Config.LLM_BASE_URL)
print(Config.LLM_MODEL_NAME)
print(Config.LLM_ENABLE_THINKING)
PY
```

检查测试（目标环境可执行时）：

```bash
python -m unittest discover -s tests -p "test_*.py"
```

如果 `python main.py` 报 `DATABASE_URL 已设置,但未指定 PROJECT_KEY`，说明当前 shell 里保留了数据库环境变量。纯文件运行时可以清空：

```bash
unset DATABASE_URL PROJECT_KEY PROJECT_NAME BATCH_KEY
```
