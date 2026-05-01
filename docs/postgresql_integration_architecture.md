# PostgreSQL 集成架构评估与需求说明

同步说明：本文是 PostgreSQL 集成的架构评估与需求说明；可实施的数据契约、字段映射、状态拆分和落地边界以 `docs/postgresql_data_contract_design.md` 为准。本文已按该数据契约同步关键结论。

## 1. 背景

当前系统是档案智能分类批处理管线，运行流程为：

```text
OCR -> LLM -> Rules Engine -> Sequence Generator -> Export
```

现有实现以文件系统为核心输入输出：

- 输入：`input_documents/` 下的档案图片目录。
- 中间处理：OCR、LLM 抽取、规则修正、件号生成。
- 输出：`output_results/` 下的单档案 JSON、`batch_summary.json`、汇总 JSON/CSV。
- LLM：外部 vLLM OpenAI 兼容 HTTP 服务，本仓库只包含 OpenAI SDK 客户端。

截至本次评估，本地样例输入规模约为：

- 目录数：19
- 图片数：86
- 图片总量：约 50 MB

从当前样例规模看，单纯为了离线批处理和文件导出，不需要数据库。但后续需求已经超出纯文件批处理范畴，需要引入面向业务管理和协作的持久化能力。

## 2. 已确认需求

### 2.1 账户与权限

系统需要支持：

- 多账户登录。
- 用户角色管理。
- 角色权限控制。
- 不同用户对档案查看、修正、导出、管理功能的权限隔离。
- 操作审计，记录用户对关键数据的访问和修改行为。

### 2.2 批次与任务管理

系统需要保留：

- 历史处理批次。
- 每个批次的输入路径、开始时间、结束时间、运行状态。
- 成功、失败、异常、需复核等统计信息。
- 单件档案的处理状态。
- 失败任务的错误码、错误信息和重试记录。
- 后续支持后台提交任务、查看进度、失败重试。

### 2.3 档案检索与统计

系统需要支持按字段快速查询、筛选和统计，包括：

- 归档年度。
- 实体分类号。
- 实体分类名称。
- 保管期限。
- 开放状态。
- 责任者。
- 题名。
- 文件形成时间。
- 档号、件号。
- 处理状态、复核状态、修正状态。

### 2.4 Web 管理后台与多人协作

系统需要支持：

- Web 管理后台查看批次和档案。
- 多人同时查看处理结果。
- 人工修正 LLM/规则生成的元数据。
- 保留修正前后的字段差异。
- 支持复核流程，例如“待复核、已修正、已确认”。
- 后续按完整平台架构预留多人协作、权限、审计、任务队列和复杂检索能力。

## 3. 架构结论

需要将 PostgreSQL 集成到系统架构中。

推荐定位是：

> PostgreSQL 作为业务状态库、管理后台数据源和审计记录库，不替代现有 JSON/CSV 交付物。

也就是说：

- 现有 OCR/LLM/规则/导出管线继续保留。
- JSON/CSV 继续作为归档、交换和人工离线复核格式。
- PostgreSQL 负责账户、权限、批次、任务状态、档案元数据、检索、统计、审计和人工修正记录。
- 原始图片继续放在文件系统或对象存储中，数据库只保存路径、hash、页数和状态信息。
- 数据库模型必须保持与当前 `batch_summary.json`、中文 metadata key 和 JSON/CSV 导出字段兼容。
- 处理状态、复核状态、修正状态必须拆分，不能混入同一个 `status` 字段。
- LLM 解析结果、规则修正结果、人工确认结果应按快照保存，避免覆盖历史。
- 引入历史批次后，件号/档号生成必须数据库化，不能继续依赖进程内计数器。

## 4. 推荐架构

```text
Web 管理后台
  -> API 服务
    -> PostgreSQL
    -> 当前分类管线
      -> OCR
      -> LLM(vLLM HTTP)
      -> Rules Engine
      -> Sequence Generator
      -> Export(JSON/CSV)
    -> 文件系统 input_documents / output_results
```

### 4.1 PostgreSQL 职责

PostgreSQL 负责保存：

- 用户、角色、权限。
- 批次记录。
- 档案记录。
- 页面记录。
- 处理任务状态。
- 抽取后的结构化元数据。
- 原始 LLM 输出、规则修正后的结果、最终人工确认结果。
- 人工修正历史。
- 操作审计日志。
- 导出文件记录。

### 4.2 文件系统职责

文件系统继续保存：

- 原始扫描图片。
- 每批次生成的 JSON/CSV 文件。
- 单档案结果 JSON。
- 可供离线移交或归档的输出文件。

### 4.3 vLLM 职责

vLLM 仍作为外部推理服务独立部署。PostgreSQL 不保存模型，不参与推理，只保存请求关联的处理结果、错误和状态。

## 5. 一期范围

一期建议采用“旁路写库先落地，完整平台能力做边界预留”的方式。

### 5.1 一期必须实现

- PostgreSQL 基础表结构和迁移机制。
- 批次入库。
- 档案处理结果旁路入库。
- metadata 快照入库：LLM 结果、规则后结果、最终确认结果。
- 处理状态、复核状态、修正状态拆分。
- 任务尝试和失败重试记录。
- 档案元数据字段查询。
- 导出记录。
- 保留现有 `batch_summary.json` 和 JSON/CSV 导出。

### 5.2 一期建议同步设计但可分步启用

- 用户、角色、权限基础模型。
- 人工修正记录。
- 操作审计日志。
- 件号/档号数据库续号。
- 页面级 OCR 文本和置信度入库。

### 5.3 一期暂不强制实现

- Celery、Redis、RabbitMQ 等独立任务队列。
- Elasticsearch 或 OpenSearch。
- 对象存储迁移。
- 完整工作流引擎。
- 实时多人协同编辑。

这些能力应在表结构和服务边界上预留，但不建议第一期全部落地。

## 6. 建议数据模型

### 6.1 账户与权限

建议表：

- `app_users`
- `roles`
- `permissions`
- `user_roles`
- `role_permissions`

核心字段：

- `app_users.id`
- `app_users.username`
- `app_users.password_hash`
- `app_users.display_name`
- `app_users.status`
- `app_users.created_at`
- `app_users.updated_at`
- `roles.code`
- `roles.name`
- `permissions.code`
- `permissions.description`

权限粒度建议先覆盖：

- 查看档案。
- 修正档案。
- 确认复核。
- 导出结果。
- 管理批次。
- 管理用户和角色。

### 6.2 批次与任务

建议表：

- `processing_batches`
- `processing_jobs`

`processing_batches` 保存一次批处理运行：

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
- `created_by`

`processing_jobs` 保存单件档案的处理状态：

- `id`
- `batch_id`
- `archive_id`
- `processing_status`
- `attempt_count`
- `last_error_code`
- `last_error_message`
- `started_at`
- `finished_at`
- `created_at`
- `updated_at`

状态设计应拆分为三类：

`processing_jobs.processing_status` 表示处理生命周期：

- `pending`
- `running`
- `success`
- `failed`
- `error`

`archive_records.review_status` 表示复核状态：

- `not_required`
- `needs_review`
- `in_review`
- `confirmed`

`archive_records.correction_status` 表示是否发生人工修正：

- `none`
- `corrected`

说明：当前 `batch_summary.json` 的结果状态只有 `success/failed/error`。`pending/running/needs_review/corrected/confirmed` 不应直接写入现有 summary 的 result `status` 字段。

### 6.3 档案与页面

建议表：

- `archive_records`
- `archive_pages`

`archive_records` 保存档案级信息：

- `id`
- `batch_id`
- `archive_name`
- `source_folder`
- `page_count`
- `processing_status`
- `review_status`
- `correction_status`
- `llm_metadata`
- `rules_metadata`
- `final_metadata`
- 高频查询冗余列，例如 `archive_year`、`classification_code`、`retention_period`、`title`、`archive_no`、`item_no`
- `created_at`
- `updated_at`

其中：

- `llm_metadata` 使用 `jsonb` 保存 LLM 解析后的 metadata。
- `rules_metadata` 使用 `jsonb` 保存规则引擎和二次题名重写后的 metadata。
- `final_metadata` 使用 `jsonb` 保存人工确认后的最终元数据。
- 初次处理成功时，`final_metadata` 可初始化为 `rules_metadata`。

`archive_pages` 保存页面级信息：

- `id`
- `archive_id`
- `page_no`
- `image_path`
- `image_name`
- `file_hash`
- `ocr_text`
- `ocr_confidence`
- `created_at`

页面 OCR 文本可以先入库，便于后台查看和后续全文检索。如果 OCR 文本量很大，也可以先只保存路径或摘要，后续再扩展。

注意：当前 OCR 客户端对外主要返回多页合并文本。页面级 `ocr_text` 和置信度入库需要先扩展 OCR 返回结构；在此之前，一期可以只保存页面路径、文件名、hash、页号等基础信息。

### 6.4 修正与审计

建议表：

- `metadata_revisions`
- `audit_logs`

`metadata_revisions` 保存人工修正记录：

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

`audit_logs` 保存系统操作审计：

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

修正记录偏业务语义，审计日志偏系统追踪，两者建议分开。

`metadata_revisions` 应按字段追加记录，不覆盖历史。`old_value` 和 `new_value` 建议使用 JSONB，避免字符串化丢失类型。

### 6.5 导出记录

建议表：

- `export_files`

核心字段：

- `id`
- `batch_id`
- `export_type`
- `file_path`
- `row_count`
- `created_by`
- `created_at`

导出文件本体仍保存到文件系统，数据库保存索引和追踪信息。

### 6.6 件号计数与重试记录

建议增加：

- `sequence_counters`
- `processing_job_attempts`

`sequence_counters` 保存 `(归档年度, 实体分类号, 保管期限代码)` 维度的当前件号值。分配件号时应在事务中锁定对应 counter 行，递增后写入 `archive_records.item_no` 和 `archive_records.archive_no`。`archive_no` 默认按全库唯一设计；如业务确认只需在全宗或其他范围内唯一，则改为范围字段加档号的组合唯一约束。

`processing_job_attempts` 保存每次处理尝试：

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

`processing_jobs` 保存当前状态和最后错误摘要，`processing_job_attempts` 追加保存完整重试历史。

## 7. 检索与索引建议

结构化高频字段建议从 `llm_metadata`、`rules_metadata` 或 `final_metadata` 中冗余成独立列，便于索引和统计；人工确认后的查询和导出优先使用 `final_metadata`。

- `archive_year`
- `classification_code`
- `classification_name`
- `retention_period`
- `openness_status`
- `responsible_party`
- `title`
- `document_date`
- `archive_no`
- `item_no`

建议索引：

- `processing_batches(processing_status, started_at)`
- `processing_jobs(batch_id, processing_status)`
- `archive_records(batch_id, processing_status)`
- `archive_records(batch_id, review_status)`
- `archive_records(archive_year)`
- `archive_records(classification_code)`
- `archive_records(retention_period)`
- `archive_records(openness_status)`
- `archive_records(archive_no)`
- `archive_records(item_no)`
- `archive_records USING GIN(llm_metadata)`
- `archive_records USING GIN(rules_metadata)`
- `archive_records USING GIN(final_metadata)`

题名、责任者、OCR 文本的普通 B-tree 索引对中文模糊检索帮助有限。一期建议优先评估 PostgreSQL `pg_trgm` trigram 索引；只有当数据规模、检索体验、中文分词或复杂相关性排序需求明显超过 PostgreSQL 能力时，再考虑 Elasticsearch 或 OpenSearch。

## 8. 处理流程调整

### 8.1 当前流程

```text
扫描目录 -> OCR -> LLM -> 规则修正 -> 件号生成 -> JSON/CSV 导出
```

### 8.2 集成 PostgreSQL 后的一期流程

```text
创建批次记录
  -> 扫描目录并创建档案记录
  -> 创建处理任务记录
  -> 执行 OCR/LLM/规则/件号生成
  -> 写入 llm_metadata / rules_metadata / final_metadata
  -> 写入冗余查询列
  -> 更新处理状态、复核状态、修正状态
  -> 写入任务尝试记录
  -> 写入 batch_summary.json 和 JSON/CSV
  -> 写入导出记录
```

人工修正流程：

```text
用户登录
  -> 查看档案结果
  -> 修改字段
  -> 写入 final_metadata
  -> 写入 metadata_revisions
  -> 写入 audit_logs
```

事务边界建议按单件档案划分：单件 metadata、状态、任务尝试和件号分配在一个事务中提交。文件导出失败不应回滚已成功处理的档案入库结果，但必须记录导出错误和导出文件状态。

## 9. 分阶段实施计划

### 阶段 1：数据库基础能力

- 增加 PostgreSQL 配置项，例如 `DATABASE_URL`。
- 增加数据库迁移工具。
- 建立批次、档案、页面基础信息、metadata 快照、任务、尝试、导出记录表。
- 将现有批处理结果旁路写入数据库。
- 保持现有 JSON/CSV 输出不变。

### 阶段 2：账户权限、人工修正与审计

- 建立账户、角色、权限表。
- 提供人工修正能力。
- 写入 `metadata_revisions`。
- 写入 `audit_logs`。
- 使用 `final_metadata` 作为最终确认结果。

### 阶段 3：管理后台 API

- 提供批次列表、批次详情 API。
- 提供档案查询、筛选、详情 API。
- 提供人工修正 API。
- 提供审计日志查询 API。
- 提供导出记录查询 API。

### 阶段 4：件号数据库化

- 引入 `sequence_counters`。
- 件号分配改为数据库事务内续号。
- 按业务确认范围建立 `archive_no` 唯一或组合唯一约束。
- 处理历史重复或空档号数据。

### 阶段 5：Web 管理后台

- 登录页。
- 批次管理页。
- 档案列表页。
- 档案详情与 OCR 文本查看页。
- 元数据修正页。
- 审计日志页。
- 用户和角色管理页。

### 阶段 6：任务队列增强

当后台提交任务、并发处理和失败重试需求变强后，再引入：

- Celery 或其他任务队列。
- Redis 或 RabbitMQ。
- 任务调度、限流、取消、重试策略。
- 后台实时进度刷新。

### 阶段 7：复杂检索增强

当 PostgreSQL 检索能力不足时，再评估：

- PostgreSQL 全文检索优化。
- 中文分词方案。
- Elasticsearch 或 OpenSearch。
- OCR 全文索引。

## 10. 风险与约束

### 10.1 数据库不是 OCR 文件仓库

不建议把原始扫描图片直接写入 PostgreSQL。图片应继续放在文件系统或对象存储中，数据库保存路径、hash 和元数据。

### 10.2 不应一次性重构完整管线

现有管线已经具备稳定的文件批处理能力。第一期应以“旁路写库”和“管理功能”为主，避免把 OCR、LLM、规则引擎、导出逻辑同时大改。

### 10.3 审计记录不可覆盖

人工修正和关键操作必须追加记录，不应只覆盖最终结果。最终结果可更新，但修正历史和审计日志必须保留。

### 10.4 JSON/CSV 仍需保留

即使引入 PostgreSQL，也应继续保留 JSON/CSV 导出，因为它们适合作为归档、交换、交付和离线核验格式。

### 10.5 现有接口契约不能被隐式破坏

当前导出字段、`batch_summary.json` schema、中文 metadata key、`success/failed/error` 结果状态都已有测试和下游语义。数据库字段可以使用英文列名，但必须有明确映射，并以 `final_metadata` 或 `rules_metadata` 生成现有导出格式。

## 11. 最终建议

建议集成 PostgreSQL，并按以下原则推进：

1. PostgreSQL 先作为业务状态库和管理后台数据源。
2. 现有文件批处理和 JSON/CSV 输出继续保留。
3. 一期重点实现旁路入库、批次追踪、档案查询、metadata 快照、任务尝试和导出记录。
4. 账户权限、人工修正、审计和件号数据库化按阶段推进，但数据模型需提前兼容。
5. 任务队列和复杂搜索先预留边界，后续按实际负载升级。
6. 原始图片继续保存在文件系统或对象存储，数据库只保存引用和元数据。

该方案能满足当前批次管理、档案检索、人工修正和审计需求，并为完整平台能力预留演进路径，同时避免第一期引入过大的重构风险。具体表结构、状态枚举、字段映射、幂等策略和待确认问题见 `docs/postgresql_data_contract_design.md`。
