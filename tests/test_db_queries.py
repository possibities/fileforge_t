"""阶段 1C queries.py 的 SQLite 回归测试。"""

from __future__ import annotations

import dataclasses
import unittest
from datetime import datetime, timezone


try:
    from sqlalchemy import create_engine, func
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import queries, repositories
    from infrastructure.db.repositories import FieldRevision
    from infrastructure.db.models import (
        ArchivePage as ArchivePageModel,
        ArchiveRecord,
        AuditLog,
        Base,
        MetadataRevision,
        ProcessingBatch,
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
class TestDataclasses(unittest.TestCase):
    def test_list_result_is_generic_and_frozen(self):
        result = queries.ListResult(
            items=[],
            total=0,
            page=1,
            page_size=50,
            has_next=False,
        )
        self.assertEqual(result.total, 0)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.total = 1  # type: ignore[misc]

    def test_archive_filter_defaults_all_none(self):
        f = queries.ArchiveFilter()
        for field in dataclasses.fields(f):
            self.assertIsNone(getattr(f, field.name), msg=field.name)

    def test_archive_filter_field_count(self):
        # 与 spec §3.2 / 数据契约 §9 锁定 12 字段
        self.assertEqual(len(dataclasses.fields(queries.ArchiveFilter)), 12)

    def test_archive_summary_field_count(self):
        # 与 spec §3.6 锁定 27 字段
        self.assertEqual(len(dataclasses.fields(queries.ArchiveSummary)), 27)

    def test_archive_detail_field_count(self):
        # 与 spec §3.7 锁定 45 字段
        self.assertEqual(len(dataclasses.fields(queries.ArchiveDetail)), 45)

    def test_dataclasses_are_frozen(self):
        now = datetime.now(timezone.utc)
        page = queries.ArchivePage(
            id=1, page_no=1, image_path="a/b.png", image_name="b.png",
            file_hash=None, file_size=None, ocr_text=None,
            ocr_avg_confidence=None, ocr_low_conf_count=None, ocr_variant=None,
            created_at=now,
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            page.page_no = 2  # type: ignore[misc]


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestPaginate(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_validate_page_lt_1_raises(self):
        with self.assertRaises(ValueError) as ctx:
            queries._validate_pagination(0, 50)
        self.assertIn("page must be >= 1", str(ctx.exception))

    def test_validate_page_size_lt_1_raises(self):
        with self.assertRaises(ValueError) as ctx:
            queries._validate_pagination(1, 0)
        self.assertIn("page_size must be in [1, 200]", str(ctx.exception))

    def test_validate_page_size_gt_200_raises(self):
        with self.assertRaises(ValueError):
            queries._validate_pagination(1, 201)

    def test_validate_accepts_boundary_values(self):
        queries._validate_pagination(1, 1)
        queries._validate_pagination(1, 200)

    def test_build_list_result_has_next_true_when_total_exceeds_page(self):
        result = queries._build_list_result(
            items=["a", "b"], total=10, page=1, page_size=2
        )
        self.assertTrue(result.has_next)
        self.assertEqual(result.total, 10)
        self.assertEqual(result.page, 1)
        self.assertEqual(result.page_size, 2)

    def test_build_list_result_has_next_false_on_last_page(self):
        result = queries._build_list_result(
            items=["i9", "i10"], total=10, page=5, page_size=2
        )
        self.assertFalse(result.has_next)

    def test_build_list_result_empty_items(self):
        result = queries._build_list_result(items=[], total=0, page=1, page_size=50)
        self.assertEqual(result.items, [])
        self.assertEqual(result.total, 0)
        self.assertFalse(result.has_next)

    def test_build_list_result_page_beyond_end_returns_empty_no_next(self):
        result = queries._build_list_result(items=[], total=3, page=99, page_size=50)
        self.assertEqual(result.items, [])
        self.assertFalse(result.has_next)
        self.assertEqual(result.total, 3)


def _seed_query_fixtures(session) -> dict:
    """种入查询测试共享 fixture。返回常用 id 映射。

    布局:
      - 项目 proj_test
      - 批次 batch_a(completed,2 success / 2 failed,total_archives=6)
      - 批次 batch_b(running,无档案)
      - 6 个档案在 batch_a:
          [0] success / not_required / none / 2025 / ZHL / 30年 / archive_no=2025-ZHL-D30-0001
          [1] success / needs_review / none / 2025 / DQL / 永久 / archive_no=2025-DQL-Y-0001
                title="测试档案 needs_review"
                responsible_party="测试单位甲"
          [2] failed / not_required / none / 2024 / YWL / 10年 / error_code=LLM_PARSE_FAIL
          [3] error / not_required / none / 2023 / ZHL / 10年 / error_code=OCR_TIMEOUT
                traceback_text="Traceback ..."
          [4] running / not_required / none / 2025 / ZHL / 30年(无 archive_no)
          [5] pending / not_required / corrected / 2025 / ZHL / 30年
                title="人工修正过的档案"
      - 每个档案 2 个 pages
      - 档案 [5] 有 2 次 revision(共 3 行 metadata_revisions)
      - 1 条 audit_logs(target_type='archive', target_id=archives[5].id)
    """
    project = Project(project_key="proj_test", project_name="测试项目")
    session.add(project)
    session.flush()

    batch_a = ProcessingBatch(
        project_id=project.id,
        batch_key="batch_a",
        batch_name="批次 A",
        input_dir="/tmp/in_a",
        output_dir="/tmp/out_a",
        batch_status="completed",
        started_at=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc),
        total_archives=6,
        total_pages=12,
        success_count=2,
        fail_count=2,
        failure_breakdown={"LLM_PARSE_FAIL": 1, "OCR_TIMEOUT": 1},
        summary_schema_version="1.0.0",
        summary_schema_ref="config/batch_summary.schema.json",
        summary_changelog_ref="config/batch_summary.schema.changelog.md",
    )
    batch_b = ProcessingBatch(
        project_id=project.id,
        batch_key="batch_b",
        batch_name="批次 B",
        input_dir="/tmp/in_b",
        output_dir="/tmp/out_b",
        batch_status="running",
        started_at=datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc),
        total_archives=0,
        total_pages=0,
    )
    session.add_all([batch_a, batch_b])
    session.flush()

    archive_specs = [
        dict(
            archive_key="ar0", archive_name="档案_0",
            processing_status="success", review_status="not_required",
            correction_status="none",
            archive_year="2025", classification_code="ZHL",
            classification_name="综合类", retention_period="30年",
            retention_period_code="D30",
            archive_no="2025-ZHL-D30-0001", item_no="0001",
            title="正常档案 0", responsible_party="测试单位甲",
            error_code=None, error_message=None, traceback_text=None,
            final_metadata={"题名": "正常档案 0", "归档年度": "2025"},
            rules_metadata={"题名": "正常档案 0", "归档年度": "2025"},
            llm_metadata={"题名": "正常档案 0", "归档年度": "2025"},
        ),
        dict(
            archive_key="ar1", archive_name="档案_1",
            processing_status="success", review_status="needs_review",
            correction_status="none",
            archive_year="2025", classification_code="DQL",
            classification_name="党群类", retention_period="永久",
            retention_period_code="Y",
            archive_no="2025-DQL-Y-0001", item_no="0001",
            title="测试档案 needs_review",
            responsible_party="测试单位甲",
            error_code=None, error_message=None, traceback_text=None,
            final_metadata={"题名": "测试档案 needs_review", "备注": "【待核查】简报题名重写失败"},
            rules_metadata={"题名": "测试档案 needs_review"},
            llm_metadata={"题名": "原始 LLM 题名"},
        ),
        dict(
            archive_key="ar2", archive_name="档案_2",
            processing_status="failed", review_status="not_required",
            correction_status="none",
            archive_year="2024", classification_code="YWL",
            classification_name="业务类", retention_period="10年",
            retention_period_code="D10",
            archive_no=None, item_no=None,
            title=None, responsible_party=None,
            error_code="LLM_PARSE_FAIL",
            error_message="LLM JSON 解析失败",
            traceback_text=None,
            final_metadata=None, rules_metadata=None, llm_metadata=None,
        ),
        dict(
            archive_key="ar3", archive_name="档案_3",
            processing_status="error", review_status="not_required",
            correction_status="none",
            archive_year="2023", classification_code="ZHL",
            classification_name="综合类", retention_period="10年",
            retention_period_code="D10",
            archive_no=None, item_no=None,
            title=None, responsible_party=None,
            error_code="OCR_TIMEOUT",
            error_message="OCR 处理超时",
            traceback_text="Traceback (most recent call last):\n  ...",
            final_metadata=None, rules_metadata=None, llm_metadata=None,
        ),
        dict(
            archive_key="ar4", archive_name="档案_4",
            processing_status="running", review_status="not_required",
            correction_status="none",
            archive_year="2025", classification_code="ZHL",
            classification_name="综合类", retention_period="30年",
            retention_period_code="D30",
            archive_no=None, item_no=None,
            title=None, responsible_party=None,
            error_code=None, error_message=None, traceback_text=None,
            final_metadata=None, rules_metadata=None, llm_metadata=None,
        ),
        dict(
            archive_key="ar5", archive_name="档案_5",
            processing_status="pending", review_status="not_required",
            correction_status="corrected",
            archive_year="2025", classification_code="ZHL",
            classification_name="综合类", retention_period="30年",
            retention_period_code="D30",
            archive_no="2025-ZHL-D30-0002", item_no="0002",
            title="人工修正过的档案",
            responsible_party="测试单位乙",
            error_code=None, error_message=None, traceback_text=None,
            final_metadata={"题名": "人工修正过的档案", "备注": "已校对"},
            rules_metadata={"题名": "原始规则题名"},
            llm_metadata={"题名": "原始 LLM 题名"},
        ),
    ]

    archives: list[ArchiveRecord] = []
    for spec in archive_specs:
        ar = ArchiveRecord(
            project_id=project.id,
            batch_id=batch_a.id,
            page_count=2,
            image_files=[f"{spec['archive_key']}/page_1.png", f"{spec['archive_key']}/page_2.png"],
            image_names=["page_1.png", "page_2.png"],
            **spec,
        )
        session.add(ar)
        archives.append(ar)
    session.flush()

    for ar in archives:
        for p in (1, 2):
            session.add(
                ArchivePageModel(
                    archive_id=ar.id,
                    page_no=p,
                    image_path=f"{ar.archive_key}/page_{p}.png",
                    image_name=f"page_{p}.png",
                    file_hash=f"hash-{ar.archive_key}-{p}",
                    file_size=1024,
                )
            )
    session.flush()

    # archive[5] 两次 revision:第一次改 2 个字段(共享 revision_no=1),第二次改 1 个字段(revision_no=2)
    repositories.record_revisions(
        session,
        archive_id=archives[5].id,
        revisions=[
            FieldRevision(field_key="题名", field_column="title",
                          old_value="原始规则题名", new_value="人工修正过的档案"),
            FieldRevision(field_key="责任者", field_column="responsible_party",
                          old_value="原始责任者", new_value="测试单位乙"),
        ],
        actor_user_id=None,
        reason="manual_correction_v1",
    )
    repositories.record_revisions(
        session,
        archive_id=archives[5].id,
        revisions=[
            FieldRevision(field_key="备注", field_column=None,
                          old_value=None, new_value="已校对"),
        ],
        actor_user_id=None,
        reason="manual_correction_v2",
    )
    repositories.record_audit_log(
        session,
        actor_user_id=None,
        action="force_rerun_rules",
        target_type="archive",
        target_id=archives[5].id,
        before_data={"题名": "旧"}, after_data={"题名": "新"},
    )
    session.flush()

    return {
        "project_id": project.id,
        "batch_a_id": batch_a.id,
        "batch_b_id": batch_b.id,
        "archive_ids": [ar.id for ar in archives],
    }


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestSeedFixture(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            self.ids = _seed_query_fixtures(session)
            session.commit()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_seed_creates_one_project(self):
        from sqlalchemy import select as sa_select
        with self.Session() as session:
            count = session.scalar(sa_select(func.count()).select_from(Project))
            self.assertEqual(count, 1)

    def test_seed_creates_two_batches(self):
        from sqlalchemy import select as sa_select
        with self.Session() as session:
            count = session.scalar(sa_select(func.count()).select_from(ProcessingBatch))
            self.assertEqual(count, 2)

    def test_seed_creates_six_archives_in_batch_a(self):
        from sqlalchemy import select as sa_select
        with self.Session() as session:
            count = session.scalar(
                sa_select(func.count())
                .select_from(ArchiveRecord)
                .where(ArchiveRecord.batch_id == self.ids["batch_a_id"])
            )
            self.assertEqual(count, 6)

    def test_seed_creates_twelve_pages(self):
        from sqlalchemy import select as sa_select
        with self.Session() as session:
            count = session.scalar(sa_select(func.count()).select_from(ArchivePageModel))
            self.assertEqual(count, 12)

    def test_seed_creates_three_revisions(self):
        from sqlalchemy import select as sa_select
        with self.Session() as session:
            count = session.scalar(sa_select(func.count()).select_from(MetadataRevision))
            self.assertEqual(count, 3)

    def test_seed_creates_one_audit_log(self):
        from sqlalchemy import select as sa_select
        with self.Session() as session:
            count = session.scalar(sa_select(func.count()).select_from(AuditLog))
            self.assertEqual(count, 1)


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestListBatches(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            self.ids = _seed_query_fixtures(session)
            session.commit()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_unknown_project_returns_empty(self):
        with self.Session() as session:
            result = queries.list_batches(session, project_key="not_exist")
        self.assertEqual(result.items, [])
        self.assertEqual(result.total, 0)
        self.assertFalse(result.has_next)

    def test_returns_two_batches_sorted_by_started_at_desc(self):
        with self.Session() as session:
            result = queries.list_batches(session, project_key="proj_test")
        self.assertEqual(result.total, 2)
        # batch_b started 2026-05-04, batch_a started 2026-05-01,b 应排在前
        self.assertEqual(result.items[0].batch_key, "batch_b")
        self.assertEqual(result.items[1].batch_key, "batch_a")
        self.assertFalse(result.has_next)

    def test_status_filter_completed_returns_only_batch_a(self):
        with self.Session() as session:
            result = queries.list_batches(
                session, project_key="proj_test", status_filter=["completed"]
            )
        self.assertEqual(result.total, 1)
        self.assertEqual(result.items[0].batch_key, "batch_a")
        self.assertEqual(result.items[0].batch_status, "completed")

    def test_status_filter_no_match_returns_empty(self):
        with self.Session() as session:
            result = queries.list_batches(
                session, project_key="proj_test", status_filter=["aborted"]
            )
        self.assertEqual(result.items, [])
        self.assertEqual(result.total, 0)

    def test_status_filter_empty_iter_treated_as_no_filter(self):
        with self.Session() as session:
            result = queries.list_batches(
                session, project_key="proj_test", status_filter=[]
            )
        self.assertEqual(result.total, 2)

    def test_pagination_page_size_1(self):
        with self.Session() as session:
            page1 = queries.list_batches(
                session, project_key="proj_test", page=1, page_size=1
            )
            page2 = queries.list_batches(
                session, project_key="proj_test", page=2, page_size=1
            )
            page3 = queries.list_batches(
                session, project_key="proj_test", page=3, page_size=1
            )
        self.assertEqual(page1.items[0].batch_key, "batch_b")
        self.assertTrue(page1.has_next)
        self.assertEqual(page2.items[0].batch_key, "batch_a")
        self.assertFalse(page2.has_next)
        self.assertEqual(page3.items, [])
        self.assertFalse(page3.has_next)
        self.assertEqual(page3.total, 2)

    def test_summary_field_does_not_include_failure_breakdown(self):
        with self.Session() as session:
            result = queries.list_batches(session, project_key="proj_test")
        # BatchSummary 不应有 failure_breakdown
        with self.assertRaises(AttributeError):
            _ = result.items[0].failure_breakdown  # type: ignore[attr-defined]

    def test_invalid_page_raises(self):
        with self.Session() as session, self.assertRaises(ValueError):
            queries.list_batches(session, project_key="proj_test", page=0)

    def test_invalid_page_size_raises(self):
        with self.Session() as session, self.assertRaises(ValueError):
            queries.list_batches(session, project_key="proj_test", page_size=201)


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestGetBatchDetail(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            self.ids = _seed_query_fixtures(session)
            session.commit()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_returns_none_when_batch_not_found(self):
        with self.Session() as session:
            result = queries.get_batch_detail(session, batch_id=99999)
        self.assertIsNone(result)

    def test_returns_batch_a_with_full_fields(self):
        with self.Session() as session:
            result = queries.get_batch_detail(session, batch_id=self.ids["batch_a_id"])
        self.assertIsNotNone(result)
        assert result is not None  # for type narrowing
        self.assertEqual(result.batch_key, "batch_a")
        self.assertEqual(result.batch_status, "completed")
        self.assertEqual(result.total_archives, 6)
        self.assertEqual(result.success_count, 2)
        self.assertEqual(result.fail_count, 2)
        self.assertEqual(
            result.failure_breakdown,
            {"LLM_PARSE_FAIL": 1, "OCR_TIMEOUT": 1},
        )
        self.assertEqual(result.summary_schema_version, "1.0.0")
        self.assertEqual(result.summary_schema_ref, "config/batch_summary.schema.json")

    def test_running_batch_has_empty_failure_breakdown(self):
        with self.Session() as session:
            result = queries.get_batch_detail(session, batch_id=self.ids["batch_b_id"])
        assert result is not None
        self.assertEqual(result.batch_status, "running")
        self.assertEqual(result.failure_breakdown, {})


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestListArchives(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            self.ids = _seed_query_fixtures(session)
            session.commit()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    # ── 基础与排序 ──
    def test_unknown_batch_returns_empty(self):
        with self.Session() as session:
            result = queries.list_archives(session, batch_id=99999)
        self.assertEqual(result.items, [])
        self.assertEqual(result.total, 0)

    def test_returns_six_archives_for_batch_a(self):
        with self.Session() as session:
            result = queries.list_archives(session, batch_id=self.ids["batch_a_id"])
        self.assertEqual(result.total, 6)
        self.assertEqual(len(result.items), 6)

    def test_sorted_by_archive_no_asc_nulls_last(self):
        with self.Session() as session:
            result = queries.list_archives(session, batch_id=self.ids["batch_a_id"])
        # 有 archive_no 的(2025-DQL-Y-0001, 2025-ZHL-D30-0001, 2025-ZHL-D30-0002) 在前,
        # 没有 archive_no 的(ar2/ar3/ar4) 排后面
        archive_nos = [a.archive_no for a in result.items]
        non_null_count = sum(1 for n in archive_nos if n is not None)
        self.assertEqual(non_null_count, 3)
        self.assertEqual(archive_nos[:3], [
            "2025-DQL-Y-0001", "2025-ZHL-D30-0001", "2025-ZHL-D30-0002",
        ])

    # ── filter 各字段 ──
    def test_filter_archive_year_int(self):
        with self.Session() as session:
            result = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
                filter=queries.ArchiveFilter(archive_year=2025),
            )
        self.assertEqual(result.total, 4)
        for a in result.items:
            self.assertEqual(a.archive_year, "2025")

    def test_filter_classification_code_in(self):
        with self.Session() as session:
            result = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
                filter=queries.ArchiveFilter(classification_code=["DQL", "YWL"]),
            )
        self.assertEqual(result.total, 2)
        codes = sorted(a.classification_code for a in result.items)
        self.assertEqual(codes, ["DQL", "YWL"])

    def test_filter_retention_period_in(self):
        with self.Session() as session:
            result = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
                filter=queries.ArchiveFilter(retention_period=["10年"]),
            )
        self.assertEqual(result.total, 2)

    def test_filter_processing_status_success(self):
        with self.Session() as session:
            result = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
                filter=queries.ArchiveFilter(processing_status=["success"]),
            )
        self.assertEqual(result.total, 2)

    def test_filter_review_status_needs_review(self):
        with self.Session() as session:
            result = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
                filter=queries.ArchiveFilter(review_status=["needs_review"]),
            )
        self.assertEqual(result.total, 1)
        self.assertEqual(result.items[0].archive_key, "ar1")

    def test_filter_correction_status_corrected(self):
        with self.Session() as session:
            result = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
                filter=queries.ArchiveFilter(correction_status="corrected"),
            )
        self.assertEqual(result.total, 1)
        self.assertEqual(result.items[0].archive_key, "ar5")

    def test_filter_archive_no_exact(self):
        with self.Session() as session:
            result = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
                filter=queries.ArchiveFilter(archive_no="2025-DQL-Y-0001"),
            )
        self.assertEqual(result.total, 1)

    def test_filter_title_like(self):
        with self.Session() as session:
            result = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
                filter=queries.ArchiveFilter(title_like="needs_review"),
            )
        self.assertEqual(result.total, 1)
        self.assertEqual(result.items[0].archive_key, "ar1")

    def test_filter_responsible_party_like(self):
        with self.Session() as session:
            result = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
                filter=queries.ArchiveFilter(responsible_party_like="测试单位甲"),
            )
        self.assertEqual(result.total, 2)

    def test_filter_error_code_in(self):
        with self.Session() as session:
            result = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
                filter=queries.ArchiveFilter(error_code=["LLM_PARSE_FAIL"]),
            )
        self.assertEqual(result.total, 1)
        self.assertEqual(result.items[0].archive_key, "ar2")

    def test_filter_openness_status_passes(self):
        # seed 没有 openness_status 数据,验证 filter 不会误命中
        with self.Session() as session:
            result = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
                filter=queries.ArchiveFilter(openness_status="开放"),
            )
        self.assertEqual(result.total, 0)

    def test_filter_item_no_exact(self):
        with self.Session() as session:
            result = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
                filter=queries.ArchiveFilter(item_no="0001"),
            )
        # 两个档案都是 0001(不同 archive_year/classification 组合下序号独立)
        self.assertEqual(result.total, 2)

    # ── filter 等价规则 ──
    def test_filter_empty_iter_equiv_to_none(self):
        with self.Session() as session:
            result_with_empty = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
                filter=queries.ArchiveFilter(classification_code=[]),
            )
            result_without = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
            )
        self.assertEqual(result_with_empty.total, result_without.total)

    def test_filter_empty_string_like_equiv_to_none(self):
        with self.Session() as session:
            result_with_empty = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
                filter=queries.ArchiveFilter(title_like=""),
            )
            result_without = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
            )
        self.assertEqual(result_with_empty.total, result_without.total)

    # ── 多字段组合 ──
    def test_multiple_filters_combine_with_and(self):
        with self.Session() as session:
            result = queries.list_archives(
                session,
                batch_id=self.ids["batch_a_id"],
                filter=queries.ArchiveFilter(
                    archive_year=2025,
                    classification_code=["ZHL"],
                    processing_status=["success"],
                ),
            )
        self.assertEqual(result.total, 1)
        self.assertEqual(result.items[0].archive_key, "ar0")

    # ── 分页 ──
    def test_pagination_page_size_2(self):
        with self.Session() as session:
            page1 = queries.list_archives(
                session, batch_id=self.ids["batch_a_id"], page=1, page_size=2
            )
            page2 = queries.list_archives(
                session, batch_id=self.ids["batch_a_id"], page=2, page_size=2
            )
            page4 = queries.list_archives(
                session, batch_id=self.ids["batch_a_id"], page=4, page_size=2
            )
        self.assertEqual(len(page1.items), 2)
        self.assertTrue(page1.has_next)
        self.assertEqual(len(page2.items), 2)
        self.assertTrue(page2.has_next)
        self.assertEqual(len(page4.items), 0)
        self.assertFalse(page4.has_next)
        self.assertEqual(page4.total, 6)

    # ── ArchiveSummary 字段验证 ──
    def test_summary_excludes_three_snapshots(self):
        with self.Session() as session:
            result = queries.list_archives(session, batch_id=self.ids["batch_a_id"])
        summary = result.items[0]
        with self.assertRaises(AttributeError):
            _ = summary.final_metadata  # type: ignore[attr-defined]
        with self.assertRaises(AttributeError):
            _ = summary.llm_metadata  # type: ignore[attr-defined]
        with self.assertRaises(AttributeError):
            _ = summary.pages  # type: ignore[attr-defined]


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestGetArchiveDetail(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            self.ids = _seed_query_fixtures(session)
            session.commit()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_returns_none_when_archive_not_found(self):
        with self.Session() as session:
            result = queries.get_archive_detail(session, archive_id=99999)
        self.assertIsNone(result)

    def test_returns_full_archive_detail_with_three_snapshots(self):
        archive_id = self.ids["archive_ids"][1]  # ar1 with metadata
        with self.Session() as session:
            result = queries.get_archive_detail(session, archive_id=archive_id)
        assert result is not None
        self.assertEqual(result.archive_key, "ar1")
        self.assertEqual(result.processing_status, "success")
        self.assertEqual(result.review_status, "needs_review")
        # 三快照都暴露
        self.assertEqual(result.final_metadata, {
            "题名": "测试档案 needs_review",
            "备注": "【待核查】简报题名重写失败",
        })
        self.assertEqual(result.rules_metadata, {"题名": "测试档案 needs_review"})
        self.assertEqual(result.llm_metadata, {"题名": "原始 LLM 题名"})

    def test_returns_pages_sorted_by_page_no_asc(self):
        archive_id = self.ids["archive_ids"][0]
        with self.Session() as session:
            result = queries.get_archive_detail(session, archive_id=archive_id)
        assert result is not None
        self.assertEqual(len(result.pages), 2)
        self.assertEqual(result.pages[0].page_no, 1)
        self.assertEqual(result.pages[1].page_no, 2)
        self.assertEqual(result.pages[0].image_path, "ar0/page_1.png")

    def test_failed_archive_has_null_metadata_and_error_info(self):
        archive_id = self.ids["archive_ids"][2]  # ar2 failed
        with self.Session() as session:
            result = queries.get_archive_detail(session, archive_id=archive_id)
        assert result is not None
        self.assertEqual(result.processing_status, "failed")
        self.assertIsNone(result.final_metadata)
        self.assertEqual(result.error_code, "LLM_PARSE_FAIL")
        self.assertIsNone(result.traceback_text)

    def test_error_archive_has_traceback(self):
        archive_id = self.ids["archive_ids"][3]
        with self.Session() as session:
            result = queries.get_archive_detail(session, archive_id=archive_id)
        assert result is not None
        self.assertEqual(result.processing_status, "error")
        self.assertEqual(result.error_code, "OCR_TIMEOUT")
        self.assertIsNotNone(result.traceback_text)

    def test_corrected_archive_has_correction_status(self):
        archive_id = self.ids["archive_ids"][5]
        with self.Session() as session:
            result = queries.get_archive_detail(session, archive_id=archive_id)
        assert result is not None
        self.assertEqual(result.correction_status, "corrected")

    def test_detail_field_count_is_45(self):
        archive_id = self.ids["archive_ids"][0]
        with self.Session() as session:
            result = queries.get_archive_detail(session, archive_id=archive_id)
        assert result is not None
        self.assertEqual(len(dataclasses.fields(result)), 45)


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestListRevisions(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            self.ids = _seed_query_fixtures(session)
            session.commit()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_returns_empty_when_archive_has_no_revisions(self):
        with self.Session() as session:
            result = queries.list_revisions(
                session, archive_id=self.ids["archive_ids"][0]
            )
        self.assertEqual(result.items, [])
        self.assertEqual(result.total, 0)

    def test_returns_three_revisions_for_corrected_archive(self):
        archive_id = self.ids["archive_ids"][5]
        with self.Session() as session:
            result = queries.list_revisions(session, archive_id=archive_id)
        self.assertEqual(result.total, 3)

    def test_revisions_sorted_revision_no_desc(self):
        archive_id = self.ids["archive_ids"][5]
        with self.Session() as session:
            result = queries.list_revisions(session, archive_id=archive_id)
        revision_nos = [r.revision_no for r in result.items]
        # revision_no=2 在前(1 行),revision_no=1 在后(2 行)
        self.assertEqual(revision_nos[0], 2)
        self.assertEqual(revision_nos[1], 1)
        self.assertEqual(revision_nos[2], 1)

    def test_revision_carries_field_key_and_old_new_values(self):
        archive_id = self.ids["archive_ids"][5]
        with self.Session() as session:
            result = queries.list_revisions(session, archive_id=archive_id)
        # 找 revision_no=1 题名 那条
        rev = next(
            (r for r in result.items if r.revision_no == 1 and r.field_key == "题名"),
            None,
        )
        self.assertIsNotNone(rev)
        assert rev is not None
        self.assertEqual(rev.field_column, "title")
        self.assertEqual(rev.old_value, "原始规则题名")
        self.assertEqual(rev.new_value, "人工修正过的档案")
        self.assertEqual(rev.reason, "manual_correction_v1")

    def test_pagination(self):
        archive_id = self.ids["archive_ids"][5]
        with self.Session() as session:
            page1 = queries.list_revisions(
                session, archive_id=archive_id, page=1, page_size=2
            )
            page2 = queries.list_revisions(
                session, archive_id=archive_id, page=2, page_size=2
            )
        self.assertEqual(len(page1.items), 2)
        self.assertTrue(page1.has_next)
        self.assertEqual(len(page2.items), 1)
        self.assertFalse(page2.has_next)
        self.assertEqual(page2.total, 3)


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestListAuditLogs(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            self.ids = _seed_query_fixtures(session)
            session.commit()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_unknown_target_type_raises(self):
        with self.Session() as session, self.assertRaises(ValueError) as ctx:
            queries.list_audit_logs(session, target_type="user", target_id=1)
        self.assertIn("unknown target_type", str(ctx.exception))

    def test_returns_empty_when_target_id_not_found(self):
        with self.Session() as session:
            result = queries.list_audit_logs(
                session, target_type="archive", target_id=99999
            )
        self.assertEqual(result.items, [])
        self.assertEqual(result.total, 0)

    def test_returns_one_audit_log_for_archive_5(self):
        archive_id = self.ids["archive_ids"][5]
        with self.Session() as session:
            result = queries.list_audit_logs(
                session, target_type="archive", target_id=archive_id
            )
        self.assertEqual(result.total, 1)
        self.assertEqual(result.items[0].action, "force_rerun_rules")
        self.assertEqual(result.items[0].target_type, "archive")
        self.assertEqual(result.items[0].target_id, archive_id)
        self.assertEqual(result.items[0].before_data, {"题名": "旧"})
        self.assertEqual(result.items[0].after_data, {"题名": "新"})

    def test_invalid_page_raises(self):
        with self.Session() as session, self.assertRaises(ValueError):
            queries.list_audit_logs(
                session, target_type="archive", target_id=1, page=0
            )
