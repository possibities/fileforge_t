# PostgreSQL 基础后台运行说明

本文覆盖当前已经落地的数据库基础 CLI 能力:人员/用户管理、登录校验、批次/档案/详情/修订/审计查询。Web 管理后台运行方式见 `docs/web_admin.md`。它不启动 OCR、LLM、PaddleOCR、vLLM,也不运行完整管线 `python main.py`。

## 1 安装依赖

```bash
pip install -r requirements/db.txt
```

## 2 配置数据库

正式运行使用 PostgreSQL。设置 `DATABASE_URL`:

```bash
export DATABASE_URL="postgresql+psycopg://user:password@127.0.0.1:5432/fileforge"
```

Windows PowerShell:

```powershell
$env:DATABASE_URL="postgresql+psycopg://user:password@127.0.0.1:5432/fileforge"
```

## 3 建表迁移

从仓库根目录运行:

```bash
alembic upgrade head
```

迁移脚本目录是 `infrastructure/db/migrations/versions/`。不要把数据库连接串写入 `alembic.ini`。

## 4 初始化角色和创建人员

初始化内置角色和权限:

```bash
python -m utils.user_admin roles init
```

创建平台管理员:

```bash
python -m utils.user_admin users create \
  --username admin \
  --password "change-this-strong-password" \
  --display-name "系统管理员" \
  --role platform_admin
```

创建单位:

```bash
python -m utils.user_admin orgs create --name "档案室"
```

创建单位操作员:

```bash
python -m utils.user_admin users create \
  --username operator01 \
  --password "change-this-strong-password" \
  --display-name "档案操作员" \
  --organization-id 1 \
  --role org_operator
```

列出人员:

```bash
python -m utils.user_admin users list
```

登录校验:

```bash
python -m utils.user_admin login --username admin --password "change-this-strong-password"
```

禁用用户:

```bash
python -m utils.user_admin users disable --username operator01
```

重置密码:

```bash
python -m utils.user_admin users reset-password \
  --username admin \
  --password "new-change-this-strong-password"
```

## 5 查询批次、档案和审计

这些命令复用阶段 1C 的只读查询层 `infrastructure/db/queries.py`。

批次列表:

```bash
python -m utils.archive_query batches list --project-key PROJECT_KEY
```

批次详情:

```bash
python -m utils.archive_query batches show --batch-id 1
```

档案列表:

```bash
python -m utils.archive_query archives list --batch-id 1 --page 1 --page-size 50
```

档案筛选:

```bash
python -m utils.archive_query archives list \
  --batch-id 1 \
  --archive-year 2026 \
  --classification-code DQL \
  --processing-status success \
  --title-like 简报
```

档案详情:

```bash
python -m utils.archive_query archives show --archive-id 1
```

修订记录:

```bash
python -m utils.archive_query revisions list --archive-id 1
```

审计记录:

```bash
python -m utils.archive_query audit list --target-type archive --target-id 1
```

## 6 本地验收

当前环境只需要 SQLite 单测即可验证这些数据库基础功能:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

不要为这组验收启动 PaddleOCR、vLLM、PostgreSQL,也不要运行 `python main.py`。

## 7 Web 后台入口

Web 后台已经单独放在 `web_admin/` 包中。安装 `requirements/web.txt`、执行 `alembic upgrade head`、用 `utils.user_admin` 初始化管理员后,通过 FastAPI app factory 启动:

```bash
uvicorn web_admin.app:create_app --factory --host 0.0.0.0 --port 8080
```

当前没有 `web_admin.manage` 模块,不要使用 `python -m web_admin.manage create-admin`。管理员初始化继续使用本文第 4 节的 `python -m utils.user_admin ...` 命令。
