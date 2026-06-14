# FileForge 数据库重构设计：上传与在线跑批

本文档描述当前数据库边界。项目暂无生产数据，因此采用重建式迁移：旧业务表可整体删除后按新 ORM 重建。

> 本文以当前代码实现为准,不是可执行验收记录。当前会话若为非可执行环境,只做静态核对；迁移、上传和在线跑批需要在目标环境执行。

## 目标边界

系统支持两种入口：

- 命令行批处理：`main.py -> BatchProcessor -> BatchRecorder`
- Web 在线跑批：浏览器上传图片/zip -> 创建上传批次 -> 后台任务复用 `BatchProcessor` -> 入库 -> Web 复核

核心原则：

- 原始文件保存在文件系统，数据库保存路径、校验值、任务状态和结构化结果。
- Web 不在请求线程同步执行 OCR/LLM；请求只创建任务，后台任务执行处理。
- OCR/LLM/规则主流程不分叉，在线跑批仍复用 `ArchiveClassifier + BatchProcessor + BatchRecorder`。
- 权限模型简化为 `app_users.role`，不再保留 `roles`、`permissions`、`user_roles`、`role_permissions` 四张表。

## 表分组

### 账号与项目

`organizations`

- 单位表。
- 关键字段：`name`、`code`、`status`。

`app_users`

- 用户表。
- 关键字段：`organization_id`、`username`、`password_hash`、`display_name`、`role`、`status`。
- `role` 取值：`platform_admin`、`org_admin`、`org_operator`。
- 权限由代码中的角色到权限映射生成，不落独立权限表。

`web_sessions`

- 浏览器登录会话。
- cookie 保存随机 token，数据库保存 `token_hash` 和 `csrf_token_hash`。

`projects`

- 档案项目。
- 关键字段：`organization_id`、`project_key`、`project_name`、`description`、`status`、`numbering_rule`。

### 上传

`upload_batches`

- 一次浏览器上传。
- 关键字段：`project_id`、`uploaded_by`、`upload_name`、`source_type`、`status`、`file_count`、`document_count`、`total_size_bytes`、`storage_root`。
- `status`：`uploading`、`uploaded`、`validated`、`processing`、`processed`、`failed`。

`uploaded_files`

- 上传后的单个图片文件。
- 关键字段：`upload_batch_id`、`original_filename`、`stored_path`、`file_ext`、`mime_type`、`size_bytes`、`sha256`、`page_no`、`document_key`、`status`。
- zip 上传按一级目录划分 `document_key`；散图上传作为同一个 `document_key`。

### 在线处理

`processing_batches`

- 一次处理批次，命令行和 Web 在线跑批共用。
- 关键字段：`project_id`、`upload_batch_id`、`batch_key`、`batch_name`、`trigger_type`、`input_dir`、`output_dir`、`batch_status`、`total_archives`、`total_pages`、`success_count`、`fail_count`。
- `trigger_type`：`manual_cli`、`web_upload`、`rerun`。
- `batch_status`：`queued`、`running`、`success`、`partial_failed`、`failed`、`cancelled`，兼容旧值 `completed`、`aborted`。

`processing_jobs`

- 单份档案处理任务，页面进度主要看这张表。
- 关键字段：`batch_id`、`project_id`、`upload_batch_id`、`archive_id`、`document_key`、`status`、`progress`、`current_stage`、`page_count`、`error_code`、`error_message`、`attempt_count`。
- `status`：`queued`、`ocr_running`、`llm_running`、`rules_running`、`exporting`、`success`、`failed`、`cancelled`、`error`。

`processing_events`

- 任务事件流，用于处理过程展示和排错。
- 关键字段：`job_id`、`batch_id`、`event_type`、`stage`、`message`、`payload`。

### 档案结果

`archive_records`

- 档案主结果表。
- 关键字段：`project_id`、`batch_id`、`job_id`、`upload_batch_id`、`archive_key`、`processing_status`、`review_status`、`correction_status`。
- 保存三份快照：`llm_metadata`、`rules_metadata`、`final_metadata`。
- 常用查询字段冗余为英文列：`title`、`responsible_party`、`archive_year`、`classification_code`、`retention_period`、`openness_status`、`archive_no`、`item_no` 等。

`archive_pages`

- 档案页面表。
- 关键字段：`archive_id`、`uploaded_file_id`、`page_no`、`image_path`、`ocr_text`、`ocr_confidence`、`layout_json`。

`llm_traces`

- LLM 调用追踪表。
- 关键字段：`archive_id`、`job_id`、`call_type`、`model_name`、`prompt_hash`、`raw_response`、`cleaned_response`、`parse_strategy`、`success`。
- `archive_records` 仍保留最近一次 LLM trace 缓存，便于旧详情页展示；完整历史以此表为准。

### 序号与导出

`sequence_counters`

- 件号计数器。
- 唯一范围：`project_id + archive_year + classification_code + retention_period_code`。

`export_files`

- 导出文件记录。
- 关键字段：`project_id`、`batch_id`、`export_type`、`file_path`、`row_count`。

### 修正与审计

`metadata_revisions`

- 字段级修正历史。
- 一次保存动作可产生多行，它们共享同一个 `revision_no`。
- `source` 用于区分 `rules_engine`、`manual_web`、`force_rerun`。

`audit_logs`

- 系统级审计日志。
- 记录上传、启动处理、人工修正、强制重跑、登录等行为。

## Web 上传处理流

1. 用户进入 `/uploads`。
2. 选择项目并上传图片或 zip。
3. 后端保存文件到 `WEB_UPLOAD_STORAGE_ROOT/{project_key}/{timestamp}/`。
4. 创建 `upload_batches` 和 `uploaded_files`。
5. 用户点击“开始处理”。
6. 创建 `processing_batches` 和 queued 状态的 `processing_jobs`。
7. FastAPI background task 调用 `run_upload_processing_batch`。
8. 后台任务复用 `BatchProcessor.process_directory()`。
9. `BatchRecorder` 写入 `archive_records`、`archive_pages`、`processing_jobs`、`processing_events`、`llm_traces`。
10. 处理完成后写入 `export_files`，Web 可查看批次、任务、档案详情和人工修正记录。

## 迁移策略

当前使用 `0005_rebuild_upload_online_processing`：

- 反射当前数据库。
- 删除除 `alembic_version` 外的所有应用表。
- 按 `infrastructure.db.models.Base.metadata` 重建全部表。

该迁移不支持 downgrade。若需要回退，直接重建数据库。

## 配置项

- `WEB_UPLOAD_STORAGE_ROOT`：上传文件保存根目录，默认 `input_documents/web_uploads`。
- `WEB_PROCESSING_OUTPUT_ROOT`：在线跑批输出根目录，默认 `output_results/web_runs`。
- `WEB_MAX_UPLOAD_BYTES`：单次上传总大小限制，默认 200 MiB。
- `WEB_MAX_UPLOAD_FILES`：单次上传文件数限制，默认 2000。
