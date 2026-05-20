# 阶段 1C 实施 Plan:读侧查询接口与 CLI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## 实施修订记录 (2026-05-20)

本计划落地后,因后续阶段又出现 `utils/user_admin.py` 与 `utils/force_rerun_cli.py` 两个 CLI,出现了相同的 `--database-url` 注册与 `_resolve_database_url` 私有 helper。重构(commit `d7d3f84`)抽出公共模块,下列偏差应**优先于**本文后续 Task 步骤里的 CLI 代码片段:

1. **`_resolve_database_url(args)` 迁出 archive_query.py**:本文在 CLI Task 中给出 `_resolve_database_url(args) -> Optional[str]` 作为模块私有 helper,但现已迁到 `utils/_cli_common.py` 作为公共 `resolve_database_url(args)`(同函数体)。**今天写新代码请 `from utils._cli_common import add_database_url_arg, resolve_database_url`,不要再定义本地版本**。
2. **argparse `--database-url` 注册迁出**:本文在 `_build_parser()` 内直接调 `parser.add_argument("--database-url", default=None, help="...")`,现在统一改成 `add_database_url_arg(parser)`。同一 helper 已在 `archive_query.py` / `user_admin.py` / `force_rerun_cli.py` 共用。
3. **数据 dataclass 继承**:Phase 1C 落地的 `BatchDetail` / `ArchiveDetail` 在 2026-05-20 重构(commit `e5d5a18`)里改为继承 `BatchSummary` / `ArchiveSummary`,字段集合不变但只在子类声明扩展字段;`get_batch_detail` / `_archive_to_detail` 用 `fields(BatchSummary)` 解包父类字段。Task 7/Task 9 的代码片段保留作为字段清单参考,但具体声明顺序在当前代码里已经按继承组织。

**Goal:** 为 `infrastructure/db` 增加只读查询层(`queries.py`)与命令行入口(`utils/archive_query.py`),为后续 Web 管理后台数据接入铺路。

**Architecture:** queries.py 暴露 6 个只读函数(返回 frozen dataclass),CLI 用 argparse 单入口 subparser dispatch,所有逻辑 SQLite 单测全覆盖。设计契约见 `docs/superpowers/specs/2026-05-04-phase-1c-readside-queries-design.md`。

**Tech Stack:** Python 3.10+、SQLAlchemy 2.x、unittest、argparse;无新 pip 依赖。

---

## File Structure

| 文件 | 类型 | 责任 |
| --- | --- | --- |
| `infrastructure/db/queries.py` | 新增 | 9 个 dataclass + 6 个查询函数 + 分页 helper |
| `utils/archive_query.py` | 新增 | argparse 单入口 CLI,subparser dispatch 到 queries 函数 |
| `tests/test_db_queries.py` | 新增 | queries 函数的 SQLite 单测 + 共享 seed fixture |
| `tests/test_archive_query_cli.py` | 新增 | CLI in-process dispatch 单测 |
| `docs/postgresql_data_contract_design.md` | 修改 | §9 新增"读侧 API 契约"小节 |

实施期间禁止修改:`main.py`、`processors/batch_processor.py`、`core/classifier.py`、`infrastructure/db/recorder.py`、`infrastructure/db/repositories.py`、`infrastructure/db/models.py`、`infrastructure/db/engine.py`、`infrastructure/db/allocator.py`。

---

## 重要 Implementation Notes(每个 Task 都要遵守)

1. **`archive_year` 类型不对称**:ORM 列是 `String(8)`(存 "2026"),`ArchiveFilter.archive_year` 是 `Optional[int]`(用户友好),`ArchiveSummary.archive_year` / `ArchiveDetail.archive_year` 是 `Optional[str]`(对齐 ORM)。`list_archives` 在构建 SQL 时把 filter 的 int 转 `str(value)`。
2. **frozen dataclass + Iterable 字段**:`ArchiveFilter` 的 `Optional[Iterable[str]]` 字段在等值比较与 hash 时可能因可变 list 出问题,但本 plan 的 ArchiveFilter 不会作为 dict key,空 iterable 当作 None 处理就够,无需强制 tuple。
3. **不修改 `infrastructure/db/repositories.py` 与 `models.py`**:即使诱惑很大也不动,任何小改都会污染本次 commit 边界。
4. **测试约定**:沿用 `tests/test_db_recorder.py` 的 `_make_engine()` + `Base.metadata.create_all` + `unittest.skipUnless(SQLALCHEMY_AVAILABLE)` 模式;使用 `unittest.TestCase`(项目内一致),不引入 pytest。
5. **运行测试命令**:
   - 单测试类:`python -m unittest tests.test_db_queries.TestListBatches -v`
   - 整文件:`python -m unittest tests.test_db_queries -v`
   - 全量回归:`python -m unittest discover -s tests -p "test_*.py"`
6. **Commit 风格**:沿用现有 `db: ...` 前缀(数据库相关)与 `docs: ...`(文档相关)。**不带 Co-Authored-By 等 AI 标识**(memory 约束)。

---

## Task 1: queries.py 模块脚手架 + 9 个 dataclass 类型

**Files:**
- Create: `infrastructure/db/queries.py`
- Create: `tests/test_db_queries.py`

- [ ] **Step 1: 写失败测试 `test_dataclasses_instantiate_and_freeze`**

写入 `tests/test_db_queries.py`(完整文件):

```python
"""阶段 1C queries.py 的 SQLite 回归测试。"""

from __future__ import annotations

import dataclasses
import unittest
from datetime import datetime, timezone


try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import queries
    from infrastructure.db.models import Base
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
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
python -m unittest tests.test_db_queries -v
```

预期:`ImportError: cannot import name 'queries' from 'infrastructure.db'`(模块还不存在)

- [ ] **Step 3: 写 `infrastructure/db/queries.py` 完整内容**

```python
"""读侧只读查询函数集合,对外暴露领域 dataclass。

调用方负责 session 生命周期;本模块不做 commit、不打开 engine。
设计参考 docs/superpowers/specs/2026-05-04-phase-1c-readside-queries-design.md。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, Iterable, Optional, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, joinedload

from .models import (
    ArchivePage as ArchivePageModel,
    ArchiveRecord,
    AuditLog,
    MetadataRevision,
    ProcessingBatch,
    Project,
)

T = TypeVar("T")


# ── 列表返回信封(spec §3.1) ─────────────────────────────────────────────────
@dataclass(frozen=True)
class ListResult(Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    has_next: bool


# ── ArchiveFilter(spec §3.2),12 字段 ────────────────────────────────────────
@dataclass(frozen=True)
class ArchiveFilter:
    archive_year: Optional[int] = None
    classification_code: Optional[Iterable[str]] = None
    retention_period: Optional[Iterable[str]] = None
    openness_status: Optional[str] = None
    processing_status: Optional[Iterable[str]] = None
    review_status: Optional[Iterable[str]] = None
    correction_status: Optional[str] = None
    archive_no: Optional[str] = None
    item_no: Optional[str] = None
    title_like: Optional[str] = None
    responsible_party_like: Optional[str] = None
    error_code: Optional[Iterable[str]] = None


# ── BatchSummary / BatchDetail(spec §3.3 / §3.4) ────────────────────────────
@dataclass(frozen=True)
class BatchSummary:
    id: int
    project_id: int
    batch_key: str
    batch_name: Optional[str]
    input_dir: Optional[str]
    output_dir: Optional[str]
    batch_status: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    total_archives: int
    total_pages: int
    success_count: int
    fail_count: int
    summary_schema_version: Optional[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BatchDetail:
    id: int
    project_id: int
    batch_key: str
    batch_name: Optional[str]
    input_dir: Optional[str]
    output_dir: Optional[str]
    batch_status: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    total_archives: int
    total_pages: int
    success_count: int
    fail_count: int
    summary_schema_version: Optional[str]
    created_at: datetime
    updated_at: datetime
    failure_breakdown: dict[str, int]
    summary_schema_ref: Optional[str]
    summary_changelog_ref: Optional[str]


# ── ArchivePage(spec §3.5) ──────────────────────────────────────────────────
@dataclass(frozen=True)
class ArchivePage:
    id: int
    page_no: int
    image_path: str
    image_name: str
    file_hash: Optional[str]
    file_size: Optional[int]
    ocr_text: Optional[str]
    ocr_avg_confidence: Optional[float]
    ocr_low_conf_count: Optional[int]
    ocr_variant: Optional[str]
    created_at: datetime


# ── ArchiveSummary(spec §3.6),27 字段 ───────────────────────────────────────
@dataclass(frozen=True)
class ArchiveSummary:
    id: int
    project_id: int
    batch_id: int
    archive_key: str
    archive_name: str
    page_count: int
    processing_status: str
    review_status: str
    correction_status: str
    error_code: Optional[str]
    error_message: Optional[str]
    archive_year: Optional[str]
    classification_code: Optional[str]
    classification_name: Optional[str]
    retention_period: Optional[str]
    retention_period_code: Optional[str]
    responsible_party: Optional[str]
    document_number: Optional[str]
    title: Optional[str]
    document_date: Optional[str]
    openness_status: Optional[str]
    archive_no: Optional[str]
    item_no: Optional[str]
    fonds_unit_name: Optional[str]
    processed_time: Optional[str]
    created_at: datetime
    updated_at: datetime


# ── ArchiveDetail(spec §3.7),45 字段 ───────────────────────────────────────
@dataclass(frozen=True)
class ArchiveDetail:
    id: int
    project_id: int
    batch_id: int
    archive_key: str
    archive_name: str
    page_count: int
    processing_status: str
    review_status: str
    correction_status: str
    error_code: Optional[str]
    error_message: Optional[str]
    archive_year: Optional[str]
    classification_code: Optional[str]
    classification_name: Optional[str]
    retention_period: Optional[str]
    retention_period_code: Optional[str]
    responsible_party: Optional[str]
    document_number: Optional[str]
    title: Optional[str]
    document_date: Optional[str]
    openness_status: Optional[str]
    archive_no: Optional[str]
    item_no: Optional[str]
    fonds_unit_name: Optional[str]
    processed_time: Optional[str]
    created_at: datetime
    updated_at: datetime
    archive_folder_name: Optional[str]
    source_folder: Optional[str]
    image_files: Optional[list[str]]
    image_names: Optional[list[str]]
    result_filename: Optional[str]
    traceback_text: Optional[str]
    category_code: Optional[str]
    security_level: Optional[str]
    secret_period: Optional[str]
    openness_delay_reason: Optional[str]
    digitized_time: Optional[str]
    llm_metadata: Optional[dict[str, Any]]
    rules_metadata: Optional[dict[str, Any]]
    final_metadata: Optional[dict[str, Any]]
    llm_raw_response: Optional[str]
    llm_cleaned_response: Optional[str]
    llm_parse_strategy: Optional[str]
    pages: list[ArchivePage]


# ── RevisionRow / AuditLogRow(spec §3.8 / §3.9) ─────────────────────────────
@dataclass(frozen=True)
class RevisionRow:
    id: int
    archive_id: int
    revision_no: int
    field_key: str
    field_column: Optional[str]
    old_value: Any
    new_value: Any
    reason: Optional[str]
    created_by: Optional[int]
    created_at: datetime


@dataclass(frozen=True)
class AuditLogRow:
    id: int
    actor_user_id: Optional[int]
    action: str
    target_type: Optional[str]
    target_id: Optional[int]
    before_data: Any
    after_data: Any
    ip_address: Optional[str]
    user_agent: Optional[str]
    created_at: datetime


# ── 内部常量 ─────────────────────────────────────────────────────────────────
_PAGE_SIZE_MIN = 1
_PAGE_SIZE_MAX = 200
_AUDIT_TARGET_TYPES_ALLOWED: frozenset[str] = frozenset({"archive"})


__all__ = [
    "ListResult",
    "ArchiveFilter",
    "BatchSummary",
    "BatchDetail",
    "ArchivePage",
    "ArchiveSummary",
    "ArchiveDetail",
    "RevisionRow",
    "AuditLogRow",
]
```

- [ ] **Step 4: 跑测试,确认 PASS**

```bash
python -m unittest tests.test_db_queries -v
```

预期:全部 6 个 dataclass 测试通过。

- [ ] **Step 5: 跑全量回归,确认无破坏**

```bash
python -m unittest discover -s tests -p "test_*.py"
```

预期:所有现有测试 + 新增 6 个测试全绿。

- [ ] **Step 6: Commit**

```bash
git add infrastructure/db/queries.py tests/test_db_queries.py
git commit -m "db: phase 1C - queries.py dataclass scaffold"
```

---

## Task 2: 分页 helper(`_paginate` + `_build_list_result`)

**Files:**
- Modify: `infrastructure/db/queries.py`(append helper 函数)
- Modify: `tests/test_db_queries.py`(append `TestPaginate` 测试类)

- [ ] **Step 1: 写失败测试 `TestPaginate`**

`tests/test_db_queries.py` 末尾追加:

```python
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
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
python -m unittest tests.test_db_queries.TestPaginate -v
```

预期:`AttributeError: module 'infrastructure.db.queries' has no attribute '_validate_pagination'`

- [ ] **Step 3: 实现 helper**

在 `infrastructure/db/queries.py` 文件末尾(`__all__` 之前)插入:

```python
# ── 分页 helper ──────────────────────────────────────────────────────────────
def _validate_pagination(page: int, page_size: int) -> None:
    """校验分页参数;不合法时立即抛 ValueError(spec §6)。"""
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    if page_size < _PAGE_SIZE_MIN or page_size > _PAGE_SIZE_MAX:
        raise ValueError(
            f"page_size must be in [{_PAGE_SIZE_MIN}, {_PAGE_SIZE_MAX}], got {page_size}"
        )


def _paginate(stmt: Select, *, page: int, page_size: int) -> Select:
    """把 page/page_size 转 LIMIT/OFFSET 拍到 select 语句上。"""
    offset = (page - 1) * page_size
    return stmt.limit(page_size).offset(offset)


def _build_list_result(
    *,
    items: list[T],
    total: int,
    page: int,
    page_size: int,
) -> "ListResult[T]":
    """统一构造 ListResult,集中算 has_next。

    has_next 语义:仍有下一页(下一页可能为空,与 page > 末页 时一致返回空集 has_next=False)。
    """
    if total <= 0 or page_size <= 0:
        has_next = False
    else:
        last_page = math.ceil(total / page_size)
        has_next = page < last_page
    return ListResult(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_next=has_next,
    )
```

- [ ] **Step 4: 跑测试,确认 PASS**

```bash
python -m unittest tests.test_db_queries.TestPaginate -v
```

- [ ] **Step 5: 跑全量回归,确认无破坏**

```bash
python -m unittest discover -s tests -p "test_*.py"
```

- [ ] **Step 6: Commit**

```bash
git add infrastructure/db/queries.py tests/test_db_queries.py
git commit -m "db: phase 1C - pagination helpers and validation"
```

---

## Task 3: 测试种子 `_seed_query_fixtures`

**Files:**
- Modify: `tests/test_db_queries.py`(append seed helper + `TestSeedFixture`)

种子 helper 是后续所有 query 测试的共享底座,先单独验证它本身工作。

- [ ] **Step 1: 写失败测试 + seed helper 骨架**

`tests/test_db_queries.py` 顶部 import 区域追加:

```python
from infrastructure.db import repositories
from infrastructure.db.repositories import FieldRevision
from infrastructure.db.models import (
    ArchivePage as ArchivePageModel,
    ArchiveRecord,
    AuditLog,
    MetadataRevision,
    ProcessingBatch,
    Project,
)
```

(把已有的 `from infrastructure.db.models import Base` 替换为同一组 import,保留 `Base`。)

文件末尾追加 seed helper:

```python
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
```

(注:`func` 已通过 `from sqlalchemy import func` 在某些模块导入,但本测试文件 import 块还没有,需要在文件顶部 import 区域加 `from sqlalchemy import func`。)

- [ ] **Step 2: 跑测试,确认 PASS**

```bash
python -m unittest tests.test_db_queries.TestSeedFixture -v
```

预期:6 个 seed 验证测试全绿(种子数据不依赖 queries.py 的查询函数,直接命中)。

- [ ] **Step 3: 跑全量回归,确认无破坏**

```bash
python -m unittest discover -s tests -p "test_*.py"
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_db_queries.py
git commit -m "db: phase 1C - shared test seed for queries"
```

---

## Task 4: `list_batches` 函数

**Files:**
- Modify: `infrastructure/db/queries.py`(append `list_batches`)
- Modify: `tests/test_db_queries.py`(append `TestListBatches` 测试类)

- [ ] **Step 1: 写失败测试 `TestListBatches`**

`tests/test_db_queries.py` 末尾追加:

```python
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
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
python -m unittest tests.test_db_queries.TestListBatches -v
```

预期:`AttributeError: module 'infrastructure.db.queries' has no attribute 'list_batches'`

- [ ] **Step 3: 实现 `list_batches`**

`infrastructure/db/queries.py` 在 `__all__` 之前追加:

```python
# ── Query 函数 ───────────────────────────────────────────────────────────────
def _batch_to_summary(batch: ProcessingBatch) -> BatchSummary:
    return BatchSummary(
        id=batch.id,
        project_id=batch.project_id,
        batch_key=batch.batch_key,
        batch_name=batch.batch_name,
        input_dir=batch.input_dir,
        output_dir=batch.output_dir,
        batch_status=batch.batch_status,
        started_at=batch.started_at,
        finished_at=batch.finished_at,
        total_archives=batch.total_archives,
        total_pages=batch.total_pages,
        success_count=batch.success_count,
        fail_count=batch.fail_count,
        summary_schema_version=batch.summary_schema_version,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )


def list_batches(
    session: Session,
    *,
    project_key: str,
    status_filter: Optional[Iterable[str]] = None,
    page: int = 1,
    page_size: int = 50,
) -> "ListResult[BatchSummary]":
    """按 project_key 过滤批次,默认按 started_at DESC NULLS LAST 排序。"""
    _validate_pagination(page, page_size)

    base = (
        select(ProcessingBatch)
        .join(Project, ProcessingBatch.project_id == Project.id)
        .where(Project.project_key == project_key)
    )
    statuses = list(status_filter) if status_filter else []
    if statuses:
        base = base.where(ProcessingBatch.batch_status.in_(statuses))

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    rows = session.scalars(
        _paginate(
            base.order_by(
                ProcessingBatch.started_at.desc().nullslast(),
                ProcessingBatch.id.desc(),
            ),
            page=page,
            page_size=page_size,
        )
    ).all()

    return _build_list_result(
        items=[_batch_to_summary(b) for b in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )
```

并在文件顶部 `__all__` 列表里追加 `"list_batches"`。

- [ ] **Step 4: 跑测试,确认 PASS**

```bash
python -m unittest tests.test_db_queries.TestListBatches -v
```

预期:9 个测试全绿。

- [ ] **Step 5: 跑全量回归**

```bash
python -m unittest discover -s tests -p "test_*.py"
```

- [ ] **Step 6: Commit**

```bash
git add infrastructure/db/queries.py tests/test_db_queries.py
git commit -m "db: phase 1C - list_batches query"
```

---

## Task 5: `get_batch_detail` 函数

**Files:**
- Modify: `infrastructure/db/queries.py`(append `get_batch_detail`)
- Modify: `tests/test_db_queries.py`(append `TestGetBatchDetail`)

- [ ] **Step 1: 写失败测试**

`tests/test_db_queries.py` 末尾追加:

```python
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
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
python -m unittest tests.test_db_queries.TestGetBatchDetail -v
```

- [ ] **Step 3: 实现 `get_batch_detail`**

在 `infrastructure/db/queries.py` 的 `list_batches` 之后追加:

```python
def get_batch_detail(
    session: Session,
    *,
    batch_id: int,
) -> Optional[BatchDetail]:
    """返回批次详情 + failure_breakdown + schema 三件套。找不到返回 None。"""
    batch = session.get(ProcessingBatch, batch_id)
    if batch is None:
        return None
    return BatchDetail(
        id=batch.id,
        project_id=batch.project_id,
        batch_key=batch.batch_key,
        batch_name=batch.batch_name,
        input_dir=batch.input_dir,
        output_dir=batch.output_dir,
        batch_status=batch.batch_status,
        started_at=batch.started_at,
        finished_at=batch.finished_at,
        total_archives=batch.total_archives,
        total_pages=batch.total_pages,
        success_count=batch.success_count,
        fail_count=batch.fail_count,
        summary_schema_version=batch.summary_schema_version,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
        failure_breakdown=dict(batch.failure_breakdown or {}),
        summary_schema_ref=batch.summary_schema_ref,
        summary_changelog_ref=batch.summary_changelog_ref,
    )
```

并在 `__all__` 追加 `"get_batch_detail"`。

- [ ] **Step 4: 跑测试,确认 PASS**

```bash
python -m unittest tests.test_db_queries.TestGetBatchDetail -v
```

- [ ] **Step 5: 跑全量回归 + Commit**

```bash
python -m unittest discover -s tests -p "test_*.py"
git add infrastructure/db/queries.py tests/test_db_queries.py
git commit -m "db: phase 1C - get_batch_detail query"
```

---

## Task 6: `list_archives` 函数 + `ArchiveFilter` 映射

**Files:**
- Modify: `infrastructure/db/queries.py`(append `_archive_to_summary`, `_apply_archive_filter`, `list_archives`)
- Modify: `tests/test_db_queries.py`(append `TestListArchives`)

这是 query 函数里逻辑最复杂的一个,必须把 12 字段 ArchiveFilter 全部覆盖到。

- [ ] **Step 1: 写失败测试 `TestListArchives`(覆盖排序、12 个 filter 字段、空 iter、`title_like`、分页)**

`tests/test_db_queries.py` 末尾追加:

```python
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
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
python -m unittest tests.test_db_queries.TestListArchives -v
```

- [ ] **Step 3: 实现 `_archive_to_summary` + `_apply_archive_filter` + `list_archives`**

在 `infrastructure/db/queries.py` 的 `get_batch_detail` 之后追加:

```python
def _archive_to_summary(ar: ArchiveRecord) -> ArchiveSummary:
    return ArchiveSummary(
        id=ar.id,
        project_id=ar.project_id,
        batch_id=ar.batch_id,
        archive_key=ar.archive_key,
        archive_name=ar.archive_name,
        page_count=ar.page_count,
        processing_status=ar.processing_status,
        review_status=ar.review_status,
        correction_status=ar.correction_status,
        error_code=ar.error_code,
        error_message=ar.error_message,
        archive_year=ar.archive_year,
        classification_code=ar.classification_code,
        classification_name=ar.classification_name,
        retention_period=ar.retention_period,
        retention_period_code=ar.retention_period_code,
        responsible_party=ar.responsible_party,
        document_number=ar.document_number,
        title=ar.title,
        document_date=ar.document_date,
        openness_status=ar.openness_status,
        archive_no=ar.archive_no,
        item_no=ar.item_no,
        fonds_unit_name=ar.fonds_unit_name,
        processed_time=ar.processed_time,
        created_at=ar.created_at,
        updated_at=ar.updated_at,
    )


def _apply_archive_filter(stmt: Select, f: ArchiveFilter) -> Select:
    """把 ArchiveFilter 12 字段映射到 SQL where 子句。

    约定(spec §3.2):
      - None 值不附加条件
      - Iterable 字段空集等价于 None
      - *_like 字段空字符串等价于 None
      - archive_year 是 int 输入,DB 存 String → 转 str(value)
    """
    if f.archive_year is not None:
        stmt = stmt.where(ArchiveRecord.archive_year == str(f.archive_year))

    classification_codes = list(f.classification_code) if f.classification_code else []
    if classification_codes:
        stmt = stmt.where(ArchiveRecord.classification_code.in_(classification_codes))

    retention_periods = list(f.retention_period) if f.retention_period else []
    if retention_periods:
        stmt = stmt.where(ArchiveRecord.retention_period.in_(retention_periods))

    if f.openness_status:
        stmt = stmt.where(ArchiveRecord.openness_status == f.openness_status)

    processing_statuses = list(f.processing_status) if f.processing_status else []
    if processing_statuses:
        stmt = stmt.where(ArchiveRecord.processing_status.in_(processing_statuses))

    review_statuses = list(f.review_status) if f.review_status else []
    if review_statuses:
        stmt = stmt.where(ArchiveRecord.review_status.in_(review_statuses))

    if f.correction_status:
        stmt = stmt.where(ArchiveRecord.correction_status == f.correction_status)

    if f.archive_no:
        stmt = stmt.where(ArchiveRecord.archive_no == f.archive_no)

    if f.item_no:
        stmt = stmt.where(ArchiveRecord.item_no == f.item_no)

    if f.title_like:
        stmt = stmt.where(ArchiveRecord.title.ilike(f"%{f.title_like}%"))

    if f.responsible_party_like:
        stmt = stmt.where(
            ArchiveRecord.responsible_party.ilike(f"%{f.responsible_party_like}%")
        )

    error_codes = list(f.error_code) if f.error_code else []
    if error_codes:
        stmt = stmt.where(ArchiveRecord.error_code.in_(error_codes))

    return stmt


def list_archives(
    session: Session,
    *,
    batch_id: int,
    filter: Optional[ArchiveFilter] = None,
    page: int = 1,
    page_size: int = 50,
) -> "ListResult[ArchiveSummary]":
    """按 batch_id 列出档案,支持 12 字段过滤,默认按 archive_no/item_no ASC NULLS LAST 排序。"""
    _validate_pagination(page, page_size)

    base = select(ArchiveRecord).where(ArchiveRecord.batch_id == batch_id)
    if filter is not None:
        base = _apply_archive_filter(base, filter)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    rows = session.scalars(
        _paginate(
            base.order_by(
                ArchiveRecord.archive_no.asc().nullslast(),
                ArchiveRecord.item_no.asc().nullslast(),
                ArchiveRecord.id.asc(),
            ),
            page=page,
            page_size=page_size,
        )
    ).all()

    return _build_list_result(
        items=[_archive_to_summary(ar) for ar in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )
```

并在 `__all__` 追加 `"list_archives"`。

- [ ] **Step 4: 跑测试,确认 PASS**

```bash
python -m unittest tests.test_db_queries.TestListArchives -v
```

预期:21 个测试全绿。

- [ ] **Step 5: 跑全量回归 + Commit**

```bash
python -m unittest discover -s tests -p "test_*.py"
git add infrastructure/db/queries.py tests/test_db_queries.py
git commit -m "db: phase 1C - list_archives query and filter mapping"
```

---

## Task 7: `get_archive_detail` 函数(含 pages 子查询)

**Files:**
- Modify: `infrastructure/db/queries.py`(append `_page_to_dataclass`, `_archive_to_detail`, `get_archive_detail`)
- Modify: `tests/test_db_queries.py`(append `TestGetArchiveDetail`)

- [ ] **Step 1: 写失败测试 `TestGetArchiveDetail`**

```python
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
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
python -m unittest tests.test_db_queries.TestGetArchiveDetail -v
```

- [ ] **Step 3: 实现 helper + `get_archive_detail`**

在 `infrastructure/db/queries.py` 的 `list_archives` 之后追加:

```python
def _page_to_dataclass(p: ArchivePageModel) -> ArchivePage:
    return ArchivePage(
        id=p.id,
        page_no=p.page_no,
        image_path=p.image_path,
        image_name=p.image_name,
        file_hash=p.file_hash,
        file_size=p.file_size,
        ocr_text=p.ocr_text,
        ocr_avg_confidence=p.ocr_avg_confidence,
        ocr_low_conf_count=p.ocr_low_conf_count,
        ocr_variant=p.ocr_variant,
        created_at=p.created_at,
    )


def _archive_to_detail(ar: ArchiveRecord, pages: list[ArchivePage]) -> ArchiveDetail:
    return ArchiveDetail(
        id=ar.id,
        project_id=ar.project_id,
        batch_id=ar.batch_id,
        archive_key=ar.archive_key,
        archive_name=ar.archive_name,
        page_count=ar.page_count,
        processing_status=ar.processing_status,
        review_status=ar.review_status,
        correction_status=ar.correction_status,
        error_code=ar.error_code,
        error_message=ar.error_message,
        archive_year=ar.archive_year,
        classification_code=ar.classification_code,
        classification_name=ar.classification_name,
        retention_period=ar.retention_period,
        retention_period_code=ar.retention_period_code,
        responsible_party=ar.responsible_party,
        document_number=ar.document_number,
        title=ar.title,
        document_date=ar.document_date,
        openness_status=ar.openness_status,
        archive_no=ar.archive_no,
        item_no=ar.item_no,
        fonds_unit_name=ar.fonds_unit_name,
        processed_time=ar.processed_time,
        created_at=ar.created_at,
        updated_at=ar.updated_at,
        archive_folder_name=ar.archive_folder_name,
        source_folder=ar.source_folder,
        image_files=list(ar.image_files) if ar.image_files else None,
        image_names=list(ar.image_names) if ar.image_names else None,
        result_filename=ar.result_filename,
        traceback_text=ar.traceback_text,
        category_code=ar.category_code,
        security_level=ar.security_level,
        secret_period=ar.secret_period,
        openness_delay_reason=ar.openness_delay_reason,
        digitized_time=ar.digitized_time,
        llm_metadata=dict(ar.llm_metadata) if ar.llm_metadata else None,
        rules_metadata=dict(ar.rules_metadata) if ar.rules_metadata else None,
        final_metadata=dict(ar.final_metadata) if ar.final_metadata else None,
        llm_raw_response=ar.llm_raw_response,
        llm_cleaned_response=ar.llm_cleaned_response,
        llm_parse_strategy=ar.llm_parse_strategy,
        pages=pages,
    )


def get_archive_detail(
    session: Session,
    *,
    archive_id: int,
) -> Optional[ArchiveDetail]:
    """返回档案详情 + 全页面列表。找不到返回 None。"""
    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return None

    page_rows = session.scalars(
        select(ArchivePageModel)
        .where(ArchivePageModel.archive_id == archive_id)
        .order_by(ArchivePageModel.page_no.asc())
    ).all()
    pages = [_page_to_dataclass(p) for p in page_rows]

    return _archive_to_detail(archive, pages)
```

并在 `__all__` 追加 `"get_archive_detail"`。

- [ ] **Step 4: 跑测试,确认 PASS**

```bash
python -m unittest tests.test_db_queries.TestGetArchiveDetail -v
```

- [ ] **Step 5: 跑全量回归 + Commit**

```bash
python -m unittest discover -s tests -p "test_*.py"
git add infrastructure/db/queries.py tests/test_db_queries.py
git commit -m "db: phase 1C - get_archive_detail with pages"
```

---

## Task 8: `list_revisions` 函数

**Files:**
- Modify: `infrastructure/db/queries.py`(append `_revision_to_row`, `list_revisions`)
- Modify: `tests/test_db_queries.py`(append `TestListRevisions`)

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
python -m unittest tests.test_db_queries.TestListRevisions -v
```

- [ ] **Step 3: 实现**

在 `infrastructure/db/queries.py` 的 `get_archive_detail` 之后追加:

```python
def _revision_to_row(rev: MetadataRevision) -> RevisionRow:
    return RevisionRow(
        id=rev.id,
        archive_id=rev.archive_id,
        revision_no=rev.revision_no,
        field_key=rev.field_key,
        field_column=rev.field_column,
        old_value=rev.old_value,
        new_value=rev.new_value,
        reason=rev.reason,
        created_by=rev.created_by,
        created_at=rev.created_at,
    )


def list_revisions(
    session: Session,
    *,
    archive_id: int,
    page: int = 1,
    page_size: int = 50,
) -> "ListResult[RevisionRow]":
    """按 archive_id 列出修正记录,默认 revision_no DESC, id DESC。"""
    _validate_pagination(page, page_size)

    base = select(MetadataRevision).where(MetadataRevision.archive_id == archive_id)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    rows = session.scalars(
        _paginate(
            base.order_by(
                MetadataRevision.revision_no.desc(),
                MetadataRevision.id.desc(),
            ),
            page=page,
            page_size=page_size,
        )
    ).all()

    return _build_list_result(
        items=[_revision_to_row(r) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )
```

并在 `__all__` 追加 `"list_revisions"`。

- [ ] **Step 4: 跑测试,确认 PASS**

```bash
python -m unittest tests.test_db_queries.TestListRevisions -v
```

- [ ] **Step 5: 跑全量回归 + Commit**

```bash
python -m unittest discover -s tests -p "test_*.py"
git add infrastructure/db/queries.py tests/test_db_queries.py
git commit -m "db: phase 1C - list_revisions query"
```

---

## Task 9: `list_audit_logs` 函数(含 target_type 白名单)

**Files:**
- Modify: `infrastructure/db/queries.py`(append `_audit_to_row`, `list_audit_logs`)
- Modify: `tests/test_db_queries.py`(append `TestListAuditLogs`)

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
python -m unittest tests.test_db_queries.TestListAuditLogs -v
```

- [ ] **Step 3: 实现**

在 `infrastructure/db/queries.py` 的 `list_revisions` 之后追加:

```python
def _audit_to_row(log: AuditLog) -> AuditLogRow:
    return AuditLogRow(
        id=log.id,
        actor_user_id=log.actor_user_id,
        action=log.action,
        target_type=log.target_type,
        target_id=log.target_id,
        before_data=log.before_data,
        after_data=log.after_data,
        ip_address=log.ip_address,
        user_agent=log.user_agent,
        created_at=log.created_at,
    )


def list_audit_logs(
    session: Session,
    *,
    target_type: str,
    target_id: int,
    page: int = 1,
    page_size: int = 50,
) -> "ListResult[AuditLogRow]":
    """按 (target_type, target_id) 列出审计记录,默认 created_at DESC, id DESC。

    一期白名单 target_type ∈ {"archive"};未知值快速失败,避免 audit 漏检(spec §6/§12.4)。
    """
    if target_type not in _AUDIT_TARGET_TYPES_ALLOWED:
        raise ValueError(
            f"unknown target_type={target_type!r}; "
            f"allowed: {sorted(_AUDIT_TARGET_TYPES_ALLOWED)}"
        )
    _validate_pagination(page, page_size)

    base = select(AuditLog).where(
        AuditLog.target_type == target_type,
        AuditLog.target_id == target_id,
    )

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    rows = session.scalars(
        _paginate(
            base.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()),
            page=page,
            page_size=page_size,
        )
    ).all()

    return _build_list_result(
        items=[_audit_to_row(r) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )
```

并在 `__all__` 追加 `"list_audit_logs"`。

- [ ] **Step 4: 跑测试,确认 PASS**

```bash
python -m unittest tests.test_db_queries.TestListAuditLogs -v
```

- [ ] **Step 5: 跑全量回归 + Commit**

```bash
python -m unittest discover -s tests -p "test_*.py"
git add infrastructure/db/queries.py tests/test_db_queries.py
git commit -m "db: phase 1C - list_audit_logs query with target_type allowlist"
```

---

## Task 10: `utils/archive_query.py` CLI 脚手架

**Files:**
- Create: `utils/archive_query.py`
- Create: `tests/test_archive_query_cli.py`

CLI 脚手架包括:argparse subparsers 全部 6 个 subcommand 注册、`run()` 入口、`main()`、退出码、`DATABASE_URL` 检查、延迟 import 数据库依赖。具体 dispatch 逻辑(实际调 queries 函数)留到 Task 11-13。

- [ ] **Step 1: 写失败测试 `TestCliBootstrap`**

完整文件 `tests/test_archive_query_cli.py`:

```python
"""阶段 1C archive_query CLI 端到端测试。

测试通过 in-process dispatch:`from utils.archive_query import run; run([...])`,
不走 subprocess(慢,且不便断言 stdout/stderr/exit-code)。
"""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock


try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db.models import Base
    from utils import archive_query as cli
    # 复用 queries 测试的 seed
    from tests.test_db_queries import _seed_query_fixtures
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
class TestCliBootstrap(unittest.TestCase):
    """脚手架级别的测试:不依赖具体 subcommand 实现。"""

    def test_no_subcommand_returns_2_and_writes_usage(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = cli.run([])
        self.assertEqual(rc, 2)

    def test_unknown_subcommand_returns_2(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = cli.run(["unknown", "thing"])
        self.assertEqual(rc, 2)

    def test_database_url_empty_returns_2(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = cli.run(["batches", "list", "--project-key", "x"])
            self.assertEqual(rc, 2)
            self.assertIn("DATABASE_URL", stderr.getvalue())
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
python -m unittest tests.test_archive_query_cli.TestCliBootstrap -v
```

预期:`ModuleNotFoundError: No module named 'utils.archive_query'`

- [ ] **Step 3: 写 CLI 脚手架 `utils/archive_query.py`(完整文件)**

```python
"""读侧只读查询的命令行入口。

用途:
  在 Web API 上线前,允许人工或自动化通过 CLI 触达 6 个 query 函数。
  输出始终是 JSON;不做表格美化。

用法:
  python -m utils.archive_query batches list   --project-key K [--status running] [--page 1 --page-size 50]
  python -m utils.archive_query batches show   --batch-id ID
  python -m utils.archive_query archives list  --batch-id ID [filter args]
  python -m utils.archive_query archives show  --archive-id ID
  python -m utils.archive_query revisions list --archive-id ID
  python -m utils.archive_query audit list     --target-type archive --target-id ID

环境变量:
  DATABASE_URL  必填,与 main.py 同源

退出码:
  0  成功
  2  参数缺失/非法(含 DATABASE_URL 空、page_size 越界、未知 target_type、subcommand 缺失)
  3  数据库连接失败
  4  资源不存在(get_*_detail 返回 None)
  9  其他未分类异常

设计参考 docs/superpowers/specs/2026-05-04-phase-1c-readside-queries-design.md。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
from typing import Any, Callable, Optional

logger = logging.getLogger("archive_query")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="archive_query",
        description="读侧 DB 查询 CLI(JSON 输出)",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="覆盖 DATABASE_URL 环境变量,通常应通过 env 注入",
    )
    sub = parser.add_subparsers(dest="resource", required=False)

    # ── batches ──
    p_batches = sub.add_parser("batches", help="批次相关查询")
    sub_batches = p_batches.add_subparsers(dest="verb", required=False)
    p_batches_list = sub_batches.add_parser("list", help="列出批次")
    p_batches_list.add_argument("--project-key", required=True)
    p_batches_list.add_argument("--status", action="append", default=[], dest="status_filter",
                                help="可重复;过滤 batch_status")
    p_batches_list.add_argument("--page", type=int, default=1)
    p_batches_list.add_argument("--page-size", type=int, default=50, dest="page_size")
    p_batches_list.set_defaults(func=_cmd_batches_list)

    p_batches_show = sub_batches.add_parser("show", help="批次详情")
    p_batches_show.add_argument("--batch-id", type=int, required=True, dest="batch_id")
    p_batches_show.set_defaults(func=_cmd_batches_show)

    # ── archives ──
    p_archives = sub.add_parser("archives", help="档案相关查询")
    sub_archives = p_archives.add_subparsers(dest="verb", required=False)
    p_archives_list = sub_archives.add_parser("list", help="列出档案")
    p_archives_list.add_argument("--batch-id", type=int, required=True, dest="batch_id")
    p_archives_list.add_argument("--archive-year", type=int, default=None, dest="archive_year")
    p_archives_list.add_argument("--classification-code", action="append", default=[],
                                 dest="classification_code")
    p_archives_list.add_argument("--retention-period", action="append", default=[],
                                 dest="retention_period")
    p_archives_list.add_argument("--openness-status", default=None, dest="openness_status")
    p_archives_list.add_argument("--processing-status", action="append", default=[],
                                 dest="processing_status")
    p_archives_list.add_argument("--review-status", action="append", default=[],
                                 dest="review_status")
    p_archives_list.add_argument("--correction-status", default=None, dest="correction_status")
    p_archives_list.add_argument("--archive-no", default=None, dest="archive_no")
    p_archives_list.add_argument("--item-no", default=None, dest="item_no")
    p_archives_list.add_argument("--title-like", default=None, dest="title_like")
    p_archives_list.add_argument("--responsible-party-like", default=None,
                                 dest="responsible_party_like")
    p_archives_list.add_argument("--error-code", action="append", default=[], dest="error_code")
    p_archives_list.add_argument("--page", type=int, default=1)
    p_archives_list.add_argument("--page-size", type=int, default=50, dest="page_size")
    p_archives_list.set_defaults(func=_cmd_archives_list)

    p_archives_show = sub_archives.add_parser("show", help="档案详情")
    p_archives_show.add_argument("--archive-id", type=int, required=True, dest="archive_id")
    p_archives_show.set_defaults(func=_cmd_archives_show)

    # ── revisions ──
    p_revisions = sub.add_parser("revisions", help="档案修正记录")
    sub_revisions = p_revisions.add_subparsers(dest="verb", required=False)
    p_rev_list = sub_revisions.add_parser("list", help="列出修正记录")
    p_rev_list.add_argument("--archive-id", type=int, required=True, dest="archive_id")
    p_rev_list.add_argument("--page", type=int, default=1)
    p_rev_list.add_argument("--page-size", type=int, default=50, dest="page_size")
    p_rev_list.set_defaults(func=_cmd_revisions_list)

    # ── audit ──
    p_audit = sub.add_parser("audit", help="审计日志")
    sub_audit = p_audit.add_subparsers(dest="verb", required=False)
    p_audit_list = sub_audit.add_parser("list", help="列出审计日志")
    p_audit_list.add_argument("--target-type", required=True, dest="target_type")
    p_audit_list.add_argument("--target-id", type=int, required=True, dest="target_id")
    p_audit_list.add_argument("--page", type=int, default=1)
    p_audit_list.add_argument("--page-size", type=int, default=50, dest="page_size")
    p_audit_list.set_defaults(func=_cmd_audit_list)

    return parser


def _resolve_database_url(args) -> Optional[str]:
    return args.database_url or os.environ.get("DATABASE_URL", "") or None


def _print_json(payload: Any) -> None:
    sys.stdout.write(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    )
    sys.stdout.write("\n")


def _list_result_to_dict(result) -> dict:
    return {
        "items": [dataclasses.asdict(it) for it in result.items],
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
        "has_next": result.has_next,
    }


# ── 子命令处理函数 ──────────────────────────────────────────────────────────
def _cmd_batches_list(args, session) -> int:
    raise NotImplementedError


def _cmd_batches_show(args, session) -> int:
    raise NotImplementedError


def _cmd_archives_list(args, session) -> int:
    raise NotImplementedError


def _cmd_archives_show(args, session) -> int:
    raise NotImplementedError


def _cmd_revisions_list(args, session) -> int:
    raise NotImplementedError


def _cmd_audit_list(args, session) -> int:
    raise NotImplementedError


def run(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse parse_args 在错误时调 sys.exit(2);run() 在 in-process 测试时
        # 想要返回退出码而不是真退出,所以拦截 SystemExit。
        code = exc.code if isinstance(exc.code, int) else 2
        return code

    if not getattr(args, "resource", None) or not getattr(args, "verb", None):
        parser.print_usage(file=sys.stderr)
        sys.stderr.write("error: missing subcommand\n")
        return 2

    func: Optional[Callable] = getattr(args, "func", None)
    if func is None:
        sys.stderr.write("error: missing subcommand handler\n")
        return 2

    database_url = _resolve_database_url(args)
    if not database_url:
        sys.stderr.write("error: DATABASE_URL not set\n")
        return 2

    # 延迟 import,避免没装 SQLAlchemy 的环境 import 阶段就崩
    try:
        from infrastructure.db.engine import (
            check_connectivity,
            dispose_engine,
            make_engine,
            make_session_factory,
        )
    except ImportError as exc:
        sys.stderr.write(
            f"error: missing database dependency: {exc}. "
            "请 pip install -r requirements/db.txt\n"
        )
        return 3

    engine = None
    try:
        engine = make_engine(database_url)
        check_connectivity(engine)
    except Exception as exc:
        sys.stderr.write(f"error: database connection failed: {exc}\n")
        if engine is not None:
            dispose_engine(engine)
        return 3

    session_factory = make_session_factory(engine)
    try:
        with session_factory() as session:
            return func(args, session)
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    except Exception as exc:
        logger.exception("unhandled error in subcommand: %s", exc)
        sys.stderr.write(f"error: {exc}\n")
        return 9
    finally:
        dispose_engine(engine)


def main() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试,确认 PASS**

```bash
python -m unittest tests.test_archive_query_cli.TestCliBootstrap -v
```

预期:3 个 bootstrap 测试全绿(子命令 dispatch 还会 NotImplementedError,但 bootstrap 测试不触发 dispatch)。

- [ ] **Step 5: 跑全量回归 + Commit**

```bash
python -m unittest discover -s tests -p "test_*.py"
git add utils/archive_query.py tests/test_archive_query_cli.py
git commit -m "db: phase 1C - archive_query CLI scaffold"
```

---

## Task 11: `batches` subcommands(`list` + `show`)

**Files:**
- Modify: `utils/archive_query.py`(填 `_cmd_batches_list` / `_cmd_batches_show`)
- Modify: `tests/test_archive_query_cli.py`(append `TestCliBatches`)

- [ ] **Step 1: 写失败测试**

`tests/test_archive_query_cli.py` 末尾追加:

```python
@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestCliBatches(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            self.ids = _seed_query_fixtures(session)
            session.commit()
        # 让 cli.run() 在内部 make_engine 时拿到同一个 in-memory DB
        self._patch_engine = mock.patch("utils.archive_query.run", new=self._run_with_session)
        self._original_run = cli.run

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _run_with_session(self, argv):
        """绕开 cli.run 的 make_engine,使用本测试的 in-memory engine 执行。"""
        parser = cli._build_parser()
        try:
            args = parser.parse_args(argv)
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 2

        if not getattr(args, "resource", None) or not getattr(args, "verb", None):
            return 2
        func = getattr(args, "func", None)
        if func is None:
            return 2
        try:
            with self.Session() as session:
                return func(args, session)
        except ValueError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 2
        except Exception as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 9

    def test_batches_list_outputs_json_envelope(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = self._run_with_session(["batches", "list", "--project-key", "proj_test"])
        self.assertEqual(rc, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["page"], 1)
        self.assertEqual(payload["page_size"], 50)
        self.assertEqual(payload["has_next"], False)
        self.assertEqual(len(payload["items"]), 2)

    def test_batches_list_status_filter(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = self._run_with_session([
                "batches", "list",
                "--project-key", "proj_test",
                "--status", "completed",
            ])
        self.assertEqual(rc, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["batch_key"], "batch_a")

    def test_batches_show_returns_detail_json(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = self._run_with_session([
                "batches", "show", "--batch-id", str(self.ids["batch_a_id"]),
            ])
        self.assertEqual(rc, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["batch_key"], "batch_a")
        self.assertEqual(payload["failure_breakdown"], {"LLM_PARSE_FAIL": 1, "OCR_TIMEOUT": 1})

    def test_batches_show_not_found_returns_4(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = self._run_with_session(["batches", "show", "--batch-id", "99999"])
        self.assertEqual(rc, 4)
        self.assertIn("not found", stderr.getvalue())
```

并在文件顶部追加 `import sys`。

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
python -m unittest tests.test_archive_query_cli.TestCliBatches -v
```

预期:NotImplementedError。

- [ ] **Step 3: 实现 `_cmd_batches_list` / `_cmd_batches_show`**

替换 `utils/archive_query.py` 中两个占位函数:

```python
def _cmd_batches_list(args, session) -> int:
    from infrastructure.db import queries
    result = queries.list_batches(
        session,
        project_key=args.project_key,
        status_filter=args.status_filter or None,
        page=args.page,
        page_size=args.page_size,
    )
    _print_json(_list_result_to_dict(result))
    return 0


def _cmd_batches_show(args, session) -> int:
    from infrastructure.db import queries
    detail = queries.get_batch_detail(session, batch_id=args.batch_id)
    if detail is None:
        sys.stderr.write(f"not found: batch id={args.batch_id}\n")
        return 4
    _print_json(dataclasses.asdict(detail))
    return 0
```

- [ ] **Step 4: 跑测试,确认 PASS**

```bash
python -m unittest tests.test_archive_query_cli.TestCliBatches -v
```

- [ ] **Step 5: 跑全量回归 + Commit**

```bash
python -m unittest discover -s tests -p "test_*.py"
git add utils/archive_query.py tests/test_archive_query_cli.py
git commit -m "db: phase 1C - CLI batches list and show subcommands"
```

---

## Task 12: `archives` subcommands(`list` + `show`)

**Files:**
- Modify: `utils/archive_query.py`(填 `_cmd_archives_list` / `_cmd_archives_show`)
- Modify: `tests/test_archive_query_cli.py`(append `TestCliArchives`)

- [ ] **Step 1: 写失败测试**

```python
@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestCliArchives(unittest.TestCase):
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

    def _run(self, argv):
        parser = cli._build_parser()
        try:
            args = parser.parse_args(argv)
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 2, "", ""
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                with self.Session() as session:
                    rc = args.func(args, session)
        except ValueError as exc:
            stderr.write(f"error: {exc}\n")
            rc = 2
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_archives_list_no_filter(self):
        rc, out, _ = self._run([
            "archives", "list", "--batch-id", str(self.ids["batch_a_id"]),
        ])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["total"], 6)

    def test_archives_list_with_filters(self):
        rc, out, _ = self._run([
            "archives", "list",
            "--batch-id", str(self.ids["batch_a_id"]),
            "--archive-year", "2025",
            "--classification-code", "ZHL",
            "--processing-status", "success",
        ])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["archive_key"], "ar0")

    def test_archives_list_repeatable_arg(self):
        rc, out, _ = self._run([
            "archives", "list",
            "--batch-id", str(self.ids["batch_a_id"]),
            "--classification-code", "DQL",
            "--classification-code", "YWL",
        ])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["total"], 2)

    def test_archives_list_title_like(self):
        rc, out, _ = self._run([
            "archives", "list",
            "--batch-id", str(self.ids["batch_a_id"]),
            "--title-like", "needs_review",
        ])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["total"], 1)

    def test_archives_show_returns_detail_with_pages(self):
        archive_id = self.ids["archive_ids"][0]
        rc, out, _ = self._run([
            "archives", "show", "--archive-id", str(archive_id),
        ])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["archive_key"], "ar0")
        self.assertEqual(len(payload["pages"]), 2)
        self.assertIn("final_metadata", payload)

    def test_archives_show_not_found_returns_4(self):
        rc, _, err = self._run([
            "archives", "show", "--archive-id", "99999",
        ])
        self.assertEqual(rc, 4)
        self.assertIn("not found", err)

    def test_archives_list_invalid_page_size_returns_2(self):
        rc, _, err = self._run([
            "archives", "list",
            "--batch-id", str(self.ids["batch_a_id"]),
            "--page-size", "500",
        ])
        self.assertEqual(rc, 2)
        self.assertIn("page_size", err)
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
python -m unittest tests.test_archive_query_cli.TestCliArchives -v
```

- [ ] **Step 3: 实现**

替换 `utils/archive_query.py` 中两个占位函数:

```python
def _cmd_archives_list(args, session) -> int:
    from infrastructure.db import queries
    f = queries.ArchiveFilter(
        archive_year=args.archive_year,
        classification_code=args.classification_code or None,
        retention_period=args.retention_period or None,
        openness_status=args.openness_status,
        processing_status=args.processing_status or None,
        review_status=args.review_status or None,
        correction_status=args.correction_status,
        archive_no=args.archive_no,
        item_no=args.item_no,
        title_like=args.title_like,
        responsible_party_like=args.responsible_party_like,
        error_code=args.error_code or None,
    )
    result = queries.list_archives(
        session,
        batch_id=args.batch_id,
        filter=f,
        page=args.page,
        page_size=args.page_size,
    )
    _print_json(_list_result_to_dict(result))
    return 0


def _cmd_archives_show(args, session) -> int:
    from infrastructure.db import queries
    detail = queries.get_archive_detail(session, archive_id=args.archive_id)
    if detail is None:
        sys.stderr.write(f"not found: archive id={args.archive_id}\n")
        return 4
    _print_json(dataclasses.asdict(detail))
    return 0
```

- [ ] **Step 4: 跑测试,确认 PASS**

```bash
python -m unittest tests.test_archive_query_cli.TestCliArchives -v
```

- [ ] **Step 5: 跑全量回归 + Commit**

```bash
python -m unittest discover -s tests -p "test_*.py"
git add utils/archive_query.py tests/test_archive_query_cli.py
git commit -m "db: phase 1C - CLI archives list and show subcommands"
```

---

## Task 13: `revisions` + `audit` subcommands

**Files:**
- Modify: `utils/archive_query.py`(填 `_cmd_revisions_list` / `_cmd_audit_list`)
- Modify: `tests/test_archive_query_cli.py`(append `TestCliRevisionsAudit`)

- [ ] **Step 1: 写失败测试**

```python
@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestCliRevisionsAudit(unittest.TestCase):
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

    def _run(self, argv):
        parser = cli._build_parser()
        try:
            args = parser.parse_args(argv)
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 2, "", ""
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                with self.Session() as session:
                    rc = args.func(args, session)
        except ValueError as exc:
            stderr.write(f"error: {exc}\n")
            rc = 2
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_revisions_list_returns_three(self):
        archive_id = self.ids["archive_ids"][5]
        rc, out, _ = self._run([
            "revisions", "list", "--archive-id", str(archive_id),
        ])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["total"], 3)

    def test_revisions_list_archive_without_revisions_returns_empty(self):
        archive_id = self.ids["archive_ids"][0]
        rc, out, _ = self._run([
            "revisions", "list", "--archive-id", str(archive_id),
        ])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["total"], 0)

    def test_audit_list_for_archive(self):
        archive_id = self.ids["archive_ids"][5]
        rc, out, _ = self._run([
            "audit", "list", "--target-type", "archive",
            "--target-id", str(archive_id),
        ])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["action"], "force_rerun_rules")

    def test_audit_list_unknown_target_type_returns_2(self):
        rc, _, err = self._run([
            "audit", "list", "--target-type", "user", "--target-id", "1",
        ])
        self.assertEqual(rc, 2)
        self.assertIn("unknown target_type", err)
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
python -m unittest tests.test_archive_query_cli.TestCliRevisionsAudit -v
```

- [ ] **Step 3: 实现**

替换两个占位:

```python
def _cmd_revisions_list(args, session) -> int:
    from infrastructure.db import queries
    result = queries.list_revisions(
        session,
        archive_id=args.archive_id,
        page=args.page,
        page_size=args.page_size,
    )
    _print_json(_list_result_to_dict(result))
    return 0


def _cmd_audit_list(args, session) -> int:
    from infrastructure.db import queries
    result = queries.list_audit_logs(
        session,
        target_type=args.target_type,
        target_id=args.target_id,
        page=args.page,
        page_size=args.page_size,
    )
    _print_json(_list_result_to_dict(result))
    return 0
```

- [ ] **Step 4: 跑测试,确认 PASS**

```bash
python -m unittest tests.test_archive_query_cli.TestCliRevisionsAudit -v
```

- [ ] **Step 5: 跑全量回归 + Commit**

```bash
python -m unittest discover -s tests -p "test_*.py"
git add utils/archive_query.py tests/test_archive_query_cli.py
git commit -m "db: phase 1C - CLI revisions and audit subcommands"
```

---

## Task 14: 数据契约 §9 文档增补

**Files:**
- Modify: `docs/postgresql_data_contract_design.md`(在 §8 之后、§10 之前插入 §9)

注:数据契约文档当前章节编号是 §1-§8、§10-§12。§9 是 spec 留出的空位。

- [ ] **Step 1: 阅读现有数据契约 §8 与 §10 之间的边界,确认插入位置**

```bash
grep -n "^## " docs/postgresql_data_contract_design.md
```

预期输出大致包含:
```
... ## 8 查询与索引
... ## 10 分阶段落地
```

- [ ] **Step 2: 在 §8 之后插入 §9 整段**

使用 Edit,把 `## 10 分阶段落地` 上方插入以下内容(注意保留两个章节之间的空行):

```markdown
## 9 读侧 API 契约

阶段 1C 落地的只读查询 API,供 CLI 与未来 Web 后台共用。代码见 `infrastructure/db/queries.py`,完整设计见 `docs/superpowers/specs/2026-05-04-phase-1c-readside-queries-design.md`。

### 9.1 查询函数清单

| 函数 | 返回类型 | 用途 |
| --- | --- | --- |
| `list_batches(session, *, project_key, status_filter=None, page=1, page_size=50)` | `ListResult[BatchSummary]` | 项目下批次列表 |
| `get_batch_detail(session, *, batch_id)` | `Optional[BatchDetail]` | 批次详情(含 `failure_breakdown`、schema 三件套) |
| `list_archives(session, *, batch_id, filter=None, page=1, page_size=50)` | `ListResult[ArchiveSummary]` | 批次下档案列表(支持 12 字段过滤) |
| `get_archive_detail(session, *, archive_id)` | `Optional[ArchiveDetail]` | 档案详情(含三快照、LLM trace、pages 列表) |
| `list_revisions(session, *, archive_id, page=1, page_size=50)` | `ListResult[RevisionRow]` | 档案修正记录 |
| `list_audit_logs(session, *, target_type, target_id, page=1, page_size=50)` | `ListResult[AuditLogRow]` | 审计日志(`target_type` 一期白名单 `{"archive"}`) |

所有函数 keyword-only,`session` 由调用方控制生命周期,内部不 commit。

### 9.2 ArchiveFilter 字段

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `archive_year` | `Optional[int]` | 等值;内部转 `str` 与 `String(8)` 列对齐 |
| `classification_code` | `Optional[Iterable[str]]` | IN |
| `retention_period` | `Optional[Iterable[str]]` | IN |
| `openness_status` | `Optional[str]` | 等值 |
| `processing_status` | `Optional[Iterable[str]]` | IN |
| `review_status` | `Optional[Iterable[str]]` | IN |
| `correction_status` | `Optional[str]` | 等值 |
| `archive_no` | `Optional[str]` | 等值 |
| `item_no` | `Optional[str]` | 等值 |
| `title_like` | `Optional[str]` | `ILIKE %x%` |
| `responsible_party_like` | `Optional[str]` | `ILIKE %x%` |
| `error_code` | `Optional[Iterable[str]]` | IN |

等价规则:`None` 不附加条件;`Iterable` 字段空集等价于 `None`;`*_like` 字段空字符串等价于 `None`。

### 9.3 默认排序

| 函数 | ORDER BY |
| --- | --- |
| `list_batches` | `started_at DESC NULLS LAST, id DESC` |
| `list_archives` | `archive_no ASC NULLS LAST, item_no ASC NULLS LAST, id ASC` |
| `list_revisions` | `revision_no DESC, id DESC` |
| `list_audit_logs` | `created_at DESC, id DESC` |

`id` 作 tiebreaker,保证分页边界稳定。一期不暴露 `sort_by` 参数。

### 9.4 错误语义

| 触发 | 行为 |
| --- | --- |
| `get_*_detail` 找不到 | 返回 `None` |
| `list_*` 无结果 | 返回 `ListResult(items=[], total=0, ..., has_next=False)` |
| `page < 1` 或 `page_size ∉ [1, 200]` | 抛 `ValueError` |
| `list_audit_logs(target_type ∉ {"archive"})` | 抛 `ValueError` |
| 未知 `project_key` / `batch_id` / `archive_id` | 视情况返回空集或 `None`;不抛 |
| `SQLAlchemyError` 子类(连接失败、SQL 错) | 原样向上抛,queries 层不吞错 |

`queries.py` 与 `repositories.py` 错误处理一致:都让异常上抛。区别在于写侧外层有 `BatchRecorder` 统一吞错保护管线热路径,读侧无此保护(读路径出错时"返回空集"和"DB 故障"在调用方的语义截然不同,不能合并)。

### 9.5 分页约束

- `page` ∈ `[1, +∞)`
- `page_size` ∈ `[1, 200]`
- `total` 始终返回(单次 `SELECT COUNT(*)`,索引下廉价)
- `has_next = page < ceil(total / page_size)`;`page` 越界返回空集 + `has_next=False`,`total` 不变
- 一期使用 limit/offset;阶段 4 数据量上来时再评估 cursor

### 9.6 target_type 白名单

一期 `{"archive"}`,与 `audit_logs` 当前实际写入 target 一致(`apply_force_rerun_rules`)。阶段 3 启用账户体系时扩展为 `{"archive", "batch", "project", "user", "role"}`,届时同步更新本节。
```

- [ ] **Step 3: 验证插入后的章节结构**

```bash
grep -n "^## " docs/postgresql_data_contract_design.md
```

预期 §9 在 §8 与 §10 之间出现,编号连续。

- [ ] **Step 4: 跑全量回归**

```bash
python -m unittest discover -s tests -p "test_*.py"
```

(纯文档变更,不影响代码;但跑一次确认无人误改 .py。)

- [ ] **Step 5: Commit**

```bash
git add docs/postgresql_data_contract_design.md
git commit -m "docs: add phase 1C readside API contract to data contract section 9"
```

---

## Self-Review Checklist(plan 写完后我已自查的项目)

- [x] **Spec coverage**:Spec §1-§12 每条都有对应 Task。§3 dataclasses → Task 1;§4 函数签名 → Task 4-9;§5 排序 → Task 4/6/8/9;§6 校验 → Task 2(`_validate_pagination`)+ Task 9(`target_type`);§7 错误处理 → Task 4-9 + Task 10;§8 CLI → Task 10-13;§9 测试 → Task 1-13;§10 文档 → Task 14;§11 实施顺序与本 plan 完全对应;§12 已知约束在实施时按描述兑现(`archive_year` 类型不对称已在 implementation notes 顶部标出)。

- [x] **Placeholder scan**:无 TBD/TODO/...,所有代码块包含完整可运行代码。

- [x] **Type consistency**:所有 dataclass 字段名与 ORM 列名严格对齐(`models.py` 已读取确认);`archive_year` 类型不对称(filter 用 int、Summary/Detail 用 str)已在 Task 6 显式 `str(value)` 转换;函数返回类型与 §3 / §4 一致;subparser arg `dest` 与 ArchiveFilter 字段名同名,确保 `_cmd_archives_list` 的 `args.X` 直接映射。

- [x] **Test coverage matrix**:每个 query 函数 ≥ 6 类 spec §9.2 用例(空集/单条/多条/过滤命中/过滤不命中/分页边界);ArchiveFilter 12 字段每个有专属测试;CLI 每个 subcommand 至少 1 happy + 1 error path;不可达分支(`make_engine` 抛非空 ValueError、`check_connectivity` 失败)依赖 mock,留给执行期视情况补,不阻塞 plan 验收。

---

# Execution Handoff

Plan 已完成。根据 brainstorming → writing-plans 流程,接下来要选执行模式。

**两个执行选项:**

1. **Subagent-Driven(skill 推荐)** - 我每个 Task dispatch 一个新 subagent,subagent 在隔离上下文里完成单个 Task,我在每两个 Task 之间 review 产出。优点:快速迭代、上下文不爆;缺点:subagent 间 context 不共享,需要 plan 自包含(本 plan 已严格自包含)。

2. **Inline Execution** - 我在本会话内直接跑 `superpowers:executing-plans`,batch 执行 Task 并在 checkpoint 让你 review。优点:你能看到每一步;缺点:本会话 context 会被代码与测试输出大量占用。

你选哪个?
