# 档案智能分类系统 项目全景

> 仓库名:`fileforge`。这是一份给"没读过代码的人"的项目全景:它解决什么问题、由哪些模块组成、数据怎么流动、有什么页面、目前到了什么程度。

---

## 1 一句话项目定位

**输入**:一堆扫描成图片的纸质档案(每个档案一个子目录,目录里是该档案的多页扫描件),也可以通过 Web 上传散图或 zip。
**输出**:每份档案的结构化元数据(题名、责任者、分类号、保管期限、档号、件号等共 17 个字段),以 JSON + CSV 双格式落盘,可选同时写入 PostgreSQL,并提供 Web 后台供上传跑批、人工查看、过滤、修正。

**核心流程**:OCR(图像→文字) → LLM(文字→结构化字段) → 规则引擎(校验/修正/补全) → 序号分配 → 导出。

---

## 2 业务背景

档案馆/档案室长期积压大量纸质档案,每份档案在归档前必须填写一组规范的元数据字段(年度、分类号、保管期限、责任者、题名、立档单位、密级、开放状态、档号、件号 ...),这些信息原本由人工逐页阅读后填到 Excel 或专业归档软件,效率低且容易出错。

本项目用"OCR + LLM + 规则引擎"的组合让这一过程半自动化:

- **OCR** 把扫描件转成可读文本(含版面信息)
- **LLM** 从 OCR 文本里抽取业务字段
- **规则引擎** 校验字段合法性、修正常见 LLM 错误(比如保管期限填错档次、分类号写新码而不是 2020 前的旧码)、补全 LLM 漏填字段
- **序号生成器** 按"项目→年度→分类→保管期限"四级分组分配单调递增的件号与档号
- **Web 后台** 支持浏览器上传、后台在线跑批、结果复核和 4 个最易错字段修正,所有上传、处理和修正都可追溯

最终交付物是档案员可以直接导入档案管理系统的 JSON/CSV(中文字段名,字段顺序按 `config/exporter.json` 模板),以及一份可审计的 PostgreSQL 持久化记录。

---

## 3 系统总体架构

### 3.1 三层视图

```
┌─────────────────────────────────────────────────────────────────────┐
│                          用户层 (User-Facing)                        │
│                                                                      │
│   ┌──────────────┐   ┌──────────────┐   ┌───────────────────────┐   │
│   │  main.py     │   │   Web 后台    │   │     CLI 工具集         │   │
│   │ (跑分类管线) │   │ (浏览器交互) │   │ (维护、查询、强制重跑) │   │
│   └──────┬───────┘   └──────┬───────┘   └───────────┬───────────┘   │
└──────────┼──────────────────┼───────────────────────┼───────────────┘
           │                  │                       │
┌──────────┼──────────────────┼───────────────────────┼───────────────┐
│          ▼                  ▼                       ▼                │
│                     核心业务层 (Core)                                │
│                                                                      │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │  ArchiveClassifier(单档案编排:OCR→LLM→Rules→序号→导出)   │  │
│   │  ├─ OcrClient    (PaddleOCR + 版面重建)                       │  │
│   │  ├─ LlmClient    (vLLM HTTP + 多级 JSON 解析 + 二次重写)     │  │
│   │  ├─ RulesEngine  (10 条补充规则 + 16 条题名清洗 + 期限锁定)  │  │
│   │  └─ SequenceGenerator (件号/档号分配,2020 年码切换)         │  │
│   └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │  BatchProcessor (多档案迭代 + 错误隔离 + 统计)               │  │
│   │  Exporter       (JSON + CSV 双输出)                          │  │
│   └──────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
           │                  │                       │
┌──────────┼──────────────────┼───────────────────────┼───────────────┐
│          ▼                  ▼                       ▼                │
│                    基础设施层 (Infrastructure)                       │
│                                                                      │
│   ┌──────────────┐   ┌──────────────────────────────────┐           │
│   │  vLLM 服务   │   │  PostgreSQL                       │           │
│   │  (外置 HTTP) │   │  (旁路写入 + 读侧查询 + Web)     │           │
│   └──────────────┘   └──────────────────────────────────┘           │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.2 核心边界

- **OCR/LLM/规则** 这条**热路径**是项目的论文贡献区,绝大部分文件没有依赖外部服务的硬要求(除了 vLLM 是 HTTP 调用)
- **PostgreSQL 入库**对 `main.py` 是**旁路**,管线不开 DB 也能跑(`DATABASE_URL` 留空时整条管线行为不变)
- **Web 后台**依赖数据库,支持上传图片/zip 并启动在线跑批;请求线程只建上传批次和处理批次,OCR/LLM 在 FastAPI background task 中执行,仍复用 `ArchiveClassifier + BatchProcessor + BatchRecorder`
- **人工修正保护**仍然成立:Web 改了元数据后,后续自动跑同一档案时会被 `correction_status = "corrected"` 标志保护,不覆盖人工结果

---

## 4 端到端数据流

跟踪一个名为 `202508_dangwei_meeting_01` 的档案(目录下含 `page_001.jpg` 与 `page_002.jpg`)从入到出的全过程。

### 4.1 输入

```
input_documents/
└─ 202508_dangwei_meeting_01/
   ├─ page_001.jpg   ← 党委会议纪要扫描件第一页
   └─ page_002.jpg   ← 第二页
```

启动方式有两种:

- 命令行:`python main.py`(`DATABASE_URL` 已设则同时写库)
- Web:`/uploads` 上传图片或 zip,再点击"开始处理"创建在线跑批

### 4.2 阶段 1:目录扫描(`BatchProcessor.process_directory`)

`processors/batch_processor.py` 遍历 `input_documents/`,把每个子目录认作一个档案,把目录里所有支持的图片扩展名(`.jpg/.jpeg/.png/.bmp/.tiff/.tif`)按文件名排序作为该档案的页序。

### 4.3 阶段 2:OCR(`OcrClient.recognize_pages`)

`infrastructure/ocr_client.py`:

1. 用 PaddleOCR 对每页图片做检测 + 识别,得到一组带 bounding box 的文本片段(`[(text, bbox, confidence), ...]`)
2. **关键创新点**:不是简单把所有文本片段按读取顺序拼接,而是用 bbox 中心点做**聚类**重建版面:
   - 按 Y 坐标聚类成"行"(同一行的 bbox 中心 Y 距离 < 阈值)
   - 行内按 X 坐标排序得到读取顺序
   - X 距离 > 阈值视为"空格"(列与列之间)
   这样能恢复"标题居中、正文左对齐、签字日期右下"这类版面信号,提升下游 LLM 抽取效果
3. 输出按页拼成完整 OCR 文本

可选预处理:`OCR_ENABLE_PREPROCESS=true` 时会做轻量去噪+对比度增强,对低质量扫描件有 5–10% 准确率提升。

### 4.4 阶段 3:LLM 抽取(`LlmClient.extract_metadata`)

`infrastructure/llm_client.py`:

1. `ArchiveClassifier` 拼装 prompt:`prompts/*.txt` 里的规则模板 + `prompts/examples.json` 里的少样本 + OCR 文本
2. 通过 OpenAI Python SDK 把这个 prompt 发给本地 vLLM 服务(默认 `http://localhost:8000/v1`,模型 `qwen3-32b-awq`)
3. 强制 JSON 输出:`response_format={"type": "json_object"}`
4. Qwen3 系列:`chat_template_kwargs={"enable_thinking": False}`(关闭"思考前缀",否则 JSON 会被前置 `<think>...</think>` 污染)
5. **多级降级解析**(因为 LLM 输出哪怕设了 JSON 模式仍可能有问题):
   - 第一级:`json.loads(response)` — 成功就走完
   - 第二级:修引号(全角→半角)、修尾逗号、去 ```json fence — 再 `json.loads`
   - 第三级:全部失败 → 按 17 个字段名用正则逐个抽取,组成 dict
   - 三级全失败,日志告警,字段保留空

LLM 返回类似:

```json
{
  "门类": "DQ",
  "归档年度": "2025",
  "实体分类号": "DQL",
  "实体分类名称": "党群类",
  "保管期限": "永久",
  "责任者": "县委办公室",
  "题名": "县委 2025 年第三次党委会议纪要",
  "文件形成时间": "2025-08-15",
  ...
}
```

LLM trace(原始响应、清理后响应、解析策略)同步存到 `last_trace` 字段,后续会随档案一起入库到 `archive_records.llm_*` 列,便于事后排查 LLM 行为。

### 4.5 阶段 4:规则引擎(`RulesEngine.apply_rules`)

`core/rules_engine.py` — 严格优先级,从上到下逐条执行:

1. **字段合法性校验**:`密级` ∈ {公开/秘密/机密/绝密},`保管期限` ∈ {永久/30年/10年};非法值用 LLM 输出或回退默认
2. **10 条补充分类/期限规则**(部分举例):
   - rule 2:题名含"简报" → 保管期限锁定为 `10年`,设置 `period_locked=True` 标志
   - rule 3:题名含"年度报告" → 保管期限锁定为 `30年`(若未被 rule 2 锁)
   - rule 5:题名含"工资表/合同/会计凭证" → `永久`
   - rule 7:`文件编号` 非空但 `保管期限` 仍空 → 兜底为 `30年`
   - 已锁定的字段后续规则跳过(避免冲突)
3. **开放状态判定**:优先级 `密级 > 个人隐私关键词 > 商业秘密 > 负面信息`,任意一项命中就把 `开放状态` 设为 `不开放`,并写 `延期开放理由`
4. **分类号校验**:`归档年度 < 2020` → 必须用旧码(001/002/003 ...),否则用新码(DQL/ZHL/YWL ...)。LLM 写错的码会被纠正
5. **题名清洗**(16 条 regex):删冗余前缀("关于"开头但内容已自带主语)、统一全/半角、删多余空格、删年度重复等
6. **rule 11 特殊**:检测**文学性简报题名**(只有"春风行动简报""保护母亲河简报"这类没有责任者+具体事件的题名),不直接改,而是设 `_需重构简报题名=True` 标志,由 `ArchiveClassifier` 二次调 LLM 重写;失败则在 `备注` 加 `【待核查】` 警告,管线**不崩**

输出:校正后的 metadata dict。

### 4.6 阶段 5:序号生成(`SequenceGenerator.assign`)

`core/sequence_generator.py`:

按 (项目, 归档年度, 实体分类号, 保管期限) 四元组分组,组内按"件号"单调递增。

档号格式取决于年份:
- **2020 年及之后**(新码):`{年}-{分类号字母码}-{期限码}-{4 位序号}`,例如 `2025-DQL-Y-0001`(Y=永久、D30=30年、D10=10年)
- **2020 年之前**(旧码):`{年}-{分类号数字码}-{期限码}-{4 位序号}`,例如 `2018-001-Y-0001`

新旧码映射表在 `core/sequence_generator.py` 与 `constants.py` 双份维护。

### 4.7 阶段 6:数据库写入(`BatchRecorder`,命令行可选,Web 必需)

`infrastructure/db/recorder.py`:

每完成一个档案立刻写入 PostgreSQL:
- `archive_records` 主表:`final_metadata`(完整 17 字段 JSON)+ 17 个冗余列(题名/责任者/分类号/期限等热查询字段)+ `llm_raw_response` / `llm_cleaned_response` / `llm_parse_strategy`(LLM trace)
- `archive_pages`:每页一行,存图片路径与 OCR 文本
- `processing_batches`:批次汇总(命令行和 Web 在线跑批共用)
- `processing_jobs`:单份档案处理任务,支撑在线进度页
- `processing_events`:批次/任务事件流,支撑过程排错
- `llm_traces`:LLM 调用历史,完整记录原始响应、清理后响应和解析策略
- `metadata_revisions`:本次跑产生的字段修正(规则引擎相对 LLM 原始输出的差异,事后可追溯哪些字段是被规则改的)
- `audit_logs`:批次启动/完成、人工修正、CLI force-rerun 等行为日志

**DB 失败不影响管线**:任何 PG 异常都被 BatchRecorder 捕获并日志,管线继续完成 JSON/CSV 交付。`correction_status="corrected"` 的档案受保护,自动管线不覆盖。

### 4.8 阶段 7:导出(`Exporter.export_to_json` / `export_to_csv`)

`processors/exporter.py`:

读 `config/exporter.json` 拿到字段顺序(中文字段名,与档案管理系统对齐),把所有档案的结果落两份文件:

```
output_results/
├─ archive_results_20250815_143027.json    # 全批次汇总
├─ archive_results_20250815_143027.csv     # CSV 表格
├─ 202508_dangwei_meeting_01_result.json   # 单档案详情
├─ 202508_xianzhang_speech_result.json
└─ ...
```

CSV 字段顺序为档案员习惯的中文列名,可直接用 Excel 打开或导入档案管理软件。

---

## 5 子系统深入

### 5.1 OCR 层 (`infrastructure/ocr_client.py`)

- **依赖**:PaddleOCR 2.x + paddlepaddle(GPU 版用 `paddlepaddle-gpu`)
- **关键参数**(都可被环境变量覆盖):
  - `OCR_LANG="ch"` — 中文
  - `OCR_USE_GPU=true` — 需要 CUDA
  - `OCR_DET_DB_THRESH=0.2` — 文本检测置信度下限
  - `OCR_DET_DB_BOX_THRESH=0.45` — 文本框得分阈值
  - `OCR_DET_DB_UNCLIP_RATIO=1.8` — 检测框膨胀系数
  - `OCR_ENABLE_PREPROCESS=true` + `OCR_PREPROCESS_*` — 预处理放大/对比度/二值化
  - `OCR_RETRY_*` — 低置信度时自动二次识别(放大+预处理后再跑)
- **版面重建算法**:核心思想:
  1. 每个 bbox 取中心点 (cx, cy)
  2. 按 cy 排序,相邻 bbox 中心 Y 距离 < 行高阈值 → 归入同一行
  3. 行内按 cx 排序,X 间距 > 字宽阈值 → 插入空格
  4. 行之间用 `\n` 连接

### 5.2 LLM 层 (`infrastructure/llm_client.py`)

- **不在本进程加载模型**:只通过 HTTP 调外置 vLLM,`OpenAI()` SDK 客户端
- **prompt 装配**:`ArchiveClassifier.__init__` 时读 `prompts/` 下:
  - `metadata_rules.txt` — 字段定义、合法值、抽取规则
  - `format_guide.txt` — JSON 格式要求
  - `examples.json` — few-shot 样例
  - `briefing_rewrite.txt` — 简报题名二次重写专用 prompt
- **三级解析**见 §4.4
- **二次调用**仅在 rule 11 触发时;失败回退不抛异常(管线鲁棒性)

### 5.3 规则引擎 (`core/rules_engine.py`)

文件约 600 行,但结构清晰:

| 段 | 行数 | 责任 |
| --- | --- | --- |
| `apply_rules()` | 入口 | 按优先级编排下列方法 |
| `_validate_fields` | ~40 行 | 字段合法性 |
| `_apply_supplementary_rules` | ~200 行 | 10 条补充规则 + 期限锁定 |
| `_determine_openness` | ~60 行 | 开放状态判定 |
| `_validate_classification_code` | ~30 行 | 新旧码切换 |
| `_clean_title` | ~150 行 | 16 条 regex 题名清洗 |

`constants.py` 集中存所有关键词列表、码表、阈值,**改规则只需要改 `constants.py`,不动逻辑代码**。

**期限锁定**机制:`period_locked` 局部 flag 防止后续规则覆盖已经被 rule 2(简报→10年)等强约束规则锁定的期限值。

### 5.4 序号生成器 (`core/sequence_generator.py`)

- **DB 启用时**:`SequenceCounter` 表存当前各组的最大件号,新档案以 `SELECT FOR UPDATE` + `MAX(item_no) + 1` 在事务里分配(`infrastructure/db/allocator.py`)
- **DB 关闭时**:内存累加,跑完一批后丢失;只适合"一次跑完不重跑"的演示场景
- 新旧码切换发生在 `归档年度 ≥ 2020` 边界
- `档号` 格式:`{年}-{分类号}-{期限码}-{4 位件号}`

### 5.5 导出层 (`processors/exporter.py`)

- 字段映射表写在 `config/exporter.json`,纯数据驱动
- JSON 与 CSV 双输出,JSON 是嵌套对象(每档一个),CSV 是平铺表格(每档一行)
- 失败档案也会出现在 CSV/JSON 里(行中相应字段空,`error_code`/`error_message` 列填错误)

### 5.6 PostgreSQL 持久化层 (`infrastructure/db/`)

数据库表分三类:

**上传与处理**:
- `upload_batches` — 浏览器上传批次
- `uploaded_files` — 上传后的图片文件,记录原文件名、保存路径、sha256、页号和档案键
- `projects` — 项目级元数据,序号边界
- `processing_batches` — 一次 `main.py` 或 Web 在线跑批对应一个处理批次
- `processing_jobs` — 单份档案处理任务,用于在线进度展示
- `processing_events` — 处理事件流

**档案结果**(管线产生):
- `archive_records` — 档案主表(17 字段 metadata + 三快照 + LLM trace)
- `archive_pages` — 每页 OCR 文本与图片路径
- `llm_traces` — LLM 调用历史
- `sequence_counters` — 件号分组计数器
- `export_files` — 每次 export 的文件路径与行数

**人工修正**(Web/CLI 写入):
- `metadata_revisions` — append-only 字段级修订;同次修订共享 `revision_no`
- `audit_logs` — 系统行为审计(force-rerun、manual-correction、登录等)

**Web 后台账号体系**:
- `organizations` — 单位
- `app_users` — 用户(带密码 pbkdf2 哈希和 `role` 字段)
- `web_sessions` — 浏览器登录会话(只存 token 的 sha256,不存明文)
- 权限不再落 4 张 RBAC 表,而是由 `infrastructure/db/accounts.py` 中的内置角色映射生成

**Alembic 迁移**(`infrastructure/db/migrations/versions/`):
- `0001_init_phase1` — 业务实体表
- `0002_revisions_audit` — 人工修正 + 审计
- `0003_web_admin_accounts` — 账号体系 6 表
- `0004_web_sessions` — Web session
- `0005_rebuild_upload_online_processing` — 重建为上传 + 在线跑批 schema,删除旧应用表后按当前 ORM 重建

**读侧 API**(`infrastructure/db/queries.py`):返回 frozen dataclass,Web + CLI 共用:
- `list_batches` / `get_batch_detail`
- `list_archives`(支持 12 字段过滤)/ `get_archive_detail`
- `list_revisions` / `list_audit_logs`
- `list_upload_batches` / `list_processing_jobs` / `list_processing_events`

**写侧服务**:按职责分文件:
- `accounts.py` — 账号、角色、组织
- `projects.py` — 项目管理(Web 新增)
- `repositories.py` — 档案/批次/页面的写侧 + force-rerun
- `recorder.py` — `BatchRecorder` 把 main.py 输出旁路写库

### 5.7 Web 管理后台 (`web_admin/`)

- **框架**:FastAPI + Jinja2 服务端渲染,无前端构建链
- **会话**:HttpOnly cookie(session_token) + 数据库存 `sha256(token)`,会话有效期默认 8 小时
- **CSRF**:每个 POST 表单都校验 csrf token(cookie + form 双重校验)
- **权限**:基于角色 + 权限码;每个路由前置 `_require_*_manage` helper
- **组织隔离**:非 `platform_admin` 用户的 list/detail 自动按 `organization_id` 过滤;跨单位访问统一 404(隐藏存在)

完整页面清单见 §7。

---

## 6 数据模型(简化版)

```
organizations  ──┐
                 │ 1:N
     ┌───────────┴───────────┐
     │                       │
   projects                 app_users(role)
     │ 1:N                    │ 1:N
     ▼                        ▼
   upload_batches ──1:N──→ uploaded_files
     │ 0/1
     ▼
   processing_batches ──1:N──→ processing_jobs ──1:N──→ processing_events
     │ 1:N                         │
     ▼                             │
   archive_records ────────────────┘
      ├──→ archive_pages       (N 页)
      ├──→ llm_traces          (N 次 LLM 调用)
      ├──→ metadata_revisions  (N 次修订)
      └──→ audit_logs          (manual_correction / force_rerun_rules / upload / batch)

   sequence_counters  (按 project_id + year + classification + retention 分组)
   export_files       (1:N 与 processing_batches)
   web_sessions       (N:1 与 app_users)
```

`archive_records` 的核心字段:
- 标识:`project_id`、`batch_id`、`archive_key`(目录名)
- 状态:`processing_status` (pending/success/failed) / `review_status` / `correction_status`
- 17 个 metadata 冗余列(用于过滤,如 `title` / `classification_code` / `retention_period`)
- 三快照:`llm_metadata`(原始)、`rules_metadata`(规则后)、`final_metadata`(最终)
- LLM trace:`llm_raw_response` / `llm_cleaned_response` / `llm_parse_strategy`

---

## 7 Web 后台页面清单

| 路径 | 用途 | 权限 |
| --- | --- | --- |
| `/login` | 登录 | 公开 |
| `/` | 登录后首页(导航) | 已登录 |
| `/admin/users` | 用户列表 | `user:manage` |
| `/admin/users/new` | 新建用户 | `user:manage` |
| `/admin/users/{id}/reset-password` | 重置密码 | `user:manage` |
| `/admin/organizations` | 单位列表 | `organization:manage` |
| `/admin/organizations/new` | 新建单位 | `organization:manage` |
| `/admin/organizations/{id}/disable\|enable` | 启用/禁用单位 | `organization:manage` |
| `/admin/projects` | 项目列表(可按单位过滤) | `project:manage` |
| `/admin/projects/new` | 新建项目(`org_admin` 锁本单位) | `project:manage` |
| `/admin/projects/{id}/disable\|enable` | 启用/禁用项目 | `project:manage` |
| `/uploads` | 上传图片/zip,查看上传记录 | `batch:manage` |
| `/uploads/{id}/start` | 启动上传批次的在线处理 | `batch:manage` |
| `/processing/batches/{id}` | 在线跑批进度、任务和事件 | `batch:manage` |
| `/batches?project_key=K` | 按项目查批次 | `archive:view` |
| `/batches/{id}` | 批次详情(失败码统计、schema 三件套) | `archive:view` |
| `/batches/{id}/archives` | 档案列表(12 字段过滤) | `archive:view` |
| `/archives/{id}` | 档案详情(三快照、页面图片) | `archive:view` |
| `/archives/{id}/edit` | 人工修正 4 个字段 | `archive:correct` |
| `/archives/{id}/revisions` | 修订历史 | `archive:view` |
| `/archives/{id}/audit` | 审计日志 | `audit:view` |

**内置角色**:
- `platform_admin` — 平台管理员,全权,跨单位
- `org_admin` — 单位管理员,看本单位
- `org_operator` — 单位操作员,看本单位,可修正

---

## 8 配置参数(`config/config.py`)

环境变量覆盖默认值。

### 8.1 OCR
- `OCR_LANG="ch"` / `OCR_USE_GPU=true` / `OCR_USE_ANGLE_CLS=true`
- `OCR_DROP_SCORE=0.1` / `OCR_DET_DB_THRESH=0.2` / 检测/识别阈值
- `OCR_ENABLE_PREPROCESS=true` / `OCR_PREPROCESS_*` 预处理
- `OCR_RETRY_*` 低置信度二次识别

### 8.2 LLM
- `LLM_BASE_URL="http://localhost:8000/v1"`
- `LLM_API_KEY="EMPTY"`
- `LLM_MODEL_NAME="qwen3-32b-awq"`
- `LLM_TEMPERATURE=0.1` / `LLM_MAX_TOKENS=512`
- `LLM_REQUEST_TIMEOUT=300` 秒
- `LLM_ENABLE_THINKING=false` — **必须 false**,否则 JSON 抽取会被 `<think>` 前缀污染

### 8.3 路径
- `INPUT_DIR` / `OUTPUT_DIR` / `EXPORTER_CONFIG_PATH`

### 8.4 数据库(留空则纯文件路径)
- `DATABASE_URL=""` — 留空跳过 DB
- `PROJECT_KEY` / `PROJECT_NAME` / `BATCH_KEY` — DB 启用时必填
- `DB_RERUN_POLICY="skip-success"` — `skip-success` / `rerun-failed-only` / `rerun-all` / `force-renumber`

### 8.5 Web 后台
- `WEB_SESSION_COOKIE_NAME="fileforge_session"`
- `WEB_SESSION_TTL_SECONDS=28800`
- `WEB_COOKIE_SECURE=false`(生产 HTTPS 后 true)
- `WEB_CSRF_ENABLED=true`
- `WEB_UPLOAD_STORAGE_ROOT="input_documents/web_uploads"`
- `WEB_PROCESSING_OUTPUT_ROOT="output_results/web_runs"`
- `WEB_MAX_UPLOAD_BYTES=209715200`(200 MiB)
- `WEB_MAX_UPLOAD_FILES=2000`

---

## 9 测试覆盖

`tests/` 目录约 30 个测试文件,327 个用例,**全部跑 SQLite in-memory**,不依赖 PaddleOCR / vLLM / PostgreSQL。

| 测试文件 | 覆盖 |
| --- | --- |
| `test_classifier.py` | 单档案编排,含 rule 11 简报重写降级 |
| `test_ocr_client.py` | 版面重建、预处理、二次识别 |
| `test_llm_client.py` | 多级 JSON 解析、二次重写、enable_thinking |
| `test_rules_engine.py` | 10 条补充规则 + 16 条题名清洗 + 期限锁定 |
| `test_sequence_generator.py` | 件号/档号 + 2020 年码切换 |
| `test_batch_processor*.py` | 多档案编排、错误隔离、跑出 JSON/CSV |
| `test_exporter.py` | 中文字段顺序、空字段处理 |
| `test_config.py` | 环境变量解析 |
| `test_batch_summary_*.py` | 批次摘要 schema 与 validator |
| `test_db_*.py` | DB 各服务(accounts/projects/recorder/repositories/queries/allocator/revisions/models) |
| `test_web_*.py` | Web 后台(auth/users/organizations/projects/batches/archives/revisions/audit) |
| `test_*_cli.py` | CLI 子命令(archive_query / force_rerun / user_admin / processing_runner) |

跑测试:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

期望 327 通过,部分用例若依赖未装(如 fastapi、paddleocr)会自动 `@unittest.skipUnless` 跳过而不是失败。

---

## 10 项目目录结构

```
fileforge/
├─ main.py                  # 管线入口
├─ constants.py             # 全部关键词、码表、正则、阈值
├─ CLAUDE.md                # 给 AI 协作者的项目说明(同时也是给人的)
├─ AGENTS.md                # AI 协作约定
│
├─ config/
│  ├─ config.py             # 环境变量驱动的配置
│  ├─ exporter.json         # CSV/JSON 字段顺序模板
│  └─ batch_summary.schema.*.json  # 批次摘要 JSON Schema(版本化)
│
├─ core/
│  ├─ classifier.py         # ArchiveClassifier(单档案编排)
│  ├─ rules_engine.py       # 规则引擎
│  └─ sequence_generator.py # 件号/档号生成
│
├─ infrastructure/
│  ├─ ocr_client.py         # PaddleOCR + 版面重建
│  ├─ llm_client.py         # vLLM 客户端 + 多级解析
│  └─ db/
│     ├─ models.py          # SQLAlchemy ORM 定义
│     ├─ engine.py          # session/engine 工厂
│     ├─ allocator.py       # 件号事务化分配
│     ├─ accounts.py        # 账号 + 组织 + 代码级角色权限
│     ├─ projects.py        # 项目管理写侧
│     ├─ repositories.py    # 档案/批次/页面写侧 + force-rerun + manual-correction
│     ├─ recorder.py        # BatchRecorder(main.py 旁路入库)
│     ├─ queries.py         # 6 个只读查询(Web/CLI 共用)
│     └─ migrations/        # Alembic
│
├─ processors/
│  ├─ batch_processor.py    # 多档案迭代 + 错误隔离 + 统计
│  └─ exporter.py           # JSON/CSV 输出
│
├─ web_admin/
│  ├─ app.py                # FastAPI app factory
│  ├─ upload_storage.py     # 上传文件落盘和 zip 展开
│  ├─ processing.py         # Web 在线跑批后台任务
│  ├─ settings.py / db.py / security.py / auth.py
│  ├─ routes/
│  │  ├─ auth.py / users.py / organizations.py / projects.py / archives.py
│  │  ├─ uploads.py         # 上传与在线处理页面
│  │  └─ __init__.py        # 共享 helper(has_platform_scope 等)
│  ├─ templates/            # Jinja2 模板
│  └─ static/admin.css
│
├─ utils/
│  ├─ _cli_common.py        # CLI 共享(--database-url 等)
│  ├─ user_admin.py         # 账号 / 组织 CLI
│  ├─ archive_query.py      # 读侧查询 CLI
│  ├─ force_rerun_cli.py    # 强制重跑 CLI
│  ├─ processing_runner.py  # 命令行处理 Web 上传批次
│  ├─ batch_summary_validator.py
│  └─ file.py
│
├─ prompts/                 # LLM prompt 模板
│  ├─ metadata_rules.txt
│  ├─ format_guide.txt
│  ├─ examples.json
│  └─ briefing_rewrite.txt
│
├─ finetune/                # 可选:QLoRA 微调 Qwen2.5-14B-Instruct
│  ├─ train.py
│  └─ data/{train,eval}.json
│
├─ requirements/            # 分层依赖
│  ├─ base.txt              # 管线核心
│  ├─ nvi.txt               # GPU + 微调
│  ├─ db.txt                # PostgreSQL
│  └─ web.txt               # Web 后台
│
├─ tests/                   # 327 个用例
│
└─ docs/                    # 文档
   ├─ project_overview.md   ← 本文
   ├─ deployment_ubuntu_a100.md     # 部署指南
   ├─ vllm_server.md                # vLLM 配置
   ├─ postgresql_data_contract_design.md  # 数据契约
   ├─ postgresql_basic_admin_runtime.md   # CLI 工具运行
   ├─ postgresql_integration_architecture.md  # 架构说明
   └─ web_admin.md                  # Web 后台运行
```

历次设计 spec / 实施 plan 已在 git log 中归档(MVP 收尾时清理出仓库),如需追溯参考相关 commit 历史。

---

## 11 部署与运行

详见 `docs/deployment_ubuntu_a100.md`。简版:

```bash
# 1) conda env + 装依赖
mamba create -n fileforge python=3.12 -y && mamba activate fileforge
pip install -r requirements/nvi.txt -r requirements/web.txt
pip install vllm>=0.6.3

# 2) 下载模型
huggingface-cli download Qwen/Qwen3-32B-AWQ --local-dir ~/.cache/huggingface/hub/Qwen3-32B-AWQ

# 3) 启动 vLLM(tmux 常驻)
vllm serve ~/.cache/huggingface/hub/Qwen3-32B-AWQ \
  --served-model-name qwen3-32b-awq \
  --max-model-len 8192 --gpu-memory-utilization 0.90

# 4) PostgreSQL + 迁移
sudo apt install -y postgresql && sudo systemctl enable --now postgresql
sudo -u postgres createuser -P fileforge
sudo -u postgres createdb -O fileforge fileforge
export DATABASE_URL="postgresql+psycopg://fileforge:PWD@127.0.0.1:5432/fileforge"
alembic upgrade head

# 5) 初始化账号
# roles init 是兼容命令;当前权限由 app_users.role + 代码映射提供
python -m utils.user_admin roles init
python -m utils.user_admin users create --username admin --password PWD --role platform_admin
python -m utils.user_admin orgs create --name '档案室'

# 6) 跑管线
mkdir -p input_documents && cp -r /path/to/scans/* input_documents/
export PROJECT_KEY=demo_2025 BATCH_KEY=run01 LLM_ENABLE_THINKING=false
python main.py

# 7) Web 后台
uvicorn web_admin.app:create_app --factory --host 0.0.0.0 --port 8080

# 8) 浏览器在线跑批
# 登录后访问 /admin/projects 创建项目,再访问 /uploads 上传图片或 zip 并点击开始处理
```

---

## 12 当前已实现 vs 未来扩展

### 12.1 已实现

| 能力 | 状态 |
| --- | --- |
| OCR 版面重建、低置信度二次识别 | ✅ |
| LLM JSON 强制输出 + 三级降级解析 | ✅ |
| LLM 二次调用重写简报题名(rule 11) | ✅ |
| 规则引擎 10 条 + 期限锁定 + 16 条题名清洗 | ✅ |
| 件号/档号生成(2020 年码切换) | ✅ |
| QLoRA 微调脚本(Qwen2.5-14B) | ✅(脚本就绪,需要训练数据) |
| PostgreSQL 旁路入库(失败不影响管线) | ✅ |
| CLI:archive_query / user_admin / force_rerun / processing_runner | ✅ |
| Web 后台:登录 + 用户/单位/项目管理 + 批次/档案/修订/审计只读 + 4 字段人工修正 | ✅ |
| Web 上传图片/zip + 在线跑批进度页 | ✅ |
| 权限矩阵(三角色)+ 组织隔离 + CSRF | ✅ |
| 完整测试覆盖(327 用例,SQLite 跑) | ✅ |

### 12.2 当前不做(架构文档 §10 阶段 3/4 待开发)

- 角色管理页(Web)— 当前角色只能 seed,不能在线编辑
- 项目重命名 / 项目转单位
- 项目归档(`status = "archived"`)Web 入口
- 导出记录查询页
- 修正字段从 4 个扩到全部 13 个非 sequence 字段
- 全文检索(`archive_pages.ocr_text` 上的 PG tsvector / trigram)
- 独立任务队列/Worker(替代当前 FastAPI background task)
- 上传批次取消/暂停/重试
- 多语言 / 多 OCR 引擎切换

---

## 13 关键设计决策回顾

1. **vLLM 外置而非进程内加载**:本进程只持 OpenAI SDK 客户端,GPU 由 vLLM 独占,避免 PaddleOCR 与 LLM 抢显存,也便于 LLM 服务横向扩展
2. **DB 旁路而非强依赖**:`DATABASE_URL` 留空时整条管线完全不依赖 DB,适合纯文件交付场景;DB 写失败不影响 JSON/CSV 交付
3. **规则引擎 vs 提示工程**:LLM 总会有错,与其反复调 prompt,不如把"客观规则"(分类号年份切换、保管期限锁定、开放状态判定)写成代码;LLM 只做"主观抽取",规则负责"客观校正"
4. **rule 11 用 flag 而非直改**:文学性简报题名的修正需要二次 LLM 调用,这超出规则引擎职责;用 flag 让 classifier 编排二次 LLM,保持规则层纯粹
5. **三快照**:`llm_metadata`(原始)/`rules_metadata`(规则后)/`final_metadata`(人工修正后) — 任何一步都可以独立查询和对比,论文里讲规则引擎"做了什么贡献"时可以直接给数据
6. **件号在事务里分配**:并发 main.py 跑同项目时不会冲突,DB 层 `SELECT FOR UPDATE`
7. **correction_status="corrected" 保护机制**:Web 人工修正后,后续自动跑批不覆盖;CLI `--force-rerun-rules` 显式才能清掉
8. **审计 action 标签分双路径**:`manual_correction`(Web 4 字段修正)vs `force_rerun_rules`(CLI 全字段重跑) — `reason` 字段也用字面值约定区分,便于事后 SQL 反查
9. **Web 在线跑批复用主流程**:Web 层只负责上传、建批次和触发后台任务,实际 OCR/LLM/规则仍走 `BatchProcessor` 和 `BatchRecorder`,避免维护第二条业务管线
10. **测试用 SQLite in-memory**:CI 不依赖 PG,327 用例几秒跑完;Web 用 `TestClient` 走 ASGI,不开 HTTP socket

---

## 14 谁来用、怎么用

| 角色 | 主要操作 |
| --- | --- |
| **档案员**(`org_operator`) | 浏览本单位档案,核对元数据,修正错误字段,导出 JSON/CSV |
| **单位管理员**(`org_admin`) | 上述全部 + 管理本单位用户与项目 |
| **平台管理员**(`platform_admin`) | 全权,跨单位运维 |
| **运维**(命令行) | 部署 vLLM / PG / Web,跑 `main.py` 入库 |
| **开发者**(代码层) | 改 `prompts/`、`constants.py`、`config/exporter.json` 调规则;改 `core/rules_engine.py` 扩规则 |

---

## 15 论文/演示可讲的点

- **bbox 版面重建**:多数 OCR 系统只输出文本流,本项目用版面坐标做行/列重建,提升下游 LLM 字段抽取
- **多级 JSON 降级**:LLM 在 JSON 模式下偶发输出残缺/带 fence,本项目用三级解析(原生 → 修复 → 字段正则)兜底,保证管线鲁棒性
- **规则引擎的"客观补强"**:LLM 抽取后,规则引擎按业务规则强制纠正(年份/分类号、期限锁定、开放状态),给 LLM 一个"安全网"
- **三快照可追溯**:任何一份档案都能查到"LLM 原始输出 → 规则修正后 → 人工修正后",论文里给数据展示规则引擎贡献的百分比
- **可演示**:跑一遍 `main.py` 出 JSON/CSV,或在 Web 上传 demo zip 启动在线跑批,再演示 4 字段修正、组织隔离、修订历史

---

## 16 文档地图

| 你想了解 | 看哪 |
| --- | --- |
| 怎么部署 | `docs/deployment_ubuntu_a100.md` |
| vLLM 参数细节 | `docs/vllm_server.md` |
| 数据库 schema 详情 | `docs/postgresql_data_contract_design.md` |
| CLI 工具集怎么用 | `docs/postgresql_basic_admin_runtime.md` |
| Web 后台运行方式 | `docs/web_admin.md` |
| 上传与在线跑批数据库设计 | `docs/database_upload_online_processing.md` |
| Demo 数据准备 | `docs/demo_data_guide.md` |
| 项目架构来龙去脉 | `docs/postgresql_integration_architecture.md` |
| 历次设计决策 / 实施步骤 | `git log` |
| AI 协作约定 | `CLAUDE.md`、`AGENTS.md` |
