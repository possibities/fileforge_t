"""阶段 1A repositories 模块的回归测试。

覆盖三条容易被遗漏的逻辑：
  - apply_classification_result 对 correction_status='corrected' 档案的保护
  - apply_classification_result 在 force_rerun_rules=True 时的强制覆盖
  - upsert_archive 重跑时的状态重置（且 corrected 档案不被清状态）
"""

from __future__ import annotations

import unittest


try:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import repositories
    from infrastructure.db.models import (
        ArchiveRecord,
        Base,
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
class TestApplyClassificationResult(unittest.TestCase):
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

    def _make_archive(self, **overrides) -> int:
        with self.Session() as session:
            archive = ArchiveRecord(
                project_id=self.project_id,
                batch_id=self.batch_id,
                archive_key=overrides.pop("archive_key", "demo"),
                archive_name=overrides.pop("archive_name", "demo"),
                **overrides,
            )
            session.add(archive)
            session.commit()
            return archive.id

    def _payload(self, title: str = "新题名", year: str = "2026"):
        return {
            "题名": title,
            "归档年度": year,
            "实体分类号": "DQL",
            "实体分类名称": "党群类",
            "保管期限": "30年",
            "件号": "0007",
            "档号": f"{year}-DQL-D30-0007",
        }

    def test_corrected_archive_is_protected_by_default(self):
        archive_id = self._make_archive(
            correction_status="corrected",
            title="人工修正后的题名",
            final_metadata={"题名": "人工修正后的题名"},
        )

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            repositories.apply_classification_result(
                session,
                archive=archive,
                final_metadata=self._payload(),
                rules_metadata=self._payload(),
                llm_metadata={"raw": "llm-output"},
            )
            session.commit()

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            # final_metadata 与冗余列保留
            self.assertEqual(archive.final_metadata, {"题名": "人工修正后的题名"})
            self.assertEqual(archive.title, "人工修正后的题名")
            # 但 llm/rules 快照刷新了
            self.assertEqual(archive.llm_metadata, {"raw": "llm-output"})
            self.assertEqual(archive.rules_metadata, self._payload())

    def test_force_rerun_rules_overrides_protection(self):
        archive_id = self._make_archive(
            correction_status="corrected",
            title="人工修正后的题名",
            final_metadata={"题名": "人工修正后的题名"},
        )

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            repositories.apply_classification_result(
                session,
                archive=archive,
                final_metadata=self._payload(title="规则重排后的题名"),
                rules_metadata=self._payload(title="规则重排后的题名"),
                force_rerun_rules=True,
            )
            session.commit()

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            self.assertEqual(archive.title, "规则重排后的题名")
            self.assertEqual(archive.final_metadata["题名"], "规则重排后的题名")
            self.assertEqual(archive.archive_no, "2026-DQL-D30-0007")
            self.assertEqual(archive.item_no, "0007")

    def test_first_time_apply_fills_redundant_columns(self):
        archive_id = self._make_archive()

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            repositories.apply_classification_result(
                session,
                archive=archive,
                final_metadata=self._payload(),
            )
            session.commit()

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            self.assertEqual(archive.title, "新题名")
            self.assertEqual(archive.archive_year, "2026")
            self.assertEqual(archive.classification_code, "DQL")
            self.assertEqual(archive.retention_period, "30年")
            self.assertEqual(archive.retention_period_code, "D30")

    def test_retention_code_resolves_old_year(self):
        archive_id = self._make_archive()

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            repositories.apply_classification_result(
                session,
                archive=archive,
                final_metadata={
                    "归档年度": "2005",
                    "保管期限": "长期",
                    "实体分类号": "002",
                    "实体分类名称": "综合类",
                    "题名": "旧档案",
                },
            )
            session.commit()

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            self.assertEqual(archive.retention_period_code, "C")

    def test_retention_code_resolves_old_year_new_vocabulary(self):
        # [R1] 归档年度<2007 但期限为 30年/10年(分类方案口径)时,
        # retention_period_code 必须映射为 D30/D10,不得回落成 None。
        archive_id = self._make_archive()

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            repositories.apply_classification_result(
                session,
                archive=archive,
                final_metadata={
                    "归档年度": "2005",
                    "保管期限": "30年",
                    "实体分类号": "002",
                    "实体分类名称": "综合类",
                    "题名": "旧档案新口径",
                },
            )
            session.commit()

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            self.assertEqual(archive.retention_period_code, "D30")


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestUpsertArchiveRerun(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            project = Project(project_key="p2")
            session.add(project)
            session.flush()
            batch = ProcessingBatch(project_id=project.id, batch_key="b2")
            session.add(batch)
            session.flush()
            self.project_id = project.id
            self.batch_id = batch.id
            session.commit()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _common_kwargs(self):
        return dict(
            project_id=self.project_id,
            batch_id=self.batch_id,
            archive_key="demo",
            archive_name="demo",
            source_folder="/in",
            page_count=1,
            image_files=["/in/0001.jpg"],
            image_names=["0001.jpg"],
            processed_time="2026-05-03T18:00:00",
        )

    def test_rerun_resets_status_for_non_corrected(self):
        with self.Session() as session:
            archive = repositories.upsert_archive(session, **self._common_kwargs())
            archive.processing_status = "failed"
            archive.error_code = "PROCESS_EXCEPTION"
            archive.error_message = "boom"
            archive.traceback_text = "trace"
            session.commit()
            first_id = archive.id

        with self.Session() as session:
            archive = repositories.upsert_archive(session, **self._common_kwargs())
            session.commit()

            self.assertEqual(archive.id, first_id)
            self.assertEqual(archive.processing_status, "running")
            self.assertIsNone(archive.error_code)
            self.assertIsNone(archive.error_message)
            self.assertIsNone(archive.traceback_text)

    def test_rerun_keeps_status_for_corrected(self):
        with self.Session() as session:
            archive = repositories.upsert_archive(session, **self._common_kwargs())
            archive.processing_status = "success"
            archive.correction_status = "corrected"
            archive.title = "人工修正题名"
            archive.error_code = None
            session.commit()

        with self.Session() as session:
            archive = repositories.upsert_archive(session, **self._common_kwargs())
            session.commit()

            self.assertEqual(archive.processing_status, "success")
            self.assertEqual(archive.correction_status, "corrected")
            self.assertEqual(archive.title, "人工修正题名")


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestToRelativePosix(unittest.TestCase):
    """验证 _to_relative_posix:image_path 归一化为相对 input_dir 的 POSIX 路径(数据契约 §4.5)。"""

    def test_under_input_dir_returns_relative_posix(self):
        from infrastructure.db.repositories import _to_relative_posix

        result = _to_relative_posix(
            image_path="input_documents/folder/page1.jpg",
            input_dir="input_documents",
        )
        self.assertEqual(result, "folder/page1.jpg")

    def test_input_dir_none_returns_posix_only(self):
        from infrastructure.db.repositories import _to_relative_posix

        result = _to_relative_posix(
            image_path=r"some\windows\path.jpg",
            input_dir=None,
        )
        self.assertEqual(result, "some/windows/path.jpg")

    def test_input_dir_empty_returns_posix_only(self):
        from infrastructure.db.repositories import _to_relative_posix

        result = _to_relative_posix(
            image_path=r"some\path.jpg",
            input_dir="",
        )
        self.assertEqual(result, "some/path.jpg")

    def test_outside_input_dir_falls_back(self):
        from infrastructure.db.repositories import _to_relative_posix

        # 两条路径都不存在,但 resolve() 仍把相对路径解析为基于 cwd 的绝对路径,
        # 在 cwd 下 outside_root 与 input_root 是同级目录而非父子关系,
        # relative_to 会抛 ValueError,触发退化分支。
        result = _to_relative_posix(
            image_path="outside_root/img.jpg",
            input_dir="input_root",
        )
        # 退化为原始路径(反斜杠转正斜杠)
        self.assertEqual(result, "outside_root/img.jpg")


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

    def _make_archive(self, *, metadata=None, status="none") -> int:
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

    def test_no_diff_returns_zero_and_writes_nothing(self):
        archive_id = self._make_archive(status="none")
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
            self.assertEqual(
                session.get(ArchiveRecord, archive_id).correction_status, "none"
            )

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
            self.assertEqual(
                {r.field_key for r in revisions},
                {"题名", "责任者", "实体分类号", "保管期限"},
            )

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
        archive_id = self._make_archive()
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
        archive_id = self._make_archive(status="none")
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
            self.assertEqual(
                session.get(ArchiveRecord, archive_id).correction_status,
                "corrected",
            )

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
            rows = (
                session.query(MetadataRevision)
                .order_by(MetadataRevision.revision_no)
                .all()
            )
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


if __name__ == "__main__":
    unittest.main()
