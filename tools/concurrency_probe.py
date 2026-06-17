"""并发跑批压测探针(自采集版):直接吃本地 input_documents/,自动从并发 1
逐级加压,自己采样 GPU 显存/占用,把整轮结果写成一个 JSON 文件——你不用盯
nvidia-smi,跑完把文件发回即可。

无需 Web 上传、无需数据库。每个并发 worker 各自 new 一份 ArchiveClassifier
(独立 OCR 实例 + LLM 客户端)和 BatchProcessor,并发处理同一批文档,压的就是
OCR + LLM + GPU 的争用,即"最多同时跑几个"的真正瓶颈。

目录约定(与 main.py 一致):input_documents/ 下每个子目录 = 一份多页文档;
默认跳过 web_uploads。

用法(Miniforge env、vLLM 已启动):

    # 自动扫描:并发 1→max,逐级压,出现失败/显存快满即停,落一个报告文件
    python -m tools.concurrency_probe --max-concurrency 4

    # 只测某一档:
    python -m tools.concurrency_probe --concurrency 3

跑完终端会打印「报告已写入: <路径>」,把那个 .json 发回即可。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

from config.config import Config
from processors.batch_processor import BatchProcessor


# ── GPU 采样(后台线程,周期性调 nvidia-smi)──────────────────────────────────
class GpuSampler:
    def __init__(self, interval: float = 0.5) -> None:
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._peak: dict[int, dict] = {}
        self._samples = 0
        self.available = shutil.which("nvidia-smi") is not None

    def _poll_once(self) -> None:
        try:
            out = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,memory.used,memory.total,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        except Exception:
            return
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 4:
                continue
            try:
                idx, used, total, util = (
                    int(parts[0]),
                    int(float(parts[1])),
                    int(float(parts[2])),
                    int(float(parts[3])),
                )
            except ValueError:
                continue
            cur = self._peak.setdefault(
                idx, {"index": idx, "used_peak_mib": 0, "total_mib": total, "util_peak_pct": 0}
            )
            cur["used_peak_mib"] = max(cur["used_peak_mib"], used)
            cur["util_peak_pct"] = max(cur["util_peak_pct"], util)
            cur["total_mib"] = total
        self._samples += 1

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._poll_once()
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._peak = {}
        self._samples = 0
        self._stop.clear()
        if not self.available:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> dict:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        per_gpu = sorted(self._peak.values(), key=lambda g: g["index"])
        near_full = any(
            g["total_mib"] and g["used_peak_mib"] / g["total_mib"] >= 0.97 for g in per_gpu
        )
        return {
            "sampled": self.available,
            "interval_s": self.interval,
            "samples": self._samples,
            "per_gpu": per_gpu,
            "near_full": near_full,
        }


def _gpu_info() -> list[dict]:
    if shutil.which("nvidia-smi") is None:
        return []
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except Exception:
        return []
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            gpus.append(
                {"index": parts[0], "name": parts[1], "memory_total": parts[2], "driver": parts[3]}
            )
    return gpus


# ── 文档发现 + worker ────────────────────────────────────────────────────────
def _discover_documents(input_root: str, exclude: set[str]) -> list[tuple[str, list[str]]]:
    scanner = BatchProcessor(classifier=None)
    archive_dict = scanner.scan_directory_structure(input_root)
    docs = [
        (name, images)
        for name, images in archive_dict.items()
        if name.split("/", 1)[0] not in exclude
    ]
    docs.sort(key=lambda kv: kv[0])
    return docs


def _run_worker(worker_id: int, archive_dict: dict[str, list[str]], output_root: str) -> dict:
    from core.classifier import ArchiveClassifier

    t0 = time.monotonic()
    output_dir = str(Path(output_root) / f"worker_{worker_id}")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    try:
        classifier = ArchiveClassifier(ocr_lang=Config.OCR_LANG, model_name=Config.LLM_MODEL_NAME)
        processor = BatchProcessor(classifier, recorder=None)
        results = processor.batch_process_archives(archive_dict, output_dir=output_dir)
        ok = sum(1 for r in results if r.get("status") == "success")
        return {
            "id": worker_id,
            "ok": True,
            "success": ok,
            "total": len(results),
            "duration_s": round(time.monotonic() - t0, 1),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - 压测要看到谁炸了
        return {
            "id": worker_id,
            "ok": False,
            "success": 0,
            "total": len(archive_dict),
            "duration_s": round(time.monotonic() - t0, 1),
            "error": repr(exc),
        }


def _run_level(concurrency: int, archive_dict: dict, output_root: str, sampler: GpuSampler) -> dict:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    print(f"\n>>> 并发 {concurrency} 开始…")
    sampler.start()
    wall0 = time.monotonic()
    workers = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(_run_worker, i + 1, dict(archive_dict), f"{output_root}/c{concurrency}")
            for i in range(concurrency)
        ]
        for fut in as_completed(futures):
            workers.append(fut.result())
    wall = round(time.monotonic() - wall0, 1)
    gpu = sampler.stop()
    workers.sort(key=lambda w: w["id"])
    ok_workers = sum(1 for w in workers if w["ok"])
    for w in workers:
        line = f"    worker {w['id']}: {'OK' if w['ok'] else '失败'}  成功 {w['success']}/{w['total']}  {w['duration_s']}s"
        if w["error"]:
            line += f"  错误={w['error']}"
        print(line)
    if gpu["sampled"] and gpu["per_gpu"]:
        for g in gpu["per_gpu"]:
            print(
                f"    GPU{g['index']} 显存峰值 {g['used_peak_mib']}/{g['total_mib']} MiB,"
                f" 利用率峰值 {g['util_peak_pct']}%"
            )
    print(f"    并发 {concurrency}: 成功 worker {ok_workers}/{concurrency},墙钟 {wall}s")
    return {"concurrency": concurrency, "wall_s": wall, "ok_workers": ok_workers, "workers": workers, "gpu": gpu}


def main() -> None:
    parser = argparse.ArgumentParser(description="并发跑批压测探针(自采集 GPU + 自动扫描)")
    parser.add_argument("--input-root", default="input_documents")
    parser.add_argument("--concurrency", type=int, default=0, help="只测这一档;不填则自动扫描")
    parser.add_argument("--max-concurrency", type=int, default=4, help="自动扫描时的并发上限")
    parser.add_argument("--docs-per-batch", type=int, default=3, help="每个 worker 处理几份文档")
    parser.add_argument("--exclude", default="web_uploads")
    parser.add_argument("--output-root", default="output_results/_concurrency_probe")
    parser.add_argument("--report", default="", help="报告文件路径;不填自动生成")
    args = parser.parse_args()

    exclude = {x.strip() for x in args.exclude.split(",") if x.strip()}
    docs = _discover_documents(args.input_root, exclude)
    if not docs:
        parser.error(f"{args.input_root} 下没扫到文档(已跳过 {exclude})")
    take = docs[: args.docs_per_batch] if args.docs_per_batch > 0 else docs
    archive_dict = {name: images for name, images in take}
    pages = sum(len(v) for v in archive_dict.values())

    levels_to_run = (
        [args.concurrency] if args.concurrency > 0 else list(range(1, args.max_concurrency + 1))
    )
    print(
        f"input_root={args.input_root};发现 {len(docs)} 份文档,每 worker 处理 "
        f"{len(archive_dict)} 份 / {pages} 页:{list(archive_dict)}"
    )
    print(f"将测并发档位:{levels_to_run}")

    sampler = GpuSampler()
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "args": vars(args),
        "environment": {
            "llm_model": Config.LLM_MODEL_NAME,
            "llm_base_url": getattr(Config, "LLM_BASE_URL", None),
            "ocr_lang": Config.OCR_LANG,
            "ocr_use_gpu": getattr(Config, "OCR_USE_GPU", None),
            "nvidia_smi": sampler.available,
            "gpus": _gpu_info(),
        },
        "docs": {"available": len(docs), "used": list(archive_dict), "pages_per_worker": pages},
        "levels": [],
        "stopped_reason": "completed",
    }

    try:
        for c in levels_to_run:
            level = _run_level(c, archive_dict, args.output_root, sampler)
            report["levels"].append(level)
            if level["ok_workers"] < c:
                report["stopped_reason"] = f"worker_failed@{c}"
                print(f"\n并发 {c} 出现失败 → 停止加压。")
                break
            if level["gpu"].get("near_full"):
                report["stopped_reason"] = f"vram_near_full@{c}"
                print(f"\n并发 {c} 显存接近打满 → 停止加压。")
                break
    except KeyboardInterrupt:
        report["stopped_reason"] = "interrupted"
        print("\n已中断。")
    finally:
        if args.report:
            report_path = Path(args.report)
        else:
            stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
            report_path = Path(args.output_root) / f"report_{stamp}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已写入: {report_path}")
        print("把这个 .json 文件发回即可,我帮你判断最大可并发数。")


if __name__ == "__main__":
    main()
