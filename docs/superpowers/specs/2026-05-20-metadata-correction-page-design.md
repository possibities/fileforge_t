# 元数据人工修正页 设计

## 1 目标与背景

阶段 2 Web 后台已经完成账号登录、用户管理、批次/档案/修订/审计的只读查询;阶段 1B 已经把 `metadata_revisions` 与 `audit_logs` 落到 PostgreSQL,并提供了 `apply_force_rerun_rules`(CLI 强制重跑通路)的写侧实现。本设计补齐 Web 后台从只读跨到可写的第一步:允许在浏览器里对档案的核心元数据做人工修正,并把变更落到 revisions/audit 两张表,与 CLI force-rerun 路径正交共存。

本设计面向单个可交付里程碑:

- 给 `/archives/{id}/edit` 加 GET 表单页与 POST 写入路径。
- 在数据层加 `apply_manual_correction()` 一个新写侧函数。
- 修正后,档案 `correction_status` 置为 `corrected`,保护后续自动管线不再覆盖;CLI `--force-rerun-rules` 仍然可以显式清除。
- 修正只允许动 4 个核心字段:`题名`、`责任者`、`实体分类号`、`保管期限`。其它字段在编辑页只读展示。

## 2 范围

### 2.1 新增能力

| 能力 | 行为 |
| --- | --- |
| 修正表单 | GET `/archives/{id}/edit` 渲染 4 字段表单,其它 13 字段只读展示 |
| 提交修正 | POST `/archives/{id}/edit` 经 CSRF 校验、表单清洗后落库;成功跳详情,失败原页 + error |
| 写侧函数 | `infrastructure/db/repositories.py` 新增 `apply_manual_correction()` |
| 修订记录 | 每次提交把变化字段以共享 `revision_no` 写入 `metadata_revisions`,reason 取表单原文,空 reason 落字面 `manual_correction` |
| 审计记录 | 一次提交一条 `audit_logs`,`action="manual_correction"`,before/after 是完整 final_metadata 快照 |
| 状态切换 | 写入后 `archive.correction_status = "corrected"` |
| 权限 | 复用既有 `archive:correct`(三个内置角色都已 seed);组织隔离复用读侧 `_can_access_archive` |
| 详情页入口 | `archive_detail.html` 在用户拥有 `archive:correct` 时显示"修正"按钮 |
| 测试 | repository 单测 + 路由用例,均跑 SQLite in-memory |

### 2.2 不在本里程碑范围

- 不允许在 Web 上编辑 `档号` / `件号`;它们由 `SequenceGenerator` 分配,Web 改动会破坏起始号与唯一约束。补改这两个值继续走 CLI force-rerun。
- 不引入其它 14 个 metadata 字段(密级、保密期限、文件形成时间、立档单位名称等)。如需修改,继续走 CLI force-rerun;后续可在 §7 扩展。
- 不实现"草稿 + 审批"的两阶段提交。
- 不实现修订回滚 / undo 功能(`metadata_revisions` 是 append-only,事后必要时用 SQL 直补)。
- 不引入乐观锁(`version_id_col`)。一期采用 last-write-wins,两个并发 POST 各拿到一个 `revision_no`,两条修订都留痕。
- 不调整 OCR/LLM 主流程或 `processors/`、`core/`、`recorder.py`。
- 不新增 Alembic 迁移(`metadata_revisions`、`audit_logs` 表已就位)。

## 3 方案比较

### 3.1 推荐方案:在 `infrastructure/db/repositories.py` 新增 `apply_manual_correction()`

新建专用写侧函数,Web 路由只做"取表单 → 调函数 → commit / rollback"。函数内部复用既有 `_diff_metadata_to_revisions` / `record_revisions` / `record_audit_log` / `_REDUNDANT_COLUMN_MAP` / `_resolve_retention_code`。

优势:

- 语义清晰,与 `apply_force_rerun_rules` 各司其职:后者重跑 rules、清掉 `corrected`;新函数只动 4 字段、置 `corrected`。
- Web 层薄,只做表单清洗与权限校验。
- 函数级单测能完整覆盖 diff、redundant column、retention_period_code、correction_status、reason 落库等逻辑,不需要拉起 FastAPI。
- 后续若 CLI 要做人工修正子命令,可直接复用同一函数。

劣势:多一个函数,与 `apply_force_rerun_rules` 在 diff 步骤上有结构性相似(都借 `_diff_metadata_to_revisions`,可接受)。

### 3.2 方案 B:路由 handler 就地组装

POST handler 直接调 `record_revisions` + `record_audit_log` + 手动改 final_metadata。

不选原因:把领域逻辑泄漏到 web 层;CLI 后续复用需要重写;违反现有数据层封装。

### 3.3 方案 C:把 `apply_force_rerun_rules` 扩展成 `apply_metadata_change(action=...)`

一个函数承担两种调用方,通过 `action` 切换"清 corrected"还是"置 corrected"。

不选原因:把语义相反的两种操作塞进一个函数,if 分支多,既有 `apply_force_rerun_rules` 的测试需要全部调整;违反"一个函数一件事"。

## 4 架构

### 4.1 文件清单

| 文件 | 类型 | 责任 |
| --- | --- | --- |
| `infrastructure/db/repositories.py` | Modify | 新增 `apply_manual_correction()`、`ManualCorrectionInput` dataclass、`EDITABLE_FIELDS` 常量 |
| `web_admin/routes/archives.py` | Modify | 新增 `get_archive_edit_form()`、`post_archive_edit()`,新增常量 `ARCHIVE_CORRECT_PERMISSION = "archive:correct"` |
| `web_admin/templates/archive_edit.html` | Create | 4 字段表单 + 其余 13 字段只读 + 可选原因 + CSRF |
| `web_admin/templates/archive_detail.html` | Modify | 加"修正"按钮,按 `archive:correct` 权限展示 |
| `tests/test_db_repositories.py` | Modify | 新增 `TestApplyManualCorrection` |
| `tests/test_web_routes_archives.py` | Modify | 新增 `TestArchiveEditRoute` |
| `docs/postgresql_data_contract_design.md` | Modify | §4.7 / §9 注脚追加 `manual_correction` action 与 4 字段约束 |

不新增 Alembic 迁移。

### 4.2 数据流

```
GET /archives/{id}/edit
  └─ archives.py
       ├─ _require_archive_view → 登录 + archive:view
       ├─ 额外校验 archive:correct(无则 403)
       ├─ _can_access_archive → 组织隔离
       └─ 渲染 archive_edit.html(4 字段 prefill + 13 字段只读 + CSRF)

POST /archives/{id}/edit
  └─ archives.py
       ├─ 权限与组织校验(同上)
       ├─ verify_csrf_from_request
       ├─ 表单清洗(strip / 长度 / 保管期限 enum)
       │    失败 → 200 重渲 archive_edit.html + error,DB 无副作用
       ├─ 构造 ManualCorrectionInput
       ├─ repositories.apply_manual_correction(...)
       │    内部:
       │      1. old_final = dict(archive.final_metadata or {})
       │      2. new_final = old_final | {4 字段新值}
       │      3. diffs = _diff_metadata_to_revisions(old_final, new_final)
       │      4. if not diffs: return 0 (无副作用)
       │      5. rev_no = record_revisions(... reason = 表单原文 or "manual_correction")
       │      6. 同步 4 个冗余列 + 重算 retention_period_code
       │      7. archive.final_metadata = new_final
       │      8. archive.correction_status = "corrected"
       │      9. record_audit_log(action="manual_correction", before/after = full snapshot)
       │     10. flush + 返回 rev_no
       ├─ rev_no == 0 → 303 → /archives/{id}?notice=no_change
       ├─ rev_no > 0  → 303 → /archives/{id}
       └─ 未预期异常(DB/SQLAlchemy)→ session.rollback,FastAPI 默认 500 → error.html
```

### 4.3 函数签名

```python
EDITABLE_FIELDS: tuple[str, ...] = ("题名", "责任者", "实体分类号", "保管期限")
RETENTION_PERIOD_CHOICES: tuple[str, ...] = ("永久", "30年", "10年")

@dataclass
class ManualCorrectionInput:
    title: str                # 题名
    responsible_party: str    # 责任者
    classification_code: str  # 实体分类号
    retention_period: str     # 保管期限 ∈ RETENTION_PERIOD_CHOICES

def apply_manual_correction(
    session: Session,
    *,
    archive: ArchiveRecord,
    new_values: ManualCorrectionInput,
    actor_user_id: int,
    reason: Optional[str] = None,
) -> int:
    """对档案做人工修正:diff → revisions → 同步冗余列 + retention_period_code
    → 置 correction_status=corrected → audit。函数自身不 commit。
    返回写入的 revision_no;无差异返回 0(无 audit、无字段更新)。
    """
```

### 4.4 表单输入清洗

| 字段 | 规则 |
| --- | --- |
| 题名 | strip;非空;1–500 字符 |
| 责任者 | strip;非空;1–200 字符 |
| 实体分类号 | strip;非空;1–32 字符;自由文本(不强校验白名单) |
| 保管期限 | 必须 ∈ `{"永久", "30年", "10年"}` |
| 原因 | strip;可空;≤500 字符 |

任一项不通过 → 重渲 `archive_edit.html`,字段下方显示 error,**不进数据库**。

## 5 权限、并发与错误处理

### 5.1 权限矩阵

| 角色 / 状态 | GET edit | POST edit |
| --- | --- | --- |
| 未登录 | 303 → `/login` | 303 → `/login` |
| 缺 `archive:correct` | 403 | 403 |
| 跨组织访问 | 404(隐藏存在) | 404 |
| `org_operator` / `org_admin`(本组织) | 200 | 303 成功 / 200 含 error 重渲 |
| `platform_admin` | 200(任何组织) | 303 成功 / 200 含 error 重渲(任何组织) |

`archive:correct` 已在 `BUILTIN_ROLES` 中 seed 给三个内置角色,无需修改 `accounts.py`。

### 5.2 并发

一期采用 **last-write-wins**,无乐观锁:

- 同一档案的两个并发 POST 各自走 `SELECT max(revision_no) + 1` 取号,各拿 `N` 与 `N+1`;两条修订都留痕。
- 最终 `final_metadata` 由后到的事务覆盖;前一次的字段值仍可在 `metadata_revisions` 中查到。
- 不在 ORM 加 `version_id_col`,不引入 `If-Match` header。
- 后续若并发频繁,可在 `apply_manual_correction` 入口处显式 `SELECT … FOR UPDATE` 锁档案行,本期不做。

### 5.3 错误处理

| 场景 | HTTP | 行为 |
| --- | --- | --- |
| 表单字段不合法 | 200 重渲 `archive_edit.html` | inline error,DB 无副作用 |
| `保管期限` 不在 enum | 200 重渲 | 同上 |
| 档案不存在或越权 | 404 | `error.html` |
| CSRF 失败 | 403 | `error.html` |
| 无差异提交 | 303 → `/archives/{id}?notice=no_change` | 详情页可显示提示;一期不强制 base.html 添加 flash 区域,detail 页内做最小展示 |
| 未知异常(DB 错误、SQLAlchemy 异常等) | 500 | rollback + 日志;`error.html` 渲染 |

**校验责任划分**:所有字段清洗与长度上限校验都在路由层做完(见 §4.4),`apply_manual_correction` 内部信任输入、不再二次校验。函数只在 `archive` 参数本身不可写(理论上不会发生)等编程性错误时才会抛异常,这种情况上升到 500。

### 5.4 关键约束

1. 编辑页只能动 4 个字段。其余 13 个字段以只读形式展示但不进表单。
2. `correction_status` 一旦被本路径置为 `corrected`,自动管线 `apply_classification_result` 就跳过覆盖,直到 CLI `--force-rerun-rules` 显式清掉。两路径正交,需要专门测试覆盖。
3. `retention_period_code` 永远跟随 `归档年度 + 保管期限` 由 `_resolve_retention_code` 派生,不接受手工写。
4. reason 表单留空 → `metadata_revisions.reason = "manual_correction"`(字面)以便 SQL 反查;非空 → 原文入库。
5. `audit_logs.before_data` / `after_data` 是完整 `final_metadata` 快照(17 字段),不只 4 字段。
6. 函数内部不 commit;事务边界由 web 路由控制。

## 6 测试策略

### 6.1 Repository 层:`tests/test_db_repositories.py` 新增 `TestApplyManualCorrection`

| 用例 | 验证点 |
| --- | --- |
| `test_no_diff_returns_zero_and_writes_nothing` | 新值等于 old_final → 返回 0;revisions/audit 无新行;correction_status 不变 |
| `test_single_field_change_writes_one_revision_and_audit` | 只改题名 → revisions 1 行;audit 1 行 `action=manual_correction`,before/after 是完整快照 |
| `test_multi_field_change_shares_one_revision_no` | 4 字段都改 → 4 行 revisions 共享同一 `revision_no` |
| `test_retention_change_recomputes_retention_period_code` | 改 `保管期限` 10年→30年 → `retention_period_code` 重新派生(2025 用新码 D30) |
| `test_classification_change_updates_redundant_column_only` | 改 `实体分类号` → `archive.classification_code` 同步;`archive_no` / `item_no` 不动 |
| `test_other_metadata_keys_are_preserved` | old_final 17 键,只改 4 → 其余 13 键值保留 |
| `test_sets_correction_status_to_corrected` | 调用后 `correction_status == "corrected"`(即使原本是 pending 或 None) |
| `test_reason_empty_stores_literal_marker` | reason 传 None → 入库 `"manual_correction"`;reason 传 `"OCR 漏字"` → 入库原文 |
| `test_actor_user_id_recorded_on_both_tables` | revisions.created_by 与 audit.actor_user_id 都等于传入 user_id |
| `test_force_rerun_rules_can_override_after_manual_correction` | 调完 `apply_manual_correction` 后 `apply_force_rerun_rules` 仍能清掉 corrected 并覆盖 |

### 6.2 Web 路由层:`tests/test_web_routes_archives.py` 追加 `TestArchiveEditRoute`

| 用例 | 验证点 |
| --- | --- |
| `test_get_edit_form_unauthenticated_redirects_to_login` | 无 cookie → 303 `/login` |
| `test_get_edit_form_missing_permission_returns_403` | 用一个不含 `archive:correct` 的合成角色 → 403 |
| `test_get_edit_form_cross_org_returns_404` | org_operator 访问别家档案 → 404 |
| `test_get_edit_form_renders_prefilled_with_current_values` | 模板上下文含 4 字段当前值 + 13 个只读字段 + CSRF token |
| `test_post_edit_csrf_missing_returns_403` | 无 CSRF cookie/token → 403 |
| `test_post_edit_invalid_retention_period_re_renders_with_error` | 保管期限 `"5年"` → 200 重渲 + error,DB 无新行 |
| `test_post_edit_blank_title_re_renders_with_error` | 题名 strip 后空 → 200 重渲 + error |
| `test_post_edit_too_long_title_re_renders_with_error` | 题名 > 500 字符 → 200 重渲 + error |
| `test_post_edit_success_redirects_to_detail` | 4 字段合法 → 303 → `/archives/{id}`,DB revisions+audit 新增 |
| `test_post_edit_no_change_redirects_with_notice` | 提交未变化值 → 303 → `/archives/{id}?notice=no_change`,DB 无新行 |
| `test_post_edit_platform_admin_can_edit_any_org` | platform_admin → 200 / 303 |
| `test_post_edit_org_operator_cannot_edit_other_org` | org_operator 跨组织 → 404 |
| `test_post_edit_records_actor_user_id_from_session` | revisions.created_by 与 audit.actor_user_id 等于 session 用户 id |

### 6.3 模板层

- `archive_edit.html` 渲染由路由用例间接覆盖(检查 form action、input name、错误展示位)。
- `archive_detail.html` 修改加一条 `test_archive_detail_shows_edit_link_when_user_has_correct_permission`,确认有 `archive:correct` 时按钮存在,没有则不存在。

### 6.4 回归

- `python -m unittest discover -s tests` 应继续全绿(开发机不跑;运行环境为 Miniforge env)。
- 预计新增 ~25 条用例,与现有 244 条合计 ~269 条。

### 6.5 不覆盖

- 真并发条件下的 `revision_no` 冲突测试。
- 多档案批量修正。
- 修正历史回滚 / undo。
- 跨字段联动校验(如分类号变了是否要同步分类名称)。

## 7 后续可能扩展

- 把可编辑字段从 4 个扩到全部 13 个非 sequence 字段。
- 引入"草稿 + 审批"两阶段:`metadata_revisions` 加 `status` 列,审批通过后才写入 `final_metadata`。
- 显式 `SELECT … FOR UPDATE` 行锁 + 422 冲突提示,替换 last-write-wins。
- 跨字段联动校验,例如分类号 ↔ 分类名称 自动同步、保管期限 ↔ 保密期限 一致性。
- 修正历史回滚一键操作(append 反向 revision,不真删旧行)。
