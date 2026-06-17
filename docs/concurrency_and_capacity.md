# 并发跑批与单机容量

记录 Web 在线跑批的并发模型、显存瓶颈分析,以及在实测硬件上的容量基准。
结论与数据来自 `tools/concurrency_probe.py` 的实测,可随时复现。

## 1. 并发模型(代码事实)

- 点「开始处理」→ `POST /uploads/{id}/start` 先建好 `ProcessingBatch` + `ProcessingJob`,
  再用 FastAPI `BackgroundTasks` 调度 `run_upload_processing_batch`(`web_admin/processing.py`)。
  同步函数的后台任务跑在 Starlette 线程池里 → **多个用户的批次是多线程并行**。
- 每个批次完全隔离:各自的 DB engine/session、各自一份 `ArchiveClassifier`
  (**独立 PaddleOCR 实例 + LLM 客户端**)、各自的 batch/jobs/输出目录。
- 同一个上传批次不能被重复启动(`status` 已是 `processing` 会拒绝);不同上传/用户可并发。

## 2. 数据正确性:并发安全

件号/档号由 `DatabaseAllocator` 分配,每次是**独立短事务 + `SELECT … FOR UPDATE`**
锁 `sequence_counters` 行(`infrastructure/db/allocator.py`、`repositories.py`)。
即便两个批次同时落到同一 `(项目, 年度, 分类, 期限)`,也不会发重号。
→ **并发跑批不会串号、不会脏数据。**

## 3. 瓶颈:不是 vLLM 并发,是 OCR 显存

- vLLM **天生高并发**(continuous batching),多个批次同时发 LLM 请求由它合批处理,
  **LLM 这侧不是瓶颈**。
- vLLM 启动时**静态圈走**约 `gpu-memory-utilization × 显存`(放权重 + KV cache 池),
  这与并发数无关——开机就占好。
- 真正的限制是:**每个并发 worker 各自在同卡加载一份 GPU PaddleOCR**(`OCR_USE_GPU=True`),
  每份约 +2 GB。vLLM 把显存圈走后,留给 OCR 的余量决定了能并发几个。

### 关键配置

| 杠杆 | 作用 | 代价 |
|---|---|---|
| vLLM `--gpu-memory-utilization 0.6` | 缩小 vLLM 显存池,给同卡 OCR 腾地方(推荐值) | KV cache 变小(短文本抽取无感) |
| `OCR_USE_GPU=false` | OCR 走 CPU、不吃显存,并发只受 CPU + vLLM 限制(最高) | 单页 OCR 变慢 |
| 共享单个 OCR 实例(未实现) | 避免每 worker 复制一份显存 | PaddleOCR 非线程安全,需加锁排队 |

详见 `docs/vllm_server.md`(已把默认值定为 0.6,并注明独占整卡可回 0.90)。

## 4. 实测基准(RTX A6000 48 GB)

环境:NVIDIA RTX A6000 49140 MiB,driver 580.95.05;`qwen3-32b-awq`(AWQ),
vLLM `--gpu-memory-utilization 0.6`、`--max-model-len 8192`;OCR=GPU/ch。
每个 worker 处理 3 份文档 / 13 页。

| 并发 | 全部成功 | 单批耗时 | 总墙钟 | 处理量 | 吞吐(份/秒) | 相对 1× | 显存峰值 |
|---|---|---|---|---|---|---|---|
| 1 | ✅ | 27.4 s | 29.0 s | 3 | 0.103 | 1.00× | 33.1 / 48 GB |
| 2 | ✅×2 | 23.8 s | 24.0 s | 6 | 0.250 | 2.42× | 35.4 GB |
| 3 | ✅×3 | 26.0 s | 26.3 s | 9 | 0.342 | 3.31× | 36.9 GB |
| 4 | ✅×4 | 28.2 s | 28.6 s | 12 | 0.420 | 4.06× | 38.9 GB |

观察:
- **吞吐到 4 并发近似线性(4.06×),单批耗时基本不变**(27→28 s)。
- 2 并发墙钟反而比 1 并发短(24 < 29 s):单跑时 worker 等 LLM 期间 GPU 空转,
  多 worker 可把 OCR(GPU)与 LLM(等待)交错填满空隙。
- vLLM(0.6)约占 29.5 GB,每多一个 worker 仅 +~2 GB;4 并发才 79%(余 ~10 GB)。
- GPU 利用率峰值一直 100%(OCR 阶段本就吃满)。
- 上表停在 4 是 `--max-concurrency 4` 的参数上限,**不是硬件到顶**。

### 结论 / 推荐

- **演示/日常(2 用户):4 并发绰绰有余**,数据正确、单批不掉速。
- 按 +2 GB/worker 估算,显存上限约 7~9;但利用率已 100%,再往上吞吐大概率见顶、
  单批变慢。建议正式定档前用 `--max-concurrency 8 --docs-per-batch 5` 再扫一轮找拐点。
- 0.6 仍不够时:`--max-model-len 4096` 或 `--gpu-memory-utilization 0.5` 再腾一点,
  或 OCR 改 CPU 把并发与显存解耦。

## 5. 如何复现 / 重测

`tools/concurrency_probe.py` 直接吃本地 `input_documents/`,无需上传/数据库,
自采样 GPU,从并发 1 逐级加压,落一个 JSON 报告:

```bash
# 另开终端可选地看实时显存:
nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv -l 1

# 自动扫描 1→max,默认只在真实 OOM 失败时停:
python -m tools.concurrency_probe --max-concurrency 8 --docs-per-batch 5
```

报告(`output_results/_concurrency_probe/report_*.json`)含每档成功/失败、单批耗时、
显存峰值与余量、空闲基线、环境信息、停止原因。

判读:取**全部成功 + 显存不打满 + 单批耗时可接受**的最大并发即推荐上限;
出现失败/OOM 的前一档为安全上限。

## 6. 局限与后续

- 这是"毕设级进程内 worker":后台任务跑在 uvicorn 进程线程池;**uvicorn 重启会丢失在跑的任务**
  (job 卡在 `running`),无重试/取消/排队。
- 当前**没有全局并发上限**,理论上可被同时点很多次压垮 GPU。可加"每项目仅一个在跑"或
  "全局最多 N 个、超出排队"的兜底(不引入 Celery)。
- 要规模化:换真正的任务队列(Celery/RQ/arq)+ 有界 worker 池 + 共享 OCR 服务。
