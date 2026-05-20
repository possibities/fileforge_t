# 单位与项目管理页 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Web 后台加 `/admin/organizations` 与 `/admin/projects` 两套页面(列表 + 新建 + 启用/禁用),数据层加 `accounts.list_organizations` / `set_organization_status` 与新建 `infrastructure/db/projects.py`,完成"从零部署到能用"的 Web 自助路径。

**Architecture:** 服务层在 `accounts.py` 补 org 三件套,新建 `projects.py` 容纳项目写侧;Web 路由 `routes/organizations.py` + `routes/projects.py` 各自 5 个端点(GET list / GET new / POST new / POST disable / POST enable);模板沿用既有 admin.css 风格,功能优先无装饰。

**Tech Stack:** Python 3.12,SQLAlchemy 2.x,FastAPI,Jinja2,unittest,SQLite in-memory。

---

## File Structure

| 文件 | 类型 | 责任 |
| --- | --- | --- |
| `infrastructure/db/accounts.py` | Modify | 加 `OrganizationRow` dataclass + `list_organizations()` + `set_organization_status()`,更新 `__all__` |
| `infrastructure/db/projects.py` | Create | `ProjectRow` dataclass + `create_project()` + `list_projects()` + `set_project_status()` |
| `web_admin/routes/organizations.py` | Create | `_require_organization_manage()` + 5 路由 |
| `web_admin/routes/projects.py` | Create | `_require_project_manage()`(含 `organization_id=None` 边界守卫) + 5 路由 |
| `web_admin/templates/organizations_list.html` | Create | 单位列表 + 启用/禁用按钮 |
| `web_admin/templates/organization_form.html` | Create | 新建单位表单(单字段 + CSRF) |
| `web_admin/templates/projects_list.html` | Create | 项目列表 + 启用/禁用 + 单位过滤 |
| `web_admin/templates/project_form.html` | Create | 新建项目表单(4 字段 + 单位下拉 + CSRF) |
| `web_admin/templates/base.html` | Modify | nav 加 "单位" / "项目" 链接(按权限) |
| `web_admin/app.py` | Modify | 注册 organizations / projects router |
| `tests/test_db_accounts.py` | Modify | 加 `TestOrganizationManagement`(6) |
| `tests/test_db_projects.py` | Create | `TestProjectManagement`(14) |
| `tests/test_web_routes_organizations.py` | Create | `TestOrganizationRoutes`(11) + 2 nav 测试 |
| `tests/test_web_routes_projects.py` | Create | `TestProjectRoutes`(19,含 org_id=None 边界) |
| `docs/postgresql_data_contract_design.md` | Modify | §9.8 注脚追加 admin entity 写侧函数说明 |
| `docs/web_admin.md` | Modify | §6 路由 + §7 权限补 `organization:manage` / `project:manage` |

无新增 Alembic 迁移。

---

## Task 1: `accounts.py` org 三件套

**Files:**
- Modify: `infrastructure/db/accounts.py`
- Modify: `tests/test_db_accounts.py`

- [ ] **Step 1: 在 `tests/test_db_accounts.py` 末尾追加 `TestOrganizationManagement`**

```python
@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestOrganizationManagement(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _seed_orgs(self):
        with self.Session() as session:
            a = Organization(name="档案室甲", status="active")
            b = Organization(name="档案室乙", status="disabled")
            c = Organization(name="档案室丙", status="active")
            session.add_all([a, b, c])
            session.commit()
            return a.id, b.id, c.id

    def test_list_organizations_returns_all_sorted_by_name(self):
        self._seed_orgs()
        with self.Session() as session:
            rows = accounts.list_organizations(session)
        # 仅验证"全部返回 + 按 name 排序",不绑定具体 codepoint 顺序
        self.assertEqual(len(rows), 3)
        names_sorted = sorted([r.name for r in rows])
        self.assertEqual([r.name for r in rows], names_sorted)

    def test_list_organizations_status_filter(self):
        self._seed_orgs()
        with self.Session() as session:
            rows = accounts.list_organizations(session, status_filter=("active",))
        self.assertEqual({r.name for r in rows}, {"档案室甲", "档案室丙"})

    def test_set_organization_status_to_disabled(self):
        a_id, _, _ = self._seed_orgs()
        with self.Session() as session:
            accounts.set_organization_status(
                session, organization_id=a_id, status="disabled"
            )
            session.commit()
        with self.Session() as session:
            self.assertEqual(session.get(Organization, a_id).status, "disabled")

    def test_set_organization_status_to_active_reenables(self):
        _, b_id, _ = self._seed_orgs()
        with self.Session() as session:
            accounts.set_organization_status(
                session, organization_id=b_id, status="active"
            )
            session.commit()
        with self.Session() as session:
            self.assertEqual(session.get(Organization, b_id).status, "active")

    def test_set_organization_status_invalid_status_raises_value_error(self):
        a_id, _, _ = self._seed_orgs()
        with self.Session() as session:
            with self.assertRaises(ValueError):
                accounts.set_organization_status(
                    session, organization_id=a_id, status="archived"
                )

    def test_set_organization_status_unknown_id_raises(self):
        with self.Session() as session:
            with self.assertRaises(ValueError):
                accounts.set_organization_status(
                    session, organization_id=99999, status="disabled"
                )
```

`Organization` 已在文件顶部 import,无需新增。

- [ ] **Step 2: 跑测试,确认 6 条 FAIL**

```bash
python -m unittest tests.test_db_accounts.TestOrganizationManagement -v
```

Expected: 6 errors,主要 `AttributeError: module 'infrastructure.db.accounts' has no attribute 'list_organizations'`。

- [ ] **Step 3: 在 `infrastructure/db/accounts.py` 加 dataclass + 函数**

确保顶部 import 含 `Iterable` 与 `ORGANIZATION_STATUS`:

```python
from typing import Iterable, Optional
```

```python
from .models import (
    AppUser,
    Organization,
    ORGANIZATION_STATUS,
    Permission,
    Role,
    RolePermission,
    UserRole,
)
```

(若 `ORGANIZATION_STATUS` 尚未在此 import,把它加入既有 `from .models import (...)` 块。)

在 `UserRow` dataclass 定义后追加:

```python
@dataclass(frozen=True)
class OrganizationRow:
    id: int
    name: str
    status: str
    created_at: datetime
    updated_at: datetime
```

在 `create_organization` 函数后追加:

```python
def list_organizations(
    session: Session,
    *,
    status_filter: Optional[Iterable[str]] = None,
) -> list[OrganizationRow]:
    """按 name 升序列出单位;status_filter=None 返回全部。"""
    stmt = select(Organization).order_by(Organization.name)
    if status_filter:
        stmt = stmt.where(Organization.status.in_(list(status_filter)))
    rows = session.scalars(stmt).all()
    return [
        OrganizationRow(
            id=o.id,
            name=o.name,
            status=o.status,
            created_at=o.created_at,
            updated_at=o.updated_at,
        )
        for o in rows
    ]


def set_organization_status(
    session: Session,
    *,
    organization_id: int,
    status: str,
) -> None:
    """切换单位 status。不 commit。非枚举值或 id 不存在 → ValueError。"""
    if status not in ORGANIZATION_STATUS:
        raise ValueError(
            f"status 必须为 {ORGANIZATION_STATUS} 之一,实际为 {status}"
        )
    org = session.get(Organization, organization_id)
    if org is None:
        raise ValueError(f"organization 不存在: {organization_id}")
    org.status = status
```

更新文件底部 `__all__` 加入:

```python
"OrganizationRow",
"list_organizations",
"set_organization_status",
```

- [ ] **Step 4: 跑测试 + 全量回归**

```bash
python -m unittest tests.test_db_accounts.TestOrganizationManagement -v
python -m unittest discover -s tests -p "test_*.py"
```

Expected: 6 PASS,全量回归保持。

- [ ] **Step 5: 提交**

```bash
git add infrastructure/db/accounts.py tests/test_db_accounts.py
git commit -m "db: add list_organizations and set_organization_status"
```

---

## Task 2: 新建 `infrastructure/db/projects.py`

**Files:**
- Create: `infrastructure/db/projects.py`
- Create: `tests/test_db_projects.py`

- [ ] **Step 1: 创建测试文件 + 14 个失败测试**

`tests/test_db_projects.py`:

```python
"""项目写侧服务的回归测试。"""

from __future__ import annotations

import unittest


try:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import projects
    from infrastructure.db.models import (
        Base,
        Organization,
        Project,
    )
except ImportError as _exc:  # pragma: no cover
    SQLALCHEMY_AVAILABLE = False
    _IMPORT_ERROR = _exc
else:
    SQLALCHEMY_AVAILABLE = True
    _IMPORT_ERROR = None


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestProjectManagement(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            org_a = Organization(name="档案室甲", status="active")
            org_b = Organization(name="档案室乙", status="disabled")
            session.add_all([org_a, org_b])
            session.commit()
            self.org_a_id = org_a.id
            self.org_b_id = org_b.id

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_create_project_success(self):
        with self.Session() as session:
            project = projects.create_project(
                session,
                project_key="proj_a",
                organization_id=self.org_a_id,
                project_name="档案甲项目",
                description="测试项目",
            )
            session.commit()
            self.assertEqual(project.status, "active")
            self.assertEqual(project.organization_id, self.org_a_id)
            self.assertEqual(project.project_name, "档案甲项目")
            self.assertEqual(project.description, "测试项目")

    def test_create_project_duplicate_key_raises(self):
        with self.Session() as session:
            projects.create_project(
                session, project_key="proj_a", organization_id=self.org_a_id,
            )
            session.commit()
        with self.Session() as session:
            with self.assertRaises(ValueError) as ctx:
                projects.create_project(
                    session, project_key="proj_a", organization_id=self.org_a_id,
                )
            self.assertIn("已存在", str(ctx.exception))

    def test_create_project_unknown_organization_raises(self):
        with self.Session() as session:
            with self.assertRaises(ValueError):
                projects.create_project(
                    session, project_key="proj_x", organization_id=99999,
                )

    def test_create_project_disabled_organization_raises(self):
        with self.Session() as session:
            with self.assertRaises(ValueError) as ctx:
                projects.create_project(
                    session, project_key="proj_x", organization_id=self.org_b_id,
                )
            self.assertIn("disabled", str(ctx.exception).lower())

    def test_create_project_blank_key_raises(self):
        with self.Session() as session:
            with self.assertRaises(ValueError):
                projects.create_project(
                    session, project_key="   ", organization_id=self.org_a_id,
                )

    def _seed_three_projects(self):
        with self.Session() as session:
            p1 = Project(
                project_key="p1", organization_id=self.org_a_id, status="active"
            )
            session.add(p1)
            session.commit()
            p2 = Project(
                project_key="p2", organization_id=self.org_a_id, status="disabled"
            )
            session.add(p2)
            session.commit()
            p3 = Project(
                project_key="p3", organization_id=self.org_b_id, status="active"
            )
            session.add(p3)
            session.commit()
            return p1.id, p2.id, p3.id

    def test_list_projects_no_filter_returns_all(self):
        self._seed_three_projects()
        with self.Session() as session:
            rows = projects.list_projects(session)
        self.assertEqual({r.project_key for r in rows}, {"p1", "p2", "p3"})

    def test_list_projects_filters_by_organization_id(self):
        self._seed_three_projects()
        with self.Session() as session:
            rows = projects.list_projects(session, organization_id=self.org_a_id)
        self.assertEqual({r.project_key for r in rows}, {"p1", "p2"})

    def test_list_projects_status_filter(self):
        self._seed_three_projects()
        with self.Session() as session:
            rows = projects.list_projects(session, status_filter=("active",))
        self.assertEqual({r.project_key for r in rows}, {"p1", "p3"})

    def test_list_projects_sorted_by_created_at_desc(self):
        self._seed_three_projects()
        with self.Session() as session:
            rows = projects.list_projects(session)
        # 最新创建的在前;_seed 顺序 p1 → p2 → p3
        self.assertEqual(rows[0].project_key, "p3")

    def test_list_projects_includes_organization_name(self):
        self._seed_three_projects()
        with self.Session() as session:
            rows = projects.list_projects(session, organization_id=self.org_a_id)
        self.assertTrue(all(r.organization_name == "档案室甲" for r in rows))

    def test_set_project_status_to_disabled(self):
        p1_id, _, _ = self._seed_three_projects()
        with self.Session() as session:
            projects.set_project_status(
                session, project_id=p1_id, status="disabled"
            )
            session.commit()
        with self.Session() as session:
            self.assertEqual(session.get(Project, p1_id).status, "disabled")

    def test_set_project_status_to_archived_accepted(self):
        p1_id, _, _ = self._seed_three_projects()
        with self.Session() as session:
            projects.set_project_status(
                session, project_id=p1_id, status="archived"
            )
            session.commit()
        with self.Session() as session:
            self.assertEqual(session.get(Project, p1_id).status, "archived")

    def test_set_project_status_invalid_status_raises(self):
        p1_id, _, _ = self._seed_three_projects()
        with self.Session() as session:
            with self.assertRaises(ValueError):
                projects.set_project_status(
                    session, project_id=p1_id, status="unknown"
                )

    def test_set_project_status_unknown_id_raises(self):
        with self.Session() as session:
            with self.assertRaises(ValueError):
                projects.set_project_status(
                    session, project_id=99999, status="disabled"
                )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试,确认 14 条 FAIL**

```bash
python -m unittest tests.test_db_projects -v
```

Expected: 14 errors,`ImportError: cannot import name 'projects'`。

- [ ] **Step 3: 创建 `infrastructure/db/projects.py`**

```python
"""项目实体的写侧服务。

Web 后台与 CLI 共用;函数本身不 commit,事务边界由调用方控制。
本模块只读 infrastructure.db.models 中已存在的 ORM,不引入新表。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Organization,
    PROJECT_STATUS,
    Project,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectRow:
    id: int
    project_key: str
    project_name: Optional[str]
    description: Optional[str]
    status: str
    organization_id: Optional[int]
    organization_name: Optional[str]
    created_at: datetime
    updated_at: datetime


def create_project(
    session: Session,
    *,
    project_key: str,
    organization_id: int,
    project_name: Optional[str] = None,
    description: Optional[str] = None,
) -> Project:
    """新建 active 项目。不 commit。

    - project_key 空 / 重复 → ValueError
    - organization_id 不存在 / 单位 disabled → ValueError
    """
    key = (project_key or "").strip()
    if not key:
        raise ValueError("project_key 不能为空")

    existing = session.scalar(select(Project).where(Project.project_key == key))
    if existing is not None:
        raise ValueError(f"project_key 已存在: {key}")

    org = session.get(Organization, organization_id)
    if org is None:
        raise ValueError(f"organization 不存在: {organization_id}")
    if org.status != "active":
        raise ValueError(
            f"organization 状态为 {org.status} (非 active),不能新建项目"
        )

    project = Project(
        project_key=key,
        project_name=(project_name or "").strip() or None,
        description=(description or "").strip() or None,
        organization_id=organization_id,
        status="active",
    )
    session.add(project)
    session.flush()
    return project


def list_projects(
    session: Session,
    *,
    organization_id: Optional[int] = None,
    status_filter: Optional[Iterable[str]] = None,
) -> list[ProjectRow]:
    """按 created_at DESC 列出。

    organization_id=None 不过滤;非 platform_admin 由上层传自己的 org_id。
    organization_name 通过 LEFT JOIN organizations 得到。
    """
    stmt = (
        select(Project, Organization.name)
        .outerjoin(Organization, Project.organization_id == Organization.id)
        .order_by(Project.created_at.desc(), Project.id.desc())
    )
    if organization_id is not None:
        stmt = stmt.where(Project.organization_id == organization_id)
    if status_filter:
        stmt = stmt.where(Project.status.in_(list(status_filter)))

    rows = session.execute(stmt).all()
    return [
        ProjectRow(
            id=p.id,
            project_key=p.project_key,
            project_name=p.project_name,
            description=p.description,
            status=p.status,
            organization_id=p.organization_id,
            organization_name=org_name,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p, org_name in rows
    ]


def set_project_status(
    session: Session,
    *,
    project_id: int,
    status: str,
) -> None:
    """切换项目 status。不 commit。非枚举值或 id 不存在 → ValueError。"""
    if status not in PROJECT_STATUS:
        raise ValueError(f"status 必须为 {PROJECT_STATUS} 之一,实际为 {status}")
    project = session.get(Project, project_id)
    if project is None:
        raise ValueError(f"project 不存在: {project_id}")
    project.status = status


__all__ = [
    "ProjectRow",
    "create_project",
    "list_projects",
    "set_project_status",
]
```

- [ ] **Step 4: 跑测试 + 全量回归**

```bash
python -m unittest tests.test_db_projects -v
python -m unittest discover -s tests -p "test_*.py"
```

Expected: 14 PASS。

- [ ] **Step 5: 提交**

```bash
git add infrastructure/db/projects.py tests/test_db_projects.py
git commit -m "db: add projects service for create/list/status"
```

---

## Task 3: 4 个 Jinja2 模板

**Files:**
- Create: `web_admin/templates/organizations_list.html`
- Create: `web_admin/templates/organization_form.html`
- Create: `web_admin/templates/projects_list.html`
- Create: `web_admin/templates/project_form.html`

- [ ] **Step 1: 创建 `organizations_list.html`**

```html
{% extends "base.html" %}
{% block title %}单位管理 - FileForge 管理后台{% endblock %}
{% block content %}
<h1>单位管理</h1>

<p><a href="/admin/organizations/new">新建单位</a></p>

<table>
  <thead>
    <tr>
      <th>ID</th>
      <th>名称</th>
      <th>状态</th>
      <th>创建时间</th>
      <th>操作</th>
    </tr>
  </thead>
  <tbody>
    {% for org in organizations %}
    <tr>
      <td>{{ org.id }}</td>
      <td>{{ org.name }}</td>
      <td>{{ org.status }}</td>
      <td>{{ org.created_at }}</td>
      <td>
        {% if org.status == "active" %}
        <form method="post" action="/admin/organizations/{{ org.id }}/disable" style="display:inline">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
          <button type="submit">禁用</button>
        </form>
        {% else %}
        <form method="post" action="/admin/organizations/{{ org.id }}/enable" style="display:inline">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
          <button type="submit">启用</button>
        </form>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 2: 创建 `organization_form.html`**

```html
{% extends "base.html" %}
{% block title %}新建单位 - FileForge 管理后台{% endblock %}
{% block content %}
<h1>新建单位</h1>

{% if error %}
<p class="error">{{ error }}</p>
{% endif %}

<form method="post" action="/admin/organizations/new">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <p>
    <label for="name">名称(必填,1–255 字符,唯一)</label><br>
    <input type="text" id="name" name="name" value="{{ values.name or '' }}" maxlength="255" required>
  </p>
  <p>
    <button type="submit">创建</button>
    <a href="/admin/organizations">取消</a>
  </p>
</form>
{% endblock %}
```

- [ ] **Step 3: 创建 `projects_list.html`**

```html
{% extends "base.html" %}
{% block title %}项目管理 - FileForge 管理后台{% endblock %}
{% block content %}
<h1>项目管理</h1>

<p><a href="/admin/projects/new">新建项目</a></p>

{% if show_org_filter %}
<form method="get" action="/admin/projects">
  <label for="organization_id">按单位过滤</label>
  <select id="organization_id" name="organization_id" onchange="this.form.submit()">
    <option value="">全部</option>
    {% for org in organizations_for_filter %}
    <option value="{{ org.id }}"{% if filter_organization_id == org.id %} selected{% endif %}>{{ org.name }}</option>
    {% endfor %}
  </select>
</form>
{% endif %}

<table>
  <thead>
    <tr>
      <th>ID</th>
      <th>project_key</th>
      <th>名称</th>
      <th>单位</th>
      <th>状态</th>
      <th>创建时间</th>
      <th>操作</th>
    </tr>
  </thead>
  <tbody>
    {% for project in projects %}
    <tr>
      <td>{{ project.id }}</td>
      <td>{{ project.project_key }}</td>
      <td>{{ project.project_name or "" }}</td>
      <td>{{ project.organization_name or "" }}</td>
      <td>{{ project.status }}</td>
      <td>{{ project.created_at }}</td>
      <td>
        {% if project.status == "active" %}
        <form method="post" action="/admin/projects/{{ project.id }}/disable" style="display:inline">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
          <button type="submit">禁用</button>
        </form>
        {% else %}
        <form method="post" action="/admin/projects/{{ project.id }}/enable" style="display:inline">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
          <button type="submit">启用</button>
        </form>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 4: 创建 `project_form.html`**

```html
{% extends "base.html" %}
{% block title %}新建项目 - FileForge 管理后台{% endblock %}
{% block content %}
<h1>新建项目</h1>

{% if error %}
<p class="error">{{ error }}</p>
{% endif %}

<form method="post" action="/admin/projects/new">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <p>
    <label for="project_key">project_key(必填,1–128 字符,字母数字/-/_,唯一)</label><br>
    <input type="text" id="project_key" name="project_key" value="{{ values.project_key or '' }}" maxlength="128" pattern="[A-Za-z0-9_\-]+" required>
  </p>
  <p>
    <label for="project_name">项目名称(可空,≤255 字符)</label><br>
    <input type="text" id="project_name" name="project_name" value="{{ values.project_name or '' }}" maxlength="255">
  </p>
  <p>
    <label for="description">描述(可空,≤1000 字符)</label><br>
    <textarea id="description" name="description" rows="3" maxlength="1000">{{ values.description or '' }}</textarea>
  </p>
  <p>
    <label for="organization_id">所属单位(必选)</label><br>
    <select id="organization_id" name="organization_id" required{% if org_locked %} disabled{% endif %}>
      {% if not org_locked %}<option value="">-- 请选择 --</option>{% endif %}
      {% for org in available_organizations %}
      <option value="{{ org.id }}"{% if values.organization_id == org.id %} selected{% endif %}>{{ org.name }}</option>
      {% endfor %}
    </select>
    {% if org_locked %}
    <input type="hidden" name="organization_id" value="{{ values.organization_id }}">
    {% endif %}
  </p>
  <p>
    <button type="submit">创建</button>
    <a href="/admin/projects">取消</a>
  </p>
</form>
{% endblock %}
```

- [ ] **Step 5: 暂不提交**

模板渲染由 Task 4 / Task 5 路由测试间接覆盖。

---

## Task 4: `routes/organizations.py` + app.py + 测试

**Files:**
- Create: `web_admin/routes/organizations.py`
- Modify: `web_admin/app.py`
- Create: `tests/test_web_routes_organizations.py`

- [ ] **Step 1: 创建测试文件 + 11 个失败测试**

`tests/test_web_routes_organizations.py`:

```python
"""单位管理路由测试。"""

from __future__ import annotations

import unittest


try:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import accounts
    from infrastructure.db.models import Base, Organization
    from web_admin.app import create_app
except ImportError as _exc:  # pragma: no cover
    DEPENDENCIES_AVAILABLE = False
    _IMPORT_ERROR: Exception | None = _exc
else:
    DEPENDENCIES_AVAILABLE = True
    _IMPORT_ERROR = None


ADMIN_USERNAME = "padmin"
ADMIN_PASSWORD = "platform-strong-pw"
ORG_ADMIN_USERNAME = "orgadmin-a"
ORG_ADMIN_PASSWORD = "orgadmin-strong-pw"


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, f"web deps missing: {_IMPORT_ERROR}")
class TestOrganizationRoutes(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            accounts.ensure_builtin_roles(session)
            org_a = Organization(name="档案室甲", status="active")
            org_b = Organization(name="档案室乙", status="disabled")
            session.add_all([org_a, org_b])
            session.flush()
            self.org_a_id = org_a.id
            self.org_b_id = org_b.id
            accounts.create_user(
                session,
                username=ADMIN_USERNAME,
                password=ADMIN_PASSWORD,
                display_name="平台管理员",
                role_codes=["platform_admin"],
            )
            accounts.create_user(
                session,
                username=ORG_ADMIN_USERNAME,
                password=ORG_ADMIN_PASSWORD,
                display_name="单位管理员",
                organization_id=org_a.id,
                role_codes=["org_admin"],
            )
            session.commit()
        self.app = create_app(database_url="sqlite://")
        self.app.state.session_factory = self.Session

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _login(self, client, username, password):
        resp = client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, {302, 303})

    def _csrf(self, client):
        return client.cookies.get("fileforge_csrf") or ""

    def test_get_list_unauthenticated_redirects_to_login(self):
        with TestClient(self.app) as client:
            resp = client.get("/admin/organizations", follow_redirects=False)
        self.assertIn(resp.status_code, {302, 303})
        self.assertTrue(resp.headers["location"].endswith("/login"))

    def test_get_list_org_admin_returns_403(self):
        with TestClient(self.app) as client:
            self._login(client, ORG_ADMIN_USERNAME, ORG_ADMIN_PASSWORD)
            resp = client.get("/admin/organizations", follow_redirects=False)
        self.assertEqual(resp.status_code, 403)

    def test_get_list_platform_admin_shows_all_orgs(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/admin/organizations")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("档案室甲", resp.text)
        self.assertIn("档案室乙", resp.text)

    def test_get_new_form_renders_with_csrf(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/admin/organizations/new")
        self.assertEqual(resp.status_code, 200)
        self.assertIn('name="csrf_token"', resp.text)
        self.assertIn('name="name"', resp.text)

    def test_post_new_csrf_missing_returns_403(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                "/admin/organizations/new",
                data={"name": "新单位", "csrf_token": ""},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 403)

    def test_post_new_success_redirects_to_list(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                "/admin/organizations/new",
                data={"name": "新单位", "csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertIn(resp.status_code, {302, 303})
        self.assertEqual(resp.headers["location"], "/admin/organizations")
        with self.Session() as session:
            orgs = session.scalars(
                select(Organization).where(Organization.name == "新单位")
            ).all()
        self.assertEqual(len(orgs), 1)
        self.assertEqual(orgs[0].status, "active")

    def test_post_new_duplicate_name_re_renders_with_error(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                "/admin/organizations/new",
                data={"name": "档案室甲", "csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("已存在", resp.text)

    def test_post_new_blank_name_re_renders_with_error(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                "/admin/organizations/new",
                data={"name": "   ", "csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("不能为空", resp.text)

    def test_post_disable_changes_status(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                f"/admin/organizations/{self.org_a_id}/disable",
                data={"csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertIn(resp.status_code, {302, 303})
        with self.Session() as session:
            self.assertEqual(
                session.get(Organization, self.org_a_id).status, "disabled"
            )

    def test_post_enable_changes_status(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                f"/admin/organizations/{self.org_b_id}/enable",
                data={"csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertIn(resp.status_code, {302, 303})
        with self.Session() as session:
            self.assertEqual(
                session.get(Organization, self.org_b_id).status, "active"
            )

    def test_post_disable_unknown_id_returns_404(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                "/admin/organizations/99999/disable",
                data={"csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试,确认 11 条 FAIL**

```bash
python -m unittest tests.test_web_routes_organizations -v
```

- [ ] **Step 3: 创建 `web_admin/routes/organizations.py`**

```python
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from infrastructure.db import accounts

from web_admin.auth import CurrentUser
from web_admin.db import get_session
from web_admin.routes import (
    load_current_user_from_request,
    verify_csrf_from_request,
)


router = APIRouter(prefix="/admin/organizations")


ORGANIZATION_MANAGE_PERMISSION = "organization:manage"


def _require_organization_manage(
    request: Request,
    session: Session,
) -> tuple[Optional[CurrentUser], Optional[Response]]:
    current_user = load_current_user_from_request(request, session)
    if current_user is None:
        return None, RedirectResponse(
            url="/login", status_code=status.HTTP_303_SEE_OTHER
        )
    if ORGANIZATION_MANAGE_PERMISSION not in current_user.permissions:
        return current_user, Response(status_code=status.HTTP_403_FORBIDDEN)
    return current_user, None


@router.get("")
def list_organizations_route(
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_organization_manage(request, session)
    if error_response is not None:
        return error_response

    orgs = accounts.list_organizations(session)
    csrf_token = request.cookies.get("fileforge_csrf", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "organizations_list.html",
        {
            "user": current_user,
            "organizations": orgs,
            "csrf_token": csrf_token,
        },
    )


@router.get("/new")
def get_new_organization_form(
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_organization_manage(request, session)
    if error_response is not None:
        return error_response

    csrf_token = request.cookies.get("fileforge_csrf", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "organization_form.html",
        {
            "user": current_user,
            "csrf_token": csrf_token,
            "values": {"name": ""},
            "error": None,
        },
    )


@router.post("/new")
def post_new_organization(
    request: Request,
    name: Optional[str] = Form(default=None),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_organization_manage(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    cleaned = (name or "").strip()
    templates = request.app.state.templates
    cookie_csrf = request.cookies.get("fileforge_csrf", "")

    error: Optional[str] = None
    if not cleaned:
        error = "名称不能为空"
    elif len(cleaned) > 255:
        error = "名称长度不能超过 255 字符"

    if error is None:
        try:
            accounts.create_organization(session, name=cleaned)
            session.commit()
        except ValueError as exc:
            session.rollback()
            error = str(exc) if "已存在" in str(exc) else f"创建失败: {exc}"

    if error is not None:
        return templates.TemplateResponse(
            request,
            "organization_form.html",
            {
                "user": current_user,
                "csrf_token": cookie_csrf,
                "values": {"name": cleaned},
                "error": error,
            },
        )

    return RedirectResponse(
        url="/admin/organizations",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{organization_id}/disable")
def post_disable_organization(
    request: Request,
    organization_id: int,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    return _set_status(request, session, organization_id, "disabled", csrf_token)


@router.post("/{organization_id}/enable")
def post_enable_organization(
    request: Request,
    organization_id: int,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    return _set_status(request, session, organization_id, "active", csrf_token)


def _set_status(
    request: Request,
    session: Session,
    organization_id: int,
    new_status: str,
    csrf_token: Optional[str],
) -> Response:
    current_user, error_response = _require_organization_manage(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    try:
        accounts.set_organization_status(
            session, organization_id=organization_id, status=new_status
        )
        session.commit()
    except ValueError:
        session.rollback()
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    return RedirectResponse(
        url="/admin/organizations",
        status_code=status.HTTP_303_SEE_OTHER,
    )
```

- [ ] **Step 4: 在 `web_admin/app.py` 注册 router**

import 段加:

```python
from web_admin.routes import organizations as organizations_routes
```

`app.include_router(...)` 段加:

```python
app.include_router(organizations_routes.router)
```

放在 `users_routes` 与 `archive_routes` 之间或就近,顺序与 nav 一致。

- [ ] **Step 5: 跑测试 + 全量回归**

```bash
python -m unittest tests.test_web_routes_organizations -v
python -m unittest discover -s tests -p "test_*.py"
```

Expected: 11 PASS。

- [ ] **Step 6: 提交**

```bash
git add web_admin/routes/organizations.py web_admin/app.py web_admin/templates/organizations_list.html web_admin/templates/organization_form.html tests/test_web_routes_organizations.py
git commit -m "web: add organization admin routes and templates"
```

---

## Task 5: `routes/projects.py` + app.py + 测试

**Files:**
- Create: `web_admin/routes/projects.py`
- Modify: `web_admin/app.py`
- Create: `tests/test_web_routes_projects.py`

- [ ] **Step 1: 创建测试文件 + 19 个失败测试**

`tests/test_web_routes_projects.py`:

```python
"""项目管理路由测试。"""

from __future__ import annotations

import unittest


try:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import accounts
    from infrastructure.db.models import Base, Organization, Project
    from web_admin.app import create_app
except ImportError as _exc:  # pragma: no cover
    DEPENDENCIES_AVAILABLE = False
    _IMPORT_ERROR: Exception | None = _exc
else:
    DEPENDENCIES_AVAILABLE = True
    _IMPORT_ERROR = None


ADMIN_USERNAME = "padmin"
ADMIN_PASSWORD = "platform-strong-pw"
ORG_ADMIN_USERNAME = "orgadmin-a"
ORG_ADMIN_PASSWORD = "orgadmin-strong-pw"
ORG_ADMIN_NO_ORG_USERNAME = "orgadmin-noorg"
ORG_ADMIN_NO_ORG_PASSWORD = "orgadmin-strong-pw"
OPERATOR_USERNAME = "operator-a"
OPERATOR_PASSWORD = "operator-strong-pw"


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, f"web deps missing: {_IMPORT_ERROR}")
class TestProjectRoutes(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            accounts.ensure_builtin_roles(session)
            org_a = Organization(name="档案室甲", status="active")
            org_b = Organization(name="档案室乙", status="active")
            org_c = Organization(name="档案室丙", status="disabled")
            session.add_all([org_a, org_b, org_c])
            session.flush()
            self.org_a_id = org_a.id
            self.org_b_id = org_b.id
            self.org_c_id = org_c.id

            accounts.create_user(
                session, username=ADMIN_USERNAME, password=ADMIN_PASSWORD,
                display_name="平台管理员", role_codes=["platform_admin"],
            )
            accounts.create_user(
                session, username=ORG_ADMIN_USERNAME, password=ORG_ADMIN_PASSWORD,
                display_name="甲单位管理员", organization_id=org_a.id,
                role_codes=["org_admin"],
            )
            accounts.create_user(
                session, username=ORG_ADMIN_NO_ORG_USERNAME,
                password=ORG_ADMIN_NO_ORG_PASSWORD,
                display_name="无单位管理员",
                role_codes=["org_admin"],
            )
            accounts.create_user(
                session, username=OPERATOR_USERNAME, password=OPERATOR_PASSWORD,
                display_name="甲单位操作员", organization_id=org_a.id,
                role_codes=["org_operator"],
            )

            proj_a = Project(
                project_key="proj_a", project_name="甲项目",
                organization_id=org_a.id, status="active",
            )
            proj_b = Project(
                project_key="proj_b", project_name="乙项目",
                organization_id=org_b.id, status="active",
            )
            session.add_all([proj_a, proj_b])
            session.flush()
            self.proj_a_id = proj_a.id
            self.proj_b_id = proj_b.id
            session.commit()
        self.app = create_app(database_url="sqlite://")
        self.app.state.session_factory = self.Session

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _login(self, client, username, password):
        resp = client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, {302, 303})

    def _csrf(self, client):
        return client.cookies.get("fileforge_csrf") or ""

    def _post_new(self, client, csrf, **fields):
        form = {
            "project_key": "new_proj",
            "project_name": "新项目",
            "description": "",
            "organization_id": str(self.org_a_id),
            "csrf_token": csrf,
        }
        form.update(fields)
        return client.post(
            "/admin/projects/new", data=form, follow_redirects=False
        )

    def test_get_list_unauthenticated_redirects_to_login(self):
        with TestClient(self.app) as client:
            resp = client.get("/admin/projects", follow_redirects=False)
        self.assertIn(resp.status_code, {302, 303})
        self.assertTrue(resp.headers["location"].endswith("/login"))

    def test_get_list_org_operator_returns_403(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = client.get("/admin/projects", follow_redirects=False)
        self.assertEqual(resp.status_code, 403)

    def test_get_list_platform_admin_sees_all_projects(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/admin/projects")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("proj_a", resp.text)
        self.assertIn("proj_b", resp.text)

    def test_get_list_org_admin_sees_only_own_org_projects(self):
        with TestClient(self.app) as client:
            self._login(client, ORG_ADMIN_USERNAME, ORG_ADMIN_PASSWORD)
            resp = client.get("/admin/projects")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("proj_a", resp.text)
        self.assertNotIn("proj_b", resp.text)

    def test_get_list_org_admin_without_organization_id_returns_403(self):
        with TestClient(self.app) as client:
            self._login(client, ORG_ADMIN_NO_ORG_USERNAME, ORG_ADMIN_NO_ORG_PASSWORD)
            resp = client.get("/admin/projects", follow_redirects=False)
        self.assertEqual(resp.status_code, 403)

    def test_get_list_filters_by_query_organization_id(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(f"/admin/projects?organization_id={self.org_a_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("proj_a", resp.text)
        self.assertNotIn("proj_b", resp.text)

    def test_get_new_form_platform_admin_lists_all_active_orgs(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/admin/projects/new")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("档案室甲", resp.text)
        self.assertIn("档案室乙", resp.text)
        self.assertNotIn("档案室丙", resp.text)

    def test_get_new_form_org_admin_dropdown_locked_to_own_org(self):
        with TestClient(self.app) as client:
            self._login(client, ORG_ADMIN_USERNAME, ORG_ADMIN_PASSWORD)
            resp = client.get("/admin/projects/new")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("档案室甲", resp.text)
        self.assertNotIn("档案室乙", resp.text)

    def test_post_new_csrf_missing_returns_403(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_new(client, csrf="")
        self.assertEqual(resp.status_code, 403)

    def test_post_new_success_redirects_to_list(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_new(client, csrf=self._csrf(client))
        self.assertIn(resp.status_code, {302, 303})
        self.assertEqual(resp.headers["location"], "/admin/projects")
        with self.Session() as session:
            rows = session.scalars(
                select(Project).where(Project.project_key == "new_proj")
            ).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "active")

    def test_post_new_org_admin_other_org_id_silently_locked(self):
        with TestClient(self.app) as client:
            self._login(client, ORG_ADMIN_USERNAME, ORG_ADMIN_PASSWORD)
            resp = self._post_new(
                client,
                csrf=self._csrf(client),
                project_key="cross_attempt",
                organization_id=str(self.org_b_id),
            )
        self.assertIn(resp.status_code, {302, 303})
        with self.Session() as session:
            row = session.scalar(
                select(Project).where(Project.project_key == "cross_attempt")
            )
        self.assertEqual(row.organization_id, self.org_a_id)

    def test_post_new_duplicate_project_key_re_renders_with_error(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_new(
                client, csrf=self._csrf(client), project_key="proj_a"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("已存在", resp.text)

    def test_post_new_invalid_project_key_re_renders_with_error(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_new(
                client, csrf=self._csrf(client), project_key="bad key!",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("project_key", resp.text)

    def test_post_new_blank_project_key_re_renders_with_error(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_new(
                client, csrf=self._csrf(client), project_key="   ",
            )
        self.assertEqual(resp.status_code, 200)

    def test_post_new_disabled_org_re_renders_with_error(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_new(
                client, csrf=self._csrf(client),
                project_key="proj_for_disabled",
                organization_id=str(self.org_c_id),
            )
        self.assertEqual(resp.status_code, 200)

    def test_post_disable_changes_status(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                f"/admin/projects/{self.proj_a_id}/disable",
                data={"csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertIn(resp.status_code, {302, 303})
        with self.Session() as session:
            self.assertEqual(
                session.get(Project, self.proj_a_id).status, "disabled"
            )

    def test_post_enable_changes_status(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            client.post(
                f"/admin/projects/{self.proj_a_id}/disable",
                data={"csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
            resp = client.post(
                f"/admin/projects/{self.proj_a_id}/enable",
                data={"csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertIn(resp.status_code, {302, 303})
        with self.Session() as session:
            self.assertEqual(
                session.get(Project, self.proj_a_id).status, "active"
            )

    def test_post_disable_cross_org_org_admin_returns_404(self):
        with TestClient(self.app) as client:
            self._login(client, ORG_ADMIN_USERNAME, ORG_ADMIN_PASSWORD)
            resp = client.post(
                f"/admin/projects/{self.proj_b_id}/disable",
                data={"csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 404)

    def test_post_disable_unknown_id_returns_404(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                "/admin/projects/99999/disable",
                data={"csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试,确认 19 条 FAIL**

```bash
python -m unittest tests.test_web_routes_projects -v
```

- [ ] **Step 3: 创建 `web_admin/routes/projects.py`**

```python
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from infrastructure.db import accounts, projects as projects_service
from infrastructure.db.models import Project

from web_admin.auth import CurrentUser
from web_admin.db import get_session
from web_admin.routes import (
    has_platform_scope,
    load_current_user_from_request,
    verify_csrf_from_request,
)


router = APIRouter(prefix="/admin/projects")


PROJECT_MANAGE_PERMISSION = "project:manage"
PROJECT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


def _require_project_manage(
    request: Request,
    session: Session,
) -> tuple[Optional[CurrentUser], Optional[Response]]:
    current_user = load_current_user_from_request(request, session)
    if current_user is None:
        return None, RedirectResponse(
            url="/login", status_code=status.HTTP_303_SEE_OTHER
        )
    if PROJECT_MANAGE_PERMISSION not in current_user.permissions:
        return current_user, Response(status_code=status.HTTP_403_FORBIDDEN)
    if not has_platform_scope(current_user) and current_user.organization_id is None:
        return current_user, Response(status_code=status.HTTP_403_FORBIDDEN)
    return current_user, None


def _scoped_organization_filter(current_user: CurrentUser) -> Optional[int]:
    if has_platform_scope(current_user):
        return None
    return current_user.organization_id


def _resolve_organization_id_for_create(
    current_user: CurrentUser,
    submitted: Optional[int],
) -> Optional[int]:
    if has_platform_scope(current_user):
        return submitted
    return current_user.organization_id


def _available_orgs(current_user: CurrentUser, session: Session):
    actives = accounts.list_organizations(session, status_filter=("active",))
    if has_platform_scope(current_user):
        return actives
    return [o for o in actives if o.id == current_user.organization_id]


def _render_new_form(
    request: Request,
    *,
    current_user: CurrentUser,
    session: Session,
    values: dict,
    error: Optional[str],
) -> Response:
    csrf_token = request.cookies.get("fileforge_csrf", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "project_form.html",
        {
            "user": current_user,
            "csrf_token": csrf_token,
            "values": values,
            "available_organizations": _available_orgs(current_user, session),
            "org_locked": not has_platform_scope(current_user),
            "error": error,
        },
    )


@router.get("")
def list_projects_route(
    request: Request,
    organization_id: Optional[int] = None,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_project_manage(request, session)
    if error_response is not None:
        return error_response

    if has_platform_scope(current_user):
        effective_org_id = organization_id
        orgs_for_filter = accounts.list_organizations(session, status_filter=("active",))
        show_org_filter = True
    else:
        effective_org_id = current_user.organization_id
        orgs_for_filter = []
        show_org_filter = False

    rows = projects_service.list_projects(session, organization_id=effective_org_id)

    csrf_token = request.cookies.get("fileforge_csrf", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "projects_list.html",
        {
            "user": current_user,
            "projects": rows,
            "csrf_token": csrf_token,
            "show_org_filter": show_org_filter,
            "organizations_for_filter": orgs_for_filter,
            "filter_organization_id": effective_org_id,
        },
    )


@router.get("/new")
def get_new_project_form(
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_project_manage(request, session)
    if error_response is not None:
        return error_response

    initial_org_id = (
        current_user.organization_id
        if not has_platform_scope(current_user)
        else None
    )
    return _render_new_form(
        request,
        current_user=current_user,
        session=session,
        values={
            "project_key": "",
            "project_name": "",
            "description": "",
            "organization_id": initial_org_id,
        },
        error=None,
    )


@router.post("/new")
def post_new_project(
    request: Request,
    project_key: Optional[str] = Form(default=None),
    project_name: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=None),
    organization_id: Optional[int] = Form(default=None),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_project_manage(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    clean_key = (project_key or "").strip()
    clean_name = (project_name or "").strip()
    clean_desc = (description or "").strip()
    effective_org_id = _resolve_organization_id_for_create(
        current_user, organization_id
    )

    submitted_values = {
        "project_key": clean_key,
        "project_name": clean_name,
        "description": clean_desc,
        "organization_id": effective_org_id,
    }

    error: Optional[str] = None
    if not clean_key:
        error = "project_key 不能为空"
    elif len(clean_key) > 128:
        error = "project_key 长度不能超过 128 字符"
    elif not PROJECT_KEY_PATTERN.match(clean_key):
        error = "project_key 只能包含字母 数字 - _"
    elif len(clean_name) > 255:
        error = "项目名称长度不能超过 255 字符"
    elif len(clean_desc) > 1000:
        error = "描述长度不能超过 1000 字符"
    elif effective_org_id is None:
        error = "必须选择一个单位"

    if error is None:
        try:
            projects_service.create_project(
                session,
                project_key=clean_key,
                organization_id=effective_org_id,
                project_name=clean_name or None,
                description=clean_desc or None,
            )
            session.commit()
        except ValueError as exc:
            session.rollback()
            error = str(exc)

    if error is not None:
        return _render_new_form(
            request,
            current_user=current_user,
            session=session,
            values=submitted_values,
            error=error,
        )

    return RedirectResponse(
        url="/admin/projects",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{project_id}/disable")
def post_disable_project(
    request: Request,
    project_id: int,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    return _set_status(request, session, project_id, "disabled", csrf_token)


@router.post("/{project_id}/enable")
def post_enable_project(
    request: Request,
    project_id: int,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    return _set_status(request, session, project_id, "active", csrf_token)


def _set_status(
    request: Request,
    session: Session,
    project_id: int,
    new_status: str,
    csrf_token: Optional[str],
) -> Response:
    current_user, error_response = _require_project_manage(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    project = session.get(Project, project_id)
    if project is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if (
        not has_platform_scope(current_user)
        and project.organization_id != current_user.organization_id
    ):
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    try:
        projects_service.set_project_status(
            session, project_id=project_id, status=new_status
        )
        session.commit()
    except ValueError:
        session.rollback()
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    return RedirectResponse(
        url="/admin/projects",
        status_code=status.HTTP_303_SEE_OTHER,
    )
```

- [ ] **Step 4: 在 `web_admin/app.py` 注册 router**

```python
from web_admin.routes import projects as projects_routes
```

```python
app.include_router(projects_routes.router)
```

- [ ] **Step 5: 跑测试 + 全量回归**

```bash
python -m unittest tests.test_web_routes_projects -v
python -m unittest discover -s tests -p "test_*.py"
```

Expected: 19 PASS。

- [ ] **Step 6: 提交**

```bash
git add web_admin/routes/projects.py web_admin/app.py web_admin/templates/projects_list.html web_admin/templates/project_form.html tests/test_web_routes_projects.py
git commit -m "web: add project admin routes and templates"
```

---

## Task 6: `base.html` nav + nav 测试

**Files:**
- Modify: `web_admin/templates/base.html`
- Modify: `tests/test_web_routes_organizations.py`

- [ ] **Step 1: 在 `TestOrganizationRoutes` 末尾追加 2 个 nav 用例**

```python
    def test_base_nav_shows_organizations_link_for_platform_admin(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/admin/organizations")
        self.assertIn('href="/admin/organizations"', resp.text)

    def test_base_nav_hides_organizations_link_for_org_admin(self):
        with TestClient(self.app) as client:
            self._login(client, ORG_ADMIN_USERNAME, ORG_ADMIN_PASSWORD)
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('href="/admin/organizations"', resp.text)
        self.assertIn('href="/admin/projects"', resp.text)
```

- [ ] **Step 2: 跑测试,确认 2 条 FAIL**

```bash
python -m unittest tests.test_web_routes_organizations -v -k nav
```

- [ ] **Step 3: 改 `web_admin/templates/base.html`**

定位 nav 段:

```html
<nav class="nav">
  <a href="/batches">批次</a>
  {% if "user:manage" in user.permissions %}
  <a href="/admin/users">用户</a>
  {% endif %}
</nav>
```

替换为:

```html
<nav class="nav">
  <a href="/batches">批次</a>
  {% if "user:manage" in user.permissions %}
  <a href="/admin/users">用户</a>
  {% endif %}
  {% if "organization:manage" in user.permissions %}
  <a href="/admin/organizations">单位</a>
  {% endif %}
  {% if "project:manage" in user.permissions %}
  <a href="/admin/projects">项目</a>
  {% endif %}
</nav>
```

- [ ] **Step 4: 跑测试 + 全量回归**

```bash
python -m unittest tests.test_web_routes_organizations -v
python -m unittest discover -s tests -p "test_*.py"
```

Expected: 2 PASS,全量回归保持。

- [ ] **Step 5: 提交**

```bash
git add web_admin/templates/base.html tests/test_web_routes_organizations.py
git commit -m "web: add nav entries for org and project admin"
```

---

## Task 7: 同步文档

**Files:**
- Modify: `docs/postgresql_data_contract_design.md`
- Modify: `docs/web_admin.md`

- [ ] **Step 1: 在 `docs/postgresql_data_contract_design.md` §9.8 段后追加**

定位 §9.8 "Web 后台写侧函数" 已有的 `apply_manual_correction` 描述段。在其后追加:

```
- `accounts.list_organizations(session, *, status_filter=None) -> list[OrganizationRow]`、`accounts.set_organization_status(session, *, organization_id, status)`:供 Web `/admin/organizations` 调用。读侧返回 frozen dataclass(id/name/status/created_at/updated_at),写侧切 status 不级联到项目;`status` 必须 ∈ `ORGANIZATION_STATUS`。
- `projects.create_project(session, *, project_key, organization_id, project_name=None, description=None) -> Project`、`projects.list_projects(session, *, organization_id=None, status_filter=None) -> list[ProjectRow]`、`projects.set_project_status(session, *, project_id, status)`:供 Web `/admin/projects` 调用。`create_project` 内置 project_key 唯一性 + organization 存在与 active 校验,失败 ValueError;`list_projects` 通过 LEFT JOIN 把 organization_name 一并返回;`set_project_status` 接受全套 `PROJECT_STATUS` 枚举(含 archived,供 CLI 复用),Web 仅暴露 active/disabled 切换。
```

- [ ] **Step 2: 在 `docs/web_admin.md` §6 路由列表追加**

定位 `## 6 当前页面范围`,在路由列表末尾追加:

```
- `/admin/organizations`:单位列表(`organization:manage`,即 `platform_admin` 专属)。
- `/admin/organizations/new`:新建单位表单与提交。
- `/admin/organizations/{organization_id}/disable` 与 `/enable`:切单位 status,不级联到项目。
- `/admin/projects`:项目列表;`platform_admin` 可用 `?organization_id=N` 过滤,`org_admin` 自动限本单位。
- `/admin/projects/new`:新建项目表单;`org_admin` 的 organization_id 在表单与后端均锁定为本单位。
- `/admin/projects/{project_id}/disable` 与 `/enable`:切项目 status,不级联到批次/档案。
```

- [ ] **Step 3: 在 `docs/web_admin.md` §7 权限段追加**

```
- 单位管理(`/admin/organizations/*`)需要 `organization:manage`,内置只 seed 给 `platform_admin`。
- 项目管理(`/admin/projects/*`)需要 `project:manage`,seed 给 `platform_admin` 与 `org_admin`;`org_admin` 仅能看 / 操作本单位项目,跨单位访问统一 404。
- 非 `platform_admin` 用户若 `app_users.organization_id` 为 NULL,项目页 / 项目操作一律 403(边界守卫,避免单位过滤失效)。
```

- [ ] **Step 4: 提交**

```bash
git add docs/postgresql_data_contract_design.md docs/web_admin.md
git commit -m "docs: sync contract and runtime for org and project admin"
```

---

## Task 8: 最终验证

**Files:** 无修改,纯验证。

- [ ] **Step 1: 全量回归**

```bash
python -m unittest discover -s tests -p "test_*.py"
```

Expected: 269(基线)+ ~52(本期含 nav)= **~321 tests OK**。本机不可执行,在 Miniforge env 跑。

- [ ] **Step 2: 看 git 日志,确认 7 个新提交**

```bash
git log --oneline -10
```

Expected(自上而下,最新在上):

```
?  docs: sync contract and runtime for org and project admin
?  web: add nav entries for org and project admin
?  web: add project admin routes and templates
?  web: add organization admin routes and templates
?  db: add projects service for create/list/status
?  db: add list_organizations and set_organization_status
324111f docs: add org and project admin page spec
```

- [ ] **Step 3: 工作树干净**

```bash
git status --short
```

Expected: 只剩 `.claude/`、`.codex/`、`pre.txt`、`初稿.md` 这些既有未跟踪文件。
