# PostgreSQL 集成架构评估与需求说明

> 实现状态修订说明（阅读前必读）：本文为**早期架构评估稿**，最终实现相对其设计有以下简化，下文涉及这些内容处属设计预案而非当前结构（以 `infrastructure/db/models.py` 为准）：权限未采用独立的 `roles` / `permissions` / `user_roles` / `role_permissions` 表，简化为 `app_users.role` 字段加代码内置权限映射；未实现 `processing_job_attempts` 表（`processing_jobs` 保存当前状态、进度/阶段、页数、最后错误和尝试次数，历史事件进入 `processing_events`）；未实现 `force-renumber` 策略（仅 `skip-success` / `rerun-failed-only` / `rerun-all`）；当前生效 schema 不含 `final_metadata` 的 GIN 索引（`0005` 重建按 `models.py` 建表，未定义 GIN，仅保留 `archive_no` 部分唯一索引）。

同步说明：本文是 PostgreSQL 集成的架构评估与需求说明；可实施的数据契约、字段映射、状态拆分和落地边界以 `docs/postgresql_data_contract_design.md` 为准。本文的字段清单、阶段划分和权限粒度已与该数据契约同步；如出现差异，以数据契约为准。

> 本文不记录当前会话的执行结果。非可执行环境只能静态核对架构与代码一致性；迁移、跑批、Web 和测试验证需在目标环境执行。

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

### 2.1 项目与编号管理

系统需要支持：

- 项目是档案件号生成和续号管理的业务单元。
- 当前 CLI 批处理阶段必须通过配置或命令行显式指定项目，例如 `PROJECT_KEY`。
- 未指定项目时不应自动按 `input_dir` 创建项目，避免同一业务项目被拆成多个编号作用域。
- 一个项目可以包含多个处理批次，同一项目下的批次共享件号计数器。
- 件号默认按项目内 `(归档年度, 实体分类号, 保管期限代码)` 分组连续生成。
- 保管期限代码必须复用当前 `SequenceGenerator` 的 2007 年前后映射规则。
- 重跑批次时默认保留已生成件号；重新编号必须作为显式操作并记录审计。

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

### 2.4 Web 管理后台、账户与多人协作

平台阶段需要支持,其中账号、登录、用户管理和只读查询 Web 页面已经在阶段 2 基础后台中部分落地：

- 多账户登录。
- 单位管理。
- 员工账号管理。
- 用户角色管理，内置平台管理员、单位管理员、单位操作员。
- 角色权限控制。
- 不同用户按平台范围或单位范围访问档案查看、修正、导出、管理功能。
- Web 管理后台查看批次、档案、修订记录和审计记录。
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
- PostgreSQL 先负责项目、批次、任务状态、档案元数据、检索、统计、件号计数和导出记录；后续平台阶段再扩展单位、账户、权限、审计和人工修正记录。
- 原始图片继续放在文件系统或对象存储中，数据库只保存路径、hash、页数和状态信息。
- 数据库模型必须保持与当前 `batch_summary.json`、中文 metadata key 和 JSON/CSV 导出字段兼容。
- 处理状态、复核状态、修正状态必须拆分，不能混入同一个 `status` 字段。
- LLM 解析结果、规则修正结果、人工校对后的最终结果应按快照保存，避免覆盖历史。
- 项目是件号/档号连续性的边界，引入历史批次后，件号/档号生成必须按项目数据库化，不能继续依赖进程内计数器。

## 4. 推荐架构

一期：

```text
当前 CLI 批处理
  -> 显式 PROJECT_KEY
  -> PostgreSQL 旁路写库
  -> OCR
  -> LLM(vLLM HTTP)
  -> Rules Engine
  -> 项目内 Sequence Counter
  -> Export(JSON/CSV)
  -> 文件系统 input_documents / output_results
```

当前 Web 管理后台阶段：

```text
Web 管理后台
  -> FastAPI + Jinja2
  -> PostgreSQL
  -> 复用读侧查询 API
```

### 4.1 PostgreSQL 职责

PostgreSQL 负责保存：

- 项目及其编号规则。
- 批次记录。
- 档案记录。
- 页面记录。
- 处理任务状态。
- 抽取后的结构化元数据。
- 原始 LLM 输出、规则修正后的结果、人工校对后的最终结果。
- 人工修正历史。
- 操作审计日志。
- 导出文件记录。
- 项目内件号计数器。
- Web 后台阶段保存单位、用户和 Web session；角色权限由 `app_users.role` 字段和代码内置映射提供。

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
- 项目基础模型，并通过 `PROJECT_KEY` 显式选择项目。
- 项目内件号/档号数据库续号。
- 批次入库。
- 档案处理结果旁路入库。
- `final_metadata` 入库，保持现有导出兼容。
- 处理状态、复核状态、修正状态拆分。
- 任务状态和处理事件记录。
- 档案元数据字段查询。
- 导出记录。
- 保留现有 `batch_summary.json` 和 JSON/CSV 导出。

### 5.2 表结构可预留但不阻塞一期

- `llm_metadata/rules_metadata/final_metadata` 三阶段快照（当前已写入）。
- 人工修正记录。
- 操作审计日志。
- 页面级 OCR 文本和置信度入库。
- 单位、用户、Web session 基础模型；角色权限使用 `app_users.role`。

### 5.3 一期暂不强制实现

- Celery、Redis、RabbitMQ 等独立任务队列。
- Elasticsearch 或 OpenSearch。
- 对象存储迁移。
- 完整工作流引擎。
- 实时多人协同编辑。

这些能力应在表结构和服务边界上预留，但不建议第一期全部落地。

## 6. 建议数据模型

批处理旁路写库不依赖账号体系,所有 `created_by` 字段在管线文件路径中仍可为空或使用固定系统账号值。Web 后台已引入账号体系用于登录、人员管理和只读查询权限控制。

### 6.1 账户、权限与 Web Session

以下模型面向 Web 管理后台和多人协作,不是批处理旁路入库的前置条件。

建议表：

- `organizations`
- `app_users`
- `web_sessions`

核心字段：

- `organizations.id`
- `organizations.name`
- `organizations.status`
- `organizations.created_at`
- `organizations.updated_at`
- `app_users.id`
- `app_users.organization_id`
- `app_users.username`
- `app_users.password_hash`
- `app_users.display_name`
- `app_users.role`
- `app_users.status`
- `app_users.created_at`
- `app_users.updated_at`
- `web_sessions.user_id`
- `web_sessions.token_hash`
- `web_sessions.csrf_token_hash`
- `web_sessions.expires_at`
- `web_sessions.revoked_at`
- `web_sessions.last_seen_at`

内置角色建议先固定为：

- 平台管理员：管理所有单位、人员、项目、数据和操作记录。
- 单位管理员：管理本单位人员、项目、数据和操作记录。
- 单位操作员：创建和操作本单位项目，上传档案，查看 AI 结果，校对结果，修改个人密码。

权限粒度由 `infrastructure/db/accounts.py` 的内置角色映射提供,下表为权限码与中文显示名对照：

| 权限码 | 中文显示名 |
| --- | --- |
| `organization:manage` | 管理单位 |
| `user:manage` | 管理用户和角色 |
| `project:manage` | 管理项目 |
| `project:operate` | 操作项目 |
| `archive:view` | 查看档案 |
| `archive:correct` | 校对或修正 AI 结果 |
| `archive:export` | 导出结果 |
| `batch:manage` | 管理批次 |
| `audit:view` | 查看审计日志 |
| `account:self_update` | 修改个人密码 |

### 6.2 项目与编号

建议表：

- `projects`
- `sequence_counters`

`projects` 是档案编号管理单元，不只是批次容器。项目决定件号生成范围、编号规则和续号状态。当前 CLI 阶段通过 `PROJECT_KEY` 显式选择项目；后续平台阶段再绑定单位。

`projects` 核心字段：

- `id`
- `project_key`
- `project_name`
- `description`
- `status`
- `numbering_rule`
- `preserve_existing_numbers_on_rerun`
- `created_at`
- `updated_at`

平台阶段预留字段：

- `organization_id`
- `created_by`

一期批处理不依赖单位和用户。

`sequence_counters` 保存项目内 `(归档年度, 实体分类号, 保管期限代码)` 维度的当前件号值。核心字段：

- `id`
- `project_id`
- `archive_year`
- `classification_code`
- `retention_period_code`
- `current_value`
- `created_at`
- `updated_at`

平台阶段预留字段：

- `organization_id`

约束：

- `sequence_counters(project_id, archive_year, classification_code, retention_period_code)` 唯一。

分配件号时应在独立短事务中锁定对应 counter 行，递增后写入 `archive_records.item_no` 和 `archive_records.archive_no`；与档案大事务解耦的理由见数据契约文档 §6.2。

项目下可以有多个处理批次，同一项目下的批次共享件号计数器。不同项目之间的件号计数互不影响。

### 6.3 批次与任务

建议表：

- `processing_batches`
- `processing_jobs`

`processing_batches` 保存一次批处理运行：

- `id`
- `project_id`
- `batch_key`
- `batch_name`
- `input_dir`
- `output_dir`
- `batch_status`
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

平台阶段预留字段：

- `organization_id`

`processing_jobs` 保存单件档案的处理状态：

- `id`
- `batch_id`
- `project_id`
- `upload_batch_id`
- `archive_id`
- `document_key`
- `processing_status`（数据库列名为 `status`，ORM 属性名为 `processing_status`）
- `progress`
- `current_stage`
- `page_count`
- `error_code`
- `error_message`
- `attempt_count`
- `started_at`
- `finished_at`
- `created_at`
- `updated_at`

状态设计应拆分为三类：

`processing_batches.batch_status` 表示批次生命周期，与档案级状态解耦：

- `queued`
- `running`
- `success`
- `partial_failed`
- `failed`
- `cancelled`
- 兼容旧值:`completed`、`aborted`

`processing_jobs.processing_status` 与 `archive_records.processing_status` 复用 5 值枚举表示档案级处理生命周期：

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

说明：当前 `batch_summary.json` 的结果状态只有 `success/failed/error`。`pending/running/needs_review/corrected/confirmed` 不应直接写入现有 summary 的 result `status` 字段。批次是否零失败以 `fail_count == 0` 和 `batch_status == success` 判断。

### 6.4 档案与页面

建议表：

- `archive_records`
- `archive_pages`

`archive_records` 保存档案级信息：

- `id`
- `project_id`
- `batch_id`
- `archive_key`
- `archive_name`
- `archive_folder_name`
- `source_folder`
- `page_count`
- `image_files`
- `image_names`
- `result_filename`
- `processing_status`
- `review_status`
- `correction_status`
- `error_code`
- `error_message`
- `traceback_text`
- `processed_time`
- `llm_metadata`
- `rules_metadata`
- `final_metadata`
- 高频查询冗余列，例如 `archive_year`、`classification_code`、`retention_period`、`retention_period_code`、`title`、`archive_no`、`item_no`
- `created_at`
- `updated_at`

平台阶段预留字段：

- `organization_id`

其中：

- `llm_metadata` 使用 `jsonb` 保存 LLM 解析后的 metadata。
- `rules_metadata` 使用 `jsonb` 保存规则引擎和二次题名重写后的 metadata。
- `final_metadata` 使用 `jsonb` 保存人工校对后的最终元数据。
- 初次处理成功时，`final_metadata` 可初始化为 `rules_metadata`。
- `digitized_time` 列必须为 `text` 类型，保留 `YYYY年M月` 中文字符串原样，不得转 `timestamptz`（来源见 `utils/file.py:get_file_creation_time`，优先文件夹 birthtime）。
- 建议预留 `llm_raw_response` / `llm_cleaned_response` / `llm_parse_strategy` 列，便于审计 `infrastructure/llm_client.py:_parse_json` 的修复/兜底分支；具体定义见数据契约文档 §4.4。
- 导出保留字段（`全宗号`、`档案馆代码`、`档案馆名称`、`外包单位名称`）至少在 `final_metadata` JSONB 中保留原值，必要时再冗余成独立列；定义见数据契约文档 §2.3。

`archive_pages` 保存页面级信息：

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

页面 OCR 文本可以先入库，便于后台查看和后续全文检索。如果 OCR 文本量很大，也可以先只保存路径或摘要，后续再扩展。

注意：当前 OCR 客户端对外主要返回多页合并文本。页面级 `ocr_text` 和置信度入库需要先扩展 OCR 返回结构；在此之前，一期可以只保存页面路径、文件名、hash、页号等基础信息。

### 6.5 修正与审计

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

`metadata_revisions` 应按字段追加记录，不覆盖历史。`old_value` 和 `new_value` 建议使用 JSONB，避免字符串化丢失类型。`revision_no` 在档案内按"一次保存共享一个编号"递增——同一次校对动作内修改 N 个字段写入 N 行，共享同一 `revision_no`，便于按"一次复核动作"回放，详见数据契约文档 §4.7。

`audit_logs.actor_user_id` 在阶段 3 启用账户体系前与 `created_by` 同策略：可为 NULL 或使用固定 `system` 占位值。

### 6.6 导出记录

建议表：

- `export_files`

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

导出文件本体仍保存到文件系统，数据库保存索引和追踪信息。

### 6.7 处理事件

当前实现使用 `processing_events` 追加保存阶段开始、结束和错误事件：

- `id`
- `job_id`
- `batch_id`
- `event_type`
- `stage`
- `message`
- `payload`
- `created_at`

`processing_jobs` 保存当前状态、进度、最后错误摘要和 `attempt_count`。当前没有独立 `processing_job_attempts` 表；若后续引入独立 worker 和精细重试历史,再增量设计 attempt 表。

## 7. 检索与索引建议

结构化高频字段建议从 `llm_metadata`、`rules_metadata` 或 `final_metadata` 中冗余成独立列，便于索引和统计；人工校对后的查询和导出优先使用 `final_metadata`。

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

- `projects(project_key)`
- `projects(status)`
- `sequence_counters(project_id, archive_year, classification_code, retention_period_code)`
- `processing_batches(project_id, batch_status, started_at)`
- `processing_batches(batch_status, started_at)`
- `processing_jobs(batch_id, processing_status)`
- `archive_records(batch_id, processing_status)`
- `archive_records(batch_id, review_status)`
- `archive_records(project_id, archive_no)`
- `archive_records(archive_year)`
- `archive_records(classification_code)`
- `archive_records(retention_period)`
- `archive_records(openness_status)`
- `archive_records(archive_no)`
- `archive_records(item_no)`
- `archive_records USING GIN(final_metadata)`（设计选项；当前 `0005` 重建后的 ORM 模型未定义该索引）

`llm_metadata` / `rules_metadata` 默认不建 GIN，作为审计快照；如出现稳定的 JSONB 检索需求再 ad-hoc 添加并跟踪命中率，详见数据契约文档 §8.2。

题名、责任者、OCR 文本的普通 B-tree 索引对中文模糊检索帮助有限。一期建议优先评估 PostgreSQL `pg_trgm` trigram 索引；只有当数据规模、检索体验、中文分词或复杂相关性排序需求明显超过 PostgreSQL 能力时，再考虑 Elasticsearch 或 OpenSearch。

## 8. 处理流程调整

### 8.1 当前流程

```text
扫描目录 -> OCR -> LLM -> 规则修正 -> 件号生成 -> JSON/CSV 导出
```

### 8.2 集成 PostgreSQL 后的一期流程

```text
读取 DATABASE_URL、PROJECT_KEY、BATCH_KEY
  -> PROJECT_KEY 或 BATCH_KEY 缺失则快速失败
  -> 按 PROJECT_KEY 查找或创建项目
  -> 按 (project_id, batch_key) 查找或创建批次
  -> 扫描目录并创建档案记录
  -> 创建处理任务记录
  -> 执行 OCR/LLM/规则
  -> 按项目锁定 sequence_counters 并生成件号/档号（尾部新发号）
  -> 写入 llm_metadata / rules_metadata / final_metadata
  -> 写入冗余查询列
  -> 更新处理状态、复核状态、修正状态
  -> 更新处理任务并追加处理事件
  -> 写入 batch_summary.json 和 JSON/CSV
  -> 写入导出记录
```

`BATCH_KEY` 必须由用户/调用方显式指定，不再隐式从 `input_dir` 或启动时间推导，详见数据契约文档 §4.3 与 §7.1。重跑发号采用尾部新发号策略，允许时间序空洞，详见数据契约文档 §4.9。

人工修正流程：

```text
用户登录
  -> 查看档案结果
  -> 修改字段
  -> 写入 final_metadata
  -> 写入 metadata_revisions
  -> 写入 audit_logs
```

事务边界建议按单件档案划分：单件 metadata、状态、任务尝试和项目内件号分配在一个事务中提交。文件导出失败不应回滚已成功处理的档案入库结果，但必须记录导出错误和导出文件状态。

## 9. 分阶段实施计划

阶段编号与口径与数据契约文档 §10 对齐，本节仅做需求与功能维度的展开。

### 阶段 1：数据库基础能力

- 增加 PostgreSQL 配置项，例如 `DATABASE_URL`。
- 增加显式项目配置，例如 `PROJECT_KEY` 和可选 `PROJECT_NAME`。
- 增加显式批次配置 `BATCH_KEY`，未指定时管线快速失败。
- 增加数据库迁移工具。
- 建立项目、批次、档案、页面基础信息、任务、尝试、导出记录表。
- 项目作为件号连续性的边界，批次必须绑定项目。
- 引入 `sequence_counters`，件号分配改为项目范围内数据库事务续号，重跑采用尾部新发号策略。
- 在 `archive_records` 上预留 `llm_raw_response` / `llm_cleaned_response` / `llm_parse_strategy` 列，便于审计 LLM 修复/兜底分支；具体定义见数据契约文档 §4.4。
- 将现有批处理结果旁路写入数据库。
- 保持现有 JSON/CSV 输出不变。

### 阶段 2：metadata 快照、人工修正与审计

- 保持 LLM 结果、规则结果、人工校对结果三个快照写入。
- 提供人工修正能力。
- 写入 `metadata_revisions`。
- 写入 `audit_logs`。
- 使用 `final_metadata` 作为人工校对后的最终结果。

### 阶段 3：账户、单位、权限与管理后台

账号、Web session、登录、用户管理、批次/档案/修订/审计只读页面已经落地；角色权限由 `app_users.role` 和代码映射提供。阶段 3 应在此基础上补齐单位/项目管理、在线修正、导出记录和更完整的 API 边界。

API 层：

- 建立或完善单位、账户和 Web session 表；角色权限继续由 `app_users.role` 提供。
- 内置平台管理员、单位管理员、单位操作员三类角色。
- 项目绑定单位。
- 提供或完善批次列表、批次详情 API（阶段 2 已有组织过滤后的只读查询）。
- 提供或完善档案查询、筛选、详情 API（阶段 2 已有组织过滤后的只读查询）。
- 提供人工修正 API。
- 提供审计日志查询 API。
- 提供导出记录查询 API。

Web 后台界面：

- 登录页（阶段 2 已有）。
- 单位管理页。
- 项目管理页。
- 批次查询页（阶段 2 已有）。
- 档案列表页（阶段 2 已有）。
- 档案详情与页面列表查看页（阶段 2 已有）。
- 元数据修正页。
- 审计日志页（阶段 2 已有档案级只读页）。
- 用户管理页（阶段 2 已有基础能力）。
- 角色管理页。

### 阶段 4：页面 OCR 和全文检索增强

包含原"页面 OCR"与"复杂检索"两部分能力，按数据契约 §10 阶段 4 合并推进。

- 扩展 OCR 返回结构，保存页面级 OCR 文本与置信度。
- 增加题名、责任者、OCR 文本检索索引。
- 根据规模评估 PostgreSQL 中文分词扩展。
- 当 PostgreSQL 检索能力不足时再评估 Elasticsearch、OpenSearch 等外部搜索引擎，作为 OCR 全文索引方案。
- trigram 写入代价与命中率追踪策略见数据契约文档 §8.3。

### 阶段 5：后台任务队列

当后台提交任务、并发处理和失败重试需求变强后，再引入：

- Celery 或其他任务队列。
- Redis 或 RabbitMQ。
- 任务调度、限流、取消、重试策略。
- 后台实时进度刷新。
- 队列系统复用 `processing_jobs` 与 `processing_events`，如需逐次尝试历史再补充 attempt 表。

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
3. 一期重点实现显式项目、项目内续号、旁路入库、批次追踪、档案查询、任务事件和导出记录。
4. metadata 快照、账户权限、人工修正和审计已经进入当前实现；后续重点是完善导出记录查询、队列化和检索能力。
5. 任务队列和复杂搜索先预留边界，后续按实际负载升级。
6. 原始图片继续保存在文件系统或对象存储，数据库只保存引用和元数据。

该方案先满足当前项目编号管理、批次管理、档案检索和文件导出兼容需求，并为单位管理、账户权限、人工修正和审计预留演进路径，同时避免第一期引入过大的平台化重构风险。具体表结构、状态枚举、字段映射、幂等策略和待确认问题见 `docs/postgresql_data_contract_design.md`。
