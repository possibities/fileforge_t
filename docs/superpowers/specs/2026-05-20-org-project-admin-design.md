# 单位与项目管理页 设计

## 1 目标与背景

阶段 2 Web 后台已经把账号、登录、用户管理、批次/档案/修订/审计只读视图、元数据人工修正写侧打通。但目前 `organizations` 与 `projects` 两张基础实体表只能通过 `python -m utils.user_admin orgs create` CLI 创建,且 CLI 不支持禁用/启用,不支持项目创建。本设计补齐 Web 后台的"从零部署到能用"路径,让 `platform_admin` 在浏览器里就能完成单位与项目的列表/新建/启用-禁用操作,让 `org_admin` 在本单位范围内自主维护项目。

本设计面向一个可交付里程碑:

- `/admin/organizations`(列表 + 新建 + enable/disable)
- `/admin/projects`(列表 + 新建 + enable/disable)
- 数据层加 `infrastructure/db/projects.py` 新文件;`accounts.py` 补 `list_organizations` 与 `set_organization_status`
- 新建项目必须绑定 active 单位;`org_admin` 创建时 `organization_id` 强制锁本单位
- disable 不级联:禁用单位不自动禁用其项目;禁用项目不影响已有批次/档案数据

## 2 范围

### 2.1 新增能力

| 能力 | 行为 |
| --- | --- |
| 单位列表 | GET `/admin/organizations` 显示全部单位,按 name 升序,标识当前 status |
| 新建单位 | GET `/admin/organizations/new` + POST,name 唯一 |
| 启用/禁用单位 | POST `/admin/organizations/{id}/disable` 与 `/enable`,只切 status |
| 项目列表 | GET `/admin/projects` 显示所有可见项目;支持 `?organization_id=N` 查询参数(`platform_admin` 用) |
| 新建项目 | GET `/admin/projects/new` + POST,project_key 唯一,organization_id 必填且必须 active |
| 启用/禁用项目 | POST `/admin/projects/{id}/disable` 与 `/enable` |
| 数据层 | `accounts.list_organizations()` + `accounts.set_organization_status()`;新建 `projects.py`:`create_project`、`list_projects`、`set_project_status` |
| Nav | `base.html` 按 `organization:manage` / `project:manage` 权限显示对应导航链接 |
| 测试 | service 单测 + 路由 SQLite 测试,均不依赖外部服务 |

### 2.2 不在本里程碑范围

- 不支持重命名单位、修改项目 name / description / project_key
- 不支持把项目从一个单位转到另一个单位
- 不支持归档(`status = "archived"`)的 Web 入口;`set_project_status` 服务层接受该枚举供 CLI 复用,但 Web 不暴露按钮
- 不支持项目级 `numbering_rule` 编辑
- 不引入 DB 唯一索引 `projects.project_key`(本期靠应用层 SELECT 校验);若后续需要硬保障,另开迁移
- 不为禁用动作加二次确认对话框;一期信任 `platform_admin` 与 `org_admin` 的判断
- disable 不级联;单位禁用不自动禁用其项目,反之亦然

## 3 方案比较

### 3.1 推荐方案:`accounts.py` 加 org 三件套 + 新建 `infrastructure/db/projects.py`

`accounts.py` 已有 `create_organization`,自然延伸 `list_organizations` 与 `set_organization_status`;`projects.py` 新建文件装项目全部写侧逻辑。Web 层 `routes/organizations.py` + `routes/projects.py` 两个新路由模块,各自薄薄一层。

优势:

- 文件主题清晰:accounts 负责人事(账号 + 组织),projects 负责项目实体
- 不让 `accounts.py` 从 ~280 行膨胀到 ~430 行的"什么都管"
- Web 层与 service 层依赖方向单一:`web_admin/routes/* → infrastructure/db/{accounts,projects}`
- 测试隔离度高,`tests/test_db_projects.py` 独立文件不与账户测试混跑

劣势:Web 层 import 多一个源(可接受)。

### 3.2 方案 B:全塞 `accounts.py`

把 org + project 的全部 CRUD 都加到 `accounts.py` 末尾。

不选原因:文件主题变成"所有人事 + 资源管理",违反单一职责;一个文件超 400 行不利于上下文加载与编辑可靠性。

### 3.3 方案 C:新建 `infrastructure/db/admin_entities.py` 统管 org + project

不选原因:与已有 `accounts.create_organization` 重叠(要么搬过来要么留双源),会出现"为什么这个 org 函数在 accounts,那个在 admin_entities"的认知负担。

## 4 架构

### 4.1 文件清单

| 文件 | 类型 | 责任 |
| --- | --- | --- |
| `infrastructure/db/accounts.py` | Modify | 加 `OrganizationRow` dataclass、`list_organizations()`、`set_organization_status()` |
| `infrastructure/db/projects.py` | Create | `ProjectRow` dataclass、`create_project()`、`list_projects()`、`set_project_status()` |
| `web_admin/routes/organizations.py` | Create | 5 路由(GET list / GET new / POST new / POST disable / POST enable) |
| `web_admin/routes/projects.py` | Create | 同结构 5 路由 |
| `web_admin/templates/organizations_list.html` | Create | 列表 + 启用/禁用按钮 + 新建链接 |
| `web_admin/templates/organization_form.html` | Create | 单字段表单(name) |
| `web_admin/templates/projects_list.html` | Create | 列表 + 启用/禁用按钮 + 新建链接 + 单位过滤(platform admin) |
| `web_admin/templates/project_form.html` | Create | 4 字段表单(project_key/name/description/organization_id 下拉) |
| `web_admin/templates/base.html` | Modify | nav 按权限显示"单位"/"项目"链接 |
| `web_admin/app.py` | Modify | 注册两个新 router |
| `tests/test_db_accounts.py` | Modify | 加 `TestOrganizationManagement`(6 用例) |
| `tests/test_db_projects.py` | Create | `TestProjectManagement`(14 用例) |
| `tests/test_web_routes_organizations.py` | Create | `TestOrganizationRoutes`(11 用例) |
| `tests/test_web_routes_projects.py` | Create | `TestProjectRoutes`(18 用例) |
| `docs/postgresql_data_contract_design.md` | Modify | §9.x 加 admin entity 写侧函数说明 |
| `docs/web_admin.md` | Modify | §6 路由清单 + §7 权限补 `organization:manage` / `project:manage` |

无新增 Alembic 迁移(`organizations` / `projects` 表已存在,只动 `status` 列与新增行)。

### 4.2 函数签名

```python
# infrastructure/db/accounts.py 新增

@dataclass(frozen=True)
class OrganizationRow:
    id: int
    name: str
    status: str
    created_at: datetime
    updated_at: datetime


def list_organizations(
    session: Session,
    *,
    status_filter: Optional[Iterable[str]] = None,
) -> list[OrganizationRow]:
    """按 name 升序列出单位;status_filter=None 返回全部。"""


def set_organization_status(
    session: Session,
    *,
    organization_id: int,
    status: str,    # 必须 ∈ ORGANIZATION_STATUS
) -> None:
    """切换单位 status。不 commit。非枚举值或 id 不存在 → ValueError。"""
```

```python
# infrastructure/db/projects.py 新建

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
    organization_id: int,           # Web 入口强制非空
    project_name: Optional[str] = None,
    description: Optional[str] = None,
) -> Project:
    """新建 active 项目。不 commit。
    - project_key 空 / 重复 → ValueError
    - organization_id 不存在 / 单位 disabled → ValueError
    """


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


def set_project_status(
    session: Session,
    *,
    project_id: int,
    status: str,    # 必须 ∈ PROJECT_STATUS
) -> None:
    """切换项目 status。不 commit。非枚举值或 id 不存在 → ValueError。"""
```

### 4.3 数据流(以"新建项目"为例)

```
POST /admin/projects/new
  └─ routes/projects.py
       ├─ _require_project_manage  (登录 + project:manage,无则 403)
       ├─ verify_csrf_from_request (无则 403)
       ├─ 表单清洗:
       │    project_key:strip,1–128 字符,正则 ^[A-Za-z0-9_\-]+$
       │    project_name:strip,可空,≤255
       │    description:strip,可空,≤1000
       │    organization_id:int,必填
       ├─ 权限边界:非 platform_admin → organization_id 强制覆盖为 current_user.organization_id
       │    (表单层面 <select> 也已 disabled,后端兜底)
       ├─ projects.create_project(session, ...)
       │    失败 (ValueError) → 200 重渲 project_form.html + error,不 commit
       ├─ commit
       └─ 303 → /admin/projects
```

### 4.4 列表的组织作用域

```python
# routes/projects.py 内
def _scoped_organization_filter(current_user) -> Optional[int]:
    if has_platform_scope(current_user):
        return None
    return current_user.organization_id
```

`list_projects(session, organization_id=_scoped_organization_filter(user))` 由数据层完成 SQL 过滤,避免内存 filter。

**边界守卫**:`_require_project_manage` 内,若非 `platform_admin` 用户的 `current_user.organization_id is None`(degenerate state — org_admin/operator 未绑定单位),直接 403。否则上面 helper 会返回 None,让该用户绕过组织隔离看到全部项目。

### 4.5 表单清洗

| 字段 | 规则 |
| --- | --- |
| 单位 name | strip;非空;1–255 字符;唯一(DB 约束) |
| 项目 project_key | strip;非空;1–128 字符;正则 `^[A-Za-z0-9_\-]+$`;应用层唯一(SELECT 校验) |
| 项目 project_name | strip;可空;≤255 字符 |
| 项目 description | strip;可空;≤1000 字符 |
| 项目 organization_id | int,必填;指向存在的 active 单位 |

不通过 → 200 重渲表单 + error,DB 无副作用。

## 5 权限、状态与错误处理

### 5.1 权限矩阵

| 路由 | 未登录 | 缺权限 | platform_admin | org_admin | org_operator |
| --- | --- | --- | --- | --- | --- |
| `GET /admin/organizations*` | 303 → `/login` | 403 | 200 全部 | 403 | 403 |
| `POST /admin/organizations/*` | 303 → `/login` | 403 | 200 / 303 | 403 | 403 |
| `GET /admin/projects` | 303 → `/login` | 403 | 200 全部(可 `?organization_id=` 过滤) | 200 本单位 | 403 |
| `POST /admin/projects/new` | 303 → `/login` | 403 | 200 / 303 任意单位 | 200 / 303,org_id 锁本单位 | 403 |
| `POST /admin/projects/{id}/disable|enable` | 303 → `/login` | 403 | 任意 | 限本单位项目,跨单位 404 | 403 |

`organization:manage` 只 seed 给 `platform_admin`;`project:manage` seed 给 `platform_admin` + `org_admin`(见 `accounts.py:BUILTIN_ROLES`,无需改动)。

### 5.2 状态语义(不级联)

| 动作 | 表层效果 | 现有代码的自然保护 |
| --- | --- | --- |
| 禁用单位 | `Organization.status = "disabled"` | `accounts.authenticate_user` 已拒绝该单位用户登录(`accounts.py:258`) |
| 启用单位 | `status = "active"` | 用户重新可登录 |
| 禁用项目 | `Project.status = "disabled"` | `repositories.get_or_create_project` 已拒绝该项目的新批次(`repositories.py:69`);现有数据不变 |
| 启用项目 | `status = "active"` | 允许新 ingest |

### 5.3 错误处理

| 场景 | HTTP | 行为 |
| --- | --- | --- |
| 表单字段不合法 | 200 重渲 form + error | 不 commit,无副作用 |
| 重名单位 / 重复 project_key | 200 重渲 + error | 同上 |
| organization_id 指向不存在或 disabled 单位 | 200 重渲 + error | 同上 |
| CSRF 校验失败 | 403 | `error.html` |
| `org_admin` 跨单位访问别人的项目 | 404 | 与读侧 archive 一致 |
| 非 platform_admin 提交别的 org_id | 后端强制覆盖,**不报错** | 表单层 `<select>` disabled,后端兜底 |
| 未知 DB 异常 | 500 | rollback + 日志 |

### 5.4 关键约束

1. 项目新建表单的"单位"下拉**只列 active 单位**(disabled 单位不能成为新项目母单位)。
2. 禁用单位**不自动**禁用其项目;管理员需分别操作(避免误伤)。
3. `org_admin` 只能看 / 操作 `organization_id == self.organization_id` 的项目;跨单位访问一律 404。
4. `project_key` 一旦创建不可修改;disabled → active 重新启用即可,无需删除。
5. 服务层 `set_project_status` 接受 `"archived"`,但 Web 不暴露该按钮;留给 CLI/未来归档流程。
6. 不允许 platform_admin 禁用自己所在单位 — 本期不做检查,因为 `organization:manage` 只给 `platform_admin`,且 platform_admin 不强制属于具体单位。

## 6 测试策略

### 6.1 Service 层 — `tests/test_db_accounts.py` 新增 `TestOrganizationManagement`

| 用例 | 验证点 |
| --- | --- |
| `test_list_organizations_returns_all_sorted_by_name` | name 升序;含 active 与 disabled |
| `test_list_organizations_status_filter` | `status_filter=("active",)` 只返回 active |
| `test_set_organization_status_to_disabled` | 调用后 `org.status == "disabled"` |
| `test_set_organization_status_to_active_reenables` | disabled → active |
| `test_set_organization_status_invalid_status_raises_value_error` | 非枚举值 → ValueError |
| `test_set_organization_status_unknown_id_raises` | 不存在的 id → ValueError |

### 6.2 Service 层 — `tests/test_db_projects.py` 新建 `TestProjectManagement`

| 用例 | 验证点 |
| --- | --- |
| `test_create_project_success` | 写入后 status="active",organization_id 正确 |
| `test_create_project_duplicate_key_raises` | 同 project_key 第二次 → ValueError |
| `test_create_project_unknown_organization_raises` | org_id 不存在 → ValueError |
| `test_create_project_disabled_organization_raises` | 选 disabled org → ValueError |
| `test_create_project_blank_key_raises` | strip 后空 → ValueError |
| `test_list_projects_no_filter_returns_all` | 全部项目 |
| `test_list_projects_filters_by_organization_id` | 只返回指定 org 的 |
| `test_list_projects_status_filter` | active 过滤 |
| `test_list_projects_sorted_by_created_at_desc` | 最新在前 |
| `test_list_projects_includes_organization_name` | `ProjectRow.organization_name` 来自 join |
| `test_set_project_status_to_disabled` | active → disabled |
| `test_set_project_status_to_archived_accepted` | 接受 `"archived"`(供 CLI 复用) |
| `test_set_project_status_invalid_status_raises` | 非枚举值 → ValueError |
| `test_set_project_status_unknown_id_raises` | 不存在 → ValueError |

### 6.3 Web 路由 — `tests/test_web_routes_organizations.py` 新建 `TestOrganizationRoutes`

| 用例 | 验证点 |
| --- | --- |
| `test_get_list_unauthenticated_redirects_to_login` | 303 `/login` |
| `test_get_list_org_admin_returns_403` | 无 `organization:manage` → 403 |
| `test_get_list_platform_admin_shows_all_orgs` | 模板含两个测试 org name |
| `test_get_new_form_renders_with_csrf` | 200,含 form action + csrf input |
| `test_post_new_csrf_missing_returns_403` | 403 |
| `test_post_new_success_redirects_to_list` | 303 → `/admin/organizations`,DB 新增 active org |
| `test_post_new_duplicate_name_re_renders_with_error` | 200 重渲 + "已存在",DB 无新行 |
| `test_post_new_blank_name_re_renders_with_error` | 200 重渲 + error |
| `test_post_disable_changes_status` | 303,DB org.status=="disabled" |
| `test_post_enable_changes_status` | disabled → active |
| `test_post_disable_unknown_id_returns_404` | 不存在 → 404 |

### 6.4 Web 路由 — `tests/test_web_routes_projects.py` 新建 `TestProjectRoutes`

| 用例 | 验证点 |
| --- | --- |
| `test_get_list_unauthenticated_redirects_to_login` | 303 `/login` |
| `test_get_list_org_operator_returns_403` | 无 `project:manage` → 403 |
| `test_get_list_platform_admin_sees_all_projects` | 跨 org 全部 |
| `test_get_list_org_admin_sees_only_own_org_projects` | 别 org 的不出现 |
| `test_get_list_filters_by_query_organization_id` | platform_admin `?organization_id=N` 过滤 |
| `test_get_new_form_platform_admin_lists_all_active_orgs` | 下拉含 active orgs,不含 disabled |
| `test_get_new_form_org_admin_dropdown_locked_to_own_org` | 模板上下文只有自家 org |
| `test_post_new_csrf_missing_returns_403` | 403 |
| `test_post_new_success_redirects_to_list` | 303 → `/admin/projects`,DB 新增 active project |
| `test_post_new_org_admin_other_org_id_silently_locked` | form 传别 org_id 实际写入仍为自家 org |
| `test_post_new_duplicate_project_key_re_renders_with_error` | 200 重渲 + error |
| `test_post_new_invalid_project_key_re_renders_with_error` | 含非法字符 → 200 重渲 |
| `test_post_new_blank_project_key_re_renders_with_error` | strip 后空 → 200 重渲 |
| `test_post_new_disabled_org_re_renders_with_error` | 选 disabled org → 200 重渲 |
| `test_post_disable_changes_status` | 303,DB project.status=="disabled" |
| `test_post_enable_changes_status` | disabled → active |
| `test_post_disable_cross_org_org_admin_returns_404` | 跨 org → 404 |
| `test_post_disable_unknown_id_returns_404` | 不存在 → 404 |
| `test_get_list_org_admin_without_organization_id_returns_403` | 非 platform 用户 `organization_id=None` 边界守卫 → 403 |

### 6.5 Nav

`test_web_routes_users.py` 或本文件加 2 条:
- `test_base_nav_organizations_link_only_for_organization_manage`
- `test_base_nav_projects_link_only_for_project_manage`

### 6.6 回归

- 现有 269 用例 + 本期 ~49 用例 ≈ **318 全绿**(本机不跑,Miniforge env 验证)
- 不影响 `archive_query.py` / `user_admin.py` / `force_rerun_cli.py` 三个 CLI

### 6.7 不覆盖

- 项目转单位
- 单位/项目重命名
- 项目归档(`status = "archived"`)的 Web 入口(仅 service 层接受)
- 项目级 `numbering_rule` 编辑
- 禁用/启用的二次确认对话框
- 并发新建同 project_key 时的 race(本期靠应用层 SELECT 校验,后续可加 DB 唯一索引)

## 7 后续可能扩展

- 单位/项目重命名(`update_organization_name` / `update_project_name`)
- 项目转单位(`reassign_project_organization`),需要同时迁移其下批次的 organization_id 与 archive 的 organization_id
- 项目归档 Web 入口
- 项目级 `numbering_rule` 编辑
- `projects.project_key` 加 DB 唯一索引(避免并发新建 race)
- 禁用/启用的二次确认对话框 + reason 字段(类似 metadata_revisions 那样可追溯)
- 单位级"项目数量"列、项目级"批次数量"列
