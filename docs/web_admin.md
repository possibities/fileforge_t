# Web 管理后台运行说明

本文说明当前 `web_admin/` 服务端渲染后台的实际运行方式。Web 后台依赖 PostgreSQL；浏览、用户管理、项目管理、人工修正只需要 Web/DB 依赖，浏览器上传后启动在线跑批时才需要 PaddleOCR、vLLM 和完整管线运行环境。

> 本文命令面向可执行目标环境。当前会话若为非可执行环境,只做静态阅读和文档维护,不声明 Web、OCR、vLLM、迁移或测试已运行。

## 1 安装依赖

只运行后台页面:

```bash
pip install -r requirements/web.txt
```

`requirements/web.txt` 会引用 `requirements/db.txt`。如果要在 Web 中点击“开始处理”执行 OCR/LLM 跑批，还需要按部署文档安装 OCR/GPU/vLLM 相关依赖，并确保 vLLM OpenAI 兼容服务可访问。

## 2 配置数据库和 Web

正式运行使用 PostgreSQL:

```bash
export DATABASE_URL="postgresql+psycopg://user:password@127.0.0.1:5432/fileforge_current"
```

Windows PowerShell:

```powershell
$env:DATABASE_URL="postgresql+psycopg://user:password@127.0.0.1:5432/fileforge_current"
```

可选 Web 配置:

```bash
export WEB_SESSION_COOKIE_NAME="fileforge_session"
export WEB_SESSION_TTL_SECONDS="28800"
export WEB_COOKIE_SECURE="false"
export WEB_CSRF_ENABLED="true"
export WEB_UPLOAD_STORAGE_ROOT="input_documents/web_uploads"
export WEB_PROCESSING_OUTPUT_ROOT="output_results/web_runs"
export WEB_MAX_UPLOAD_BYTES="209715200"
export WEB_MAX_UPLOAD_FILES="2000"
```

生产 HTTPS 后应把 `WEB_COOKIE_SECURE=true`。`WEB_UPLOAD_STORAGE_ROOT` 保存上传原图，`WEB_PROCESSING_OUTPUT_ROOT` 保存在线跑批导出的 JSON/CSV。

### 2.1 长期固定配置

服务器长期运行时不要把数据库名和密码写进代码。推荐固定到 `/etc/fileforge/fileforge.env`:

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

手动执行迁移、初始化账号或运行 CLI 前加载:

```bash
cd /path/to/fileforge
source .venv/bin/activate
set -a
source /etc/fileforge/fileforge.env
set +a
```

如果数据库密码包含 `@`、`:`、`/`、`#` 等字符,需要在 `DATABASE_URL` 里做 URL 编码;也可以先使用不含这些字符的强密码降低部署复杂度。

## 3 建表迁移

从仓库根目录运行:

```bash
alembic upgrade head
```

当前 schema 由 `0005_upload_online_processing` 重建。项目没有生产数据，因此该迁移会删除除 `alembic_version` 外的旧应用表，再按当前 ORM 建表。

当前 Web 后台需要的核心表:

- 账号与会话:`organizations`、`app_users`、`web_sessions`
- 上传:`upload_batches`、`uploaded_files`
- 处理:`processing_batches`、`processing_jobs`、`processing_events`
- 结果:`archive_records`、`archive_pages`、`llm_traces`
- 序号、导出、追溯:`sequence_counters`、`export_files`、`metadata_revisions`、`audit_logs`

角色权限已简化为 `app_users.role` 字段。`python -m utils.user_admin roles init` 仍可执行，但只是兼容命令，不再向独立 RBAC 表写 seed 数据。

## 4 初始化管理员

账号初始化使用 CLI:

```bash
python -m utils.user_admin roles init
python -m utils.user_admin users create \
  --username admin \
  --password "change-this-strong-password" \
  --display-name "系统管理员" \
  --role platform_admin
```

也可以继续使用同一 CLI 创建单位和单位用户:

```bash
python -m utils.user_admin orgs create --name "档案室"
python -m utils.user_admin users create \
  --username operator01 \
  --password "change-this-strong-password" \
  --display-name "档案操作员" \
  --organization-id 1 \
  --role org_operator
```

## 5 启动 Web 后台

`create_app()` 是 FastAPI app factory,用 `--factory` 启动:

```bash
uvicorn web_admin.app:create_app --factory --host 0.0.0.0 --port 8080
```

本地访问:

```text
http://127.0.0.1:8080/login
```

### 5.1 systemd 长期运行

如果希望 Web 后台随服务器启动,创建 systemd 服务:

```bash
sudo nano /etc/systemd/system/fileforge-web.service
```

示例内容,把 `/path/to/fileforge` 换成实际项目路径:

```ini
[Unit]
Description=FileForge Web Admin
After=network.target docker.service

[Service]
WorkingDirectory=/path/to/fileforge
EnvironmentFile=/etc/fileforge/fileforge.env
ExecStart=/path/to/fileforge/.venv/bin/uvicorn web_admin.app:create_app --factory --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启动并设置开机自启:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fileforge-web
sudo systemctl status fileforge-web
```

查看日志:

```bash
journalctl -u fileforge-web -f
```

## 6 上传和在线跑批

在线跑批路径:

1. 登录后台。
2. 进入 `/admin/projects` 创建或确认项目。
3. 进入 `/uploads`。
4. 选择项目,上传散图或 zip。
5. 上传成功后点击“开始处理”。
6. 系统创建 `processing_batches` 和 `processing_jobs`。
7. FastAPI background task 调用 `run_upload_processing_batch`。
8. 后台任务复用 `ArchiveClassifier + BatchProcessor + BatchRecorder` 完成 OCR、LLM、规则、导出和入库。
9. 进入 `/processing/batches/{batch_id}` 查看任务进度、事件和结果入口。

zip 上传约定:一级目录表示一份档案；散图上传会被归为同一份档案。

也可以用 CLI 处理已经上传成功的批次，适合演示补跑或未来独立 worker 过渡:

```bash
python -m utils.processing_runner --upload-batch-id 1
```

该命令会创建同样的 `processing_batches` / `processing_jobs`，再复用 Web 后台任务的处理函数；它不是第二套 OCR/LLM 抽取逻辑。

## 7 当前页面范围

- `/login`: 登录页。
- `/`: 登录后的后台首页。
- `/uploads`: 上传图片/zip、查看上传批次、启动处理。
- `/processing/batches/{batch_id}`: 在线跑批进度、任务列表和事件流。
- `/admin/users`: 用户列表。
- `/admin/users/new`: 新建用户。
- `/admin/users/{user_id}/reset-password`: 重置密码。
- `/admin/organizations`: 单位列表。
- `/admin/organizations/new`: 新建单位。
- `/admin/organizations/{organization_id}/disable` 与 `/enable`: 切单位 status。
- `/admin/projects`: 项目列表。
- `/admin/projects/new`: 新建项目；项目唯一标识由系统自动生成。
- `/admin/projects/{project_id}/disable` 与 `/enable`: 切项目 status。
- `/batches`: 按 `project_key` 查询批次。
- `/batches/{batch_id}`: 批次详情。
- `/batches/{batch_id}/archives`: 批次下档案列表和筛选。
- `/archives/{archive_id}`: 档案详情;通过 `?notice=no_change` 显示“无字段变化”提示。
- `/archives/{archive_id}/revisions`: 修订记录。
- `/archives/{archive_id}/audit`: 审计记录。
- `/archives/{archive_id}/edit`: 元数据人工修正(GET 表单 + POST 提交);仅允许编辑题名 / 责任者 / 实体分类号 / 保管期限 4 个字段。

## 8 权限与范围

- 登录使用 `app_users` 与 `web_sessions`。
- cookie 保存明文随机 session token,数据库只保存 `sha256(token)`。
- POST 表单使用 CSRF token 校验。
- `platform_admin` 可查看全平台数据。
- 普通单位用户只能访问本单位 `organization_id` 范围内的数据;跨单位访问统一 404。
- 上传和启动处理需要 `batch:manage`。
- 批次、档案、修订记录需要 `archive:view`。
- 元数据修正需要 `archive:correct`。
- 审计记录需要 `audit:view`。
- 用户管理需要 `user:manage`。
- 单位管理需要 `organization:manage`。
- 项目管理需要 `project:manage`。

内置角色:

- `platform_admin`: 平台管理员,全权,跨单位。
- `org_admin`: 单位管理员,管理本单位项目、用户、上传和档案。
- `org_operator`: 单位操作员,查看本单位档案,可上传跑批和修正核心字段。

## 9 当前限制

- 在线处理现在使用 FastAPI background task,适合毕业设计演示和小规模使用;生产环境建议替换为独立 worker/队列。
- Web 在线修正只覆盖 4 个核心字段(题名 / 责任者 / 实体分类号 / 保管期限);其它字段仍走规则重跑或后续扩展。
- `档号` / `件号` 由 `SequenceGenerator` 分配,任何时候都不开放手工编辑。
- 一期修正采用 last-write-wins,无乐观锁;并发提交都会留痕,后到的覆盖 `final_metadata`。
- 当前页面是服务端渲染 HTML,无前端构建链。

## 10 验证建议

在可运行目标环境中安装依赖后,至少执行:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

如果只验证 Web 登录和查询页面,不要求启动 PaddleOCR、vLLM 或运行 `python main.py`。如果验证 `/uploads` 的在线跑批,必须准备可访问的 vLLM 服务和 OCR 运行环境。非可执行环境只能确认文档和代码路径是否一致。
