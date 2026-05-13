# Phase 2 Web Admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal FastAPI Web admin that supports PostgreSQL-backed login, user management, and read-only batch/archive/revision/audit querying.

**Architecture:** Keep the OCR/LLM pipeline untouched. Add a separate `web_admin` package that uses SQLAlchemy sessions, account/session services, Jinja2 templates, and the existing `infrastructure/db/queries.py` read-side API.

**Tech Stack:** Python, FastAPI, Jinja2, SQLAlchemy 2.x, Alembic, unittest, SQLite in-memory tests, PostgreSQL for production runtime.

---

## 实施修订记录 (2026-05-13)

下列 3 处偏差已被采纳为最终方案,原任务描述保留作为设计参考,**以本节为准**:

1. **账户服务位置**:服务实现落在 `infrastructure/db/accounts.py`,而非原计划的 `web_admin/accounts.py`。原因:`utils/user_admin.py` CLI 与 `web_admin` 同时消费账户服务,放在 infrastructure 层让依赖单向 `web_admin → infrastructure/db`,避免 CLI 反向依赖表现层。
2. **密码哈希位置**:`hash_password` / `verify_password` 随账户服务一起放在 `infrastructure/db/accounts.py`,而非原计划的 `web_admin/security.py`。后者仅承担 session / CSRF token 相关原语。两种 hash 按消费者域切分(密码 ↔ 账户,token ↔ web session),依赖方向正确。
3. **迁移拆分**:原计划单一 `0003_web_admin_accounts.py` 包含 7 张表;实际拆为 `0003`(6 张账户类表,结构性域模型)+ `0004_web_sessions.py`(运行时 session 表)。两类表生命周期不同,分迁移利于回滚与并行演进。

附带的位置/命名调整:
- 管理 CLI 落在 `utils/user_admin.py`,而非 `web_admin/manage.py`(同样为避免 CLI 反向依赖 web 层)。
- 账户/CLI 相关测试用 `tests/test_db_*.py` / `tests/test_user_admin_cli.py` 命名,而非 `tests/test_web_*.py`。

---

## File Structure

| 文件 | 类型 | 责任 |
| --- | --- | --- |
| `requirements/web.txt` | Create | Web/admin dependency layer extending `requirements/db.txt` |
| `web_admin/__init__.py` | Create | Package marker, no DB connection at import time |
| `web_admin/app.py` | Create | `create_app()` factory and router registration |
| `web_admin/settings.py` | Create | Web config from env with test overrides |
| `web_admin/db.py` | Create | Request-level SQLAlchemy session dependency |
| `web_admin/security.py` | Create | Session/CSRF token helpers(`generate_token` / `hash_token` / `verify_token_hash`)。密码哈希见 `infrastructure/db/accounts.py` |
| `web_admin/auth.py` | Create | Auth/session/permission service,通过 `infrastructure.db.accounts.authenticate_user` 校验密码 |
| `infrastructure/db/accounts.py` | Create(caf33f6 已落地) | Account service:密码哈希、用户/角色/权限 CRUD、`authenticate_user`,Web 与 CLI 共用 |
| `utils/user_admin.py` | Create(caf33f6 已落地) | 管理 CLI:`roles init` / `orgs create` / `users {create,list,disable,reset-password}` / `login` |
| `web_admin/routes/auth.py` | Create | Login/logout routes |
| `web_admin/routes/users.py` | Create | User management routes |
| `web_admin/routes/archives.py` | Create | Batch/archive/revision/audit routes |
| `web_admin/templates/*.html` | Create | Server-rendered admin templates |
| `web_admin/static/admin.css` | Create | Minimal admin styling |
| `infrastructure/db/models.py` | Modify | Add account/session ORM models only |
| `infrastructure/db/migrations/versions/0003_web_admin_accounts.py` | Create(caf33f6 已落地) | 账户类 6 张表迁移(organizations / app_users / roles / permissions / user_roles / role_permissions) |
| `infrastructure/db/migrations/versions/0004_web_sessions.py` | Create | `web_sessions` 表独立迁移,结构性 vs 运行时分离 |
| `docs/postgresql_data_contract_design.md` | Modify | Add Phase 2 Web/admin account contract |
| `tests/test_web_*.py` | Create | Web 层 SQLite 与 FastAPI route tests |
| `tests/test_db_accounts.py` / `test_db_account_models.py` / `test_user_admin_cli.py` | Create(caf33f6 已落地) | 账户服务、ORM 元数据、CLI 子命令的 SQLite 单测 |

Implementation note: modifying `infrastructure/db/models.py` is necessary because Phase 2 adds database-backed users, roles, permissions, and sessions. Do not change existing pipeline models beyond adding these new classes and exports.

---

## Task 1: Web Dependency Layer And App Scaffold

> **Status (2026-05-13):** 已实现于工作树(未提交)— `requirements/web.txt`、`web_admin/{__init__,settings,app,db}.py`、`tests/test_web_app.py`。`/healthz` 走通。

**Files:**
- Create: `requirements/web.txt`
- Create: `web_admin/__init__.py`
- Create: `web_admin/settings.py`
- Create: `web_admin/app.py`
- Create: `web_admin/db.py`
- Create: `tests/test_web_app.py`

- [ ] **Step 1: Write failing tests**

Add `tests/test_web_app.py`:

```python
import unittest


class TestWebAppScaffold(unittest.TestCase):
    def test_create_app_returns_fastapi_app(self):
        from fastapi import FastAPI
        from web_admin.app import create_app

        app = create_app(database_url="sqlite://")
        self.assertIsInstance(app, FastAPI)

    def test_healthcheck(self):
        from fastapi.testclient import TestClient
        from web_admin.app import create_app

        client = TestClient(create_app(database_url="sqlite://"))
        response = client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
```

- [ ] **Step 2: Run the focused test and confirm failure**

Run:

```bash
python -m unittest tests.test_web_app -v
```

Expected: import fails because `web_admin` does not exist.

- [ ] **Step 3: Add dependencies and minimal app**

Create `requirements/web.txt`:

```text
-r db.txt

fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
jinja2>=3.1,<4.0
python-multipart>=0.0.9,<1.0
httpx>=0.27,<1.0
```

Implement `create_app(database_url: str | None = None)`, store settings on `app.state.settings`, and add `GET /healthz`.

- [ ] **Step 4: Run focused test and full regression**

Run:

```bash
python -m unittest tests.test_web_app -v
python -m unittest discover -s tests -p "test_*.py"
```

- [ ] **Step 5: Commit**

```bash
git add requirements/web.txt web_admin tests/test_web_app.py
git commit -m "web: scaffold FastAPI admin app"
```

---

## Task 2: Account ORM Models And Migration

> **Status (2026-05-13):** 已分两次落地:
> - caf33f6 提交了 `0003_web_admin_accounts.py` + 6 个账户 ORM 模型(`Organization`、`AppUser`、`Role`、`Permission`、`UserRole`、`RolePermission`)+ `tests/test_db_account_models.py`。
> - 工作树新增 `0004_web_sessions.py` + `WebSession` ORM 模型 + `models.py` `__all__` 导出。
>
> 原计划单 0003 改为 0003(账户)+ 0004(session)拆分,见顶部"实施修订记录"。`tests/test_web_models.py` 不再单独创建;`web_sessions` 元数据断言落在 `tests/test_web_auth.py::test_web_session_table_is_registered`,账户表元数据断言落在 `tests/test_db_account_models.py`。

**Files:**
- Modify: `infrastructure/db/models.py`
- Create: `infrastructure/db/migrations/versions/0003_web_admin_accounts.py`
- Create: `tests/test_web_models.py`

- [ ] **Step 1: Write failing model tests**

Add tests that assert `Base.metadata.tables` contains:

```python
{
    "organizations",
    "app_users",
    "roles",
    "permissions",
    "user_roles",
    "role_permissions",
    "web_sessions",
}
```

Also assert `app_users.username` and `web_sessions.token_hash` have unique constraints or unique indexes.

- [ ] **Step 2: Run focused test and confirm failure**

Run:

```bash
python -m unittest tests.test_web_models -v
```

Expected: missing table assertions fail.

- [ ] **Step 3: Add ORM models**

Append models:

- `Organization`
- `AppUser`
- `Role`
- `Permission`
- `UserRole`
- `RolePermission`
- `WebSession`

Use status enums as VARCHAR-backed SQLAlchemy `Enum(native_enum=False)`. Add exports to `__all__`. Keep existing model classes unchanged.

- [ ] **Step 4: Add migration**

Create `0003_web_admin_accounts.py` with `down_revision = "0002_revisions_audit"`. It must create the seven new tables and useful indexes:

- `ix_app_users_status`
- `ix_app_users_organization`
- `ix_roles_code`
- `ix_permissions_code`
- `ix_web_sessions_user_expires`

Use `NotImplementedError` in `downgrade()`, matching existing migrations.

- [ ] **Step 5: Run focused test and full regression**

Run:

```bash
python -m unittest tests.test_web_models -v
python -m unittest discover -s tests -p "test_*.py"
```

- [ ] **Step 6: Commit**

```bash
git add infrastructure/db/models.py infrastructure/db/migrations/versions/0003_web_admin_accounts.py tests/test_web_models.py
git commit -m "db: add web admin account models"
```

---

## Task 3: Password And Token Security Service

> **Status (2026-05-13):** 职责已重新切分:
> - **密码哈希**(`hash_password` / `verify_password`,`pbkdf2_sha256$390000$...`)随账户服务一起放在 `infrastructure/db/accounts.py`(caf33f6),测试在 `tests/test_db_accounts.py`。
> - 本任务收窄为 **token 安全**:`web_admin/security.py` 含 `generate_token` / `hash_token` / `verify_token_hash`(工作树未提交)。`tests/test_web_security.py` 尚未单独建文件,token 行为由 `tests/test_web_auth.py::test_token_hash_is_stable_and_does_not_store_plain_token` 间接覆盖。
>
> 见顶部"实施修订记录"第 2 条。

**Files:**
- Create: `web_admin/security.py`
- Create: `tests/test_web_security.py`

- [ ] **Step 1: Write failing tests**

Cover:

- `hash_password("long-password")` returns a string starting with `pbkdf2_sha256$`.
- `verify_password()` accepts the correct password.
- `verify_password()` rejects a wrong password.
- two hashes for the same password differ because salts differ.
- `generate_token()` returns URL-safe text.
- `hash_token()` returns a stable SHA-256 hex digest.

- [ ] **Step 2: Run focused test and confirm failure**

Run:

```bash
python -m unittest tests.test_web_security -v
```

- [ ] **Step 3: Implement security helpers**

Use:

- `secrets.token_urlsafe(32)`
- `hashlib.pbkdf2_hmac("sha256", password_bytes, salt, iterations)`
- `hmac.compare_digest`
- hash format: `pbkdf2_sha256$390000$<salt_b64>$<digest_b64>`

- [ ] **Step 4: Run focused test and full regression**

Run:

```bash
python -m unittest tests.test_web_security -v
python -m unittest discover -s tests -p "test_*.py"
```

- [ ] **Step 5: Commit**

```bash
git add web_admin/security.py tests/test_web_security.py
git commit -m "web: add password and token security helpers"
```

---

## Task 4: Auth Service, Sessions, CSRF, Permissions

> **Status (2026-05-13):** 已实现于工作树(未提交)— `web_admin/auth.py`(`create_session` / `login_user` / `load_current_user` / `verify_csrf_token` / `logout_session` / `require_permission`)+ `tests/test_web_auth.py`(login/logout/expire/CSRF/permission 全覆盖)。密码校验委托给 `infrastructure.db.accounts.authenticate_user`,session token 与 CSRF token 都只存 sha256。

**Files:**
- Create: `web_admin/auth.py`
- Modify: `web_admin/settings.py`
- Create: `tests/test_web_auth.py`

- [ ] **Step 1: Write failing tests**

Seed SQLite with one active platform admin and one disabled user. Test:

- active user with correct password authenticates.
- wrong password returns `None`.
- disabled user cannot authenticate.
- `create_session()` stores token hash and csrf hash, not raw tokens.
- `load_session_user()` rejects expired or revoked sessions.
- `require_permission(user, "user:manage")` accepts platform admin.
- org operator without `user:manage` is rejected.

- [ ] **Step 2: Run focused test and confirm failure**

Run:

```bash
python -m unittest tests.test_web_auth -v
```

- [ ] **Step 3: Implement auth service**

Implement service functions that accept an explicit SQLAlchemy `Session`; do not open engines inside auth functions. Built-in role permission mapping should live in `auth.py` as constants and be seeded into DB by `accounts.py` / `manage.py`.

- [ ] **Step 4: Run focused test and full regression**

Run:

```bash
python -m unittest tests.test_web_auth -v
python -m unittest discover -s tests -p "test_*.py"
```

- [ ] **Step 5: Commit**

```bash
git add web_admin/auth.py web_admin/settings.py tests/test_web_auth.py
git commit -m "web: add database session authentication"
```

---

## Task 5: Account Management Service And Bootstrap CLI

> **Status (2026-05-13):** 已落地于 caf33f6,位置不同:
> - **账户服务** → `infrastructure/db/accounts.py`(原计划 `web_admin/accounts.py`):`ensure_builtin_roles` / `create_user` / `list_users` / `disable_user` / `reset_password` / `authenticate_user`,内置三角色 `platform_admin` / `org_admin` / `org_operator` 与 10 个权限。
> - **管理 CLI** → `utils/user_admin.py`(原计划 `web_admin/manage.py`),子命令:`roles init` / `orgs create` / `users {create,list,disable,reset-password}` / `login`,通过 `DATABASE_URL` 或 `--database-url` 解析。
> - 测试:`tests/test_db_accounts.py`、`tests/test_user_admin_cli.py`、`tests/test_postgresql_basic_admin_docs.py`。
>
> 偏离原因见顶部"实施修订记录"第 1 条:CLI 与 Web 共用同一账户服务,所以服务必须沉到 infrastructure 层,依赖单向 `web_admin / utils → infrastructure/db`。

**Files:**
- Create: `web_admin/accounts.py`
- Create: `web_admin/manage.py`
- Create: `tests/test_web_accounts.py`
- Create: `tests/test_web_manage_cli.py`

- [ ] **Step 1: Write failing service tests**

Cover:

- `ensure_builtin_roles(session)` creates three roles and required permissions idempotently.
- `create_user()` creates active user with role assignment.
- duplicate username raises `ValueError`.
- `disable_user()` sets status to `disabled`.
- disabling the current user raises `ValueError`.
- `reset_password()` changes password hash and old password no longer verifies.

- [ ] **Step 2: Write failing CLI tests**

In-process test `web_admin.manage.run([...])`:

- `create-admin --username admin --password strong...` creates a platform admin.
- missing `DATABASE_URL` returns exit code 2.
- duplicate admin returns exit code 2 with an error.

- [ ] **Step 3: Run focused tests and confirm failure**

Run:

```bash
python -m unittest tests.test_web_accounts tests.test_web_manage_cli -v
```

- [ ] **Step 4: Implement service and CLI**

CLI must delay DB imports until `run()` executes, matching `utils/force_rerun_cli.py`. It must not run migrations automatically; docs instruct operators to run `alembic upgrade head`.

- [ ] **Step 5: Run focused tests and full regression**

Run:

```bash
python -m unittest tests.test_web_accounts tests.test_web_manage_cli -v
python -m unittest discover -s tests -p "test_*.py"
```

- [ ] **Step 6: Commit**

```bash
git add web_admin/accounts.py web_admin/manage.py tests/test_web_accounts.py tests/test_web_manage_cli.py
git commit -m "web: add admin account management service"
```

---

## Task 6: Login And Logout Routes

**Files:**
- Create: `web_admin/routes/auth.py`
- Create: `web_admin/templates/base.html`
- Create: `web_admin/templates/login.html`
- Modify: `web_admin/app.py`
- Create: `tests/test_web_routes_auth.py`

- [ ] **Step 1: Write failing route tests**

Using `TestClient` and SQLite dependency overrides, cover:

- `GET /login` returns 200 and contains a login form.
- `POST /login` with valid credentials sets `fileforge_session` cookie and redirects.
- invalid credentials return 401 or re-render with an error.
- `POST /logout` revokes the session and clears cookie.
- protected route redirects to `/login` when unauthenticated.

- [ ] **Step 2: Run focused test and confirm failure**

Run:

```bash
python -m unittest tests.test_web_routes_auth -v
```

- [ ] **Step 3: Implement routes and templates**

Keep templates functional and minimal. Do not add frontend build tooling.

- [ ] **Step 4: Run focused test and full regression**

Run:

```bash
python -m unittest tests.test_web_routes_auth -v
python -m unittest discover -s tests -p "test_*.py"
```

- [ ] **Step 5: Commit**

```bash
git add web_admin/routes/auth.py web_admin/templates web_admin/app.py tests/test_web_routes_auth.py
git commit -m "web: add login and logout routes"
```

---

## Task 7: User Management Pages

**Files:**
- Create: `web_admin/routes/users.py`
- Create: `web_admin/templates/users_list.html`
- Create: `web_admin/templates/user_form.html`
- Create: `web_admin/templates/user_reset_password.html`
- Modify: `web_admin/app.py`
- Create: `tests/test_web_routes_users.py`

- [ ] **Step 1: Write failing route tests**

Cover:

- platform admin can list users.
- org operator receives 403 for user management.
- new user form creates a user and redirects to list.
- duplicate username re-renders with an error.
- disable user changes status.
- disabling self is rejected.
- reset password updates password hash.

- [ ] **Step 2: Run focused test and confirm failure**

Run:

```bash
python -m unittest tests.test_web_routes_users -v
```

- [ ] **Step 3: Implement user routes**

All POST routes must validate CSRF. User rows must show username, display name, organization, roles, status, created time, and action links/buttons.

- [ ] **Step 4: Run focused test and full regression**

Run:

```bash
python -m unittest tests.test_web_routes_users -v
python -m unittest discover -s tests -p "test_*.py"
```

- [ ] **Step 5: Commit**

```bash
git add web_admin/routes/users.py web_admin/templates web_admin/app.py tests/test_web_routes_users.py
git commit -m "web: add user management pages"
```

---

## Task 8: Batch List And Detail Pages

**Files:**
- Create: `web_admin/routes/archives.py`
- Create: `web_admin/templates/batches_list.html`
- Create: `web_admin/templates/batch_detail.html`
- Modify: `web_admin/app.py`
- Create: `tests/test_web_routes_batches.py`

- [ ] **Step 1: Write failing route tests**

Seed project/batch data using existing models. Cover:

- platform admin can list batches by `project_key`.
- org user can list batches only for own organization.
- org user gets 404 or 403 for another organization.
- `GET /batches/{batch_id}` shows failure breakdown and schema refs.
- invalid `page_size=500` returns 400.

- [ ] **Step 2: Run focused test and confirm failure**

Run:

```bash
python -m unittest tests.test_web_routes_batches -v
```

- [ ] **Step 3: Implement batch routes**

Use `queries.list_batches()` and `queries.get_batch_detail()`. Keep organization-scope checks in Web route/service code before returning data.

- [ ] **Step 4: Run focused test and full regression**

Run:

```bash
python -m unittest tests.test_web_routes_batches -v
python -m unittest discover -s tests -p "test_*.py"
```

- [ ] **Step 5: Commit**

```bash
git add web_admin/routes/archives.py web_admin/templates web_admin/app.py tests/test_web_routes_batches.py
git commit -m "web: add batch query pages"
```

---

## Task 9: Archive List And Detail Pages

**Files:**
- Modify: `web_admin/routes/archives.py`
- Create: `web_admin/templates/archives_list.html`
- Create: `web_admin/templates/archive_detail.html`
- Create: `tests/test_web_routes_archives.py`

- [ ] **Step 1: Write failing route tests**

Cover:

- archive list calls Phase 1C filters: year, classification, retention, status, title-like, responsible-party-like.
- archive detail shows final metadata, status fields, LLM parse strategy, and page list.
- archive not found returns 404.
- org-scope violation returns 404 or 403.

- [ ] **Step 2: Run focused test and confirm failure**

Run:

```bash
python -m unittest tests.test_web_routes_archives -v
```

- [ ] **Step 3: Implement archive routes**

Map query string parameters to `queries.ArchiveFilter`. Preserve `page` and `page_size` behavior from Phase 1C.

- [ ] **Step 4: Run focused test and full regression**

Run:

```bash
python -m unittest tests.test_web_routes_archives -v
python -m unittest discover -s tests -p "test_*.py"
```

- [ ] **Step 5: Commit**

```bash
git add web_admin/routes/archives.py web_admin/templates tests/test_web_routes_archives.py
git commit -m "web: add archive query pages"
```

---

## Task 10: Revision And Audit Read-Only Pages

**Files:**
- Modify: `web_admin/routes/archives.py`
- Create: `web_admin/templates/revisions_list.html`
- Create: `web_admin/templates/audit_list.html`
- Create: `tests/test_web_routes_revisions_audit.py`

- [ ] **Step 1: Write failing route tests**

Cover:

- `GET /archives/{archive_id}/revisions` lists revision rows.
- `GET /archives/{archive_id}/audit` lists audit rows.
- audit route requires `audit:view`.
- unknown archive returns 404.
- organization scope is enforced before querying.

- [ ] **Step 2: Run focused test and confirm failure**

Run:

```bash
python -m unittest tests.test_web_routes_revisions_audit -v
```

- [ ] **Step 3: Implement routes**

Use `queries.list_revisions()` and `queries.list_audit_logs(target_type="archive", target_id=archive_id)`. Do not add metadata editing in this task.

- [ ] **Step 4: Run focused test and full regression**

Run:

```bash
python -m unittest tests.test_web_routes_revisions_audit -v
python -m unittest discover -s tests -p "test_*.py"
```

- [ ] **Step 5: Commit**

```bash
git add web_admin/routes/archives.py web_admin/templates tests/test_web_routes_revisions_audit.py
git commit -m "web: add revision and audit pages"
```

---

## Task 11: Admin Styling, Navigation, And Error Pages

**Files:**
- Create: `web_admin/static/admin.css`
- Create: `web_admin/templates/error.html`
- Modify: `web_admin/templates/base.html`
- Modify: `web_admin/app.py`
- Create: `tests/test_web_routes_errors.py`

- [ ] **Step 1: Write failing tests**

Cover:

- authenticated pages include navigation links appropriate to role.
- 403 returns an HTML error page.
- 404 returns an HTML error page.
- form validation errors do not produce stack traces.

- [ ] **Step 2: Run focused test and confirm failure**

Run:

```bash
python -m unittest tests.test_web_routes_errors -v
```

- [ ] **Step 3: Implement minimal admin UI shell**

Keep CSS scoped to admin layout, tables, forms, buttons, and alerts. Avoid decorative landing-page patterns; the first screen after login should be operational.

- [ ] **Step 4: Run focused test and full regression**

Run:

```bash
python -m unittest tests.test_web_routes_errors -v
python -m unittest discover -s tests -p "test_*.py"
```

- [ ] **Step 5: Commit**

```bash
git add web_admin/static web_admin/templates web_admin/app.py tests/test_web_routes_errors.py
git commit -m "web: polish admin navigation and errors"
```

---

## Task 12: Data Contract And Runtime Docs

**Files:**
- Modify: `docs/postgresql_data_contract_design.md`
- Create: `docs/web_admin.md`
- Create: `tests/test_web_docs_contract.py`

- [ ] **Step 1: Write lightweight doc contract test**

Add a test that asserts `docs/web_admin.md` exists and contains:

- `alembic upgrade head`
- `python -m web_admin.manage create-admin`
- `uvicorn`
- `requirements/web.txt`

- [ ] **Step 2: Run focused test and confirm failure**

Run:

```bash
python -m unittest tests.test_web_docs_contract -v
```

- [ ] **Step 3: Add docs**

`docs/web_admin.md` must include:

- install command: `pip install -r requirements/web.txt`
- migration command: `alembic upgrade head`
- bootstrap admin command
- run command: `uvicorn web_admin.app:create_app --factory --host 0.0.0.0 --port 8080`
- required env: `DATABASE_URL`
- optional env: cookie name, secure cookie flag, session TTL
- test command: `python -m unittest discover -s tests -p "test_*.py"`
- note that `python main.py`, PaddleOCR, vLLM, and PostgreSQL are not required for SQLite tests

Update `docs/postgresql_data_contract_design.md` with a Phase 2 Web/admin account section.

- [ ] **Step 4: Run focused test and full regression**

Run:

```bash
python -m unittest tests.test_web_docs_contract -v
python -m unittest discover -s tests -p "test_*.py"
```

- [ ] **Step 5: Commit**

```bash
git add docs/postgresql_data_contract_design.md docs/web_admin.md tests/test_web_docs_contract.py
git commit -m "docs: add web admin runtime guide"
```

---

## Final Verification

- [ ] Run full regression:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

- [ ] Check working tree:

```bash
git status --short
```

Expected: no tracked changes remain after commits; existing untracked `pre.txt` remains untracked and must not be added.

---

## Self-Review

- Spec coverage: login/logout, user tables, roles/permissions, password hashing, session/cookie auth, user management, batch/archive/detail/revision/audit read pages, PostgreSQL runtime, SQLite tests, Alembic migration, and TDD strategy are each mapped to tasks.
- Restricted files: only `infrastructure/db/models.py` is planned for modification, with a specific schema reason. OCR/LLM hot-path files remain untouched.
- Query reuse: batch/archive/revision/audit routes explicitly call `infrastructure/db/queries.py`; no duplicate read SQL is planned.
- Verification: every task has focused unittest commands plus full regression before commit.
- Commit hygiene: all commit messages avoid AI identifiers and no task includes pushing to origin.
