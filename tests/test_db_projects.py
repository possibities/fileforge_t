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

    def test_create_project_generates_key_when_blank(self):
        with self.Session() as session:
            project = projects.create_project(
                session,
                project_key=" ",
                organization_id=self.org_a_id,
                project_name="自动标识项目",
            )
            session.commit()
            self.assertRegex(project.project_key, r"^prj_\d{8}_[0-9a-f]{8}$")

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
