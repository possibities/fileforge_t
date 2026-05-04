# 阶段 1C 设计:读侧查询接口与 CLI

## 1 目标与背景

为 `infrastructure/db` 增加只读查询层与命令行入口。本期保持只读、不引入 HTTP 框架、不改动现有写路径,作为后续 Web 管理后台(数据契约 §10 阶段 3)的数据接入边界。

阶段 1A / 1B 已经把项目、批次、档案、页面、任务、修正、审计、导出全部表结构落地;`infrastructure/db/repositories.py` 提供写侧仓储函数;`BatchRecorder` 在批处理热路径上吞错保护管线。1C 是写路径的镜像:把"只读"那一面以同样的颗粒度暴露出来,供 CLI 排查与未来 Web API view 直接调用。

设计依据见 `docs/postgresql_data_contract_design.md`(数据契约)与 `docs/postgresql_integration_architecture.md`(架构评估)。

## 2 范围

### 2.1 新增/修改文件

| 文件 | 类型 | 用途 |
| --- | --- | --- |
| `infrastructure/db/queries.py` | 新增 | 6 个只读查询函数 + 配套 dataclass |
| `utils/archive_query.py` | 新增 | 单入口 CLI(argparse subparser) |
| `tests/test_db_queries.py` | 新增 | queries.py SQLite 单测 |
| `tests/test_archive_query_cli.py` | 新增 | CLI 端到端单测(in-process dispatch) |
| `docs/postgresql_data_contract_design.md` | 修改 | §9 增"读侧 API 契约"小节 |

### 2.2 不在范围

- HTTP 框架接入(FastAPI / Flask 决策属阶段 3)
- 单位 / 用户 / 角色 / 权限相关查询(阶段 3)
- 修改 `main.py` / `processors/batch_processor.py` / `core/classifier.py` / `infrastructure/db/recorder.py` / `infrastructure/db/repositories.py`
- replay 工具(阶段 1D)与批次摘要 DB 化校验(阶段 1E)
- 页面 OCR 全文检索(阶段 4)
- 暴露 `sort_by` / `include_total` / cursor 分页等参数(YAGNI)

## 3 数据类型

全部为 `@dataclass(frozen=True)`,定义在 `infrastructure/db/queries.py` 顶部。字段一律英文标识符,中文 metadata key 仅在 `final_metadata` / `rules_metadata` / `llm_metadata` 三个 dict 内部出现(数据契约 §2.3 不变)。时间戳使用 `datetime`,JSONB 使用 `dict` / `Any`,字符串列保持 `str`。

### 3.1 `ListResult[T]`

```python
T = TypeVar("T")

@dataclass(frozen=True)
class ListResult(Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    has_next: bool
```

### 3.2 `ArchiveFilter`

```python
@dataclass(frozen=True)
class ArchiveFilter:
    archive_year: Optional[int] = None
    classification_code: Optional[Iterable[str]] = None     # IN
    retention_period: Optional[Iterable[str]] = None        # IN
    openness_status: Optional[str] = None
    processing_status: Optional[Iterable[str]] = None       # IN
    review_status: Optional[Iterable[str]] = None           # IN
    correction_status: Optional[str] = None
    archive_no: Optional[str] = None                        # 等值
    item_no: Optional[str] = None                           # 等值
    title_like: Optional[str] = None                        # ILIKE %x%
    responsible_party_like: Optional[str] = None            # ILIKE %x%
    error_code: Optional[Iterable[str]] = None              # IN
```

约定:
- 字段值为 `None` 时不附加 SQL 条件
- `Iterable` 字段为空集(`[]` / `()` / `set()`)等价于 `None`
- `*_like` 字段为空字符串等价于 `None`
- 迭代型字段在 SQL 层折叠为 `IN (...)`;长度为 1 时也走 IN(不优化为等值,SQLAlchemy 自动处理)

### 3.3 `BatchSummary`

`processing_batches` 列表态:`id, project_id, batch_key, batch_name, input_dir, output_dir, batch_status, started_at, finished_at, total_archives, total_pages, success_count, fail_count, summary_schema_version, created_at, updated_at`。

不含 `failure_breakdown` JSONB(列表态不展示)、不含 `summary_schema_ref` / `summary_changelog_ref`(详情字段)。

### 3.4 `BatchDetail`

`BatchSummary` 全字段 + `failure_breakdown: dict[str, int]` + `summary_schema_ref: Optional[str]` + `summary_changelog_ref: Optional[str]`。

### 3.5 `ArchivePage`

`archive_pages` 全字段映射:`id, page_no, image_path, image_name, file_hash, file_size, ocr_text, ocr_avg_confidence, ocr_low_conf_count, ocr_variant, created_at`。`archive_id` 不出现,因为该 dataclass 总是嵌套在 `ArchiveDetail.pages` 中,父档案已知。

### 3.6 `ArchiveSummary`

`archive_records` 列表态精简版,共 27 字段:

- 标识(6):`id, project_id, batch_id, archive_key, archive_name, page_count`
- 状态三元组(3):`processing_status, review_status, correction_status`
- 错误(2):`error_code, error_message`(不含 `traceback_text`)
- 高频冗余列(13):`archive_year, classification_code, classification_name, retention_period, retention_period_code, responsible_party, document_number, title, document_date, openness_status, archive_no, item_no, fonds_unit_name`
- 时间戳(3):`processed_time, created_at, updated_at`

不含三快照、LLM trace 三列、pages、image_files / image_names、低频列。

### 3.7 `ArchiveDetail`

`ArchiveSummary` 27 字段 + 以下 18 字段(含 `pages`),共 45 字段:

- 标识/路径补全(6):`archive_folder_name, source_folder, image_files, image_names, result_filename, traceback_text`
- 低频冗余列(5):`category_code, security_level, secret_period, openness_delay_reason, digitized_time`
- JSONB 三快照(3):`llm_metadata: Optional[dict], rules_metadata: Optional[dict], final_metadata: Optional[dict]`
- LLM trace(3):`llm_raw_response: Optional[str], llm_cleaned_response: Optional[str], llm_parse_strategy: Optional[str]`
- 页面列表(1):`pages: list[ArchivePage]`(按 `page_no ASC` 排序)

### 3.8 `RevisionRow`

`metadata_revisions` 全字段映射:`id, archive_id, revision_no, field_key, field_column, old_value, new_value, reason, created_by, created_at`。

`old_value` / `new_value` 字段类型为 `Any`(JSONB 可能是 str / int / None / dict / list)。

### 3.9 `AuditLogRow`

`audit_logs` 全字段映射:`id, actor_user_id, action, target_type, target_id, before_data, after_data, ip_address, user_agent, created_at`。

`before_data` / `after_data` 字段类型为 `Any`。

## 4 函数签名

```python
def list_batches(
    session: Session, *,
    project_key: str,
    status_filter: Optional[Iterable[str]] = None,
    page: int = 1,
    page_size: int = 50,
) -> ListResult[BatchSummary]: ...

def get_batch_detail(
    session: Session, *,
    batch_id: int,
) -> Optional[BatchDetail]: ...

def list_archives(
    session: Session, *,
    batch_id: int,
    filter: Optional[ArchiveFilter] = None,
    page: int = 1,
    page_size: int = 50,
) -> ListResult[ArchiveSummary]: ...

def get_archive_detail(
    session: Session, *,
    archive_id: int,
) -> Optional[ArchiveDetail]: ...

def list_revisions(
    session: Session, *,
    archive_id: int,
    page: int = 1,
    page_size: int = 50,
) -> ListResult[RevisionRow]: ...

def list_audit_logs(
    session: Session, *,
    target_type: str,
    target_id: int,
    page: int = 1,
    page_size: int = 50,
) -> ListResult[AuditLogRow]: ...
```

签名约定:
- 第一参数 `session: Session`,与 `repositories.py` 写侧风格对称
- 其余参数 keyword-only(强制 `*` 分隔)
- 不做 `session.commit()`(只读查询无副作用)
- 不打开新 session 也不关闭 session(生命周期由调用方控制)

## 5 排序契约

| 函数 | 默认 ORDER BY |
| --- | --- |
| `list_batches` | `started_at DESC NULLS LAST, id DESC` |
| `list_archives` | `archive_no ASC NULLS LAST, item_no ASC NULLS LAST, id ASC` |
| `list_revisions` | `revision_no DESC, id DESC` |
| `list_audit_logs` | `created_at DESC, id DESC` |

`id` 作为 tiebreaker,保证分页边界稳定;一期不暴露 `sort_by` 参数(可在后续阶段以白名单字段方式加入)。

## 6 校验规则

| 触发条件 | 行为 |
| --- | --- |
| `page < 1` | `raise ValueError("page must be >= 1")` |
| `page_size < 1` 或 `page_size > 200` | `raise ValueError("page_size must be in [1, 200]")` |
| `list_audit_logs` 的 `target_type ∉ {"archive"}` | `raise ValueError(f"unknown target_type={target_type!r}; allowed: ['archive']")` |
| `project_key` 不存在 | `list_batches` 返回空 `ListResult`(JOIN 自然为空,不额外校验) |
| `batch_id` / `archive_id` 不存在 | `get_*_detail` 返回 `None`;`list_*` 返回空 `ListResult` |

## 7 错误处理

- `get_*_detail` 找不到 → 返回 `None`
- `list_*` 无结果 → 返回 `ListResult(items=[], total=0, page=page, page_size=page_size, has_next=False)`
- `SQLAlchemyError` 子类(连接失败、SQL 错、IntegrityError 等)由 queries.py **原样向上抛**,不在查询层 try/except
- 与 `repositories.py` 写侧对称:repositories 同样让异常抛,但写路径外层有 `BatchRecorder` 统一吞错以保护管线热路径;查询路径无热路径保护语义,出错就让 CLI / 未来 API view 处理

## 8 CLI:`utils/archive_query.py`

### 8.1 命令矩阵

```bash
python -m utils.archive_query batches list   --project-key K [--status running] [--page 1 --page-size 50]
python -m utils.archive_query batches show   --batch-id ID
python -m utils.archive_query archives list  --batch-id ID [filter args] [--page N --page-size M]
python -m utils.archive_query archives show  --archive-id ID
python -m utils.archive_query revisions list --archive-id ID [--page N --page-size M]
python -m utils.archive_query audit list     --target-type archive --target-id ID [--page N --page-size M]
```

### 8.2 `archives list` 过滤参数

```
--archive-year YYYY
--classification-code CODE        (可重复;映射为 ArchiveFilter.classification_code 的 list)
--retention-period PERIOD         (可重复)
--openness-status STATUS
--processing-status STATUS        (可重复)
--review-status STATUS            (可重复)
--correction-status STATUS
--archive-no STR
--item-no STR
--title-like STR
--responsible-party-like STR
--error-code CODE                 (可重复)
```

可重复字段使用 argparse `action="append"`,空 list 等价于不过滤。

### 8.3 输出

- `list_*`:stdout 写 `json.dumps({"items": [...], "total": N, "page": N, "page_size": N, "has_next": bool}, ensure_ascii=False, indent=2, default=str)`
- `show_*`:stdout 写 `json.dumps(dataclasses.asdict(detail), ensure_ascii=False, indent=2, default=str)`
- `get_*_detail` 返回 `None` 时 stderr 写 `not found: <resource> id=<id>`,退出码 4
- `default=str` 兜底序列化 `datetime`(转 ISO 字符串);dict / list 透传

### 8.4 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 2 | 参数缺失/非法(含 `DATABASE_URL` 空、`page_size` 越界、未知 `target_type`、subcommand 缺失或拼写错误) |
| 3 | 数据库连接失败(`check_connectivity` 失败、DSN 指向不可达数据库) |
| 4 | 资源不存在(get_*_detail 返回 None) |
| 9 | 其他未分类异常 |

### 8.5 实现风格

- 沿用 `utils/force_rerun_cli.py` 的"延迟 import 数据库依赖 + `main()` 设置 logging"模式,确保未安装 SQLAlchemy 的环境 import 阶段不会崩
- engine + session_factory 在 `run()` 内创建,`run()` 退出前调 `dispose_engine`
- session 用 `with session_factory() as session:` 块包裹查询调用
- 不做 commit(查询路径无副作用)
- subparser dispatch 通过 `func` attribute 映射到子命令处理函数,不写 if/elif 链

## 9 测试策略

### 9.1 fixture

- 沿用 `tests/test_db_*.py` 已有的 SQLite in-memory engine fixture 模式(参见 `tests/test_db_recorder.py`、`tests/test_db_repositories.py`)
- 新增 `_seed_query_fixtures(session)` helper,落入:
  - 1 个项目 `proj_test`
  - 2 个批次(1 个 completed、1 个 running)
  - 6 个档案,覆盖 `processing_status` 五值各一个 + 一个补足分类/年度组合,覆盖不同 `review_status` / `correction_status` / `error_code` / `title` / `responsible_party`
  - 每个档案 1-3 个 pages
  - 至少 1 个档案有 2 次 revision(共 3 行 metadata_revisions)
  - 至少 1 条 audit_logs(target_type="archive")
- 所有 query 用例共享此 seed,通过细分 batch / archive id 做不同场景断言

### 9.2 用例矩阵

每个 query 函数最少覆盖 6 类:

| 用例 | 期望 |
| --- | --- |
| 空集 | 返回空 `ListResult` 或 `None` |
| 单条 | 返回 1 条 |
| 多条 | 返回 N 条且按默认排序契约(§5)|
| 过滤命中 | 返回过滤后子集 |
| 过滤不命中 | 返回空集 |
| 分页边界 | `page` 末页 `has_next=False`;`page` 越界返回空集 + `has_next=False`;`total` 不变 |

### 9.3 校验类用例

- `page=0` / `page=-1` → `ValueError`
- `page_size=0` / `page_size=201` → `ValueError`
- `list_audit_logs(target_type="user")` → `ValueError`
- `ArchiveFilter` 空迭代字段(`classification_code=[]`)与 `None` 行为等价
- `ArchiveFilter` 空字符串 `*_like`(`title_like=""`)与 `None` 行为等价

### 9.4 CLI 用例

- in-process dispatch:`from utils.archive_query import run; assert run(["batches", "list", "--project-key", "proj_test"]) == 0`
- capsys 捕获 stdout,断言 JSON 结构与字段
- 每个 subcommand 至少 1 个 happy path + 1 个错误路径(资源不存在 → 退出码 4;参数非法 → 退出码 2)
- `DATABASE_URL` 空时 → 退出码 2,stderr 写 `DATABASE_URL not set`
- 不走 subprocess(慢且难调试)

### 9.5 不测的部分

- PostgreSQL 特定行为(JSONB GIN、partial unique index、ILIKE 中文 trigram)—— 沿用现有 dialect-guarded 测试路径
- 真实 vLLM / OCR 数据 —— 1C 是只读查询,与推理链路解耦

## 10 数据契约 §9 增补

新增"读侧 API 契约"小节,内容:

1. 6 个 query 函数签名表
2. `ArchiveFilter` 字段表(12 字段 + 类型 + 语义 + 等价规则)
3. 排序契约表(每个 list_ 默认 ORDER BY)
4. 错误语义表(`None` vs 空集 vs raise)
5. 分页约束(`page_size ∈ [1, 200]`、`page < 1` 抛)
6. `target_type` 一期白名单 `{"archive"}`,后续阶段扩展时同步更新本表

不复述 dataclass 全字段,只列**契约级**约束;实际字段以 `infrastructure/db/queries.py` 为准。

## 11 实施顺序

1. queries.py 顶部增加 dataclass 类型(`ListResult`、`ArchiveFilter`、6 个 row 类型),无 SQL,可独立 review
2. `_paginate(query, page, page_size)` helper + `_build_list_result(items, total, page, page_size)` 工厂,集中分页逻辑
3. 6 个 query 函数,逐个实现 + 单测(逻辑独立,可拆 6 个内部小步,但建议在同一 commit 内交付)
4. `archive_query.py` CLI + subparser dispatch + 单测
5. 数据契约 §9 文档增补

预计总规模:代码 ~700 行 + 测试 ~700 行 + 文档 ~80 行,合计 ~1.5k 行,1 个 commit。

## 12 已知约束与权衡

### 12.1 与 repositories.py 的不对称

- 写侧 `repositories.py` 让 `SQLAlchemyError` 抛,`BatchRecorder` 在外层吞错保护管线热路径
- 读侧 `queries.py` 同样让 `SQLAlchemyError` 抛,但**没有外层吞错**,直接抛到 CLI / 未来 API view
- 取舍:读路径出错时"返回空集"和"DB 故障"在调用方语义截然不同,不能用 `None` / 空集隐藏故障;CLI 把异常映射为退出码,Web API view 自己决定 5xx

### 12.2 LIKE 性能

- `title_like` / `responsible_party_like` 一期裸 LIKE 走 seq scan
- 单批次 < 1 万行规模可接受;阶段 4 上 trigram 不影响 API 形状(filter 字段不变,只是底层索引改),可作为后续无感优化

### 12.3 ListResult 泛型

- 使用 `Generic[T]` 泛型 dataclass,Python 3.10+ 可用(`from typing import Generic, TypeVar`)
- 6 个具体 list_ 函数返回 `ListResult[BatchSummary]` 等具体化类型
- 退路:若阶段 3 接 Pydantic / FastAPI 时发现泛型 dataclass 序列化不顺(Pydantic v1 已知对 generic dataclass 支持有限),或 mypy 类型推断在调用方报错,可改为 6 个非泛型类(`BatchListResult` / `ArchiveListResult` / ...);形状不变,仅类型注解换

### 12.4 target_type 白名单

- 一期仅 `{"archive"}`,与 `audit_logs` 当前实际写入的 target_type 一致(`apply_force_rerun_rules` 写 `target_type="archive"`)
- 阶段 3 启用账户体系时,扩为 `{"archive", "batch", "project", "user", "role"}`,同步更新数据契约 §9
- 不引入 Enum,保持字符串约定与数据契约 §4.7 对齐
- 调用方传未知值快速失败,避免 audit 漏检

### 12.5 不做的事

- 不暴露 `sort_by` 参数(YAGNI;真要时按白名单字段加成本极低)
- 不暴露 `include_total` 参数(总数始终返回,索引下廉价;一期数据规模无 COUNT 慢查询风险)
- 不做 cursor-based 分页(单批次 < 1 万行,offset 够用)
- 不在 queries.py 内打开 / 关闭 engine(由 CLI 或未来 API view 负责)
- 不做 CLI 表格美化输出(`--pretty`),输出全部 JSON
