"""Smoke test for DeepResearch queue backends in multi-worker mode."""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from typing import Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from service.run_queue_store import RedisRunQueueStore, SqliteRunQueueStore


@dataclass(frozen=True)
class SmokeConfig:
    """Configuration for the smoke test."""

    backend: str
    data_root: Path
    redis_host: str
    redis_port: int
    redis_db: int
    redis_password: Optional[str]
    redis_prefix: str
    runs: int
    workers: int
    work_seconds: float
    lease_seconds: int
    max_active_runs: int
    aging_seconds: int
    duration_seconds: int
    simulate_expire: bool
    requeue_interval: float
    reset: bool


def build_store(config: SmokeConfig):
    """Create a queue store for the selected backend."""

    if config.backend == "redis":
        return RedisRunQueueStore(
            host=config.redis_host,
            port=config.redis_port,
            db=config.redis_db,
            password=config.redis_password,
            prefix=config.redis_prefix,
        )
    return SqliteRunQueueStore(config.data_root)


def reset_backend(config: SmokeConfig) -> None:
    """Clear existing queue data to avoid cross-test interference."""

    if config.backend == "redis":
        store = RedisRunQueueStore(
            host=config.redis_host,
            port=config.redis_port,
            db=config.redis_db,
            password=config.redis_password,
            prefix=config.redis_prefix,
        )
        redis_client = store._redis  # noqa: SLF001 - test helper reset only
        pattern = f"{config.redis_prefix.rstrip(':')}:*"
        keys = list(redis_client.scan_iter(pattern))
        if keys:
            redis_client.delete(*keys)
        return
    db_path = config.data_root / "run_queue.db"
    if db_path.exists():
        db_path.unlink()


def enqueue_runs(config: SmokeConfig, store) -> Dict[str, int]:
    """Seed the queue with dummy run ids."""

    priority_map: Dict[str, int] = {}
    run_prefix = f"smoke_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    for idx in range(config.runs):
        run_id = f"{run_prefix}_{idx}"
        priority = (idx % 5) - 2
        store.enqueue(run_id, priority, datetime.utcnow().isoformat())
        priority_map[run_id] = priority
    return priority_map


def worker_loop(
    worker_id: str,
    config: SmokeConfig,
    claimed,
    completed,
    stop_event,
) -> None:
    """Run worker claims until the deadline or stop flag."""

    store = build_store(config)
    deadline = time.time() + config.duration_seconds
    while time.time() < deadline and not stop_event.is_set():
        run_id = store.claim_next(
            owner_id=worker_id,
            lease_seconds=config.lease_seconds,
            max_active_runs=config.max_active_runs,
            aging_seconds=config.aging_seconds,
        )
        if not run_id:
            time.sleep(0.1)
            continue
        claimed.append(run_id)
        try:
            run_index = int(run_id.rsplit("_", 1)[-1])
        except ValueError:
            run_index = 0
        should_expire = config.simulate_expire and (run_index % 5 == 0)
        sleep_seconds = (
            config.lease_seconds * 1.5 if should_expire else config.work_seconds
        )
        time.sleep(sleep_seconds)
        if should_expire:
            continue
        store.remove(run_id)
        completed.append(run_id)


def requeue_expired_loop(
    config: SmokeConfig,
    priority_map: Dict[str, int],
    stop_flag: Event,
) -> None:
    """Requeue expired leases to simulate scheduler recovery."""

    store = build_store(config)
    while not stop_flag.is_set():
        expired = store.list_expired_running(datetime.utcnow())
        if expired:
            now_iso = datetime.utcnow().isoformat()
            for run_id in expired:
                priority = priority_map.get(run_id, 0)
                store.requeue(run_id, priority, now_iso)
        stop_flag.wait(config.requeue_interval)


def parse_args() -> SmokeConfig:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(description="DeepResearch queue smoke test")
    parser.add_argument("--backend", choices=["sqlite", "redis"], default="sqlite")
    parser.add_argument("--data-root", default=os.getenv("DATA_ROOT", "./data/deep_research"))
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--work-seconds", type=float, default=0.5)
    parser.add_argument("--lease-seconds", type=int, default=5)
    parser.add_argument("--max-active-runs", type=int, default=2)
    parser.add_argument("--aging-seconds", type=int, default=300)
    parser.add_argument("--duration-seconds", type=int, default=20)
    parser.add_argument("--simulate-expire", action="store_true")
    parser.add_argument("--requeue-interval", type=float, default=1.0)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--redis-host", default=os.getenv("REDIS_HOST", "127.0.0.1"))
    parser.add_argument("--redis-port", type=int, default=int(os.getenv("REDIS_PORT", "6379")))
    parser.add_argument("--redis-db", type=int, default=int(os.getenv("REDIS_DB", "0")))
    parser.add_argument("--redis-password", default=os.getenv("REDIS_PASSWORD"))
    parser.add_argument("--redis-prefix", default=os.getenv("REDIS_QUEUE_PREFIX", "deep_research:queue:smoke"))
    args = parser.parse_args()
    return SmokeConfig(
        backend=args.backend,
        data_root=Path(args.data_root),
        redis_host=args.redis_host,
        redis_port=args.redis_port,
        redis_db=args.redis_db,
        redis_password=args.redis_password,
        redis_prefix=args.redis_prefix,
        runs=args.runs,
        workers=args.workers,
        work_seconds=args.work_seconds,
        lease_seconds=args.lease_seconds,
        max_active_runs=args.max_active_runs,
        aging_seconds=args.aging_seconds,
        duration_seconds=args.duration_seconds,
        simulate_expire=args.simulate_expire,
        requeue_interval=args.requeue_interval,
        reset=args.reset,
    )


def main() -> None:
    """Entrypoint for the smoke test."""

    config = parse_args()
    if config.reset:
        reset_backend(config)
    store = build_store(config)
    priority_map = enqueue_runs(config, store)

    ctx = mp.get_context("spawn")
    manager = ctx.Manager()
    claimed = manager.list()
    completed = manager.list()
    stop_event = ctx.Event()

    requeue_stop = Event()
    requeue_thread = Thread(
        target=requeue_expired_loop,
        args=(config, priority_map, requeue_stop),
        daemon=True,
    )
    requeue_thread.start()

    processes: List[mp.Process] = []
    for idx in range(config.workers):
        worker_id = f"worker_{idx}"
        process = ctx.Process(
            target=worker_loop,
            args=(worker_id, config, claimed, completed, stop_event),
        )
        process.start()
        processes.append(process)

    deadline = time.time() + config.duration_seconds
    while time.time() < deadline:
        if len(completed) >= config.runs:
            break
        time.sleep(0.2)

    stop_event.set()
    requeue_stop.set()
    for process in processes:
        process.join(timeout=5)

    claimed_list = list(claimed)
    completed_list = list(completed)
    duplicates = [item for item, count in Counter(claimed_list).items() if count > 1]
    pending_left = store.count_pending()
    running_left = store.count_running(datetime.utcnow())

    print("Smoke test summary")
    print(f"Backend: {config.backend}")
    print(f"Claimed: {len(claimed_list)}")
    print(f"Completed: {len(completed_list)} / {config.runs}")
    print(f"Duplicates: {len(duplicates)}")
    print(f"Pending left: {pending_left}")
    print(f"Running left: {running_left}")
    if duplicates:
        print("Duplicate run_ids detected:")
        for run_id in duplicates:
            print(f" - {run_id}")


if __name__ == "__main__":
    main()
