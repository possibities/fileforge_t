# PostgreSQL 数据契约与落地设计

## 1. 目标

本文档补充 `docs/postgresql_integration_architecture.md`，用于把 PostgreSQL 集成从架构评估推进到可实施的数据契约设计。

核心目标：

- PostgreSQL 作为业务状态库、后台查询库、修正记录库和审计库。
- 保留当前文件批处理管线，不用数据库替代 `input_documents/`、`output_results/`、单档案 JSON、`batch_summary.json` 和汇总 JSON/CSV。
- 以当前代码契约为基线，避免引入和现有字段、状态、导出格式不兼容的数据库模型。
- 一期以旁路写库、批次追踪、档案查询、人工修正和审计为主，不一次性引入任务队列、全文搜索引擎或对象存储迁移。

非目标：

- 不把原始扫描图片写入 PostgreSQL。
- 不在一期重构 OCR、LLM、规则引擎和导出主流程。
- 不用 PostgreSQL 直接替代现有 JSON/CSV 交付物。
- 不在本设计中绑定具体 Web 框架、ORM 或权限中间件。

## 2. 现有契约基线

### 2.1 批处理结果状态

当前 `BatchProcessor` 对单件档案只定义三种处理状态：

- `success`
- `failed`
- `error`

`batch_summary.json` schema 已把这三种值作为结果状态契约。数据库设计不得直接把复核状态、修正状态、任务排队状态混入同一个字段，否则会破坏现有统计和导出语义。

### 2.2 单件结果结构

当前每条结果至少包含：

- `archive_name`
- `source_folder`
- `page_count`
- `image_files`
- `image_names`
- `processed_time`
- `metadata`
- `status`
- `error_code`
- `error_message`
- `error`
- `traceback`，仅 `error` 状态需要

数据库应能保存或还原这些字段。至少需要保证 `batch_summary.json` 仍可由文件产物或数据库结果重新生成。

### 2.3 metadata 字段

当前 metadata 的权威字段来自 `constants.METADATA_SCHEMA` 和 `config/exporter.json`。核心字段是中文 key：

| metadata key | 数据库列建议 | 说明 |
| --- | --- | --- |
| `门类` | `category_code` | 档案门类代码 |
| `归档年度` | `archive_year` | 用于查询、统计、件号分组 |
| `实体分类号` | `classification_code` | 新旧编码均保留原值 |
| `实体分类名称` | `classification_name` | 党群类、综合类、业务类 |
| `保管期限` | `retention_period` | 永久、30年、10年、长期、短期 |
| `责任者` | `responsible_party` | 发文单位或责任人 |
| `文件编号` | `document_number` | 原始文号 |
| `题名` | `title` | 规则清洗后的题名 |
| `文件形成时间` | `document_date` | 当前格式为 `YYYYMMDD` 字符串 |
| `密级` | `security_level` | 非涉密、内部、秘密、机密、绝密或空 |
| `保密期限` | `secret_period` | 1年、5年、10年或空 |
| `备注` | `notes` | 包含待核查提示 |
| `开放状态` | `openness_status` | 开放、控制 |
| `延期开放理由` | `openness_delay_reason` | 工作秘密、商业秘密、个人隐私、负面信息 |
| `立档单位名称` | `fonds_unit_name` | 缺失时规则会用责任者补齐 |
| `数字化时间` | `digitized_time` | 当前来自首张图片文件属性 |
| `页数` | `page_count` | 批处理阶段追加 |
| `档号` | `archive_no` | 件号生成后追加 |
| `件号` | `item_no` | 件号生成后追加，当前为 4 位字符串 |
| `档案文件夹` | `archive_folder_name` | 分类器阶段追加 |
| `source_folder` | `source_folder` | 批处理阶段追加 |
| `processed_time` | `processed_time` | 当前使用首张图片 mtime |

数据库可以用英文列支持高频查询，但必须保留完整中文 metadata JSONB 快照，避免导出字段和历史 JSON 兼容性丢失。

## 3. 状态拆分

一期建议拆成三个独立状态字段。

### 3.1 处理状态

字段：`processing_status`

取值：

- `pending`
- `running`
- `success`
- `failed`
- `error`

含义：

- `success/failed/error` 与当前 `BatchProcessor` 结果状态保持一致。
- `pending/running` 只用于数据库任务生命周期，不写入现有 `batch_summary.json` 的结果状态。

### 3.2 复核状态

字段：`review_status`

取值：

- `not_required`
- `needs_review`
- `in_review`
- `confirmed`

含义：

- 规则或二次 LLM 失败产生的待核查提示应映射为 `needs_review`。
- 人工确认后改为 `confirmed`。

### 3.3 修正状态

字段：`correction_status`

取值：

- `none`
- `corrected`

含义：

- 只表示是否发生过人工修正。
- 具体修正内容必须写入 append-only 的修正记录表。

## 4. 数据模型

以下是逻辑模型，不绑定具体 ORM。所有表建议包含 `created_at` 和 `updated_at`，时间字段使用带时区时间类型。

### 4.1 用户与权限

表：

- `app_users`
- `roles`
- `permissions`
- `user_roles`
- `role_permissions`

建议约束：

- `app_users.username` 唯一。
- `roles.code` 唯一。
- `permissions.code` 唯一。
- `user_roles(user_id, role_id)` 唯一。
- `role_permissions(role_id, permission_id)` 唯一。

一期权限粒度：

- `archive:view`
- `archive:correct`
- `archive:confirm`
- `archive:export`
- `batch:manage`
- `user:manage`
- `audit:view`

### 4.2 批次表

表：`processing_batches`

核心字段：

- `id`
- `batch_key`
- `batch_name`
- `input_dir`
- `output_dir`
- `processing_status`
- `started_at`
- `finished_at`
- `total_archives`
- `total_pages`
- `success_count`
- `fail_count`
- `failure_breakdown`
- `summary_schema_version`
- `summary_schema_ref`
- `summary_changelog_ref`
- `created_by`
- `created_at`
- `updated_at`

约束：

- `batch_key` 唯一，用于幂等重跑。
- `failure_breakdown` 使用 JSONB，保存当前 `error_code -> count` 映射。

`batch_key` 建议由 `input_dir`、批次启动时间或用户指定批次名生成。若业务需要“同一输入目录重跑覆盖同一批次”，应显式传入固定 `batch_key`。

### 4.3 档案表

表：`archive_records`

核心字段：

- `id`
- `batch_id`
- `archive_key`
- `archive_name`
- `archive_folder_name`
- `source_folder`
- `page_count`
- `image_files`
- `image_names`
- `processing_status`
- `review_status`
- `correction_status`
- `error_code`
- `error_message`
- `traceback_text`
- `processed_time`
- `created_at`
- `updated_at`

冗余查询列：

- `category_code`
- `archive_year`
- `classification_code`
- `classification_name`
- `retention_period`
- `retention_period_code`
- `responsible_party`
- `document_number`
- `title`
- `document_date`
- `security_level`
- `secret_period`
- `openness_status`
- `openness_delay_reason`
- `fonds_unit_name`
- `digitized_time`
- `archive_no`
- `item_no`

JSONB 快照列：

- `llm_metadata`
- `rules_metadata`
- `final_metadata`

默认约束：

- `archive_records(batch_id, archive_key)` 唯一。
- `archive_no` 按全库唯一设计；若业务确认档号只需在全宗或其他范围内唯一，则改为对应范围的组合唯一约束。
- 若历史数据存在空档号或重复档号，应先允许为空并在确认期后收紧约束。
- `processing_status`、`review_status`、`correction_status` 使用枚举或 check constraint。

`archive_key` 建议优先使用相对输入目录的档案路径；如果同名目录可能重复，应包含归一化后的相对路径和图片文件 hash 摘要。

### 4.4 页面表

表：`archive_pages`

核心字段：

- `id`
- `archive_id`
- `page_no`
- `image_path`
- `image_name`
- `file_hash`
- `file_size`
- `ocr_text`
- `ocr_avg_confidence`
- `ocr_low_conf_count`
- `ocr_variant`
- `created_at`

约束：

- `archive_pages(archive_id, page_no)` 唯一。
- `archive_pages(archive_id, image_path)` 唯一。

注意：当前 OCR 对外接口只返回多页合并文本。页面级 OCR 入库需要先把 OCR 客户端返回结构扩展为页面结果列表，或者在一期只保存 `image_path/image_name/file_hash`，暂缓 `ocr_text` 和置信度。

### 4.5 任务与重试

表：`processing_jobs`

核心字段：

- `id`
- `batch_id`
- `archive_id`
- `job_type`
- `processing_status`
- `attempt_count`
- `last_error_code`
- `last_error_message`
- `started_at`
- `finished_at`
- `created_at`
- `updated_at`

表：`processing_job_attempts`

核心字段：

- `id`
- `job_id`
- `attempt_no`
- `processing_status`
- `error_code`
- `error_message`
- `traceback_text`
- `started_at`
- `finished_at`
- `created_at`

说明：

- `processing_jobs` 保存当前任务状态。
- `processing_job_attempts` 追加保存每次尝试，避免只保留最后一次错误。
- 一期即使不引入独立任务队列，也应保存 attempt 记录，方便后台查看失败原因和重跑历史。

### 4.6 修正与审计

表：`metadata_revisions`

核心字段：

- `id`
- `archive_id`
- `revision_no`
- `field_key`
- `field_column`
- `old_value`
- `new_value`
- `reason`
- `created_by`
- `created_at`

约束：

- `metadata_revisions(archive_id, revision_no, field_key)` 唯一。

说明：

- `field_key` 保存中文 metadata key。
- `field_column` 保存对应英文列名。
- `old_value/new_value` 使用 JSONB，避免字符串化丢失类型。
- 每次人工保存可以产生多条字段级 revision。

表：`audit_logs`

核心字段：

- `id`
- `actor_user_id`
- `action`
- `target_type`
- `target_id`
- `before_data`
- `after_data`
- `ip_address`
- `user_agent`
- `created_at`

说明：

- 修正记录面向业务差异。
- 审计日志面向访问、导出、确认、权限变更等系统行为。
- 两者都应追加写入，不应覆盖。

### 4.7 导出记录

表：`export_files`

核心字段：

- `id`
- `batch_id`
- `export_type`
- `file_path`
- `template_name`
- `row_count`
- `file_hash`
- `created_by`
- `created_at`

说明：

- 文件本体仍保存在 `output_results/` 或后续对象存储。
- 数据库只保存路径、类型、行数、hash 和创建人。

### 4.8 件号计数

表：`sequence_counters`

核心字段：

- `id`
- `archive_year`
- `classification_code`
- `retention_period_code`
- `current_value`
- `created_at`
- `updated_at`

约束：

- `sequence_counters(archive_year, classification_code, retention_period_code)` 唯一。

说明：

- 当前代码的件号计数器是进程内内存状态，每次运行从 `0001` 开始。
- 引入历史批次后，件号生成必须改为数据库续号。
- 分配件号时应在事务中锁定对应 counter 行，递增后写入 `archive_records.item_no/archive_no`。
- `archive_records.archive_no` 默认建立全库唯一约束，作为最终防线；若业务范围不是全库唯一，应改为范围字段加 `archive_no` 的组合唯一约束。

## 5. metadata 快照策略

当前代码只有一个最终 metadata dict，且规则引擎会原地修改。为了满足“保存 LLM 输出、规则修正结果、人工确认结果”的需求，需要在实现时引入快照边界。

建议契约：

1. LLM 解析完成后保存 `llm_metadata`。
2. 规则引擎和题名二次重写完成后保存 `rules_metadata`。
3. 初次成功处理时，`final_metadata = rules_metadata`。
4. 人工修正时更新 `final_metadata`，并同步更新高频查询列。
5. 每次人工修正写入 `metadata_revisions` 和 `audit_logs`。

实现影响：

- `LlmClient.extract_metadata()` 如需保存原始 response，应返回包含 `raw_response`、`cleaned_response` 和 `metadata` 的结构，或由调用方增加单独记录钩子。
- `RulesEngine.apply_all()` 之前应复制 LLM metadata，避免原地修改导致原始结果丢失。
- 简报题名二次重写失败写入 `备注` 的待核查提示时，应同步设置 `review_status = needs_review`。

## 6. 写入流程

### 6.1 一期旁路写库流程

```text
创建或恢复 processing_batches
  -> 扫描目录
  -> 为每个档案 upsert archive_records 和 archive_pages 基础信息
  -> 创建 processing_jobs
  -> 执行 OCR / LLM / Rules / Sequence
  -> 写入 llm_metadata / rules_metadata / final_metadata
  -> 写入冗余查询列
  -> 更新 archive_records.processing_status
  -> 更新 processing_jobs 和 processing_job_attempts
  -> 继续生成单档案 JSON、batch_summary.json、汇总 JSON/CSV
  -> 写入 export_files
```

### 6.2 事务边界

建议按单件档案建立事务边界：

- 单件档案 metadata、状态、任务尝试、件号分配在一个事务中提交。
- 批次统计可以在单件完成后增量更新，也可以批次结束时从 `archive_records` 汇总回填。
- 文件导出失败不得回滚已成功处理的档案记录，但应写入批次级或导出级错误日志。

### 6.3 文件与数据库一致性

文件系统不是事务资源，因此一期采用“数据库记录真实处理状态，文件导出记录真实交付状态”的策略：

- 档案处理成功但导出失败：`archive_records.processing_status = success`，`export_files` 不写成功记录，批次或日志记录导出错误。
- 单档案 JSON 写出成功但 DB 写入失败：应标记批次异常并保留文件结果；后续可用文件重新补写 DB。
- DB 写入成功但单档案 JSON 写出失败：应允许从 DB 重新导出该档案结果。

## 7. 幂等与重跑

### 7.1 批次幂等

- 用户未指定 `batch_key` 时，每次运行创建新批次。
- 用户指定同一 `batch_key` 时，系统应恢复或重跑同一批次。
- 重跑策略必须显式选择：跳过已成功档案、只重跑失败档案、或全部重跑。

### 7.2 档案幂等

档案唯一性建议使用：

- `batch_id`
- `archive_key`

其中 `archive_key` 来自输入目录下的相对档案路径。若需要跨批次识别同一档案，可额外计算 `content_hash`，由图片路径、大小、mtime 或文件 hash 组合生成。

### 7.3 重试记录

每次重试都新增 `processing_job_attempts`，不要覆盖历史错误。`processing_jobs.last_error_*` 只作为当前状态摘要。

## 8. 查询与索引

### 8.1 结构化查询索引

建议索引：

- `processing_batches(processing_status, started_at)`
- `archive_records(batch_id, processing_status)`
- `archive_records(batch_id, review_status)`
- `archive_records(archive_year)`
- `archive_records(classification_code)`
- `archive_records(retention_period)`
- `archive_records(openness_status)`
- `archive_records(archive_no)`
- `archive_records(item_no)`
- `processing_jobs(batch_id, processing_status)`

### 8.2 JSONB 索引

建议：

- `archive_records USING GIN(llm_metadata)`
- `archive_records USING GIN(rules_metadata)`
- `archive_records USING GIN(final_metadata)`

JSONB 用于完整快照和低频字段查询，高频字段仍应冗余为独立列。

### 8.3 题名、责任者和 OCR 检索

普通 B-tree 索引不适合中文题名模糊查询。一期建议：

- 对 `title`、`responsible_party` 使用 `pg_trgm` trigram 索引支持 `LIKE/ILIKE` 或相似度检索。
- OCR 文本量可控时，对 `archive_pages.ocr_text` 建 trigram 索引。
- 如需更好的中文分词、相关性排序和复杂全文检索，再评估 PostgreSQL 中文分词扩展或 Elasticsearch/OpenSearch。

## 9. API 与导出兼容原则

数据库 API 可以返回英文字段，但导出仍必须使用当前中文字段顺序：

- 导出 JSON/CSV 继续以 `config/exporter.json` 为准。
- `final_metadata` 是人工修正后的最终导出来源。
- 若 `final_metadata` 为空但 `rules_metadata` 存在，导出可回退到 `rules_metadata`。
- 不应让后台展示字段名反向影响 LLM prompt 或 `METADATA_SCHEMA`。

## 10. 分阶段落地

### 阶段 1：数据契约和旁路入库

- 增加 `DATABASE_URL` 配置。
- 增加迁移工具。
- 建立批次、档案、页面基础信息、metadata 快照、任务、尝试、导出记录表。
- 当前文件输出保持不变。
- 成功档案写入数据库并支持基础查询。

### 阶段 2：人工修正与审计

- 增加用户、角色、权限表。
- 增加人工修正 API。
- 保存 `metadata_revisions`。
- 保存 `audit_logs`。
- 使用 `final_metadata` 作为最终确认结果。

### 阶段 3：件号数据库化

- 引入 `sequence_counters`。
- 件号分配改为数据库事务内续号。
- 按业务确认的范围建立 `archive_no` 唯一或组合唯一约束。
- 处理历史重复或空档号数据。

### 阶段 4：页面 OCR 和全文检索增强

- 扩展 OCR 返回结构，保存页面级 OCR 文本与置信度。
- 增加题名、责任者、OCR 文本检索索引。
- 根据规模评估 PostgreSQL 中文分词或外部搜索引擎。

### 阶段 5：后台任务队列

- 当后台提交、并发处理、取消、限流、定时重试成为刚需时，再引入 Celery、Redis、RabbitMQ 或其他任务系统。
- 队列系统复用 `processing_jobs` 和 `processing_job_attempts`，不重新定义状态模型。

## 11. 仍需业务确认的问题

以下问题不阻塞一期旁路入库设计，但会影响后续实现细节：

1. 同一输入目录重复运行时，默认创建新批次，还是复用同一个 `batch_key`？
2. `archive_no` 是否必须全库唯一，还是只在某个全宗或业务范围内唯一？
3. 人工修正后是否允许再次运行规则覆盖 `final_metadata`？
4. 页面级 OCR 文本是否有敏感信息保存限制？
5. Web 后台是否需要字段级权限，例如允许查看但不允许查看 OCR 原文？

## 12. 结论

PostgreSQL 集成应先从数据契约和旁路入库开始，而不是直接把现有批处理改造成数据库驱动流程。关键原则是：

1. 现有 `batch_summary.json` 和 JSON/CSV 导出契约不破坏。
2. 中文 metadata key 继续作为导出和 LLM 契约，数据库英文列只做查询冗余。
3. 处理状态、复核状态、修正状态必须拆分。
4. LLM 原始结果、规则结果、人工确认结果必须按快照保存。
5. 件号/档号在引入历史批次后必须数据库化，不能继续依赖进程内计数器。
6. 修正、审计和重试记录必须追加写入，不能只保存最后状态。
