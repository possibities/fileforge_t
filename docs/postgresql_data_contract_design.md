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
| `实体分类号` | `classification_code` | 新旧编码均保留原值；2020 年切分见 `constants.CODE_SWITCH_YEAR` |
| `实体分类名称` | `classification_name` | 党群类、综合类、业务类 |
| `保管期限` | `retention_period` | 取值集合按年份切分：2007 起为 `永久/30年/10年`，2006 及以前为 `永久/长期/短期`；同一项目下两套值可并存，见 `core/sequence_generator.py` |
| `责任者` | `responsible_party` | 发文单位或责任人 |
| `文件编号` | `document_number` | 原始文号 |
| `题名` | `title` | 规则清洗后的题名 |
| `文件形成时间` | `document_date` | 当前格式为 `YYYYMMDD` 字符串；数据库列建议 `text`，不要转 `date` |
| `密级` | `security_level` | 非涉密、内部、秘密、机密、绝密或空 |
| `保密期限` | `secret_period` | 1年、5年、10年或空 |
| `备注` | `notes` | 包含待核查提示 |
| `开放状态` | `openness_status` | 开放、控制 |
| `延期开放理由` | `openness_delay_reason` | 工作秘密、商业秘密、个人隐私、负面信息 |
| `立档单位名称` | `fonds_unit_name` | 缺失时规则会用责任者补齐 |
| `数字化时间` | `digitized_time` | 当前是 `YYYY年M月` 中文字符串（如 `2025年2月`），不是 ISO 时间戳；来源优先级见下方注释。数据库列必须为 `text`，不得转 `timestamptz` |
| `页数` | `page_count` | 批处理阶段追加 |
| `档号` | `archive_no` | 件号生成后追加 |
| `件号` | `item_no` | 件号生成后追加，当前为 4 位字符串 |
| `档案文件夹` | `archive_folder_name` | 分类器阶段追加 |
| `source_folder` | `source_folder` | 批处理阶段追加，首张图片所在父目录 |
| `processed_time` | `processed_time` | 当前是首张图片的 **mtime**（修改时间）的 ISO 字符串，与 `数字化时间`（birthtime）不同；不是真实"处理完成时间" |

`数字化时间` 取值来源（见 `utils/file.py:get_file_creation_time`）：

1. 首张图片所在文件夹的 birthtime
2. 首张图片自身的 birthtime
3. 兜底：当前时间

导出保留字段（见 `constants.EXPORT_RESERVED_FIELDS` 与 `config/exporter.json`）不走 LLM 抽取，但仍随导出输出，需要在数据库侧体现：

| metadata key | 数据库列建议 | 说明 |
| --- | --- | --- |
| `全宗号` | `fonds_code` | 下游模板占位，默认 NULL |
| `档案馆代码` | `archive_house_code` | 当前规则强制置空，下游模板占位 |
| `档案馆名称` | `archive_house_name` | 当前规则强制置空，下游模板占位 |
| `外包单位名称` | `outsourcing_unit_name` | 下游模板占位，默认 NULL |

实现侧两条路均可：把这四个字段冗余成独立列；或仅依赖 `final_metadata` JSONB 携带，导出时由模板从 JSONB 取值。一期建议至少在 JSONB 快照中保留，避免导出回退时缺列。

数据库可以用英文列支持高频查询，但必须保留完整中文 metadata JSONB 快照，避免导出字段和历史 JSON 兼容性丢失。

## 3. 状态拆分

一期建议拆成三个独立状态字段。

### 3.1 处理状态

字段：`processing_status`，用于 `archive_records` 与 `processing_jobs`。

取值：

- `pending`
- `running`
- `success`
- `failed`
- `error`

含义：

- `success/failed/error` 与当前 `BatchProcessor` 结果状态保持一致。
- `pending/running` 只用于数据库任务生命周期，不写入现有 `batch_summary.json` 的结果状态。
- 历史 `batch_summary.json` 反向入库（见 §6.3 的 replay 路径）时，`processing_status` 直接复用文件中的三值，不会出现 `pending/running`；后两者仅在 DB 驱动的实时跑批中产生（`pending` = 已建 `processing_jobs` 行但尚未开始执行；`running` = 正在执行）。

注意：批次级别不复用此 5 值枚举，使用独立的 `batch_status`（见 §4.3），以避免"批次 success 是否要求 fail_count==0"这类语义二义。

### 3.2 复核状态

字段：`review_status`

取值：

- `not_required`
- `needs_review`
- `in_review`
- `confirmed`

含义：

- 规则或二次 LLM 失败产生的待核查提示应映射为 `needs_review`。
- 有校对权限的用户校对保存后改为 `confirmed`，不要求单位管理员二次确认。

### 3.3 修正状态

字段：`correction_status`

取值：

- `none`
- `corrected`

含义：

- 只表示是否发生过人工修正。
- 具体修正内容必须写入 append-only 的修正记录表。

## 4. 数据模型

以下是逻辑模型，不绑定具体 ORM。所有表建议包含 `created_at` 和 `updated_at`，时间字段使用带时区时间类型。当前批处理一期没有账号体系，所有 `created_by` 字段在账户体系启用前可为空或使用固定系统账号值。

### 4.1 项目表

表：`projects`

项目是档案件号/档号的编号作用域，用于确定件号生成规则、编号范围和续号状态。当前批处理 CLI 没有登录、单位和项目选择界面，因此数据库集成一期必须通过配置或命令行显式指定项目，不能根据 `input_dir` 自动创建随机项目。

一期建议通过以下运行参数确定项目：

- `PROJECT_KEY`：项目稳定标识，必填，用于查找或创建项目。
- `PROJECT_NAME`：项目显示名称，可选；首次创建项目时使用。

核心字段：

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

建议约束：

- `projects.project_key` 唯一。
- `status` 使用枚举或 check constraint，取值固定为 `active/disabled/archived`，不引入 `deleted` 软删除态。
- 不允许物理删除已存在档案/批次的项目；外部 FK（`processing_batches.project_id`、`archive_records.project_id`、`sequence_counters.project_id`）使用 `ON DELETE RESTRICT`。

说明：

- `numbering_rule` 保存项目采用的编号规则摘要，一期必须复用当前 `SequenceGenerator` 规则：按 `(归档年度, 实体分类号, 保管期限代码)` 分组续号，且保管期限代码按 2007 年前后使用不同映射。
- `preserve_existing_numbers_on_rerun` 控制重跑时是否保留已分配件号，默认应为 `true`。
- 项目状态语义：`active` 可继续创建批次和编号；`disabled` 禁止新建批次但保留只读访问；`archived` 视同封存，禁止任何写入但允许导出/查询。
- 不引入软删除的原因：项目是件号连续性的边界，即便不再使用也必须保留以支撑历史档号溯源；`disabled` 已覆盖"不再可用"，`archived` 已覆盖"封存只读"，无第三种语义需要 `deleted`。
- 平台阶段启用单位管理后，项目可绑定 `organization_id`；一期批处理旁路入库不依赖单位。

### 4.2 单位、用户与权限（平台阶段预留）

以下模型面向 Web 管理后台和多人协作；它们不是批处理旁路入库的一期必需项,但阶段 2 基础后台已落地账号、角色、权限和 Web session 表。

表：

- `organizations`
- `app_users`
- `roles`
- `permissions`
- `user_roles`
- `role_permissions`
- `web_sessions`

建议约束：

- `organizations.name` 全平台唯一。若后续需要多级单位，再增加 `parent_id` 并调整为同一上级下唯一。
- `app_users.username` 唯一。
- `roles.code` 唯一。
- `permissions.code` 唯一。
- `user_roles(user_id, role_id)` 唯一。
- `role_permissions(role_id, permission_id)` 唯一。
- `web_sessions.token_hash` 唯一。

`organizations` 表示平台内单位。单位不建议物理删除，应通过状态控制可用性。

`organizations.status` 建议取值：

- `active`
- `disabled`

单位被禁用后，该单位下用户不应继续登录或操作项目，历史批次、档案、修正记录和审计日志仍保留。

`app_users` 应绑定单位：

- 平台管理员账号可以不绑定具体单位，或绑定平台虚拟单位。
- 单位管理员和单位操作员必须绑定所属单位。
- `app_users.status` 建议取值为 `active/disabled`。

内置角色：

| 角色代码 | 角色名称 | 权限范围 | 说明 |
| --- | --- | --- | --- |
| `platform_admin` | 平台管理员 | 全平台 | 管理所有单位、人员、项目、数据和操作记录。 |
| `org_admin` | 单位管理员 | 本单位 | 管理本单位人员、项目、数据和操作记录。 |
| `org_operator` | 单位操作员 | 本单位 | 创建和操作本单位项目，上传档案，查看 AI 结果，校对结果，修改个人密码。 |

平台阶段权限粒度：

- `organization:manage`
- `project:manage`
- `project:operate`
- `archive:view`
- `archive:correct`
- `archive:export`
- `batch:manage`
- `user:manage`
- `audit:view`
- `account:self_update`

权限边界：

- 平台管理员可访问所有单位、项目、批次和档案数据。
- 单位管理员只能访问本单位数据，可管理本单位用户和项目。
- 单位操作员只能访问和操作本单位项目，可校对 AI 结果，不需要单位管理员二次确认。
- 导出权限可授予平台管理员、单位管理员和单位操作员。
- 平台管理员是否可以直接修改所有单位的 AI 结果暂不强制，作为业务配置项保留。

Web 登录 session：

- `web_sessions.user_id` 指向 `app_users.id`。
- cookie 保存明文随机 session token；数据库只保存 `sha256(token)`。
- CSRF token 同样只保存 hash，表单提交时按 hash 校验。
- `expires_at` 控制过期，`revoked_at` 非空表示已退出或被撤销。
- `last_seen_at` 在有效请求加载当前用户时刷新。

### 4.3 批次表

表：`processing_batches`

核心字段：

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
- `created_at`
- `updated_at`

平台阶段预留字段：

- `organization_id`

约束：

- `processing_batches(project_id, batch_key)` 唯一，用于项目内幂等重跑。
- `failure_breakdown` 使用 JSONB，保存当前 `error_code -> count` 映射。
- `batch_status` 使用枚举或 check constraint。

`summary_schema_*` 三个字段用于把每个批次锁定到其入库时刻的 `batch_summary.json` schema 版本，便于后续 schema 升级时识别历史批次：

- `summary_schema_version`：取自 `BatchProcessor.SUMMARY_SCHEMA_VERSION`（当前值 `1.0.0`，见 `processors/batch_processor.py`）。
- `summary_schema_ref`：取自 `BatchProcessor.SUMMARY_SCHEMA_REF`（当前为 `config/batch_summary.schema.json`）。
- `summary_changelog_ref`：取自 `BatchProcessor.SUMMARY_CHANGELOG_REF`（当前为 `config/batch_summary.schema.changelog.md`）。
- 写入时机：`get_or_create_batch` 创建批次行时落入；后续重跑同一 `batch_key` 不重写，以保留首次入库时的 schema 版本。

`batch_status` 取值：

- `running`：批次正在执行或被中断尚未收尾。
- `completed`：批次执行结束（无论档案级是否有 failed/error），等价于”已生成 `batch_summary.json`”。
- `aborted`：批次被显式终止或异常退出，无 `batch_summary.json` 产物。

注意：`batch_status` 与 `archive_records.processing_status`（5 值枚举）解耦。批次 `completed` 不暗示零失败，零失败需查 `fail_count == 0`。

`batch_key` 必须由用户/调用方显式指定（CLI 参数或环境变量），未指定时管线快速失败，不再隐式从 `input_dir` 或启动时间推导，以杜绝同一项目因输入路径变化被拆成多个不可比批次。

`output_dir` 保存批次写出单档案 JSON 与 `batch_summary.json` 的目录，用于后续 replay/补写。单档案 JSON 文件名规则当前为 `{idx:04d}_{safe(archive_name)}_result.json`（见 `BatchProcessor.batch_process_archives`），文件名中的 `idx` 是批内顺序，与 `archive_no` 无关；如需从 DB 反查文件，应在 `archive_records` 中保存 `result_filename` 列（见 §4.4）。

### 4.4 档案表

表：`archive_records`

核心字段：

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
- `created_at`
- `updated_at`

平台阶段预留字段：

- `organization_id`

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

LLM 原始响应列（可选，建议一期预留以便审计兜底解析路径）：

- `llm_raw_response`：vLLM 返回的原始 `message.content`（清洗前）
- `llm_cleaned_response`：去掉 ``` 包裹和 `{...}` 截取后的字符串
- `llm_parse_strategy`：取值 `json`（直接解析成功）/ `repaired`（修复引号尾逗号成功）/ `regex`（逐字段抽取兜底）/ `failed`

未启用此列时，`infrastructure/llm_client.py:_parse_json` 走兜底分支只落日志、不可回溯，所以即使一期不展示，也建议留列后台审计。

默认约束：

- `archive_records(batch_id, archive_key)` 唯一。
- `archive_records(project_id, archive_no)` 唯一，作为项目内档号唯一的最终防线；允许 `archive_no` 为空时建议使用 partial unique index，仅约束非空档号。
- 若历史数据存在空档号或重复档号，应先允许为空并在确认期后收紧约束。
- `processing_status`、`review_status`、`correction_status` 使用枚举或 check constraint。

`archive_key` 是档案在批次内的稳定标识，使用相对 `processing_batches.input_dir` 的归一化档案路径（POSIX 风格、正斜杠分隔）。当前批处理实现取档案文件夹名（见 `processors/batch_processor.py` 中 `archive_key = archive_name`），同名目录在不同子树下复用时可能冲突；后续若出现冲突需把归一化后的相对路径全量纳入。跨批次识别同一档案不通过 `archive_key`，而是另外计算 `content_hash`（见 §7.2），避免把"档案物理位置"和"档案内容指纹"两种语义塞进同一字段。

`image_files` 与 `image_names` 是来自 `BatchProcessor` 的冗余快照列，方便在不联表时还原 `batch_summary.json` 的结果项；页面级权威信息以 `archive_pages` 为准。两边出现不一致时应以 `archive_pages` 为准并触发数据修复任务。

`result_filename` 保存当前批次对应的单档案 JSON 文件名（见 §4.3），由批处理写盘时回填，便于人工或工具按 `output_dir/result_filename` 直接定位文件。

`digitized_time` 列必须为 `text` 类型（保留 `YYYY年M月` 中文字符串原样），同 §2.3 注释。

### 4.5 页面表

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
- `image_path` 必须存为相对 `processing_batches.input_dir` 的相对路径（POSIX 风格、正斜杠分隔）。不允许存绝对路径，否则跨机器重跑或目录搬迁时唯一约束会失效，破坏幂等性。
- 当前实现状态：`infrastructure/db/repositories.py:upsert_pages` 直接落入调用方传入的原值，未做归一化；该规则需在后续 commit 中由 `BatchRecorder.on_archive_start` 统一调用 `Path(p).relative_to(input_dir).as_posix()` 后再落库。

注意：当前 OCR 对外接口只返回多页合并文本。页面级 OCR 入库需要先把 OCR 客户端返回结构扩展为页面结果列表，或者在一期只保存 `image_path/image_name/file_hash`，暂缓 `ocr_text` 和置信度。

### 4.6 任务与重试

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
- `job_type` 一期建议固定取 `archive_classify`（覆盖单档案的 OCR→LLM→Rules→Sequence 整段处理），暂不按子阶段拆分；后续真正引入异步任务队列再扩展为 `ocr/llm/rules/sequence/export` 等子类型。

### 4.7 修正与审计

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
- `revision_no` 在档案内按"一次保存共享一个编号"递增:同一次校对动作内修改 N 个字段写入 N 行,共享同一 `revision_no`,便于按"一次复核动作"回放。`revision_no` 通过事务内"读取该档案当前最大 `revision_no` + 1"的方式分配;高并发场景需要在事务中对 `archive_records` 行加锁,避免并发动作冲突。
- `reason` 可空。**字面值约定**:Web 端人工修正若未填表单原因,统一存字面 `manual_correction`;CLI 自动重跑统一存 `rules_rerun_force`。其它来源应使用语义化短字符串便于 SQL 反查。

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
- `actor_user_id` 在一期账户体系未启用时与 `created_by` 同策略：可为 NULL，或使用固定 `system` 占位值（推荐 NULL，落地时通过中间件统一处理）；阶段 3 启用账户体系后才强制非空。

### 4.8 导出记录

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

### 4.9 件号计数

表：`sequence_counters`

核心字段：

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

说明：

- 当前代码的件号计数器是进程内内存状态，每次运行从 `0001` 开始。
- 引入项目和历史批次后，件号生成必须改为数据库续号。
- 项目是件号连续性的边界，不同项目之间的件号计数互不影响。
- `retention_period_code` 必须复用当前 `SequenceGenerator` 的映射规则：2007 年及以后 `永久=Y/30年=D30/10年=D10`，2006 年及以前 `永久=Y/长期=C/短期=D`。
- 分配件号时应在事务中锁定对应 counter 行，递增后写入 `archive_records.item_no/archive_no`。
- `archive_records(project_id, archive_no)` 默认建立组合唯一约束，作为项目内档号唯一的最终防线；允许空档号时只约束非空值。

重跑发号策略：

- 默认策略：**尾部新发号**。已成功档案的 `archive_no` 不变；首次失败、二次成功的档案在二次成功时从 `current_value + 1` 取号，允许时间序上出现"按归档年度+分类+期限分组的件号空洞"。
- 该策略实现简单、无锁竞争，但需要在交付端明确"件号 = 项目内单调分配序号，不保证按业务时间或档案目录顺序连续"。
- 如确实需要"二次成功时回填首次预留位次"，应作为后续阶段的显式选项，并在 `sequence_counters` 之外引入"件号空洞回收表"，本期不实现。
- 显式重排（人工触发）必须作为高风险操作记入审计，不得静默。

## 5. metadata 快照策略

当前代码只有一个最终 metadata dict，且规则引擎会原地修改。为了满足“保存 LLM 输出、规则修正结果、人工校对后的最终结果”的需求，需要在实现时引入快照边界。

建议契约：

1. LLM 解析完成后保存 `llm_metadata`。
2. 规则引擎和题名二次重写完成后保存 `rules_metadata`。
3. 初次成功处理时，`final_metadata = rules_metadata`。
4. 人工修正时更新 `final_metadata`，并同步更新高频查询列。
5. 每次人工修正写入 `metadata_revisions` 和 `audit_logs`。

### 5.1 重跑时的 final_metadata 保护

已 corrected 档案（`correction_status = corrected`）在批次重跑时按以下规则处理：

- **默认**：仅刷新 `llm_metadata` 与 `rules_metadata`，**不**覆盖 `final_metadata`，不修改高频查询列，不修改 `correction_status/review_status`，不写 `metadata_revisions`。
- **`--force-rerun-rules` 显式 flag**：用 `rules_metadata` 覆盖 `final_metadata`，同步刷新高频查询列。每个被覆盖的字段必须由系统自动写一行 `metadata_revisions`（共享同一 `revision_no`），约定 `created_by = system`、`reason = rules_rerun_force`，并同步写一行 `audit_logs`（`action = force_rerun_rules`，`target_type = archive`）。
- 未 corrected 档案不受此条限制，按 §7.1 的重跑策略正常更新。

实现状态（阶段 1B 已落地）：

- `infrastructure/db/repositories.py:apply_force_rerun_rules(session, archive, new_metadata, actor_user_id, reason)` 已实现:diff 旧/新 `final_metadata` → 写 `metadata_revisions`(共享 `revision_no`)→ 写一条 `audit_logs`(`action=force_rerun_rules`,`target_type=archive`)→ 调 `apply_classification_result(force_rerun_rules=True)` 覆盖列。无差异时整段 no-op。
- `infrastructure/db/recorder.py:BatchRecorder.force_rerun_rules_for_archive(archive_key, new_metadata, actor_user_id, reason)` 暴露 hook,内部走 `apply_force_rerun_rules`,DB 错误返回 None 不抛。
- `utils/force_rerun_cli.py` 是命令行入口(`python -m utils.force_rerun_cli ...`),阶段 2 的人工修正 Web API 上线前作为最小可用触发面。

实现影响：

- `LlmClient.extract_metadata()` 如需保存原始 response，应返回包含 `raw_response`、`cleaned_response` 和 `metadata` 的结构，或由调用方增加单独记录钩子。
- `RulesEngine.apply_all()` 之前应复制 LLM metadata，避免原地修改导致原始结果丢失。
- 简报题名二次重写失败写入 `备注` 的待核查提示时，应同步设置 `review_status = needs_review`。
- 写入 `final_metadata` 的代码路径必须先检查 `correction_status`，命中 `corrected` 时跳过覆盖（除非显式 force flag 已落到本次调用上下文）。

## 6. 写入流程

### 6.1 一期旁路写库流程

```text
读取 DATABASE_URL、PROJECT_KEY、BATCH_KEY
  -> PROJECT_KEY 或 BATCH_KEY 缺失则快速失败
  -> 按 PROJECT_KEY 查找或创建项目
  -> 按 (project_id, batch_key) 查找或创建批次（processing_batches）
  -> 扫描目录
  -> 为每个档案 upsert archive_records 和 archive_pages 基础信息
  -> 创建 processing_jobs（job_type=archive_classify）
  -> 执行 OCR / LLM / Rules
  -> 按 project_id 锁定 sequence_counters 并生成件号/档号（尾部新发号）
  -> 写入 llm_metadata / rules_metadata / final_metadata
  -> 写入冗余查询列
  -> 更新 archive_records.processing_status
  -> 更新 processing_jobs 和 processing_job_attempts
  -> 继续生成单档案 JSON、batch_summary.json、汇总 JSON/CSV
  -> 回填 archive_records.result_filename
  -> 写入 export_files
  -> 更新 processing_batches.batch_status（completed/aborted）
```

### 6.2 事务边界

建议按单件档案建立事务边界，并把件号分配从档案大事务中拆出：

- 档案大事务：单件 metadata、状态、任务尝试、`archive_records` 与 `archive_pages` 的写入在一个事务中提交。该事务覆盖 OCR/LLM/规则全程，可能持续数十秒到数分钟。
- 件号短事务：项目内件号分配走独立的短事务，只锁 `sequence_counters` 一行、写 `current_value + 1` 并回填 `archive_records.archive_no/item_no`。短事务在档案大事务"业务结果就绪、即将提交"时调用，避免 `SELECT FOR UPDATE sequence_counters` 长时间持锁导致并发跑批被串行化。
- 批次统计可以在单件完成后增量更新，也可以批次结束时从 `archive_records` 汇总回填。
- 文件导出失败不得回滚已成功处理的档案记录，但应写入批次级或导出级错误日志。

实现侧：`infrastructure/db/allocator.py` 的 `DatabaseAllocator` 已使用独立 session 实现短事务策略；档案大事务在 commit 前调用 allocator 取号，两条事务互不嵌套。

### 6.3 文件与数据库一致性

文件系统不是事务资源，因此一期采用”数据库记录真实处理状态，文件导出记录真实交付状态”的策略：

- 档案处理成功但导出失败：`archive_records.processing_status = success`，`export_files` 不写成功记录，批次或日志记录导出错误。
- 单档案 JSON 写出成功但 DB 写入失败：应标记批次异常并保留文件结果；后续可用文件重新补写 DB。
- DB 写入成功但单档案 JSON 写出失败：应允许从 DB 重新导出该档案结果。

恢复路径（占位，落地工具留待实现）：

- “凭文件补 DB”：以 `output_dir/batch_summary.json` 为入口，遍历同目录下的单档案 JSON，回写 `archive_records / archive_pages / processing_jobs / processing_job_attempts`，缺失的件号需走 §4.9 的尾部新发号策略；建议落在 `scripts/replay_files_to_db.py`。
- “凭 DB 补文件”：以 `processing_batches.id` 为入口，从 `final_metadata` 与 `archive_pages` 重新生成单档案 JSON 与 `batch_summary.json`，输出到 `processing_batches.output_dir`；建议落在 `scripts/replay_db_to_files.py`。
- 两个工具一期不强制实现，但表结构（特别是 `result_filename`、`output_dir`、`summary_schema_version`、JSONB 快照）必须够用，避免反向工具被字段缺失阻塞。

## 7. 幂等与重跑

### 7.1 批次幂等

- `batch_key` 必须由用户/调用方显式指定，未指定时管线必须快速失败，不得隐式从 `input_dir` 或启动时间推导。
- 用户在同一项目下指定同一 `batch_key` 时，系统应恢复或重跑同一批次。
- 重跑策略默认值为 **`skip-success`**：只重跑上次 `processing_status` 为 `failed/error/pending` 的档案，跳过 `success`。`success` 档案的 `archive_no/item_no` 不变。
- 其它重跑策略必须作为显式 flag 选择：
  - `rerun-failed-only`：等同于默认（保留以便文档与 CLI 对齐）。
  - `rerun-all`：所有档案重跑，已成功档案的 metadata 与状态被更新；件号默认仍走"尾部新发号"，已分配的 `archive_no` 不重排。
  - `force-renumber`：在 `rerun-all` 基础上额外重排件号，是高风险操作，必须写入审计。
- 人工修正过的档案（`correction_status = corrected`）默认在任何重跑策略下都**不**覆盖 `final_metadata`，详见 §5.1。
- `PROJECT_KEY` 同样不得由 `input_dir` 隐式推导，避免同一业务项目因输入路径变化被拆成多个编号作用域。

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

- `projects(project_key)`
- `projects(status)`
- `processing_batches(project_id, processing_status, started_at)`
- `processing_batches(processing_status, started_at)`
- `archive_records(batch_id, processing_status)`
- `archive_records(batch_id, review_status)`
- `archive_records(project_id, archive_no)`
- `archive_records(archive_year)`
- `archive_records(classification_code)`
- `archive_records(retention_period)`
- `archive_records(openness_status)`
- `archive_records(archive_no)`
- `archive_records(item_no)`
- `processing_jobs(batch_id, processing_status)`

### 8.2 JSONB 索引

建议：

- `archive_records USING GIN(final_metadata)`

`llm_metadata` 与 `rules_metadata` 默认不建 GIN：它们是审计快照，多数情况只在档案详情页打开时整行读取，几乎不参与 JSONB 路径查询。给三列都建 GIN 会显著放大写入成本（每次档案首次写入与人工修正都会触发多个 GIN 维护），不划算。

如确实出现"对历史 LLM 输出做 JSONB 检索"的需求，再按需对单列 ad-hoc 建 GIN，并在 `pg_stat_user_indexes` 中跟踪命中率，决定是否常驻。

JSONB 用于完整快照和低频字段查询，高频字段仍应冗余为独立列。

### 8.3 题名、责任者和 OCR 检索

普通 B-tree 索引不适合中文题名模糊查询。一期建议：

- 对 `title`、`responsible_party` 使用 `pg_trgm` trigram 索引支持 `LIKE/ILIKE` 或相似度检索。这两列单档案各一个值、长度有限，trigram 维护成本可控。
- `archive_pages.ocr_text` 一期不建议直接加 trigram。理由：trigram 索引每个被索引行需要按 3-gram 拆分写入 GIN 内部结构，OCR 文本动辄数千字，单档案数页累计可达 10⁴ 级 token；批量入库时 GIN 增量维护会显著放大 WAL 写入和提交时延，对一期"旁路写库不能拖慢现有管线"的目标不利。等阶段 4（页面 OCR 全文检索增强）有真实检索需求时，再评估 trigram、PostgreSQL 中文分词扩展或 Elasticsearch/OpenSearch。
- 写入代价评估：trigram 索引上线前应先在生产数据子集上跑 `EXPLAIN (ANALYZE, BUFFERS)` 对比 INSERT 时延，并通过 `pg_stat_user_indexes.idx_tup_read` 跟踪命中率。命中率长期低于 5% 的索引应清理。
- 如需更好的中文分词、相关性排序和复杂全文检索，再评估 PostgreSQL 中文分词扩展或 Elasticsearch/OpenSearch。

## 9. 读侧 API 与导出兼容原则

阶段 1C 落地的只读查询 API,供 CLI 与当前 Web 后台共用。代码见 `infrastructure/db/queries.py`,完整设计见 `docs/superpowers/specs/2026-05-04-phase-1c-readside-queries-design.md`。

### 9.1 查询函数清单

| 函数 | 返回类型 | 用途 |
| --- | --- | --- |
| `list_batches(session, *, project_key, status_filter=None, organization_id=None, page=1, page_size=50)` | `ListResult[BatchSummary]` | 项目下批次列表 |
| `get_batch_detail(session, *, batch_id)` | `Optional[BatchDetail]` | 批次详情(含 `failure_breakdown`、schema 三件套) |
| `list_archives(session, *, batch_id, filter=None, organization_id=None, page=1, page_size=50)` | `ListResult[ArchiveSummary]` | 批次下档案列表(支持 12 字段过滤) |
| `get_archive_detail(session, *, archive_id)` | `Optional[ArchiveDetail]` | 档案详情(含三快照、LLM trace、pages 列表) |
| `list_revisions(session, *, archive_id, page=1, page_size=50)` | `ListResult[RevisionRow]` | 档案修正记录 |
| `list_audit_logs(session, *, target_type, target_id, page=1, page_size=50)` | `ListResult[AuditLogRow]` | 审计日志(`target_type` 一期白名单 `{"archive"}`) |

所有函数 keyword-only,`session` 由调用方控制生命周期,内部不 commit。`organization_id=None` 表示不附加单位过滤;Web 后台中非 `platform_admin` 用户会传入当前用户单位,使批次和档案在分页、总数统计前完成组织范围过滤。

`BatchDetail` 继承 `BatchSummary`,`ArchiveDetail` 继承 `ArchiveSummary` —— 子类只声明扩展字段(`BatchDetail` 加 3 个,`ArchiveDetail` 加 18 个),`isinstance(detail, Summary) == True`。两组映射函数(`_batch_to_summary` / `get_batch_detail`、`_archive_to_summary` / `_archive_to_detail`)通过 `dataclasses.fields()` 解包父类字段,避免逐行重复字段名。

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

### 9.7 导出兼容原则

数据库 API 可以返回英文字段，但导出仍必须使用当前中文字段顺序：

- 导出 JSON/CSV 继续以 `config/exporter.json` 为准。
- `final_metadata` 是人工修正后的最终导出来源。
- 若 `final_metadata` 为空但 `rules_metadata` 存在，导出可回退到 `rules_metadata`。
- 不应让后台展示字段名反向影响 LLM prompt 或 `METADATA_SCHEMA`。

### 9.8 Web 后台写侧函数

阶段 2 Web 后台 `/archives/{id}/edit` 引入一个新写侧函数,与既有 `apply_force_rerun_rules` 正交:

- `infrastructure/db/repositories.py:apply_manual_correction(session, *, archive, new_values, actor_user_id, reason=None) -> int`:供 Web 后台 POST 调用。只接受 4 个字段(`题名` / `责任者` / `实体分类号` / `保管期限`)的新值,内部 diff、记 `metadata_revisions` 与 `audit_logs(action="manual_correction")`,把 `correction_status` 置为 `corrected`,并按新值重新派生 `retention_period_code`。函数自身不 commit;无差异返回 0(无副作用)。
- `new_values` 由 `ManualCorrectionInput` dataclass 承载,所有字段在 Web 入口处完成 strip / 长度 / enum 校验(`保管期限` ∈ `{"永久", "30年", "10年"}`),函数本身不二次校验。
- 与 `apply_force_rerun_rules(action="force_rerun_rules")` 互补:后者清掉 `corrected` 强制覆盖整套 metadata,适合 CLI 全字段重跑;前者只动 4 字段并置 `corrected`,适合 Web 端人工微调。两条路径写不同的 `action` 标签,便于审计反查。

## 10. 分阶段落地

### 阶段 1：数据契约和旁路入库

- 增加 `DATABASE_URL` 配置。
- 增加显式项目配置，例如 `PROJECT_KEY` 和可选 `PROJECT_NAME`；未指定 `PROJECT_KEY` 时不执行数据库续号入库。
- 增加迁移工具。
- 建立项目、批次、档案、页面基础信息、任务、尝试、导出记录表。
- 项目作为件号连续性的边界，批次必须绑定项目。
- 引入 `sequence_counters`，件号分配改为项目范围内数据库事务续号。
- 建立 `archive_records(project_id, archive_no)` 组合唯一约束或非空 partial unique index。
- `archive_pages` 一期至少保存图片路径、文件名、页号和 hash；页面级 OCR 文本可先预留字段，不强制入库。
- 当前文件输出保持不变。
- 成功档案写入数据库并支持基础查询。
- `final_metadata` 作为一期导出兼容来源；`llm_metadata/rules_metadata` 字段可先预留，待分类器返回快照边界后再完整写入。

### 阶段 2：metadata 快照与人工修正

- 调整分类器返回结构或增加处理钩子，保存 `llm_metadata`、`rules_metadata`、`final_metadata` 三个快照。
- 增加人工修正 API。
- 保存 `metadata_revisions`。
- 保存 `audit_logs`。
- 使用 `final_metadata` 作为人工校对后的最终结果。

### 阶段 3：账户、单位、权限与管理后台 API

- 增加单位、用户、角色、权限表。
- 内置平台管理员、单位管理员、单位操作员三类角色。
- 项目绑定单位，批次和档案可冗余 `organization_id` 方便查询。
- 提供批次列表、批次详情 API。
- 提供档案查询、筛选、详情 API。
- 提供人工修正、审计日志、导出记录查询 API。

### 阶段 4：页面 OCR 和全文检索增强

- 扩展 OCR 返回结构，保存页面级 OCR 文本与置信度。
- 增加题名、责任者、OCR 文本检索索引。
- 根据规模评估 PostgreSQL 中文分词或外部搜索引擎。

### 阶段 5：后台任务队列

- 当后台提交、并发处理、取消、限流、定时重试成为刚需时，再引入 Celery、Redis、RabbitMQ 或其他任务系统。
- 队列系统复用 `processing_jobs` 和 `processing_job_attempts`，不重新定义状态模型。

## 11. 仍需业务确认的问题

阶段 1 决策已收敛：

1. **同一 `batch_key` 重跑默认行为** → 已定为 `skip-success`（只重跑 failed/error/pending），`rerun-all/force-renumber` 作为显式 flag。详见 §7.1。
2. **项目删除策略** → 已定为只允许 `disabled/archived`，不引入软删除；FK `ON DELETE RESTRICT`。详见 §4.1。
3. **人工修正后是否允许规则覆盖 `final_metadata`** → 已定为默认保护，仅在显式 `--force-rerun-rules` 下覆盖，覆盖必须由系统自动写 `metadata_revisions` 与 `audit_logs`。详见 §5.1。阶段 1B 已落地相应表与 `apply_force_rerun_rules` / `BatchRecorder.force_rerun_rules_for_archive` / `utils/force_rerun_cli.py`。

阶段 3-4 决策项（不阻塞一期旁路入库，落到对应阶段时再回头确认）：

4. 页面级 OCR 文本是否有敏感信息保存限制？影响阶段 4 `archive_pages.ocr_text` 是否预留及脱敏字段设计。
5. Web 后台是否需要字段级权限（如允许查看 metadata 但不允许查看 OCR 原文）？影响阶段 3 接口粒度与 ACL 复杂度。

注：原列于此的"平台管理员是否允许直接修改所有单位的 AI 结果"已在 §4.2 收敛为"暂不强制，作为业务配置项保留"，不再列入待确认。

## 12. 结论

PostgreSQL 集成应先从数据契约和旁路入库开始，而不是直接把现有批处理改造成数据库驱动流程。关键原则是：

1. 现有 `batch_summary.json` 和 JSON/CSV 导出契约不破坏。
2. 中文 metadata key 继续作为导出和 LLM 契约，数据库英文列只做查询冗余。
3. 处理状态、复核状态、修正状态必须拆分。
4. LLM 原始结果、规则结果、人工校对后的最终结果必须按快照保存。
5. 项目是件号/档号连续性的边界，件号计数必须按项目数据库化，不能继续依赖进程内计数器。
6. 修正、审计和重试记录必须追加写入，不能只保存最后状态。
