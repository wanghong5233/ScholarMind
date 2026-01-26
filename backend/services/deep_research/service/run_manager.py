"""Async run manager for DeepResearch pipelines."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from schemas.common import DeepResearchRequest, DeepResearchStatus
from service.pipeline import ResearchPipeline
from service.run_queue_store import create_queue_store
from service.state_store import StateStore


class RunManager:
    """Manage background DeepResearch runs."""

    def __init__(self, rag_service_url: str, data_root: str, request_timeout: int) -> None:
        """Initialize the run manager."""

        self._rag_service_url = rag_service_url
        self._data_root = Path(data_root)
        self._request_timeout = request_timeout
        self._tasks: Dict[str, asyncio.Task] = {}
        self._watchdogs: Dict[str, asyncio.Task] = {}
        self._max_active_runs = max(1, settings.MAX_ACTIVE_RUNS)
        self._max_pending_runs = settings.QUEUE_MAX_PENDING
        self._queue_store = create_queue_store(self._data_root)
        self._instance_id = uuid.uuid4().hex
        self._scheduler_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._silent_cancel: set[str] = set()

    async def submit(
        self,
        request: DeepResearchRequest,
        user_id: int,
        *,
        research_id: Optional[str] = None,
        resume: bool = False,
    ) -> Tuple[str, DeepResearchStatus, Optional[int], int, int]:
        """Submit a DeepResearch run in the background."""

        run_id = research_id or ResearchPipeline._new_research_id()
        async with self._lock:
            if self._is_active_locked(run_id):
                raise ValueError("Research task already running")
            queue_status = self._queue_store.get_status(run_id)
            if queue_status == "queued":
                raise ValueError("Research task already queued")
            if queue_status == "running":
                raise ValueError("Research task already running")

            store = StateStore(self._data_root, run_id)
            now = datetime.utcnow().isoformat()
            priority = self._normalize_priority(request.metadata.get("priority"))
            pending_count_global = self._queue_store.count_pending()
            local_active = self._count_local_active_locked()
            running_count = self._queue_store.count_running(datetime.utcnow())
            can_start_now = local_active < self._max_active_runs and running_count < self._max_active_runs
            should_queue = pending_count_global > 0 or not can_start_now
            if should_queue and self._max_pending_runs == 0:
                raise ValueError("Queue disabled")
            if should_queue and self._max_pending_runs > 0 and pending_count_global >= self._max_pending_runs:
                raise ValueError("Queue full")
            if resume:
                meta = store.load_meta()
                if not meta:
                    raise ValueError("Research meta not found")
                store.update_meta(
                    {
                        "status": DeepResearchStatus.QUEUED.value,
                        "submitted_at": now,
                        "resume_requested_at": now,
                        "resume_pending": True,
                        "priority": meta.get("priority", priority),
                    }
                )
            else:
                store.save_meta(
                    {
                        "research_id": run_id,
                        "status": DeepResearchStatus.QUEUED.value,
                        "topic": request.topic,
                        "mode": request.mode.value,
                        "submitted_at": now,
                        "user_id": user_id,
                        "request": request.model_dump(mode="json"),
                        "priority": priority,
                    }
                )
            self._queue_store.enqueue(run_id, priority, now)
            pending_entries = self._queue_store.list_pending(settings.QUEUE_PRIORITY_AGING_SECONDS)
            pending_ids = [entry.research_id for entry in pending_entries]
            queue_position = pending_ids.index(run_id) + 1 if run_id in pending_ids else None
            active_count = running_count
            pending_count = len(pending_entries)
        await self._schedule_once()
        queue_status = self._queue_store.get_status(run_id)
        status = DeepResearchStatus.RUNNING if queue_status == "running" else DeepResearchStatus.QUEUED
        if status == DeepResearchStatus.RUNNING:
            queue_position = None
        return run_id, status, queue_position, active_count, pending_count

    async def cancel(self, research_id: str) -> str:
        """Request cancellation for a research task."""

        async with self._lock:
            task = self._tasks.get(research_id)
        if task and not task.done():
            store = StateStore(self._data_root, research_id)
            store.update_meta({"cancel_reason": "user_cancel"})
            self._append_control_event(research_id, "Cancellation requested")
            task.cancel()
            return "cancel_requested"
        queue_status = self._queue_store.get_status(research_id)
        if queue_status == "queued":
            self._queue_store.remove(research_id)
            return "cancelled_queued"
        if queue_status == "running":
            return "cancel_requested"
        return "not_found"

    def is_active(self, research_id: str) -> bool:
        """Return whether a task is currently running."""

        task = self._tasks.get(research_id)
        return bool(task and not task.done())

    async def get_queue_snapshot(self) -> Dict[str, Any]:
        """Return queue snapshot information."""

        active_ids = self._queue_store.list_running()
        pending_entries = self._queue_store.list_pending(settings.QUEUE_PRIORITY_AGING_SECONDS)
        pending_ids = [entry.research_id for entry in pending_entries]
        return {
            "active_ids": active_ids,
            "pending_ids": pending_ids,
            "max_active_runs": self._max_active_runs,
        }

    async def start_scheduler(self) -> None:
        """Start background scheduler loop."""

        if self._scheduler_task and not self._scheduler_task.done():
            return
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def _scheduler_loop(self) -> None:
        interval = max(2, settings.SCHEDULER_RENEW_SECONDS)
        logger = logging.getLogger(__name__)
        while True:
            try:
                await self._renew_leases()
                await self._cancel_lost_leases()
                await self._requeue_expired_runs()
                await self._schedule_once()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Scheduler loop failed: %s", exc)
            await asyncio.sleep(interval)

    async def update_priority(self, research_id: str, priority: int) -> Dict[str, Any]:
        """Update the priority of a run and reorder the queue if needed."""

        async with self._lock:
            store = StateStore(self._data_root, research_id)
            meta = store.load_meta()
            if not meta:
                raise ValueError("Research meta not found")
            store.update_meta({"priority": priority})
            status = meta.get("status")
            queue_position = None
            if status == DeepResearchStatus.QUEUED.value:
                self._queue_store.update_priority(research_id, priority)
                pending_entries = self._queue_store.list_pending(settings.QUEUE_PRIORITY_AGING_SECONDS)
                pending_ids = [entry.research_id for entry in pending_entries]
                if research_id in pending_ids:
                    queue_position = pending_ids.index(research_id) + 1
                pending_count = len(pending_entries)
            else:
                pending_count = self._queue_store.count_pending()
            active_count = self._queue_store.count_running(datetime.utcnow())
        return {
            "status": status,
            "queue_position": queue_position,
            "active_runs": active_count,
            "pending_runs": pending_count,
        }

    async def bootstrap(self) -> int:
        """Recover queued runs and mark stale running runs."""

        marked_failed = self.mark_stale_runs()
        self._sync_queue_from_meta()
        await self._schedule_once()
        return marked_failed

    def _is_active_locked(self, research_id: str) -> bool:
        task = self._tasks.get(research_id)
        return bool(task and not task.done())

    def _count_local_active_locked(self) -> int:
        return sum(1 for task in self._tasks.values() if task and not task.done())

    async def _schedule_once(self) -> None:
        async with self._lock:
            local_slots = self._max_active_runs - self._count_local_active_locked()
        if local_slots <= 0:
            return
        for _ in range(local_slots):
            run_id = self._queue_store.claim_next(
                owner_id=self._instance_id,
                lease_seconds=settings.SCHEDULER_LEASE_SECONDS,
                max_active_runs=self._max_active_runs,
                aging_seconds=settings.QUEUE_PRIORITY_AGING_SECONDS,
            )
            if not run_id:
                return
            status = self._start_task_from_meta(run_id)
            if status == DeepResearchStatus.RUNNING:
                self._append_control_event(run_id, "Run started from queue")
            else:
                self._queue_store.remove(run_id)
                self._append_control_event(run_id, "Run dropped from queue")

    async def _renew_leases(self) -> None:
        async with self._lock:
            active_ids = [
                research_id
                for research_id, task in self._tasks.items()
                if task and not task.done()
            ]
        self._queue_store.renew_leases(
            self._instance_id,
            active_ids,
            settings.SCHEDULER_LEASE_SECONDS,
        )

    async def _cancel_lost_leases(self) -> None:
        owned_ids = set(self._queue_store.list_running_by_owner(self._instance_id))
        async with self._lock:
            active_tasks = {
                research_id: task
                for research_id, task in self._tasks.items()
                if task and not task.done()
            }
        for research_id, task in active_tasks.items():
            if research_id in owned_ids:
                continue
            async with self._lock:
                self._silent_cancel.add(research_id)
            logging.getLogger(__name__).warning(
                "Lease lost for %s; cancelling local task", research_id
            )
            task.cancel()

    async def _requeue_expired_runs(self) -> None:
        expired_ids = self._queue_store.list_expired_running(datetime.utcnow())
        if not expired_ids:
            return
        for research_id in expired_ids:
            if self.is_active(research_id):
                continue
            store = StateStore(self._data_root, research_id)
            meta = store.load_meta()
            if not meta:
                self._queue_store.remove(research_id)
                continue
            if meta.get("status") != DeepResearchStatus.RUNNING.value:
                self._queue_store.remove(research_id)
                continue
            now = datetime.utcnow().isoformat()
            resume_allowed = bool(store.load_json("queue.json"))
            store.update_meta(
                {
                    "status": DeepResearchStatus.QUEUED.value,
                    "submitted_at": now,
                    "resume_pending": resume_allowed,
                    "resume_requested_at": now if resume_allowed else None,
                }
            )
            priority = self._extract_priority_from_meta(meta)
            self._queue_store.requeue(research_id, priority, now)
            self._append_control_event(research_id, "Run requeued after lease expired")

    def _sync_queue_from_meta(self) -> None:
        runs = StateStore.list_runs(self._data_root)
        queued_ids = {
            meta.get("research_id")
            for meta in runs
            if meta.get("status") == DeepResearchStatus.QUEUED.value and meta.get("research_id")
        }
        running_meta_ids = {
            meta.get("research_id")
            for meta in runs
            if meta.get("status") == DeepResearchStatus.RUNNING.value and meta.get("research_id")
        }
        for meta in runs:
            if meta.get("status") != DeepResearchStatus.QUEUED.value:
                continue
            research_id = meta.get("research_id")
            if not research_id:
                continue
            submitted_at = meta.get("submitted_at") or datetime.utcnow().isoformat()
            priority = self._extract_priority_from_meta(meta)
            try:
                self._queue_store.enqueue(research_id, priority, submitted_at)
            except ValueError:
                continue
        pending_entries = self._queue_store.list_pending(settings.QUEUE_PRIORITY_AGING_SECONDS)
        pending_ids = {entry.research_id for entry in pending_entries}
        running_ids = set(self._queue_store.list_running())
        for run_id in pending_ids:
            if run_id not in queued_ids:
                self._queue_store.remove(run_id)
        for run_id in running_ids:
            if run_id not in running_meta_ids:
                self._queue_store.remove(run_id)

    def _start_task(
        self,
        request: DeepResearchRequest,
        user_id: int,
        research_id: str,
        resume: bool,
    ) -> None:
        store = StateStore(self._data_root, research_id)
        now = datetime.utcnow().isoformat()
        if resume:
            store.update_meta(
                {
                    "status": DeepResearchStatus.RUNNING.value,
                    "resumed_at": now,
                    "resume_pending": False,
                }
            )
        else:
            store.update_meta(
                {
                    "status": DeepResearchStatus.RUNNING.value,
                    "started_at": now,
                    "resume_pending": False,
                }
            )
        task = asyncio.create_task(
            self._run_task(request, user_id=user_id, research_id=research_id, resume=resume)
        )
        self._tasks[research_id] = task
        task.add_done_callback(
            lambda _: asyncio.create_task(self._handle_task_done(research_id))
        )
        self._start_watchdog(research_id)

    def _start_task_from_meta(self, research_id: str) -> DeepResearchStatus:
        store = StateStore(self._data_root, research_id)
        meta = store.load_meta() or {}
        request_payload = meta.get("request")
        if not isinstance(request_payload, dict):
            self._mark_interrupted(store, research_id, reason="missing_request")
            return DeepResearchStatus.FAILED
        try:
            payload = DeepResearchRequest(**request_payload)
        except Exception:
            self._mark_interrupted(store, research_id, reason="invalid_request")
            return DeepResearchStatus.FAILED
        user_id = int(meta.get("user_id") or 0)
        resume = bool(meta.get("resume_pending"))
        self._start_task(payload, user_id, research_id, resume=resume)
        return DeepResearchStatus.RUNNING

    async def _handle_task_done(self, research_id: str) -> None:
        async with self._lock:
            self._tasks.pop(research_id, None)
            self._silent_cancel.discard(research_id)
        watchdog = self._watchdogs.pop(research_id, None)
        if watchdog and not watchdog.done():
            watchdog.cancel()
        owned_ids = set(self._queue_store.list_running_by_owner(self._instance_id))
        if research_id in owned_ids:
            self._queue_store.remove(research_id)
        await self._schedule_once()

    def _start_watchdog(self, research_id: str) -> None:
        if settings.RUN_TIMEOUT_SECONDS <= 0 and settings.RUN_IDLE_TIMEOUT_SECONDS <= 0:
            return
        watchdog = asyncio.create_task(self._watch_run(research_id))
        self._watchdogs[research_id] = watchdog
        watchdog.add_done_callback(lambda _: self._watchdogs.pop(research_id, None))

    async def _watch_run(self, research_id: str) -> None:
        interval = max(2, settings.RUN_WATCHDOG_INTERVAL_SECONDS)
        while True:
            await asyncio.sleep(interval)
            task = self._tasks.get(research_id)
            if not task or task.done():
                return
            store = StateStore(self._data_root, research_id)
            meta = store.load_meta() or {}
            now = datetime.utcnow()
            if meta.get("cancel_requested_at"):
                store.update_meta({"cancel_reason": meta.get("cancel_reason") or "user_cancel"})
                self._append_control_event(research_id, "Run cancelled by request")
                task.cancel()
                return
            if self._is_run_timed_out(meta, now):
                store.update_meta({"cancel_reason": "timeout"})
                self._append_control_event(research_id, "Run timeout detected")
                task.cancel()
                return
            if self._is_idle_timed_out(meta, now):
                store.update_meta({"cancel_reason": "idle_timeout"})
                self._append_control_event(research_id, "Run idle timeout detected")
                task.cancel()
                return

    @staticmethod
    def _parse_iso(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _is_run_timed_out(self, meta: Dict[str, Any], now: datetime) -> bool:
        timeout_seconds = settings.RUN_TIMEOUT_SECONDS
        if not timeout_seconds or timeout_seconds <= 0:
            return False
        start_time = (
            self._parse_iso(meta.get("started_at"))
            or self._parse_iso(meta.get("resumed_at"))
            or self._parse_iso(meta.get("submitted_at"))
        )
        if not start_time:
            return False
        return (now - start_time).total_seconds() > timeout_seconds

    def _is_idle_timed_out(self, meta: Dict[str, Any], now: datetime) -> bool:
        idle_seconds = settings.RUN_IDLE_TIMEOUT_SECONDS
        if not idle_seconds or idle_seconds <= 0:
            return False
        last_progress = self._parse_iso(meta.get("last_progress_at")) or self._parse_iso(
            meta.get("started_at")
        )
        if not last_progress:
            return False
        return (now - last_progress).total_seconds() > idle_seconds

    @staticmethod
    def _normalize_priority(value: Any) -> int:
        if value is None or isinstance(value, bool):
            return 0
        if isinstance(value, (int, float)):
            try:
                priority = int(value)
            except (TypeError, ValueError):
                return 0
        elif isinstance(value, str):
            try:
                priority = int(value.strip())
            except ValueError:
                return 0
        else:
            return 0
        return max(-10, min(10, priority))

    def _extract_priority_from_meta(self, meta: Dict[str, Any]) -> int:
        priority = meta.get("priority")
        if priority is not None:
            return self._normalize_priority(priority)
        request_payload = meta.get("request")
        if isinstance(request_payload, dict):
            metadata = request_payload.get("metadata")
            if isinstance(metadata, dict):
                return self._normalize_priority(metadata.get("priority"))
        return 0

    def _compute_effective_priority(self, meta: Dict[str, Any], now: datetime) -> int:
        base_priority = self._extract_priority_from_meta(meta)
        aging_seconds = settings.QUEUE_PRIORITY_AGING_SECONDS
        if not aging_seconds or aging_seconds <= 0:
            return base_priority
        submitted_at = meta.get("submitted_at") or meta.get("started_at")
        submitted_ts = self._parse_iso(submitted_at)
        if not submitted_ts:
            return base_priority
        wait_seconds = max(0.0, (now - submitted_ts).total_seconds())
        bonus = int(wait_seconds // aging_seconds)
        effective = base_priority + bonus
        return max(-10, min(10, effective))

    def compute_queue_metrics(self, meta: Dict[str, Any]) -> Dict[str, Optional[float]]:
        """Compute effective priority and wait time for a queued run."""

        now = datetime.utcnow()
        submitted_at = meta.get("submitted_at") or meta.get("started_at")
        submitted_ts = self._parse_iso(submitted_at)
        wait_seconds = (now - submitted_ts).total_seconds() if submitted_ts else None
        effective_priority: Optional[int] = None
        if meta.get("status") == DeepResearchStatus.QUEUED.value:
            effective_priority = self._compute_effective_priority(meta, now)
        return {
            "effective_priority": effective_priority,
            "wait_seconds": wait_seconds,
        }

    async def _run_task(
        self,
        request: DeepResearchRequest,
        user_id: int,
        *,
        research_id: str,
        resume: bool,
    ) -> None:
        pipeline = ResearchPipeline(
            rag_service_url=self._rag_service_url,
            data_root=str(self._data_root),
            request_timeout=self._request_timeout,
        )
        try:
            timeout_seconds = settings.RUN_TIMEOUT_SECONDS
            if timeout_seconds and timeout_seconds > 0:
                await asyncio.wait_for(
                    pipeline.run(
                        request,
                        user_id=user_id,
                        research_id=research_id,
                        resume=resume,
                    ),
                    timeout=timeout_seconds,
                )
            else:
                await pipeline.run(
                    request,
                    user_id=user_id,
                    research_id=research_id,
                    resume=resume,
                )
        except asyncio.TimeoutError:
            store = StateStore(self._data_root, research_id)
            finished_at = datetime.utcnow().isoformat()
            store.update_meta(
                {
                    "status": DeepResearchStatus.FAILED.value,
                    "finished_at": finished_at,
                    "error": "timeout",
                    "cancel_reason": "timeout",
                }
            )
            self._append_control_event(research_id, "Run timeout")
        except asyncio.CancelledError:
            async with self._lock:
                silent_cancel = research_id in self._silent_cancel
                if silent_cancel:
                    self._silent_cancel.discard(research_id)
            if silent_cancel:
                return
            store = StateStore(self._data_root, research_id)
            meta = store.load_meta() or {}
            cancel_reason = meta.get("cancel_reason")
            finished_at = datetime.utcnow().isoformat()
            if cancel_reason in {"timeout", "idle_timeout"}:
                status = DeepResearchStatus.FAILED.value
                error = cancel_reason
                message = "Run timeout"
            else:
                status = DeepResearchStatus.CANCELLED.value
                error = cancel_reason or "cancelled"
                message = "Run cancelled"
            store.update_meta(
                {
                    "status": status,
                    "finished_at": finished_at,
                    "error": error,
                }
            )
            self._append_control_event(research_id, message)
            raise

    def _append_control_event(self, research_id: str, message: str) -> None:
        store = StateStore(self._data_root, research_id)
        store.append_progress(
            {
                "research_id": research_id,
                "stage": "control",
                "message": message,
                "timestamp": datetime.utcnow().isoformat(),
                "payload": {},
            }
        )

    def mark_stale_runs(self) -> int:
        """Mark running runs as failed after a service restart."""

        count = 0
        running_ids = set(self._queue_store.list_running())
        runs = StateStore.list_runs(self._data_root)
        for meta in runs:
            status = meta.get("status")
            if status != DeepResearchStatus.RUNNING.value:
                continue
            research_id = meta.get("research_id")
            if not research_id:
                continue
            if self.is_active(research_id):
                continue
            if research_id in running_ids:
                continue
            store = StateStore(self._data_root, research_id)
            now = datetime.utcnow().isoformat()
            store.update_meta(
                {
                    "status": DeepResearchStatus.FAILED.value,
                    "finished_at": now,
                    "error": "service_restart",
                }
            )
            self._append_control_event(research_id, "Run marked failed after service restart")
            count += 1
        return count

    def _mark_interrupted(
        self,
        store: StateStore,
        research_id: str,
        *,
        reason: str = "interrupted_by_restart",
    ) -> None:
        now = datetime.utcnow().isoformat()
        store.update_meta(
            {
                "status": DeepResearchStatus.FAILED.value,
                "finished_at": now,
                "error": reason,
            }
        )
        store.append_progress(
            {
                "research_id": research_id,
                "stage": "control",
                "message": f"Run interrupted: {reason}",
                "timestamp": now,
                "payload": {},
            }
        )
