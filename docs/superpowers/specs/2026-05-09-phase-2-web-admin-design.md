# 阶段 2 设计:Web 管理后台 MVP

## 0 实施修订记录 (2026-05-13)

实施过程中针对包结构与迁移做了 3 处调整,已纳入最终方案(本节优先于下方相关段落):

1. **账户服务** 落在 `infrastructure/db/accounts.py`,不在 `web_admin/accounts.py`。原因:`utils/user_admin.py` CLI 与 Web 共用账户服务,沉到 infrastructure 层让依赖单向 `web_admin / utils → infrastructure/db`。
2. **密码哈希**(`hash_password` / `verify_password`)随账户服务放在 `infrastructure/db/accounts.py`。`web_admin/security.py` 仅承担 session/CSRF token 原语。
3. **迁移拆分**:`0003_web_admin_accounts.py`(6 张账户表)+ `0004_web_sessions.py`(运行时 session 表)。结构性 vs 运行时分两次迁移,生命周期不同。

附带调整:管理 CLI 落在 `utils/user_admin.py`,而非 `web_admin/manage.py`;账户/CLI 单测命名为 `tests/test_db_*.py` / `tests/test_user_admin_cli.py`。

## 1 目标与背景

阶段 1C 已经完成只读查询层 `infrastructure/db/queries.py` 和 CLI 查询入口。阶段 2 的目标是在不改动 OCR/LLM 主流程的前提下,启动 Web 管理后台,让 PostgreSQL 从“旁路写库和 CLI 查询”演进为“可登录、可管人、可查询业务数据”的管理端数据源。

本设计面向第一个可执行里程碑:

- 使用 PostgreSQL 数据库账号登录。
- 提供人员/用户管理:用户列表、新建、禁用、重置密码。
- 通过 Web 查询批次、档案、档案详情、修订记录、审计记录。
- 正式运行使用 PostgreSQL,单测继续使用 SQLite in-memory。
- 不启动 `python main.py`,不启动 PaddleOCR、vLLM、PostgreSQL。

## 2 范围

### 2.1 新增能力

| 能力 | 阶段 2 MVP 行为 |
| --- | --- |
| Web 框架 | 新增轻量 FastAPI 应用,服务端渲染 HTML |
| 登录/退出 | 用户名 + 密码登录,服务端 session,退出撤销 session |
| 用户模型 | `organizations`、`app_users`、`roles`、`permissions`、关联表、`web_sessions` |
| 密码哈希 | 标准库 `hashlib.pbkdf2_hmac("sha256")`,带随机 salt 和迭代次数 |
| 认证 | HttpOnly cookie 保存随机 session token,数据库只保存 token hash |
| 权限 | 内置 `platform_admin`、`org_admin`、`org_operator`;MVP 以角色聚合权限 |
| 用户管理 | 列表、新建、禁用、重置密码;不物理删除用户 |
| 批次查询 | 复用 `queries.list_batches` / `get_batch_detail` |
| 档案查询 | 复用 `queries.list_archives` / `get_archive_detail` |
| 修订/审计 | 复用 `queries.list_revisions` / `list_audit_logs`,只读展示 |
| 迁移 | 新增 `0003_web_admin_accounts.py`,不修改既有 0001/0002 |
| 测试 | `unittest` + SQLite + FastAPI `TestClient`,不依赖外部服务 |

### 2.2 不在本里程碑范围

- 不实现档案 metadata 的 Web 在线修正保存。
- 不实现上传、后台提交 OCR/LLM 任务、任务队列或重试按钮。
- 不实现单位/项目完整管理页面。
- 不实现 OCR 原文全文搜索或 trigram 索引。
- 不把 Web app 接入 `main.py` 或批处理热路径。
- 不修改 `processors/batch_processor.py`、`core/classifier.py`、`infrastructure/db/recorder.py`、`infrastructure/db/repositories.py`、`infrastructure/db/engine.py`、`infrastructure/db/allocator.py`。

`infrastructure/db/models.py` 需要新增账户相关 ORM 类。这是数据库 schema 变更的必要入口,不影响 OCR/LLM 主流程;实施时应单独说明原因并只追加账号模型,不改已有表语义。

## 3 方案比较

### 3.1 推荐方案:FastAPI + Jinja2 服务端渲染

FastAPI 适合本项目的第一阶段后台:测试客户端成熟,依赖少,路由函数可直接使用 SQLAlchemy session,HTML 表单即可覆盖登录和用户管理。服务端渲染避免引入前端构建链,也避免当前阶段把 API、SPA、鉴权状态管理同时铺开。

### 3.2 备选方案:Flask + Jinja2

Flask 也能满足需求,依赖更少,但本项目后续很可能需要 JSON API、类型化请求/响应和自动测试夹具。FastAPI 在这些方向上更顺滑,同时不会显著增加复杂度。

### 3.3 备选方案:API-only + 前端 SPA

API-only 更适合功能成熟后的前后端分离平台,但第一阶段需要登录、用户管理和查询页面即可验收。引入 SPA 会增加 Node/构建/部署/测试成本,且不提升当前数据查询能力。

结论:阶段 2 MVP 采用 FastAPI + Jinja2 服务端渲染。

## 4 架构设计

### 4.1 包结构

新增 `web_admin/` 包(以 § 0 修订为准):

| 文件 | 责任 |
| --- | --- |
| `web_admin/app.py` | `create_app()` 工厂,注册路由、模板、静态文件和异常处理 |
| `web_admin/settings.py` | Web 配置读取:数据库 URL、cookie 名称、session TTL、CSRF 开关 |
| `web_admin/db.py` | Web 请求级 session dependency,复用 `infrastructure.db.engine` |
| `web_admin/security.py` | session/CSRF token 生成、token hash、常量时间比较(密码哈希见 `infrastructure/db/accounts.py`) |
| `web_admin/auth.py` | 登录校验、session 创建/撤销、当前用户加载、权限判断;密码校验委托给 `infrastructure.db.accounts.authenticate_user` |
| `web_admin/routes/auth.py` | 登录页、登录提交、退出 |
| `web_admin/routes/users.py` | 用户列表、新建、禁用、重置密码 |
| `web_admin/routes/archives.py` | 批次、档案、详情、修订、审计只读页面 |
| `web_admin/templates/` | Jinja2 模板 |
| `web_admin/static/` | 少量 CSS,无前端构建链 |

共享于 Web 与 CLI 的账户服务与管理 CLI:

| 文件 | 责任 |
| --- | --- |
| `infrastructure/db/accounts.py` | 账户服务:密码哈希、用户/角色/权限 CRUD、`authenticate_user`、`ensure_builtin_roles` |
| `utils/user_admin.py` | 管理 CLI:`roles init` / `orgs create` / `users {create,list,disable,reset-password}` / `login` |

`web_admin` 可以直接依赖数据库包,因为它只在安装 `requirements/web.txt` 后运行。批处理文件-only 路径不得 import `web_admin`。

### 4.2 数据模型

新增表:

| 表 | 关键字段 | 约束 |
| --- | --- | --- |
| `organizations` | `id,name,status,created_at,updated_at` | `name` 唯一;状态 `active/disabled` |
| `app_users` | `id,organization_id,username,password_hash,display_name,status,last_login_at,created_at,updated_at` | `username` 唯一;状态 `active/disabled` |
| `roles` | `id,code,name,description,created_at` | `code` 唯一 |
| `permissions` | `id,code,description,created_at` | `code` 唯一 |
| `user_roles` | `user_id,role_id,created_at` | `(user_id,role_id)` 唯一 |
| `role_permissions` | `role_id,permission_id,created_at` | `(role_id,permission_id)` 唯一 |
| `web_sessions` | `id,user_id,token_hash,csrf_token_hash,expires_at,revoked_at,created_at,last_seen_at` | `token_hash` 唯一 |

内置角色与权限沿用数据契约 §4.2:

- `platform_admin`:全平台管理,可访问所有批次、档案、用户、审计。
- `org_admin`:本单位用户管理和本单位数据查询。
- `org_operator`:本单位数据查询,可查看批次、档案、修订和审计。

MVP 中 `projects.organization_id`、`processing_batches.organization_id`、`archive_records.organization_id` 已是预留整数字段。阶段 2 不强行给旧表补 FK,只新增索引并在查询服务中使用这些字段做范围过滤。`organization_id IS NULL` 的项目/批次/档案只允许 `platform_admin` 访问,避免无单位归属数据被普通单位用户误查。

### 4.3 认证与 session

登录流程:

1. 用户提交 username/password。
2. `auth.authenticate_user()` 查找 active 用户,验证密码 hash。
3. 用户所属单位若存在且已 disabled,拒绝登录。
4. 生成 32 字节随机 session token 和 CSRF token。
5. 数据库存储 `sha256(token)` 与 `sha256(csrf_token)`,cookie 只保存明文 token。
6. 响应设置 `fileforge_session` cookie:`HttpOnly`, `SameSite=Lax`, `Secure` 由配置控制。

退出流程:

1. 按 cookie token hash 找到 session。
2. 写入 `revoked_at`。
3. 清除 cookie。

CSRF:

- 所有会修改状态的 HTML form 使用 hidden `csrf_token`。
- GET 页面读取 session 的 CSRF token并渲染表单。
- POST 请求校验 token hash,失败返回 403。

### 4.4 权限与数据范围

权限判断分两层:

- 路由权限:例如用户管理需要 `user:manage`,批次/档案查询需要 `archive:view`,审计查询需要 `audit:view`。
- 数据范围:platform admin 不限范围;org admin/operator 只能访问 `organization_id == current_user.organization_id` 的项目、批次和档案。

Phase 1C 的 `queries.py` 以 `project_key`、`batch_id`、`archive_id` 为入口,没有内置账号范围。Web 层应先做范围校验,再调用 `queries.py`,不把账号概念塞进 `queries.py`。

### 4.5 页面设计

页面采用朴素后台布局:顶部导航 + 左侧或顶部菜单 + 主内容表格。第一阶段优先信息密度、筛选效率和错误反馈,不做营销式首页。

页面清单:

| 页面 | 路径 | 权限 |
| --- | --- | --- |
| 登录 | `/login` | 未登录 |
| 退出 | `/logout` | 已登录 |
| 用户列表 | `/admin/users` | `user:manage` |
| 新建用户 | `/admin/users/new` | `user:manage` |
| 禁用用户 | `/admin/users/{id}/disable` | `user:manage` |
| 重置密码 | `/admin/users/{id}/reset-password` | `user:manage` |
| 批次列表 | `/batches?project_key=...` | `archive:view` |
| 批次详情 | `/batches/{batch_id}` | `archive:view` |
| 档案列表 | `/batches/{batch_id}/archives` | `archive:view` |
| 档案详情 | `/archives/{archive_id}` | `archive:view` |
| 修订记录 | `/archives/{archive_id}/revisions` | `archive:view` |
| 审计记录 | `/archives/{archive_id}/audit` | `audit:view` |

列表页面继续使用 Phase 1C 的分页约束:`page >= 1`, `page_size ∈ [1, 200]`。

### 4.6 错误处理

- 未登录访问保护页面:重定向到 `/login`。
- 已登录但权限不足:403。
- 找不到批次/档案/用户:404。
- 数据库连接失败:启动或请求阶段显式 500,不返回空列表伪装成功。
- 表单校验失败:重新渲染表单并显示字段错误。
- 禁用当前登录用户:禁止,返回表单错误。
- 重置密码要求新密码满足最小长度 12。

### 4.7 依赖

新增 `requirements/web.txt`:

```text
-r db.txt

fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
jinja2>=3.1,<4.0
python-multipart>=0.0.9,<1.0
httpx>=0.27,<1.0
```

`httpx` 用于 FastAPI/Starlette `TestClient` 测试。`requirements/base.txt` 和 `requirements/db.txt` 不加 Web 依赖,避免污染纯批处理环境。

### 4.8 迁移设计

新增两个迁移文件(以 § 0 修订为准):

- `infrastructure/db/migrations/versions/0003_web_admin_accounts.py`:6 张账户类表(`organizations` / `app_users` / `roles` / `permissions` / `user_roles` / `role_permissions`)及索引。
- `infrastructure/db/migrations/versions/0004_web_sessions.py`:`web_sessions` 运行时表及索引(`ix_web_sessions_user` / `ix_web_sessions_expires` / `ix_web_sessions_revoked`,以及 `uq_web_sessions_token_hash`)。

拆分理由:账户类表是结构性域模型,session 表是运行时状态。两类表生命周期不同,分两个 revision 让任一边后续演进不污染另一边。与现有迁移一致,两个文件的 `downgrade()` 均抛 `NotImplementedError`。

SQLite 单测用 `Base.metadata.create_all()` 创建模型,不会跑真实 PostgreSQL migration。迁移文件仍需与 ORM 字段保持一致,并保留 JSON/时间类型的 SQLite 兼容写法。

## 5 测试策略

测试继续使用 `unittest`:

- `tests/test_web_security.py`:密码 hash、token hash、常量时间比较。
- `tests/test_web_auth.py`:登录成功/失败、禁用用户拒绝、session 过期、logout 撤销。
- `tests/test_web_accounts.py`:创建用户、唯一用户名、角色绑定、禁用、重置密码。
- `tests/test_web_routes_auth.py`:登录页、登录提交、退出 cookie 行为。
- `tests/test_web_routes_users.py`:用户管理页面权限与表单行为。
- `tests/test_web_routes_queries.py`:批次/档案/详情/修订/审计页面复用 `queries.py`。
- `tests/test_web_migrations.py`:ORM metadata 包含新表和关键约束名。

所有测试使用 SQLite in-memory 和 FastAPI `TestClient`,不连接 PostgreSQL,不启动 OCR/LLM,不运行 `main.py`。

验收命令:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

## 6 数据契约增补

Phase 2 完成后应在 `docs/postgresql_data_contract_design.md` 新增 Web 管理后台章节,记录:

- 账户表、session 表和角色权限映射。
- session cookie 与 CSRF 策略。
- Web 层如何复用 Phase 1C read-side queries。
- 组织范围过滤规则:`organization_id IS NULL` 仅平台管理员可见。

## 7 自审

- 范围聚焦在 Web MVP,没有把任务队列、在线修正、全文搜索放入第一个里程碑。
- 账户模型与数据契约 §4.2 保持一致,并补充 `web_sessions` 满足数据库登录和退出。
- 读侧查询不重复实现 SQL,Web 层复用 `infrastructure/db/queries.py`。
- SQLite 单测兼容路径明确,无 PostgreSQL、vLLM、PaddleOCR 运行要求。
- 唯一需要触碰的受限核心文件是 `infrastructure/db/models.py`;原因是新增账户 ORM 模型,不涉及 OCR/LLM 主流程。
