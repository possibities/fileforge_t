# 答辩演示 Runbook

本文用于毕业答辩或阶段验收现场演示。目标是保证演示流程完整、可复现、可兜底，不把系统包装成生产级档案馆平台。

> 本 Runbook 面向答辩现场或可执行演示环境。当前会话若为非可执行环境,只做静态准备和文档核对,不执行健康检查、迁移、Web 启动或在线跑批。

## 1 演示定位

本系统演示的是“档案整理辅助处理”的闭环:

```text
创建单位 -> 创建整理项目 -> 上传扫描件 -> 启动处理 -> 查看进度 -> 查看档案结果 -> 人工修正 -> 查看修订记录/审计记录 -> 获取 JSON/CSV 导出
```

答辩时重点说明:

- 系统处理对象是扫描档案图片或 zip 包。
- 核心问题是降低档案元数据录入和核对成本。
- Web 后台用于演示上传、处理、复核和修正闭环。
- 当前是毕业设计原型，不承诺完整生产级队列、审批、借阅、库房和长期运维能力。

## 2 演示前准备

### 2.1 环境检查

进入项目目录并激活环境:

```bash
cd /path/to/fileforge
source .venv/bin/activate
```

如果使用 conda:

```bash
cd /path/to/fileforge
conda activate ftest
```

检查 Web 服务依赖:

```bash
python -c "import fastapi, sqlalchemy, psycopg; print('web deps ok')"
```

检查数据库连接:

```bash
python -c "from web_admin.settings import WebAdminSettings; settings=WebAdminSettings.from_env(); print('database configured:', bool(settings.database_url))"
```

检查 Web 健康接口:

```bash
curl http://127.0.0.1:18080/healthz
```

正常返回:

```json
{"status":"ok"}
```

### 2.2 数据库准备

正式演示建议使用 PostgreSQL:

```bash
export DB_PASSWORD="replace-with-demo-password"
export DATABASE_URL="postgresql+psycopg://fileforge:${DB_PASSWORD}@127.0.0.1:5432/fileforge_current"
alembic upgrade head
```

如只做界面演示,可使用临时 SQLite:

```bash
export DATABASE_URL="sqlite:////tmp/fileforge_web_demo.sqlite"
python -c "from sqlalchemy import create_engine; from infrastructure.db.models import Base; import os; engine=create_engine(os.environ['DATABASE_URL'], future=True); Base.metadata.create_all(engine)"
```

创建管理员账号:

```bash
python -m utils.user_admin users create \
  --username admin \
  --password "change-this-password" \
  --display-name "管理员" \
  --role platform_admin
```

不要把真实密码写入仓库或 PPT 截图。

### 2.3 处理环境准备

只浏览 Web 页面不需要 OCR 和模型服务。

如果现场点击“开始处理”,必须提前确认:

- PaddleOCR 运行环境可用。
- vLLM OpenAI 兼容服务已启动。
- `LLM_BASE_URL`、`LLM_MODEL_NAME`、`LLM_API_KEY`、`LLM_ENABLE_THINKING` 已按服务器环境设置。
- 演示数据规模很小,建议 3 到 5 份档案,每份 1 到 3 页。

建议现场准备两套数据:

- `demo_upload_small.zip`: 用于现场实时跑批。
- 已处理完成的数据库记录和导出文件: 用于模型或 OCR 环境不稳定时兜底展示。

## 3 启动顺序

### 3.1 启动模型服务

如需现场跑批,先启动 vLLM。命令以 `docs/vllm_server.md` 为准。

启动后确认 OpenAI 兼容接口可访问:

```bash
curl http://127.0.0.1:8000/v1/models
```

### 3.2 启动 Web 后台

```bash
uvicorn web_admin.app:create_app --factory --host 0.0.0.0 --port 18080
```

浏览器访问:

```text
http://服务器IP:18080/login
```

## 4 标准演示脚本

### 4.1 登录

打开 `/login`,使用演示账号登录。

讲述重点:

- 系统按用户和单位做基本权限隔离。
- 后台入口只保留整理项目、上传、批次、用户和单位等必要功能。

### 4.2 创建或确认单位

进入“单位”,确认已有演示单位。没有则新建:

```text
单位名称: 档案室
```

讲述重点:

- 单位用于区分数据归属和权限范围。
- 普通单位用户只能查看本单位数据。

### 4.3 创建整理项目

进入“整理项目”,新建一条演示项目:

```text
项目标识: demo_2026
整理项目名称: 毕业设计演示档案整理
单位: 档案室
描述: 用于展示扫描档案上传、处理、复核和导出流程
```

讲述重点:

- 整理项目表示一次档案整理任务。
- 上传、批次、结果和修订记录都归属到整理项目下。

### 4.4 上传扫描件

进入“上传”,选择整理项目并上传 `demo_upload_small.zip`。

zip 结构建议:

```text
demo_upload_small.zip
├─ doc_001/
│  ├─ page_001.jpg
│  └─ page_002.jpg
├─ doc_002/
│  └─ page_001.jpg
└─ doc_003/
   └─ page_001.jpg
```

讲述重点:

- zip 一级目录对应一份档案。
- 散图上传会被归为同一份档案,适合快速测试。

### 4.5 启动处理

上传成功后,在上传记录中点击“开始处理”。

讲述重点:

- 系统会创建处理批次和单档案任务。
- Web 请求只触发任务,实际 OCR、解析、规则校正和导出在后台执行。

### 4.6 查看处理进度

进入在线跑批进度页,查看:

- 批次状态。
- 每份档案任务状态。
- 进度百分比。
- 处理事件。

讲述重点:

- 批次级状态便于判断整体进度。
- 任务级状态便于定位单份档案失败。

### 4.7 查看档案结果

处理完成后,进入“查看档案结果”。

重点展示字段:

- 题名
- 责任者
- 年度
- 实体分类号
- 保管期限
- 档号
- 件号
- 开放状态

讲述重点:

- 最终字段不是单纯模型输出,还经过规则校正和件号生成。
- 档号和件号由系统按分组规则生成,不开放手工编辑。

### 4.8 人工修正

打开一条档案详情,点击“修正”,修改一个核心字段,例如题名或责任者。

讲述重点:

- Web 只开放 4 个最常修正字段:题名、责任者、实体分类号、保管期限。
- 修正会保留修订记录和审计日志。
- 人工修正后的记录受到保护,后续自动重跑不应直接覆盖人工结果。

### 4.9 查看修订和审计

进入修订记录和审计记录页面。

讲述重点:

- 修订记录回答“字段怎么变了”。
- 审计记录回答“谁在什么时候做了什么操作”。

### 4.10 查看导出结果

Web 在线跑批完成后,导出文件写入配置的输出目录,默认在:

```text
output_results/web_runs/
```

常见文件:

```text
archive_results_YYYYMMDD_HHMMSS.json
archive_results_YYYYMMDD_HHMMSS.csv
```

讲述重点:

- JSON 适合系统间交换和复核。
- CSV 适合 Excel 打开和人工校验。

## 5 兜底演示方案

如果现场 OCR、模型或 GPU 环境不可用,不要强行排错。改用兜底流程:

1. 登录 Web。
2. 展示已存在的整理项目。
3. 展示已处理完成的批次。
4. 打开档案结果和详情页。
5. 演示人工修正、修订记录和审计记录。
6. 展示提前生成的 JSON/CSV 文件。

答辩表述:

```text
现场为了保证时间,这里展示一批已经跑通的处理结果。系统的实时处理入口仍然保留在上传页,完整处理链路与命令行管线共用同一套 OCR、解析、规则校正和导出模块。
```

## 6 常见问题处理

### 6.1 数据库密码错误

现象:

```text
password authentication failed
```

处理:

- 检查 `DATABASE_URL` 中用户名、密码、数据库名。
- 密码包含 `@`、`:`、`/`、`#` 时需要 URL 编码。
- 先用 `psql` 单独验证连接。

### 6.2 登录页打不开

处理:

- 检查 `uvicorn` 是否启动。
- 检查端口是否被占用。
- 检查 `/healthz` 是否返回正常。

### 6.3 上传页没有整理项目

处理:

- 先进入“整理项目”创建 active 状态项目。
- 普通单位用户需要确认自己属于对应单位。

### 6.4 点击开始处理后失败

处理:

- 检查 PaddleOCR 和模型服务是否可用。
- 检查上传 zip 是否至少包含一张支持格式图片。
- 查看在线跑批进度页的事件和错误字段。
- 如 Web 后台任务中断,可用 CLI 补跑:

```bash
python -m utils.processing_runner --upload-batch-id 1
```

### 6.5 处理时间过长

处理:

- 现场只跑 3 到 5 份档案。
- 每份档案控制在 1 到 3 页。
- 优先使用已经压缩过的 jpg/png。
- 准备已处理完成的兜底结果。

## 7 答辩前检查清单

- Web 后台能打开 `/login`。
- 演示账号能登录。
- 至少有一个单位和一个整理项目。
- `demo_upload_small.zip` 能上传。
- vLLM 服务已启动并能返回模型列表。
- 小样本能完成一次处理。
- 已准备一批处理完成的兜底结果。
- 已准备 JSON/CSV 导出文件截图或实物文件。
- PPT 中所有截图已脱敏。
- 仓库没有提交真实扫描件、真实账号密码、本机路径或服务器内网 IP。
