# Demo 数据准备说明

本文只说明毕业答辩或验收演示需要的数据结构和脱敏要求，不提交真实扫描件。

## 1 数据规模

建议准备两套 demo 数据:

- 最小现场演示:3 到 5 份档案,每份 1 到 3 页。
- 论文实验评测:30 到 50 份档案,每份保留人工标注真值。

现场演示优先使用已跑通的结果、截图和录屏。只有在运行环境稳定时,再现场执行 `/uploads -> 开始处理 -> 进度页 -> 档案详情`。

## 2 Web 上传 zip 结构

zip 一级目录表示一份档案:

```text
demo_upload.zip
├─ 2025_party_meeting_001/
│  ├─ page_001.jpg
│  └─ page_002.jpg
├─ 2025_report_001/
│  └─ page_001.jpg
└─ 2024_notice_001/
   ├─ page_001.png
   └─ page_002.png
```

散图上传会被归为同一份档案,更适合快速试跑;zip 更适合展示多档案批处理。

## 3 命名建议

- 档案目录使用稳定英文或拼音 key,例如 `2025_party_meeting_001`。
- 页文件用 `page_001.jpg`、`page_002.jpg` 递增命名。
- 不要在文件名里放真实姓名、身份证号、手机号、合同编号等敏感信息。
- 如果需要展示中文语义,放在脱敏后的扫描图内容里,不要依赖文件名。

## 4 脱敏要求

真实材料进入 demo 前必须处理:

- 替换姓名、手机号、身份证号、银行卡号、详细住址。
- 替换单位内部编号、合同号、账号、密级标识。
- 删除印章、签名、二维码、条形码或改为模拟元素。
- 对涉密、未公开、人事、财务、纪检等材料只使用仿真样例。

论文和 PPT 截图中也应使用脱敏材料。不要把原始 `input_documents/`、`input_documents/web_uploads/` 或 `output_results/` 直接提交到仓库。

## 5 人工标注表

实验评测建议维护一份表格:

| 字段 | 说明 |
| --- | --- |
| demo_id | 样本编号 |
| archive_key | 档案目录名 |
| page_count | 页数 |
| title_truth | 人工题名 |
| responsible_party_truth | 人工责任者 |
| archive_year_truth | 人工归档年度 |
| classification_code_truth | 人工实体分类号 |
| retention_period_truth | 人工保管期限 |
| openness_status_truth | 人工开放状态 |
| notes | 特殊情况说明 |

这张表用于计算字段级准确率、规则引擎消融和人工修正率。

## 6 演示流程

推荐演示路径:

```text
登录 Web
-> /admin/projects 确认项目
-> /uploads 上传 demo_upload.zip
-> 点击开始处理
-> /processing/batches/{id} 查看任务和事件
-> /batches/{id}/archives 查看结果
-> /archives/{id}/edit 修改 4 个核心字段
-> /archives/{id}/revisions 查看修订记录
-> /archives/{id}/audit 查看审计日志
```

如果浏览器后台任务中断,可用 CLI 对已上传批次补跑:

```bash
python -m utils.processing_runner --upload-batch-id 1
```

该命令仍复用同一条 OCR/LLM/规则/入库主流程。
