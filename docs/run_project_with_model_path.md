# FileForge 可执行环境运行说明（使用本机模型路径）

本文用于在可执行 Linux 环境中从头运行项目。假设你不使用之前已有的 vLLM 专用环境，而是在 FileForge 项目环境里直接安装并启动 vLLM；模型文件路径使用 `~/.cache/huggingface/hub/Qwen3-32B-AWQ`。

> 当前文档中的命令面向真实可执行环境。若只是在非可执行会话中阅读或维护文档，不要声称这些命令已经运行。

## 1 拉取最新代码

```bash
cd ~/document/mybishe/fileforge_t
git status -sb
git pull --ff-only
```

如果 `git pull` 提示本地改动会被覆盖，先处理本地改动，不要直接覆盖未确认文件。

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
cd ~/document/mybishe/fileforge_t
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

## 7 可选：启动 Web 后台

Web 后台需要 PostgreSQL。先设置数据库连接并迁移：

```bash
export DATABASE_URL="postgresql+psycopg://fileforge:你的密码@127.0.0.1:5432/fileforge_current"
alembic upgrade head
```

初始化账号：

```bash
python -m utils.user_admin roles init
python -m utils.user_admin users create \
  --username admin \
  --password '你的管理员密码' \
  --role platform_admin
```

启动 Web：

```bash
uvicorn web_admin.app:create_app --factory --host 0.0.0.0 --port 8080
```

浏览器访问：

```text
http://服务器IP:8080/login
```

## 8 常见检查

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
