# 元数据人工修正页 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## 实施修订记录 (2026-05-20)

本计划实施完成后,紧接着做了一轮重构。下列偏差应**优先于**本文后续 Task 步骤中给出的代码片段:

1. **`_has_platform_scope` 迁出 archives.py**:实现写在了 archives.py 的私有 helper `_has_platform_scope`,但随后(commit `960f153`)发现 `users.py` 有同名同实现,合并到 `web_admin/routes/__init__.py` 作为公共 `has_platform_scope`。**Task 3 Step 3 的"_has_platform_scope" 调用,在如今的代码里应是 `has_platform_scope`(无下划线前缀,从 routes 包 import)**。
2. **`_EDITABLE_FIELD_KEYS` 删除**:Task 3 Step 4 给出 `_EDITABLE_FIELD_KEYS: tuple[str, ...] = ("题名", "责任者", "实体分类号", "保管期限")` 这个本地常量,但 `infrastructure/db/repositories.py` 早已有同值的 `EDITABLE_FIELDS`(Task 1 Step 4 引入)。重构(commit `f98e543`)把 archives.py 顶部直接 `from infrastructure.db.repositories import EDITABLE_FIELDS, RETENTION_PERIOD_CHOICES, ManualCorrectionInput, apply_manual_correction`,删除 `_EDITABLE_FIELD_KEYS` 与 3 处 lazy import。**今天写新代码请直接 import `EDITABLE_FIELDS`,不要再定义本地版本**。
3. **`_render_edit_with_error` 重命名并合并入口**:Task 4 Step 4 引入了 `_render_edit_with_error(...)` 专门处理失败重渲染。重构(commit `238fcd5`)把 GET 路由原地构造 context 的逻辑也抽进去,统一改名为 `_render_edit_form(... error: Optional[str])`,GET 路由与失败重渲染走同一个 helper(`error=None` / `error=str`)。**今天 grep 不到 `_render_edit_with_error`,应使用 `_render_edit_form`**。

附带说明:Task 6 §9.4 在数据契约文档里的实际编号为 §9.8(原文档已有 §9.1–9.7),实施时按既有编号顺延。

**Goal:** 在 Web 后台加 `/archives/{id}/edit` 页面与一个新写侧函数 `apply_manual_correction()`,允许有 `archive:correct` 权限的用户修正档案 4 个核心字段(题名 / 责任者 / 实体分类号 / 保管期限),并把变更写入 `metadata_revisions` + `audit_logs`,与 CLI force-rerun 路径正交共存。

**Architecture:** 数据层新增 `apply_manual_correction()` 函数,复用既有 `_diff_metadata_to_revisions` / `record_revisions` / `record_audit_log` / `_REDUNDANT_COLUMN_MAP` / `_resolve_retention_code`。Web 层只做表单清洗 + CSRF + 权限校验,然后调函数。模板用最简 Jinja2(沿用 `admin.css`)。

**Tech Stack:** Python 3.12,SQLAlchemy 2.x,FastAPI,Jinja2,unittest,SQLite in-memory。

---

## File Structure

| 文件 | 类型 | 责任 |
| --- | --- | --- |
| `infrastructure/db/repositories.py` | Modify | 新增 `EDITABLE_FIELDS`、`RETENTION_PERIOD_CHOICES`、`ManualCorrectionInput`、`apply_manual_correction()` |
| `web_admin/routes/archives.py` | Modify | 新增 `ARCHIVE_CORRECT_PERMISSION` 常量、`_require_archive_correct()` helper、`get_archive_edit_form()` 与 `post_archive_edit()` 两个路由;`get_archive_detail` 加 `notice` 参数 |
| `web_admin/templates/archive_edit.html` | Create | 4 字段表单 + 13 字段只读展示 + 可选原因 + CSRF |
| `web_admin/templates/archive_detail.html` | Modify | 加"修正"按钮 + `?notice=no_change` 提示 |
| `tests/test_db_repositories.py` | Modify | 新增 `TestApplyManualCorrection`(10 用例) |
| `tests/test_web_routes_archives.py` | Modify | 新增 `TestArchiveEditRoute`(13 用例) + 详情页两条用例 |
| `docs/postgresql_data_contract_design.md` | Modify | §4.7 / §9 注脚追加 `manual_correction` action 与 4 字段约束 |

不新增 Alembic 迁移。

---

## Task 1: Repository function `apply_manual_correction`

**Files:**
- Modify: `infrastructure/db/repositories.py`
- Modify: `tests/test_db_repositories.py`

- [ ] **Step 1: 在 `tests/test_db_repositories.py` 末尾追加 TestCase + helper**

```python
@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestApplyManualCorrection(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            project = Project(project_key="p")
            session.add(project)
            session.flush()
            batch = ProcessingBatch(project_id=project.id, batch_key="b")
            session.add(batch)
            session.flush()
            self.project_id = project.id
            self.batch_id = batch.id
            session.commit()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _baseline_metadata(self) -> dict:
        return {
            "门类": "DQ",
            "归档年度": "2025",
            "实体分类号": "DQL",
            "实体分类名称": "党群类",
            "保管期限": "10年",
            "责任者": "县档案室",
            "文件编号": "DQ-2025-001",
            "题名": "原题名",
            "文件形成时间": "2025-03-01",
            "密级": "公开",
            "保密期限": "",
            "开放状态": "开放",
            "延期开放理由": "",
            "立档单位名称": "县档案馆",
            "数字化时间": "2025-04-10",
            "档号": "2025-DQL-D10-0001",
            "件号": "1",
        }

    def _make_archive(self, *, metadata=None, status="pending") -> int:
        md = metadata if metadata is not None else self._baseline_metadata()
        with self.Session() as session:
            archive = ArchiveRecord(
                project_id=self.project_id,
                batch_id=self.batch_id,
                archive_key="demo",
                archive_name="demo",
                title=md.get("题名"),
                responsible_party=md.get("责任者"),
                classification_code=md.get("实体分类号"),
                retention_period=md.get("保管期限"),
                archive_year=md.get("归档年度"),
                final_metadata=md,
                correction_status=status,
            )
            session.add(archive)
            session.commit()
            return archive.id

    def _input(self, **overrides):
        base = {
            "title": "原题名",
            "responsible_party": "县档案室",
            "classification_code": "DQL",
            "retention_period": "10年",
        }
        base.update(overrides)
        return repositories.ManualCorrectionInput(**base)
```

- [ ] **Step 2: 追加 10 个失败测试**

```python
    def test_no_diff_returns_zero_and_writes_nothing(self):
        archive_id = self._make_archive(status="pending")
        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            rev_no = repositories.apply_manual_correction(
                session,
                archive=archive,
                new_values=self._input(),
                actor_user_id=1,
            )
            session.commit()
        self.assertEqual(rev_no, 0)
        with self.Session() as session:
            from infrastructure.db.models import AuditLog, MetadataRevision
            self.assertEqual(session.query(MetadataRevision).count(), 0)
            self.assertEqual(session.query(AuditLog).count(), 0)
            self.assertEqual(session.get(ArchiveRecord, archive_id).correction_status, "pending")

    def test_single_field_change_writes_one_revision_and_audit(self):
        archive_id = self._make_archive()
        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            rev_no = repositories.apply_manual_correction(
                session,
                archive=archive,
                new_values=self._input(title="新题名"),
                actor_user_id=42,
            )
            session.commit()
        self.assertEqual(rev_no, 1)
        with self.Session() as session:
            from infrastructure.db.models import AuditLog, MetadataRevision
            revisions = session.query(MetadataRevision).all()
            self.assertEqual(len(revisions), 1)
            self.assertEqual(revisions[0].field_key, "题名")
            self.assertEqual(revisions[0].old_value, "原题名")
            self.assertEqual(revisions[0].new_value, "新题名")
            audits = session.query(AuditLog).all()
            self.assertEqual(len(audits), 1)
            self.assertEqual(audits[0].action, "manual_correction")
            self.assertEqual(audits[0].target_type, "archive")
            self.assertEqual(audits[0].target_id, archive_id)
            self.assertEqual(audits[0].before_data["题名"], "原题名")
            self.assertEqual(audits[0].after_data["题名"], "新题名")
            self.assertEqual(audits[0].before_data["立档单位名称"], "县档案馆")

    def test_multi_field_change_shares_one_revision_no(self):
        archive_id = self._make_archive()
        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            rev_no = repositories.apply_manual_correction(
                session,
                archive=archive,
                new_values=self._input(
                    title="新题名",
                    responsible_party="县档案馆",
                    classification_code="ZHL",
                    retention_period="30年",
                ),
                actor_user_id=1,
            )
            session.commit()
        with self.Session() as session:
            from infrastructure.db.models import MetadataRevision
            revisions = session.query(MetadataRevision).all()
            self.assertEqual(len(revisions), 4)
            self.assertEqual({r.revision_no for r in revisions}, {rev_no})
            self.assertEqual({r.field_key for r in revisions}, {"题名", "责任者", "实体分类号", "保管期限"})

    def test_retention_change_recomputes_retention_period_code(self):
        archive_id = self._make_archive()
        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            repositories.apply_manual_correction(
                session,
                archive=archive,
                new_values=self._input(retention_period="30年"),
                actor_user_id=1,
            )
            session.commit()
        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            self.assertEqual(archive.retention_period, "30年")
            self.assertEqual(archive.retention_period_code, "D30")

    def test_classification_change_updates_redundant_column_only(self):
        md = self._baseline_metadata()
        archive_id = self._make_archive(metadata=md)
        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            archive.archive_no = "2025-DQL-D10-0001"
            archive.item_no = "1"
            session.commit()
        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            repositories.apply_manual_correction(
                session,
                archive=archive,
                new_values=self._input(classification_code="ZHL"),
                actor_user_id=1,
            )
            session.commit()
        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            self.assertEqual(archive.classification_code, "ZHL")
            self.assertEqual(archive.archive_no, "2025-DQL-D10-0001")
            self.assertEqual(archive.item_no, "1")

    def test_other_metadata_keys_are_preserved(self):
        archive_id = self._make_archive()
        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            repositories.apply_manual_correction(
                session,
                archive=archive,
                new_values=self._input(title="新题名"),
                actor_user_id=1,
            )
            session.commit()
        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            self.assertEqual(archive.final_metadata["立档单位名称"], "县档案馆")
            self.assertEqual(archive.final_metadata["数字化时间"], "2025-04-10")
            self.assertEqual(archive.final_metadata["题名"], "新题名")

    def test_sets_correction_status_to_corrected(self):
        archive_id = self._make_archive(status="pending")
        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            repositories.apply_manual_correction(
                session,
                archive=archive,
                new_values=self._input(title="新题名"),
                actor_user_id=1,
            )
            session.commit()
        with self.Session() as session:
            self.assertEqual(session.get(ArchiveRecord, archive_id).correction_status, "corrected")

    def test_reason_empty_stores_literal_marker(self):
        archive_id = self._make_archive()
        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            repositories.apply_manual_correction(
                session,
                archive=archive,
                new_values=self._input(title="A"),
                actor_user_id=1,
                reason=None,
            )
            repositories.apply_manual_correction(
                session,
                archive=archive,
                new_values=self._input(title="B"),
                actor_user_id=1,
                reason="OCR 漏字",
            )
            session.commit()
        with self.Session() as session:
            from infrastructure.db.models import MetadataRevision
            rows = session.query(MetadataRevision).order_by(MetadataRevision.revision_no).all()
            self.assertEqual(rows[0].reason, "manual_correction")
            self.assertEqual(rows[-1].reason, "OCR 漏字")

    def test_actor_user_id_recorded_on_both_tables(self):
        archive_id = self._make_archive()
        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            repositories.apply_manual_correction(
                session,
                archive=archive,
                new_values=self._input(title="新题名"),
                actor_user_id=77,
            )
            session.commit()
        with self.Session() as session:
            from infrastructure.db.models import AuditLog, MetadataRevision
            self.assertEqual(session.query(MetadataRevision).first().created_by, 77)
            self.assertEqual(session.query(AuditLog).first().actor_user_id, 77)

    def test_force_rerun_rules_can_override_after_manual_correction(self):
        archive_id = self._make_archive()
        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            repositories.apply_manual_correction(
                session,
                archive=archive,
                new_values=self._input(title="手工题名"),
                actor_user_id=1,
            )
            session.commit()
        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            self.assertEqual(archive.correction_status, "corrected")
            new_md = dict(archive.final_metadata)
            new_md["题名"] = "重跑题名"
            repositories.apply_force_rerun_rules(
                session,
                archive=archive,
                new_metadata=new_md,
                actor_user_id=1,
            )
            session.commit()
        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            self.assertEqual(archive.final_metadata["题名"], "重跑题名")
            self.assertEqual(archive.title, "重跑题名")
```

- [ ] **Step 3: 跑测试,确认 10 条 FAIL**

```bash
python -m unittest tests.test_db_repositories.TestApplyManualCorrection -v
```

Expected: 10 个 error,主要错误是 `AttributeError: module 'infrastructure.db.repositories' has no attribute 'ManualCorrectionInput'`。

- [ ] **Step 4: 在 `infrastructure/db/repositories.py` 末尾追加常量、dataclass、函数**

```python
EDITABLE_FIELDS: tuple[str, ...] = ("题名", "责任者", "实体分类号", "保管期限")
RETENTION_PERIOD_CHOICES: tuple[str, ...] = ("永久", "30年", "10年")


@dataclass
class ManualCorrectionInput:
    """人工修正提交的 4 个字段新值。

    所有字段都应在 Web/CLI 入口处完成 strip / 长度 / enum 校验后再传入;
    `apply_manual_correction` 不做二次校验。
    """

    title: str
    responsible_party: str
    classification_code: str
    retention_period: str


def apply_manual_correction(
    session: Session,
    *,
    archive: ArchiveRecord,
    new_values: ManualCorrectionInput,
    actor_user_id: int,
    reason: Optional[str] = None,
) -> int:
    """对档案做人工修正:diff → revisions → 同步冗余列 + retention_period_code
    → 置 correction_status='corrected' → audit。函数自身不 commit。
    返回写入的 revision_no;无差异返回 0(无 audit、无字段更新)。
    """
    old_final = dict(archive.final_metadata or {})
    overlay = {
        "题名": new_values.title,
        "责任者": new_values.responsible_party,
        "实体分类号": new_values.classification_code,
        "保管期限": new_values.retention_period,
    }
    new_final = {**old_final, **overlay}

    diffs = _diff_metadata_to_revisions(old_final, new_final)
    if not diffs:
        return 0

    stored_reason = reason if reason else "manual_correction"
    rev_no = record_revisions(
        session,
        archive_id=archive.id,
        revisions=diffs,
        actor_user_id=actor_user_id,
        reason=stored_reason,
    )

    archive.final_metadata = new_final
    for key, column in _REDUNDANT_COLUMN_MAP.items():
        if key in overlay:
            value = overlay[key]
            setattr(archive, column, str(value) if value is not None else None)
    archive.retention_period_code = _resolve_retention_code(
        new_final.get("归档年度"),
        new_final.get("保管期限"),
    )
    archive.correction_status = "corrected"

    record_audit_log(
        session,
        actor_user_id=actor_user_id,
        action="manual_correction",
        target_type="archive",
        target_id=archive.id,
        before_data=old_final,
        after_data=new_final,
    )
    return rev_no
```

定位文件顶部已有的 `__all__` 列表(约第 537–555 行),把这 4 个加进去(按字母序或就近):

```python
"EDITABLE_FIELDS",
"RETENTION_PERIOD_CHOICES",
"ManualCorrectionInput",
"apply_manual_correction",
```

- [ ] **Step 5: 跑测试 + 全量回归**

```bash
python -m unittest tests.test_db_repositories.TestApplyManualCorrection -v
python -m unittest discover -s tests -p "test_*.py"
```

Expected: 10 PASS,全量 ~254 OK。

- [ ] **Step 6: 提交**

```bash
git add infrastructure/db/repositories.py tests/test_db_repositories.py
git commit -m "db: add apply_manual_correction for web edit path"
```

---

## Task 2: 模板 `archive_edit.html`

**Files:**
- Create: `web_admin/templates/archive_edit.html`

- [ ] **Step 1: 创建模板**

```html
{% extends "base.html" %}
{% block title %}修正档案 - FileForge 管理后台{% endblock %}
{% block content %}
<h1>修正档案元数据</h1>
<p>项目: {{ project.project_key }};批次: {{ batch.batch_key }};档案: {{ archive.archive_key }}</p>

{% if error %}
<p class="error">{{ error }}</p>
{% endif %}

<form method="post" action="/archives/{{ archive.id }}/edit">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">

  <fieldset>
    <legend>可修正字段</legend>
    <p>
      <label for="title">题名(必填,1–500 字符)</label><br>
      <input type="text" id="title" name="title" value="{{ values.title or '' }}" maxlength="500" required>
    </p>
    <p>
      <label for="responsible_party">责任者(必填,1–200 字符)</label><br>
      <input type="text" id="responsible_party" name="responsible_party" value="{{ values.responsible_party or '' }}" maxlength="200" required>
    </p>
    <p>
      <label for="classification_code">实体分类号(必填,1–32 字符)</label><br>
      <input type="text" id="classification_code" name="classification_code" value="{{ values.classification_code or '' }}" maxlength="32" required>
    </p>
    <p>
      <label for="retention_period">保管期限</label><br>
      <select id="retention_period" name="retention_period" required>
        {% for choice in retention_choices %}
        <option value="{{ choice }}"{% if values.retention_period == choice %} selected{% endif %}>{{ choice }}</option>
        {% endfor %}
      </select>
    </p>
    <p>
      <label for="reason">修正原因(选填,≤500 字符)</label><br>
      <textarea id="reason" name="reason" rows="3" maxlength="500">{{ values.reason or '' }}</textarea>
    </p>
  </fieldset>

  <fieldset>
    <legend>其它字段(只读)</legend>
    <dl>
      {% for key, value in readonly_fields %}
      <dt>{{ key }}</dt>
      <dd>{{ value or "" }}</dd>
      {% endfor %}
    </dl>
  </fieldset>

  <p>
    <button type="submit">保存修正</button>
    <a href="/archives/{{ archive.id }}">取消</a>
  </p>
</form>
{% endblock %}
```

- [ ] **Step 2: 暂不单独提交**

模板的实际渲染由 Task 3 的 GET 路由测试覆盖。文件放在工作树等 Task 3 一起提交。

---

## Task 3: GET edit 路由

**Files:**
- Modify: `web_admin/routes/archives.py`
- Modify: `tests/test_web_routes_archives.py`

- [ ] **Step 1: 在 `tests/test_web_routes_archives.py` 末尾追加 TestArchiveEditRoute + 4 个 GET 用例**

```python
@unittest.skipUnless(DEPENDENCIES_AVAILABLE, f"web deps missing: {_IMPORT_ERROR}")
class TestArchiveEditRoute(unittest.TestCase):
    def setUp(self):
        from infrastructure.db import accounts
        from infrastructure.db.models import (
            ArchiveRecord,
            Base,
            Organization,
            ProcessingBatch,
            Project,
        )
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            accounts.ensure_builtin_roles(session)
            org_a = Organization(name="档案室甲")
            org_b = Organization(name="档案室乙")
            session.add_all([org_a, org_b])
            session.flush()
            accounts.create_user(
                session,
                username=ADMIN_USERNAME,
                password=ADMIN_PASSWORD,
                display_name="平台管理员",
                role_codes=["platform_admin"],
            )
            accounts.create_user(
                session,
                username=OPERATOR_USERNAME,
                password=OPERATOR_PASSWORD,
                display_name="甲单位操作员",
                organization_id=org_a.id,
                role_codes=["org_operator"],
            )
            project_a = Project(project_key="proj_a", organization_id=org_a.id)
            project_b = Project(project_key="proj_b", organization_id=org_b.id)
            session.add_all([project_a, project_b])
            session.flush()
            batch_a = ProcessingBatch(
                project_id=project_a.id,
                batch_key="batch_a",
                batch_status="completed",
                organization_id=org_a.id,
            )
            batch_b = ProcessingBatch(
                project_id=project_b.id,
                batch_key="batch_b",
                batch_status="completed",
                organization_id=org_b.id,
            )
            session.add_all([batch_a, batch_b])
            session.flush()
            archive_a = ArchiveRecord(
                project_id=project_a.id,
                batch_id=batch_a.id,
                archive_key="arc_a",
                archive_name="甲档案",
                title="原题名",
                responsible_party="县档案室",
                classification_code="DQL",
                retention_period="10年",
                archive_year="2025",
                organization_id=org_a.id,
                final_metadata={
                    "门类": "DQ",
                    "归档年度": "2025",
                    "实体分类号": "DQL",
                    "保管期限": "10年",
                    "责任者": "县档案室",
                    "题名": "原题名",
                    "立档单位名称": "县档案馆",
                },
                correction_status="pending",
            )
            archive_b = ArchiveRecord(
                project_id=project_b.id,
                batch_id=batch_b.id,
                archive_key="arc_b",
                archive_name="乙档案",
                title="乙题名",
                organization_id=org_b.id,
                final_metadata={"题名": "乙题名"},
            )
            session.add_all([archive_a, archive_b])
            session.flush()
            self.archive_a_id = archive_a.id
            self.archive_b_id = archive_b.id
            session.commit()
        self.app = create_app(database_url="sqlite://")
        self.app.state.session_factory = self.Session

    def tearDown(self):
        from infrastructure.db.models import Base
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _login(self, client, username: str, password: str) -> None:
        resp = client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, {302, 303})

    def _csrf(self, client) -> str:
        return client.cookies.get("fileforge_csrf") or ""

    def test_get_edit_form_unauthenticated_redirects_to_login(self):
        with TestClient(self.app) as client:
            resp = client.get(
                f"/archives/{self.archive_a_id}/edit",
                follow_redirects=False,
            )
        self.assertIn(resp.status_code, {302, 303})
        self.assertTrue(resp.headers["location"].endswith("/login"))

    def test_get_edit_form_missing_permission_returns_403(self):
        from infrastructure.db import accounts
        from infrastructure.db.models import AppUser, Role, UserRole
        with self.Session() as session:
            role = Role(code="readonly", name="只读")
            session.add(role)
            session.flush()
            accounts.create_user(
                session,
                username="readonly_user",
                password="readonly-strong-pw",
                display_name="只读用户",
                role_codes=[],
            )
            user = session.query(AppUser).filter_by(username="readonly_user").first()
            session.add(UserRole(user_id=user.id, role_id=role.id))
            session.commit()
        with TestClient(self.app) as client:
            self._login(client, "readonly_user", "readonly-strong-pw")
            resp = client.get(
                f"/archives/{self.archive_a_id}/edit",
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 403)

    def test_get_edit_form_cross_org_returns_404(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = client.get(
                f"/archives/{self.archive_b_id}/edit",
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 404)

    def test_get_edit_form_renders_prefilled_with_current_values(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(f"/archives/{self.archive_a_id}/edit")
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn('name="title"', body)
        self.assertIn("原题名", body)
        self.assertIn("县档案室", body)
        self.assertIn('name="csrf_token"', body)
        self.assertIn("立档单位名称", body)
```

- [ ] **Step 2: 跑 GET 测试,确认 4 条 FAIL**

```bash
python -m unittest tests.test_web_routes_archives.TestArchiveEditRoute -v
```

Expected: 4 failures(404 / 405)。

- [ ] **Step 3: 在 `web_admin/routes/archives.py` 顶部加常量 + helper**

定位到 `AUDIT_VIEW_PERMISSION = "audit:view"`,后面加一行:

```python
ARCHIVE_CORRECT_PERMISSION = "archive:correct"
```

定位 `_require_archive_view` 函数(已存在),在它末尾(`return current_user, None` 之后)追加新 helper:

```python
def _require_archive_correct(
    request: Request,
    session: Session,
) -> tuple[Optional[CurrentUser], Optional[Response]]:
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return current_user, error_response
    if ARCHIVE_CORRECT_PERMISSION not in current_user.permissions:
        return current_user, Response(status_code=status.HTTP_403_FORBIDDEN)
    return current_user, None
```

- [ ] **Step 4: 在文件末尾追加 GET 路由**

```python
_EDITABLE_FIELD_KEYS: tuple[str, ...] = ("题名", "责任者", "实体分类号", "保管期限")


def _readonly_fields(archive: ArchiveRecord) -> list[tuple[str, str]]:
    md = dict(archive.final_metadata or {})
    seen = set(_EDITABLE_FIELD_KEYS)
    return [(key, md.get(key) or "") for key in md.keys() if key not in seen]


def _current_values_from_archive(archive: ArchiveRecord) -> dict[str, str]:
    md = archive.final_metadata or {}
    return {
        "title": md.get("题名") or archive.title or "",
        "responsible_party": md.get("责任者") or archive.responsible_party or "",
        "classification_code": md.get("实体分类号") or archive.classification_code or "",
        "retention_period": md.get("保管期限") or archive.retention_period or "永久",
        "reason": "",
    }


@router.get("/archives/{archive_id}/edit")
def get_archive_edit_form(
    request: Request,
    archive_id: int,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_archive_correct(request, session)
    if error_response is not None:
        return error_response

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    access = _can_access_archive(session, current_user, archive)
    if access is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    batch, project = access

    from infrastructure.db.repositories import RETENTION_PERIOD_CHOICES
    csrf_token = request.cookies.get("fileforge_csrf", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "archive_edit.html",
        {
            "user": current_user,
            "project": project,
            "batch": batch,
            "archive": archive,
            "values": _current_values_from_archive(archive),
            "readonly_fields": _readonly_fields(archive),
            "retention_choices": list(RETENTION_PERIOD_CHOICES),
            "csrf_token": csrf_token,
            "error": None,
        },
    )
```

- [ ] **Step 5: 跑 GET 用例,确认 4 PASS**

```bash
python -m unittest tests.test_web_routes_archives.TestArchiveEditRoute -v
```

Expected: 4 PASS。

- [ ] **Step 6: 提交**

```bash
git add web_admin/routes/archives.py web_admin/templates/archive_edit.html tests/test_web_routes_archives.py
git commit -m "web: add archive edit form GET route"
```

---

## Task 4: POST edit 路由

**Files:**
- Modify: `web_admin/routes/archives.py`
- Modify: `tests/test_web_routes_archives.py`

- [ ] **Step 1: 在 `TestArchiveEditRoute` 末尾追加 9 个 POST 用例**

```python
    def _post_edit(self, client, archive_id: int, csrf: str, **fields):
        form = {
            "title": "新题名",
            "responsible_party": "县档案室",
            "classification_code": "DQL",
            "retention_period": "10年",
            "reason": "",
            "csrf_token": csrf,
        }
        form.update(fields)
        return client.post(
            f"/archives/{archive_id}/edit",
            data=form,
            follow_redirects=False,
        )

    def test_post_edit_csrf_missing_returns_403(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_edit(client, self.archive_a_id, csrf="")
        self.assertEqual(resp.status_code, 403)

    def test_post_edit_invalid_retention_period_re_renders_with_error(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_edit(
                client,
                self.archive_a_id,
                csrf=self._csrf(client),
                retention_period="5年",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("保管期限", resp.text)
        from infrastructure.db.models import MetadataRevision
        with self.Session() as session:
            self.assertEqual(session.query(MetadataRevision).count(), 0)

    def test_post_edit_blank_title_re_renders_with_error(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_edit(
                client,
                self.archive_a_id,
                csrf=self._csrf(client),
                title="   ",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("题名", resp.text)

    def test_post_edit_too_long_title_re_renders_with_error(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_edit(
                client,
                self.archive_a_id,
                csrf=self._csrf(client),
                title="x" * 501,
            )
        self.assertEqual(resp.status_code, 200)

    def test_post_edit_success_redirects_to_detail(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_edit(
                client,
                self.archive_a_id,
                csrf=self._csrf(client),
                title="新题名",
            )
        self.assertIn(resp.status_code, {302, 303})
        self.assertEqual(resp.headers["location"], f"/archives/{self.archive_a_id}")
        from infrastructure.db.models import AuditLog, MetadataRevision
        with self.Session() as session:
            self.assertEqual(session.query(MetadataRevision).count(), 1)
            self.assertEqual(session.query(AuditLog).count(), 1)

    def test_post_edit_no_change_redirects_with_notice(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_edit(
                client,
                self.archive_a_id,
                csrf=self._csrf(client),
                title="原题名",
            )
        self.assertIn(resp.status_code, {302, 303})
        self.assertEqual(
            resp.headers["location"],
            f"/archives/{self.archive_a_id}?notice=no_change",
        )
        from infrastructure.db.models import MetadataRevision
        with self.Session() as session:
            self.assertEqual(session.query(MetadataRevision).count(), 0)

    def test_post_edit_platform_admin_can_edit_any_org(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_edit(
                client,
                self.archive_b_id,
                csrf=self._csrf(client),
                title="跨组织修改",
                responsible_party="X",
                classification_code="DQL",
                retention_period="永久",
            )
        self.assertIn(resp.status_code, {302, 303})

    def test_post_edit_org_operator_cannot_edit_other_org(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = self._post_edit(
                client,
                self.archive_b_id,
                csrf=self._csrf(client),
                title="跨组织尝试",
            )
        self.assertEqual(resp.status_code, 404)

    def test_post_edit_records_actor_user_id_from_session(self):
        from infrastructure.db.models import AppUser, AuditLog, MetadataRevision
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            self._post_edit(
                client,
                self.archive_a_id,
                csrf=self._csrf(client),
                title="新题名",
                reason="OCR 漏字",
            )
        with self.Session() as session:
            admin_id = session.query(AppUser).filter_by(username=ADMIN_USERNAME).first().id
            rev = session.query(MetadataRevision).first()
            audit = session.query(AuditLog).first()
            self.assertEqual(rev.created_by, admin_id)
            self.assertEqual(rev.reason, "OCR 漏字")
            self.assertEqual(audit.actor_user_id, admin_id)
```

- [ ] **Step 2: 跑测试,确认 9 条 FAIL(405 Method Not Allowed)**

```bash
python -m unittest tests.test_web_routes_archives.TestArchiveEditRoute -v
```

- [ ] **Step 3: 修 import 头**

确认 `web_admin/routes/archives.py` 顶部已经 `from fastapi import APIRouter, Depends, Form, Query, Request, Response, status`(若没有 `Form` 则补上)。

确认顶部已经 `from web_admin.routes import (load_current_user_from_request, verify_csrf_from_request)`(若 `verify_csrf_from_request` 没 import 则补上)。

- [ ] **Step 4: 在 GET 路由后追加 POST 路由 + 表单清洗 helper**

```python
def _clean_form_field(
    raw: Optional[str],
    *,
    max_len: int,
    name: str,
    required: bool = True,
) -> tuple[Optional[str], Optional[str]]:
    value = (raw or "").strip()
    if not value:
        if required:
            return None, f"{name}不能为空"
        return "", None
    if len(value) > max_len:
        return None, f"{name}长度不能超过 {max_len} 字符"
    return value, None


def _render_edit_with_error(
    request: Request,
    *,
    current_user: CurrentUser,
    project,
    batch,
    archive: ArchiveRecord,
    values: dict[str, str],
    error: str,
) -> Response:
    from infrastructure.db.repositories import RETENTION_PERIOD_CHOICES
    csrf_token = request.cookies.get("fileforge_csrf", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "archive_edit.html",
        {
            "user": current_user,
            "project": project,
            "batch": batch,
            "archive": archive,
            "values": values,
            "readonly_fields": _readonly_fields(archive),
            "retention_choices": list(RETENTION_PERIOD_CHOICES),
            "csrf_token": csrf_token,
            "error": error,
        },
    )


@router.post("/archives/{archive_id}/edit")
def post_archive_edit(
    request: Request,
    archive_id: int,
    title: Optional[str] = Form(default=None),
    responsible_party: Optional[str] = Form(default=None),
    classification_code: Optional[str] = Form(default=None),
    retention_period: Optional[str] = Form(default=None),
    reason: Optional[str] = Form(default=None),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_archive_correct(request, session)
    if error_response is not None:
        return error_response

    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    access = _can_access_archive(session, current_user, archive)
    if access is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    batch, project = access

    from infrastructure.db import repositories
    from infrastructure.db.repositories import (
        ManualCorrectionInput,
        RETENTION_PERIOD_CHOICES,
    )

    err: Optional[str] = None
    clean_title, err = _clean_form_field(title, max_len=500, name="题名")
    clean_party = clean_class = clean_retention = clean_reason = None
    if err is None:
        clean_party, err = _clean_form_field(responsible_party, max_len=200, name="责任者")
    if err is None:
        clean_class, err = _clean_form_field(classification_code, max_len=32, name="实体分类号")
    if err is None:
        clean_retention = (retention_period or "").strip()
        if clean_retention not in RETENTION_PERIOD_CHOICES:
            err = f"保管期限必须为 {', '.join(RETENTION_PERIOD_CHOICES)} 之一"
    if err is None:
        clean_reason, err = _clean_form_field(
            reason, max_len=500, name="原因", required=False,
        )

    submitted_values = {
        "title": (title or "").strip(),
        "responsible_party": (responsible_party or "").strip(),
        "classification_code": (classification_code or "").strip(),
        "retention_period": (retention_period or "").strip(),
        "reason": (reason or "").strip(),
    }

    if err is not None:
        return _render_edit_with_error(
            request,
            current_user=current_user,
            project=project,
            batch=batch,
            archive=archive,
            values=submitted_values,
            error=err,
        )

    try:
        rev_no = repositories.apply_manual_correction(
            session,
            archive=archive,
            new_values=ManualCorrectionInput(
                title=clean_title,
                responsible_party=clean_party,
                classification_code=clean_class,
                retention_period=clean_retention,
            ),
            actor_user_id=current_user.id,
            reason=clean_reason or None,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    if rev_no == 0:
        return RedirectResponse(
            url=f"/archives/{archive_id}?notice=no_change",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/archives/{archive_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
```

`current_user.id` 直接用 `web_admin/auth.py:36` 上的 `CurrentUser.id: int`。

- [ ] **Step 5: 跑全部 13 条 edit 用例 + 全量回归**

```bash
python -m unittest tests.test_web_routes_archives.TestArchiveEditRoute -v
python -m unittest discover -s tests -p "test_*.py"
```

Expected: 13 PASS;全量 ~267 OK。

- [ ] **Step 6: 提交**

```bash
git add web_admin/routes/archives.py tests/test_web_routes_archives.py
git commit -m "web: add archive edit form POST route"
```

---

## Task 5: `archive_detail.html` 加修正按钮 + no_change 提示

**Files:**
- Modify: `web_admin/routes/archives.py`
- Modify: `web_admin/templates/archive_detail.html`
- Modify: `tests/test_web_routes_archives.py`

- [ ] **Step 1: 在 `TestArchiveQueryRoutes` 末尾追加 2 个用例**

(`TestArchiveQueryRoutes` 是文件中**已存在**的类,不是新建的 `TestArchiveEditRoute`)

```python
    def test_archive_detail_shows_edit_link_when_user_has_correct_permission(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(f"/archives/{self.archive_spring_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(f"/archives/{self.archive_spring_id}/edit", resp.text)

    def test_archive_detail_no_change_notice_renders(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                f"/archives/{self.archive_spring_id}?notice=no_change",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("无字段变化", resp.text)
```

- [ ] **Step 2: 改 `get_archive_detail` 加 `notice` 参数透传**

定位 `get_archive_detail`,加 `notice` query 参数:

```python
@router.get("/archives/{archive_id}")
def get_archive_detail(
    request: Request,
    archive_id: int,
    notice: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    access = _can_access_archive(session, current_user, archive)
    if access is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    batch, project = access

    detail = queries.get_archive_detail(session, archive_id=archive_id)
    if detail is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "archive_detail.html",
        {
            "user": current_user,
            "project": project,
            "batch": batch,
            "archive": detail,
            "notice": notice,
        },
    )
```

- [ ] **Step 3: 改 `web_admin/templates/archive_detail.html`**

定位:

```html
<p>
  <a href="/archives/{{ archive.id }}/revisions">修订记录</a>
  {% if "audit:view" in user.permissions %}
  <a href="/archives/{{ archive.id }}/audit">审计记录</a>
  {% endif %}
</p>
```

替换成:

```html
{% if notice == "no_change" %}
<p class="notice">无字段变化,未生成新修订。</p>
{% endif %}

<p>
  <a href="/archives/{{ archive.id }}/revisions">修订记录</a>
  {% if "audit:view" in user.permissions %}
  <a href="/archives/{{ archive.id }}/audit">审计记录</a>
  {% endif %}
  {% if "archive:correct" in user.permissions %}
  <a href="/archives/{{ archive.id }}/edit">修正</a>
  {% endif %}
</p>
```

- [ ] **Step 4: 跑测试**

```bash
python -m unittest tests.test_web_routes_archives -v
python -m unittest discover -s tests -p "test_*.py"
```

Expected: 全量 ~269 OK。

- [ ] **Step 5: 提交**

```bash
git add web_admin/routes/archives.py web_admin/templates/archive_detail.html tests/test_web_routes_archives.py
git commit -m "web: surface edit entry and no-change notice on archive detail"
```

---

## Task 6: 数据契约文档同步

**Files:**
- Modify: `docs/postgresql_data_contract_design.md`

- [ ] **Step 1: 在 §4.7 `metadata_revisions` 描述追加 reason 字面值约定**

用 `metadata_revisions` 关键字定位 §4.7,在 `reason` 字段说明附近补一段:

```
- `reason`:可空。Web 端人工修正若未填表单原因,统一存字面 `manual_correction`;CLI 自动重跑统一存 `rules_rerun_force`。其它来源应使用语义化短字符串便于 SQL 反查。
```

- [ ] **Step 2: 在 §9 后追加新小节,描述 Web 写侧函数**

文档已有 §9.1 / §9.2 / §9.3,在末尾追加 §9.4(若 §9 已有更多小节,顺延编号):

```
### 9.4 Web 后台写侧函数

- `apply_manual_correction(session, *, archive, new_values, actor_user_id, reason=None) -> int`:供 Web 后台 `/archives/{id}/edit` POST 调用。只接受 4 个字段(`题名` / `责任者` / `实体分类号` / `保管期限`)的新值,内部 diff、记 `metadata_revisions` 与 `audit_logs(action="manual_correction")`,把 `correction_status` 置为 `corrected`,并按新值重新派生 `retention_period_code`。函数自身不 commit;无差异返回 0(无副作用)。
- 与 `apply_force_rerun_rules(action="force_rerun_rules")` 互补:后者清掉 `corrected` 强制覆盖整套 metadata,适合 CLI 全字段重跑;前者只动 4 字段并置 `corrected`,适合 Web 端人工微调。
```

- [ ] **Step 3: 提交**

```bash
git add docs/postgresql_data_contract_design.md
git commit -m "docs: contract — apply_manual_correction and manual_correction action"
```

---

## Task 7: 最终验证

**Files:** 无修改,纯验证步骤。

- [ ] **Step 1: 跑全量回归**

```bash
python -m unittest discover -s tests -p "test_*.py"
```

Expected: 269 tests OK(244 基线 + 25 新增)。本机不可执行,需在 Miniforge env 跑。

- [ ] **Step 2: 看 git 日志,确认 5 个新提交**

```bash
git log --oneline -10
```

Expected(自上而下,最新在上):

```
?  docs: contract — apply_manual_correction and manual_correction action
?  web: surface edit entry and no-change notice on archive detail
?  web: add archive edit form POST route
?  web: add archive edit form GET route
?  db: add apply_manual_correction for web edit path
63537cf docs: tighten metadata correction spec error semantics
a8dbb14 docs: add metadata correction page spec
```

- [ ] **Step 3: 工作树确认干净**

```bash
git status --short
```

Expected: 只剩 `.claude/`、`.codex/`、`pre.txt`、`初稿.md` 这些既有未跟踪文件。
