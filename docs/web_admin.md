# Web 管理后台运行说明

本文说明当前 `web_admin/` 服务端渲染后台的实际运行方式。它只使用 PostgreSQL 数据库,不启动 OCR、PaddleOCR、vLLM,也不运行完整管线 `python main.py`。

## 1 安装依赖

Web 后台依赖数据库层和 FastAPI/Jinja2:

```bash
pip install -r requirements/web.txt
```

`requirements/web.txt` 会引用 `requirements/db.txt`。PaddlePaddle、vLLM、微调依赖不属于 Web 后台启动前置条件。

## 2 配置数据库

正式运行使用 PostgreSQL:

```bash
export DATABASE_URL="postgresql+psycopg://user:password@127.0.0.1:5432/fileforge"
```

Windows PowerShell:

```powershell
$env:DATABASE_URL="postgresql+psycopg://user:password@127.0.0.1:5432/fileforge"
```

可选 Web 配置:

```bash
export WEB_SESSION_COOKIE_NAME="fileforge_session"
export WEB_SESSION_TTL_SECONDS="28800"
export WEB_COOKIE_SECURE="false"
export WEB_CSRF_ENABLED="true"
```

生产 HTTPS 后应把 `WEB_COOKIE_SECURE=true`。

## 3 建表迁移

从仓库根目录运行:

```bash
alembic upgrade head
```

当前 Web 后台需要以下账号和 session 表已经存在:

- `organizations`
- `app_users`
- `roles`
- `permissions`
- `user_roles`
- `role_permissions`
- `web_sessions`

## 4 初始化管理员

当前仓库没有 `web_admin.manage` 模块。账号初始化使用已落地的 CLI:

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

## 6 当前页面范围

当前 Web 后台已经有以下页面和路由:

- `/login`: 登录页。
- `/`: 登录后的后台首页。
- `/admin/users`: 用户列表。
- `/admin/users/new`: 新建用户。
- `/admin/users/{user_id}/reset-password`: 重置密码。
- `/batches`: 按 `project_key` 查询批次。
- `/batches/{batch_id}`: 批次详情。
- `/batches/{batch_id}/archives`: 批次下档案列表和筛选。
- `/archives/{archive_id}`: 档案详情。
- `/archives/{archive_id}/revisions`: 修订记录。
- `/archives/{archive_id}/audit`: 审计记录。

## 7 权限与范围

- 登录使用 `app_users` 与 `web_sessions`。
- cookie 保存明文随机 session token,数据库只保存 `sha256(token)`。
- POST 表单使用 CSRF token 校验。
- `platform_admin` 可查看全平台数据。
- 普通单位用户只能访问本单位 `organization_id` 范围内的数据;批次和档案列表在数据库查询层按组织过滤后再分页。
- 批次、档案、修订记录需要 `archive:view`。
- 审计记录需要 `audit:view`。
- 用户管理需要 `user:manage`。

## 8 当前限制

- Web 后台不触发 OCR/LLM 跑批。
- Web 后台不提供在线 metadata 修正保存。
- Web 后台不提供项目/单位管理页面。
- `web_admin.manage` 尚未实现;管理员初始化继续使用 `utils.user_admin`。
- 当前页面是服务端渲染 HTML,无前端构建链。

## 9 验证建议

在可运行环境中安装依赖后,至少执行:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

本说明不要求启动 PaddleOCR、vLLM 或运行 `python main.py`。
