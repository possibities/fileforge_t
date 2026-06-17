"""并发跑批压测探针:同时启动 N 个"已上传"批次,测单机最大可并发跑几个。

它精确复刻 Web「开始处理」的执行路径:对每个 upload_batch_id 先
``create_upload_processing_batch`` 建好 batch/jobs,再用线程池并发调用
``run_upload_processing_batch``(每个批次各自一份 engine + OCR 实例 + LLM 客户端,
与线上后台任务一致)。这样测出来的并发上限就接近真实表现。

准备工作:在 Web 后台把同一份样本目录上传 N 次,得到 N 个状态为
``uploaded`` 的上传批次,记下它们的 id。

用法(在 Miniforge env、能连到 vLLM + PostgreSQL 的机器上跑):

    # 另开一个终端持续看显存(关键指标):
    nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu \
        --format=csv -l 1

    # 先测并发 2,再 3、4… 逐步加,直到出现失败/显存打满/明显变慢:
    python -m tools.concurrency_probe \
        --database-url "$DATABASE_URL" \
        --output-root output_results/web_runs \
        --uploads 12,13

判读:
  - 任一批次「失败」、或 vLLM/OCR 日志出现 OOM → 已超过上限,回退一档。
  - 全部「OK」但单批耗时随并发数明显变长 → 到了"还能跑但不划算"的拐点。
  - 取"全部成功 + 显存不打满 + 单批耗时可接受"的最大 N,即为推荐并发上限。
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from infrastructure.db.engine import dispose_engine, make_engine, make_session_factory
from web_admin.processing import (
    create_upload_processing_batch,
    run_upload_processing_batch,
)


def _prepare(session_factory, upload_id: int, output_root: str) -> str:
    """建好 DB 侧 batch/jobs(单独短事务),返回 batch_key。"""
    with session_factory() as session:
        batch = create_upload_processing_batch(
            session,
            upload_batch_id=upload_id,
            output_root=output_root,
        )
        session.commit()
        return batch.batch_key


def _run_one(database_url: str, upload_id: int, batch_key: str, output_root: str):
    t0 = time.monotonic()
    ok = run_upload_processing_batch(
        database_url=database_url,
        upload_batch_id=upload_id,
        batch_key=batch_key,
        output_root=output_root,
    )
    return upload_id, ok, time.monotonic() - t0


def main() -> None:
    parser = argparse.ArgumentParser(description="并发跑批压测探针")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output-root", default="output_results/web_runs")
    parser.add_argument(
        "--uploads",
        required=True,
        help="逗号分隔的 upload_batch_id,例如 12,13,14",
    )
    args = parser.parse_args()

    upload_ids = [int(x) for x in args.uploads.split(",") if x.strip()]
    if not upload_ids:
        parser.error("--uploads 不能为空")

    engine = make_engine(args.database_url)
    session_factory = make_session_factory(engine)
    try:
        prepared = [(uid, _prepare(session_factory, uid, args.output_root)) for uid in upload_ids]
    finally:
        dispose_engine(engine)

    print(f"并发启动 {len(prepared)} 个批次:{[uid for uid, _ in prepared]}")
    wall0 = time.monotonic()
    results = []
    with ThreadPoolExecutor(max_workers=len(prepared)) as pool:
        futures = [
            pool.submit(_run_one, args.database_url, uid, batch_key, args.output_root)
            for uid, batch_key in prepared
        ]
        for fut in as_completed(futures):
            results.append(fut.result())
    wall = time.monotonic() - wall0

    print("\n=== 结果 ===")
    for uid, ok, dur in sorted(results):
        print(f"  upload {uid}: {'OK' if ok else '失败'}  用时 {dur:.1f}s")
    ok_n = sum(1 for _, ok, _ in results if ok)
    durs = [d for _, _, d in results]
    print(
        f"成功 {ok_n}/{len(results)};总墙钟 {wall:.1f}s;"
        f"单批 min/avg/max = {min(durs):.1f}/{sum(durs) / len(durs):.1f}/{max(durs):.1f}s"
    )


if __name__ == "__main__":
    main()
