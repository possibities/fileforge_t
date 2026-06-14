# 图表源码说明

本目录保存论文、PPT 和答辩材料可直接渲染的流程图源码。推荐优先使用 Mermaid 放入 Markdown 文档,使用 PlantUML 生成论文或 PPT 所需的高清图片。

> 当前会话若为非可执行环境,本文中的 Mermaid/PlantUML 命令只作为目标环境渲染方式,不表示已生成图片。

## 图表清单

| 文件 | 格式 | 用途 |
| --- | --- | --- |
| `system_architecture.mmd` | Mermaid | 系统总体架构图 |
| `processing_pipeline.mmd` | Mermaid | OCR 到导出的核心处理流程 |
| `web_demo_flow.mmd` | Mermaid | 答辩演示操作流程 |
| `correction_audit_flow.mmd` | Mermaid | 人工修正与追溯流程 |
| `system_components.puml` | PlantUML | 系统组件图 |
| `processing_sequence.puml` | PlantUML | 单个 Web 上传批次处理时序图 |
| `web_activity_flow.puml` | PlantUML | Web 后台活动流程图 |

## 渲染方式

Mermaid 可在支持 Mermaid 的 Markdown 编辑器、GitHub、Typora 或 VS Code 插件中直接预览。也可以使用 Mermaid CLI:

```bash
mmdc -i docs/diagrams/system_architecture.mmd -o system_architecture.png
```

PlantUML 可使用本地 jar、VS Code 插件或在线渲染器:

```bash
java -jar plantuml.jar docs/diagrams/system_components.puml
```

## 使用建议

- 论文正文建议放 3 张图:系统总体架构图、核心处理流程图、Web 操作流程图。
- 答辩 PPT 建议放 2 张图:系统总体架构图、Web 演示流程图。
- 时序图适合用于解释“点击开始处理后系统内部发生了什么”。
- 人工修正与追溯流程图适合回答“为什么自动处理后仍需要人工复核”。
