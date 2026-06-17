"""端到端的 BatchRecorder 行为测试，使用 SQLite + 假 Classifier。

覆盖：
  - on_batch_start → on_archive_start → on_archive_complete → on_batch_finish 链路
    把 archive_records / processing_jobs / processing_job_attempts / processing_batches
    四张表都正确填上。
  - skip-success：同一 BATCH_KEY 二次运行时，已成功档案不再处理且件号不变。
  - DB 写失败必须不冒泡到 BatchProcessor。
"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path


try:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db.models import (
        ArchivePage,
        ArchiveRecord,
        Base,
        LlmTrace,
        ProcessingBatch,
        ProcessingEvent,
        ProcessingJob,
    )
    from infrastructure.db.recorder import BatchRecorder
    from processors.batch_processor import BatchProcessor
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


class _StubClassifier:
    def __init__(self, payload, trace=None, rewrite_trace=None, progress_events=None):
        self._payload = payload
        self.calls = 0
        self.last_extraction_trace = trace
        self.last_rewrite_trace = rewrite_trace
        self.progress_events = progress_events or []

    def process_multi_page_document(self, archive_name, image_paths):
        self.calls += 1
        callback = getattr(self, "progress_callback", None)
        if callback is not None:
            for event in self.progress_events:
                callback(**event)
        return dict(self._payload)


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestBatchRecorder(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)

        self.tmp_root = Path("tests") / "_tmp_recorder_case"
        if self.tmp_root.exists():
            shutil.rmtree(self.tmp_root, ignore_errors=True)
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        archive_dir = self.tmp_root / "input" / "demo_archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "0001.jpg").write_bytes(b"fake-image-bytes")

        self.archive_dict = {"demo_archive": [str(archive_dir / "0001.jpg")]}
        self.payload = {
            "归档年度": "2026",
            "实体分类号": "DQL",
            "实体分类名称": "党群类",
            "保管期限": "30年",
            "题名": "测试题名",
            "责任者": "测试单位",
            "文件形成时间": "20260503",
            "备注": "",
        }

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _make_recorder(self, batch_key="b1", policy="skip-success"):
        return BatchRecorder(
            engine=self.engine,
            session_factory=self.Session,
            project_key="proj_a",
            project_name=None,
            batch_key=batch_key,
            rerun_policy=policy,
            input_dir=str(self.tmp_root / "input"),
            output_dir=str(self.tmp_root / "out"),
        )

    def test_full_lifecycle_writes_expected_rows(self):
        recorder = self._make_recorder()
        processor = BatchProcessor(_StubClassifier(self.payload), recorder=recorder)
        results = processor.batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "success")
        self.assertEqual(results[0]["metadata"]["件号"], "0001")
        self.assertEqual(results[0]["metadata"]["档号"], "2026-DQL-D30-0001")

        with self.Session() as session:
            archive = session.scalar(select(ArchiveRecord))
            self.assertIsNotNone(archive)
            self.assertEqual(archive.processing_status, "success")
            self.assertEqual(archive.title, "测试题名")
            self.assertEqual(archive.archive_no, "2026-DQL-D30-0001")
            self.assertEqual(archive.item_no, "0001")
            self.assertEqual(archive.retention_period_code, "D30")
            self.assertEqual(archive.classification_code, "DQL")
            self.assertEqual(archive.result_filename, "0001_demo_archive_result.json")

            batch = session.scalar(select(ProcessingBatch))
            self.assertEqual(batch.batch_status, "success")
            self.assertEqual(batch.success_count, 1)
            self.assertEqual(batch.fail_count, 0)
            self.assertEqual(batch.total_archives, 1)

            job = session.scalar(select(ProcessingJob))
            self.assertEqual(job.processing_status, "success")
            self.assertEqual(job.attempt_count, 1)

            events = session.scalars(
                select(ProcessingEvent).where(ProcessingEvent.job_id == job.id)
            ).all()
            self.assertGreaterEqual(len(events), 2)
            self.assertIn("stage_started", {event.event_type for event in events})
            self.assertIn("stage_finished", {event.event_type for event in events})

    def test_classifier_progress_callback_writes_job_events(self):
        recorder = self._make_recorder()
        classifier = _StubClassifier(
            self.payload,
            progress_events=[
                {
                    "stage": "llm",
                    "status": "llm_running",
                    "progress": 45,
                    "message": "开始 LLM 抽取",
                },
                {
                    "stage": "rules",
                    "status": "rules_running",
                    "progress": 75,
                    "message": "应用规则",
                },
            ],
        )

        BatchProcessor(classifier, recorder=recorder).batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )

        with self.Session() as session:
            job = session.scalar(select(ProcessingJob))
            self.assertEqual(job.progress, 100)
            events = session.scalars(
                select(ProcessingEvent)
                .where(ProcessingEvent.job_id == job.id)
                .order_by(ProcessingEvent.id.asc())
            ).all()
            stages = [event.stage for event in events]
            self.assertIn("llm", stages)
            self.assertIn("rules", stages)
            llm_event = next(event for event in events if event.stage == "llm")
            self.assertEqual(llm_event.payload["progress"], 45)

    def test_skip_success_on_rerun(self):
        # 第一次：正常跑
        recorder = self._make_recorder()
        classifier = _StubClassifier(self.payload)
        processor = BatchProcessor(classifier, recorder=recorder)
        processor.batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )
        self.assertEqual(classifier.calls, 1)

        # 第二次：复用 batch_key，应该 skip-success，classifier 不被调用
        recorder2 = self._make_recorder(batch_key="b1")
        classifier2 = _StubClassifier(self.payload)
        processor2 = BatchProcessor(classifier2, recorder=recorder2)
        results = processor2.batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )
        self.assertEqual(classifier2.calls, 0)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "success")
        # 件号必须不变
        self.assertEqual(results[0]["metadata"]["档号"], "2026-DQL-D30-0001")

    def test_rerun_failed_only_skips_prior_success(self):
        """rerun-failed-only：上次成功的档案应被复用、不再重跑(等同 skip-success 的跳过判定)。"""
        recorder = self._make_recorder()
        classifier = _StubClassifier(self.payload)
        BatchProcessor(classifier, recorder=recorder).batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )
        self.assertEqual(classifier.calls, 1)

        recorder2 = self._make_recorder(batch_key="b1", policy="rerun-failed-only")
        classifier2 = _StubClassifier(self.payload)
        results = BatchProcessor(classifier2, recorder=recorder2).batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )
        self.assertEqual(classifier2.calls, 0)
        self.assertEqual(results[0]["status"], "success")
        self.assertEqual(results[0]["metadata"]["档号"], "2026-DQL-D30-0001")

    def test_rerun_all_reprocesses_prior_success(self):
        """rerun-all：即使上次成功也要重新处理(从不跳过)。"""
        recorder = self._make_recorder()
        classifier = _StubClassifier(self.payload)
        BatchProcessor(classifier, recorder=recorder).batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )
        self.assertEqual(classifier.calls, 1)

        recorder2 = self._make_recorder(batch_key="b1", policy="rerun-all")
        classifier2 = _StubClassifier(self.payload)
        results = BatchProcessor(classifier2, recorder=recorder2).batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )
        self.assertEqual(classifier2.calls, 1)
        self.assertEqual(results[0]["status"], "success")

    def test_llm_metadata_snapshot_persisted_from_trace(self):
        """llm_metadata 落库 = 规则前的 LLM 原始输出(ExtractionTrace.parsed_metadata)，
        与 final_metadata(规则后+发号)区分。"""
        from infrastructure.llm_client import ExtractionTrace, PARSE_STRATEGY_JSON

        llm_raw = dict(self.payload)
        llm_raw["保管期限"] = "永久"  # 规则前的原始值，最终 payload 是 30年
        trace = ExtractionTrace(
            raw_response="{}",
            cleaned_response="{}",
            parse_strategy=PARSE_STRATEGY_JSON,
            parsed_metadata=llm_raw,
        )
        recorder = self._make_recorder()
        classifier = _StubClassifier(self.payload, trace=trace)
        BatchProcessor(classifier, recorder=recorder).batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )

        with self.Session() as session:
            archive = session.scalar(select(ArchiveRecord))
            self.assertIsNotNone(archive.llm_metadata)
            self.assertEqual(archive.llm_metadata["保管期限"], "永久")
            self.assertEqual(archive.final_metadata["保管期限"], "30年")
            # llm_metadata 是规则前快照，不含发号字段
            self.assertNotIn("档号", archive.llm_metadata)
            self.assertEqual(archive.final_metadata["档号"], "2026-DQL-D30-0001")

    def test_llm_metadata_null_when_trace_absent(self):
        """无 trace(或 trace 无 parsed_metadata)时 llm_metadata 应保持 NULL。"""
        recorder = self._make_recorder()
        classifier = _StubClassifier(self.payload, trace=None)
        BatchProcessor(classifier, recorder=recorder).batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )
        with self.Session() as session:
            archive = session.scalar(select(ArchiveRecord))
            self.assertIsNone(archive.llm_metadata)

    def test_db_failure_does_not_break_pipeline(self):
        recorder = self._make_recorder()

        # 故意让 on_archive_complete 触发数据库错误：drop archive_records 表
        Base.metadata.tables["archive_records"].drop(self.engine)

        processor = BatchProcessor(_StubClassifier(self.payload), recorder=recorder)
        results = processor.batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )

        # 文件路径仍要返回成功
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "success")
        # 计数器记录到了失败
        self.assertGreater(recorder.db_error_count, 0)

    def test_review_status_set_to_needs_review_when_note_marker(self):
        recorder = self._make_recorder()
        payload_with_warning = dict(self.payload)
        payload_with_warning["备注"] = "【待核查】简报题名疑为文学性标题"
        processor = BatchProcessor(_StubClassifier(payload_with_warning), recorder=recorder)
        processor.batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )

        with self.Session() as session:
            archive = session.scalar(select(ArchiveRecord))
            self.assertEqual(archive.review_status, "needs_review")

    def test_review_status_set_to_pending_without_marker(self):
        recorder = self._make_recorder()
        processor = BatchProcessor(_StubClassifier(self.payload), recorder=recorder)
        processor.batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )

        with self.Session() as session:
            archive = session.scalar(select(ArchiveRecord))
            self.assertEqual(archive.review_status, "pending")

    def test_review_status_not_required_when_processing_fails(self):
        class _FailingClassifier:
            def process_multi_page_document(self, archive_name, image_paths):
                raise RuntimeError("boom")

        recorder = self._make_recorder()
        processor = BatchProcessor(_FailingClassifier(), recorder=recorder)
        processor.batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )

        with self.Session() as session:
            archive = session.scalar(select(ArchiveRecord))
            self.assertIn(archive.processing_status, ("failed", "error"))
            # 处理失败的档案不进入待审核队列,审核状态置为 not_required(无需审核)。
            self.assertEqual(archive.review_status, "not_required")

    def test_llm_trace_persisted_to_archive_columns(self):
        from infrastructure.llm_client import ExtractionTrace, PARSE_STRATEGY_REPAIRED

        trace = ExtractionTrace(
            raw_response='{"题名": "v",}',
            cleaned_response='{"题名": "v",}',
            parse_strategy=PARSE_STRATEGY_REPAIRED,
        )
        recorder = self._make_recorder()
        classifier = _StubClassifier(self.payload, trace=trace)
        processor = BatchProcessor(classifier, recorder=recorder)
        processor.batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )

        with self.Session() as session:
            archive = session.scalar(select(ArchiveRecord))
            self.assertEqual(archive.llm_raw_response, '{"题名": "v",}')
            self.assertEqual(archive.llm_cleaned_response, '{"题名": "v",}')
            self.assertEqual(archive.llm_parse_strategy, "repaired")
            trace_row = session.scalar(select(LlmTrace))
            self.assertIsNotNone(trace_row)
            self.assertEqual(trace_row.parse_strategy, "repaired")

    def test_briefing_rewrite_trace_persisted_as_second_row(self):
        # [R2] 二次简报重写调用必须作为独立 LlmTrace(call_type=briefing_rewrite)入库，
        # 且不得覆盖 archive 上主抽取的缓存 llm_* 列。
        from infrastructure.llm_client import ExtractionTrace, PARSE_STRATEGY_JSON

        extract_trace = ExtractionTrace(
            raw_response='{"extract": 1}',
            cleaned_response='{"extract": 1}',
            parse_strategy=PARSE_STRATEGY_JSON,
            parsed_metadata=dict(self.payload),
        )
        rewrite_trace = ExtractionTrace(
            raw_response='{"题名": "关于开展X活动的简报"}',
            cleaned_response='{"题名": "关于开展X活动的简报"}',
            parse_strategy=PARSE_STRATEGY_JSON,
            parsed_metadata={"题名": "关于开展X活动的简报"},
        )
        classifier = _StubClassifier(
            self.payload, trace=extract_trace, rewrite_trace=rewrite_trace
        )
        recorder = self._make_recorder()
        BatchProcessor(classifier, recorder=recorder).batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )

        with self.Session() as session:
            traces = session.scalars(select(LlmTrace)).all()
            call_types = {t.call_type for t in traces}
            self.assertEqual(len(traces), 2)
            self.assertIn("metadata_extract", call_types)
            self.assertIn("briefing_rewrite", call_types)
            # 缓存列反映主抽取，不被重写覆盖
            archive = session.scalar(select(ArchiveRecord))
            self.assertEqual(archive.llm_raw_response, '{"extract": 1}')

    def test_llm_trace_absent_leaves_columns_null(self):
        recorder = self._make_recorder()
        # 显式不带 trace
        classifier = _StubClassifier(self.payload, trace=None)
        processor = BatchProcessor(classifier, recorder=recorder)
        processor.batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )

        with self.Session() as session:
            archive = session.scalar(select(ArchiveRecord))
            self.assertIsNone(archive.llm_raw_response)
            self.assertIsNone(archive.llm_cleaned_response)
            self.assertIsNone(archive.llm_parse_strategy)

    def test_image_path_stored_as_relative_posix(self):
        """数据契约 §4.5:archive_pages.image_path 必须是相对 input_dir 的 POSIX 路径。"""
        recorder = self._make_recorder()
        processor = BatchProcessor(_StubClassifier(self.payload), recorder=recorder)
        processor.batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )

        with self.Session() as session:
            page = session.scalar(select(ArchivePage))
            self.assertIsNotNone(page)
            self.assertEqual(page.image_path, "demo_archive/0001.jpg")
            self.assertEqual(page.image_name, "0001.jpg")
            self.assertEqual(page.page_no, 1)


if __name__ == "__main__":
    unittest.main()
