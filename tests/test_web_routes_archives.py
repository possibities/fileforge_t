"""Web admin archive query route tests (Phase 2 Task 9)."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest


try:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import accounts
    from infrastructure.db.models import (
        ArchivePage,
        ArchiveRecord,
        Base,
        Organization,
        ProcessingBatch,
        Project,
    )
    from web_admin.app import create_app
except ImportError as _exc:  # pragma: no cover
    DEPENDENCIES_AVAILABLE = False
    _IMPORT_ERROR: Exception | None = _exc
else:
    DEPENDENCIES_AVAILABLE = True
    _IMPORT_ERROR = None


ADMIN_USERNAME = "padmin"
ADMIN_PASSWORD = "platform-strong-pw"
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
class TestArchiveQueryRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp_images = tempfile.TemporaryDirectory()
        self.image_root = Path(self.tmp_images.name)
        image_dir = self.image_root / "arc_spring"
        image_dir.mkdir(parents=True, exist_ok=True)
        self.page_bytes = b"fake-jpeg-bytes"
        (image_dir / "page_001.jpg").write_bytes(self.page_bytes)

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
                input_dir=str(self.image_root),
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
            self.batch_a_id = batch_a.id

            archive_spring = ArchiveRecord(
                project_id=project_a.id,
                batch_id=batch_a.id,
                archive_key="arc_spring",
                archive_name="春风行动档案",
                title="春风行动简报",
                responsible_party="县档案室",
                archive_year="2021",
                classification_code="DQL",
                retention_period="永久",
                processing_status="success",
                openness_status="开放",
                llm_parse_strategy="repaired",
                final_metadata={"归档说明": "整编完成-FM标记"},
            )
            archive_daily = ArchiveRecord(
                project_id=project_a.id,
                batch_id=batch_a.id,
                archive_key="arc_daily",
                archive_name="日常公文档案",
                title="日常公文汇编",
                responsible_party="办公室",
                archive_year="2020",
                classification_code="ZHL",
                retention_period="30年",
                processing_status="failed",
            )
            archive_other_org = ArchiveRecord(
                project_id=project_b.id,
                batch_id=batch_b.id,
                archive_key="arc_b",
                archive_name="乙单位档案",
                title="乙单位文件",
                organization_id=org_b.id,
            )
            session.add_all([archive_spring, archive_daily, archive_other_org])
            session.flush()
            self.archive_spring_id = archive_spring.id
            self.archive_other_org_id = archive_other_org.id

            page = ArchivePage(
                archive_id=archive_spring.id,
                page_no=1,
                image_path="arc_spring/page_001.jpg",
                image_name="page_001.jpg",
            )
            session.add(page)
            session.flush()
            self.archive_spring_page_id = page.id
            session.commit()

        self.app = create_app(database_url="sqlite://")
        self.app.state.session_factory = self.Session

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        self.tmp_images.cleanup()

    def _login(self, client, username: str, password: str) -> None:
        resp = client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, {302, 303})

    def test_archive_list_filters_by_title_like(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                f"/batches/{self.batch_a_id}/archives",
                params={"title_like": "春风"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("春风行动简报", resp.text)
        self.assertNotIn("日常公文汇编", resp.text)

    def test_archive_list_filters_by_year(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                f"/batches/{self.batch_a_id}/archives",
                params={"archive_year": 2020},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("日常公文汇编", resp.text)
        self.assertNotIn("春风行动简报", resp.text)

    def test_archive_list_ignores_blank_query_fields(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                f"/batches/{self.batch_a_id}/archives",
                params={
                    "archive_year": "",
                    "classification_code": "",
                    "retention_period": "",
                    "processing_status": "",
                    "openness_status": "",
                    "review_status": "",
                    "correction_status": "",
                    "title_like": "",
                    "responsible_party_like": "",
                    "page": "",
                    "page_size": "",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("春风行动简报", resp.text)
        self.assertIn("日常公文汇编", resp.text)

    def test_archive_list_invalid_year_returns_400_not_422(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                f"/batches/{self.batch_a_id}/archives",
                params={"archive_year": "not-a-year"},
            )
        self.assertEqual(resp.status_code, 400)

    def test_archive_list_filters_by_classification_retention_and_status(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                f"/batches/{self.batch_a_id}/archives",
                params={
                    "classification_code": "DQL",
                    "retention_period": "永久",
                    "processing_status": "success",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("春风行动简报", resp.text)
        self.assertNotIn("日常公文汇编", resp.text)

    def test_archive_list_filters_by_responsible_party_like(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                f"/batches/{self.batch_a_id}/archives",
                params={"responsible_party_like": "办公室"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("日常公文汇编", resp.text)
        self.assertNotIn("春风行动简报", resp.text)

    def test_archive_detail_shows_metadata_strategy_and_pages(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(f"/archives/{self.archive_spring_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("整编完成-FM标记", body)
        self.assertIn("repaired", body)
        self.assertIn("page_001.jpg", body)
        self.assertIn(
            f"/archives/{self.archive_spring_id}/pages/{self.archive_spring_page_id}/image",
            body,
        )
        self.assertIn('class="page-thumb"', body)

    def test_archive_page_image_route_serves_page_file(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                f"/archives/{self.archive_spring_id}/pages/{self.archive_spring_page_id}/image"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, self.page_bytes)
        self.assertTrue(resp.headers["content-type"].startswith("image/jpeg"))

    def test_archive_not_found_returns_404(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/archives/999999", follow_redirects=False)
        self.assertEqual(resp.status_code, 404)

    def test_org_scope_violation_returns_404(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = client.get(
                f"/archives/{self.archive_other_org_id}",
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 404)

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

    # ── 全局档案查询(跨批次)──────────────────────────────────────────────
    def test_global_search_lists_across_batches_for_platform_admin(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/archives")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("春风行动简报", resp.text)
        self.assertIn("日常公文汇编", resp.text)
        self.assertIn("乙单位文件", resp.text)

    def test_global_search_fetch_header_returns_grid_fragment(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/archives", headers={"X-Requested-With": "fetch"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("data-grid-table", resp.text)
        # 片段不含整页骨架。
        self.assertNotIn('class="topbar"', resp.text)

    def test_global_search_org_scope_hides_other_org(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = client.get("/archives")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("春风行动简报", resp.text)
        self.assertIn("日常公文汇编", resp.text)
        self.assertNotIn("乙单位文件", resp.text)

    def test_global_search_filters_by_title_like(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/archives", params={"title_like": "春风"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("春风行动简报", resp.text)
        self.assertNotIn("日常公文汇编", resp.text)

    def test_global_search_filters_by_project_key(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/archives", params={"project_key": "proj_a"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("春风行动简报", resp.text)
        self.assertNotIn("乙单位文件", resp.text)

    def test_global_search_sort_by_year_desc_orders_rows(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                "/archives", params={"sort": "archive_year", "dir": "desc"}
            )
        self.assertEqual(resp.status_code, 200)
        # 2021(春风)应排在 2020(日常)之前。
        self.assertLess(
            resp.text.index("春风行动简报"), resp.text.index("日常公文汇编")
        )

    def test_global_search_unknown_sort_field_falls_back(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                "/archives", params={"sort": "evil); DROP TABLE archive_records;--"}
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("春风行动简报", resp.text)

    def test_global_search_page_size_over_cap_returns_400(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/archives", params={"page_size": "999"})
        self.assertEqual(resp.status_code, 400)

    def test_global_search_selected_renders_panel_inline(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                "/archives", params={"selected": self.archive_spring_id}
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("整编完成-FM标记", resp.text)
        self.assertIn(
            f"/archives/{self.archive_spring_id}/pages/{self.archive_spring_page_id}/image",
            resp.text,
        )

    def test_global_search_selected_other_org_ignored(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = client.get(
                "/archives", params={"selected": self.archive_other_org_id}
            )
        self.assertEqual(resp.status_code, 200)
        # 越权选中被忽略:不渲染他单位档案的详情片段。
        self.assertNotIn("乙单位文件", resp.text)

    # ── 详情片段(主从右栏)────────────────────────────────────────────────
    def test_archive_panel_fragment_renders_without_chrome(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(f"/archives/{self.archive_spring_id}/panel")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("整编完成-FM标记", resp.text)
        self.assertIn("page_001.jpg", resp.text)
        self.assertIn('class="page-thumb"', resp.text)
        # 片段不应包含整页骨架(顶栏)。
        self.assertNotIn('class="topbar"', resp.text)

    def test_archive_panel_fragment_org_scope_returns_404(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = client.get(
                f"/archives/{self.archive_other_org_id}/panel",
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 404)


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, f"web deps missing: {_IMPORT_ERROR}")
class TestArchiveEditRoute(unittest.TestCase):
    def setUp(self):
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
                correction_status="none",
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
        old_operator_role = accounts.BUILTIN_ROLES["org_operator"]
        with self.Session() as session:
            accounts.create_user(
                session,
                username="readonly_user",
                password="readonly-strong-pw",
                display_name="只读用户",
                role_codes=["org_operator"],
            )
            session.commit()
        try:
            accounts.BUILTIN_ROLES["org_operator"] = (
                old_operator_role[0],
                tuple(
                    permission
                    for permission in old_operator_role[1]
                    if permission != "archive:correct"
                ),
            )
            with TestClient(self.app) as client:
                self._login(client, "readonly_user", "readonly-strong-pw")
                resp = client.get(
                    f"/archives/{self.archive_a_id}/edit",
                    follow_redirects=False,
                )
            self.assertEqual(resp.status_code, 403)
        finally:
            accounts.BUILTIN_ROLES["org_operator"] = old_operator_role

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
        from infrastructure.db.models import MetadataRevision
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
        from infrastructure.db.models import AuditLog, MetadataRevision
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
        with self.Session() as session:
            self.assertEqual(session.query(MetadataRevision).count(), 1)
            self.assertEqual(session.query(AuditLog).count(), 1)

    def test_post_edit_no_change_redirects_with_notice(self):
        from infrastructure.db.models import MetadataRevision
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


if __name__ == "__main__":
    unittest.main()
