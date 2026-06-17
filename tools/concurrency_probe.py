"""并发跑批压测探针:直接拿本地 input_documents/ 里的文档,同时跑 N 份,
测单机最大能同时跑几个批次。

无需 Web 上传、无需数据库——它直接复刻真正的处理内核:每个并发 worker
各自 new 一份 ``ArchiveClassifier``(独立 OCR 实例 + LLM 客户端)和
``BatchProcessor``,并发处理同一批文档。这样压的正是 OCR + LLM + GPU 的争用,
也就是"最多同时跑几个"的真正瓶颈。

目录约定(和 main.py 一致):input_documents/ 下每个子目录 = 一份多页文档。
默认会跳过 web_uploads 这种非样本目录。

准备:文档已经放在 input_documents/(例如 001、002 … 这些子目录)。

用法(在 Miniforge env、vLLM 已启动的机器上):

    # 另开一个终端持续看显存(关键指标):
    nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu \
        --format=csv -l 1

    # 先测并发 1 做基线,再 2、3、4… 逐步加:
    python -m tools.concurrency_probe --concurrency 1
    python -m tools.concurrency_probe --concurrency 2
    python -m tools.concurrency_probe --concurrency 3

每个 worker 默认处理前 3 份文档(--docs-per-batch 可调),所以每个 worker 的
工作量固定,唯一变量就是并发数 N,方便横向对比。

判读:
  - 任一 worker「失败」、或 vLLM/OCR 日志出现 OOM → 已超上限,回退一档。
  - 全部「OK」但单 worker 耗时随 N 明显变长 → 到了"还能跑但不划算"的拐点。
  - 取"全部成功 + 显存不打满 + 单 worker 耗时可接受"的最大 N,即推荐并发上限。
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from config.config import Config
from processors.batch_processor import BatchProcessor


def _discover_documents(input_root: str, exclude: set[str]) -> list[tuple[str, list[str]]]:
    """扫描 input_root,返回 [(文档名, [图片路径...]), ...],跳过 exclude 顶层目录。"""
    scanner = BatchProcessor(classifier=None)
    archive_dict = scanner.scan_directory_structure(input_root)
    docs = []
    for name, images in archive_dict.items():
        top = name.split("/", 1)[0]
        if top in exclude:
            continue
        docs.append((name, images))
    docs.sort(key=lambda kv: kv[0])
    return docs


def _run_worker(worker_id: int, archive_dict: dict[str, list[str]], output_root: str):
    from core.classifier import ArchiveClassifier

    t0 = time.monotonic()
    output_dir = str(Path(output_root) / f"worker_{worker_id}")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    try:
        classifier = ArchiveClassifier(
            ocr_lang=Config.OCR_LANG,
            model_name=Config.LLM_MODEL_NAME,
        )
        processor = BatchProcessor(classifier, recorder=None)
        results = processor.batch_process_archives(archive_dict, output_dir=output_dir)
        ok = sum(1 for r in results if r.get("status") == "success")
        return worker_id, True, ok, len(results), time.monotonic() - t0, None
    except Exception as exc:  # noqa: BLE001 - 压测要看到是谁炸了
        return worker_id, False, 0, len(archive_dict), time.monotonic() - t0, repr(exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="并发跑批压测探针(本地 input_documents)")
    parser.add_argument("--input-root", default="input_documents")
    parser.add_argument("--concurrency", type=int, default=2, help="同时跑几个 worker")
    parser.add_argument(
        "--docs-per-batch",
        type=int,
        default=3,
        help="每个 worker 处理多少份文档(取前 N 份,各 worker 相同以便对比)",
    )
    parser.add_argument(
        "--exclude",
        default="web_uploads",
        help="逗号分隔的、需跳过的顶层目录名",
    )
    parser.add_argument("--output-root", default="output_results/_concurrency_probe")
    args = parser.parse_args()

    exclude = {x.strip() for x in args.exclude.split(",") if x.strip()}
    docs = _discover_documents(args.input_root, exclude)
    if not docs:
        parser.error(f"{args.input_root} 下没扫到文档(已跳过 {exclude})")

    take = docs[: args.docs_per_batch] if args.docs_per_batch > 0 else docs
    archive_dict = {name: images for name, images in take}
    pages = sum(len(v) for v in archive_dict.values())
    print(
        f"input_root={args.input_root};共发现 {len(docs)} 份文档,"
        f"每个 worker 处理 {len(archive_dict)} 份 / {pages} 页:{list(archive_dict)}"
    )
    print(f"并发 worker 数 = {args.concurrency}\n")

    wall0 = time.monotonic()
    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(_run_worker, i + 1, dict(archive_dict), args.output_root)
            for i in range(args.concurrency)
        ]
        for fut in as_completed(futures):
            results.append(fut.result())
    wall = time.monotonic() - wall0

    print("\n=== 结果 ===")
    for wid, ok, succ, total, dur, err in sorted(results):
        line = f"  worker {wid}: {'OK' if ok else '失败'}  成功 {succ}/{total}  用时 {dur:.1f}s"
        if err:
            line += f"  错误={err}"
        print(line)
    ok_workers = sum(1 for r in results if r[1])
    durs = [r[4] for r in results]
    print(
        f"\n成功 worker {ok_workers}/{len(results)};总墙钟 {wall:.1f}s;"
        f"单 worker min/avg/max = {min(durs):.1f}/{sum(durs) / len(durs):.1f}/{max(durs):.1f}s"
    )


if __name__ == "__main__":
    main()
