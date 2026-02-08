"""Queue store implementations for DeepResearch runs."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import redis

from core.config import settings


@dataclass(frozen=True)
class QueueEntry:
    """Queue entry record."""

    research_id: str
    priority: int
    submitted_at: Optional[str]
    effective_priority: int
    wait_seconds: Optional[float]


class SqliteRunQueueStore:
    """Persistent queue store based on SQLite."""

    def __init__(self, base_dir: Path) -> None:
        """Initialize queue storage under the data root."""

        self._db_path = base_dir / "run_queue.db"
        base_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def enqueue(self, research_id: str, priority: int, submitted_at: str) -> None:
        """Insert or update a queued run."""

        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM run_queue WHERE research_id = ?",
                (research_id,),
            ).fetchone()
            if row and row[0] == "running":
                conn.execute("COMMIT")
                raise ValueError("Research task already running")
            conn.execute(
                """
                INSERT INTO run_queue (research_id, status, priority, submitted_at, updated_at)
                VALUES (?, 'queued', ?, ?, ?)
                ON CONFLICT(research_id) DO UPDATE SET
                    status = 'queued',
                    priority = excluded.priority,
                    submitted_at = excluded.submitted_at,
                    updated_at = excluded.updated_at,
                    owner_id = NULL,
                    lease_until = NULL
                """,
                (research_id, priority, submitted_at, now),
            )
            conn.execute("COMMIT")

    def get_status(self, research_id: str) -> Optional[str]:
        """Return the queue status for a run."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM run_queue WHERE research_id = ?",
                (research_id,),
            ).fetchone()
            return row[0] if row else None

    def list_running(self) -> List[str]:
        """Return running run ids."""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT research_id FROM run_queue WHERE status = 'running'"
            ).fetchall()
        return [row[0] for row in rows]

    def list_running_by_owner(self, owner_id: str) -> List[str]:
        """Return running run ids owned by a specific instance."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT research_id
                FROM run_queue
                WHERE status = 'running' AND owner_id = ?
                """,
                (owner_id,),
            ).fetchall()
        return [row[0] for row in rows]

    def list_pending(self, aging_seconds: int) -> List[QueueEntry]:
        """Return queued runs sorted by effective priority."""

        now = datetime.utcnow()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT research_id, priority, submitted_at
                FROM run_queue
                WHERE status = 'queued'
                """
            ).fetchall()
        entries: List[QueueEntry] = []
        for research_id, priority, submitted_at in rows:
            submitted_ts = _parse_iso(submitted_at)
            wait_seconds = (now - submitted_ts).total_seconds() if submitted_ts else None
            effective = _compute_effective_priority(priority, wait_seconds, aging_seconds)
            entries.append(
                QueueEntry(
                    research_id=research_id,
                    priority=priority,
                    submitted_at=submitted_at,
                    effective_priority=effective,
                    wait_seconds=wait_seconds,
                )
            )
        entries.sort(
            key=lambda item: (-item.effective_priority, item.submitted_at or "")
        )
        return entries

    def count_pending(self) -> int:
        """Return pending queue size."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM run_queue WHERE status = 'queued'"
            ).fetchone()
        return int(row[0]) if row else 0

    def count_running(self, now: datetime) -> int:
        """Return count of running runs with valid leases."""

        with self._connect() as conn:
            return self._count_running(conn, now)

    def claim_next(
        self,
        *,
        owner_id: str,
        lease_seconds: int,
        max_active_runs: int,
        aging_seconds: int,
    ) -> Optional[str]:
        """Claim the next available queued run."""

        now = datetime.utcnow()
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            running_count = self._count_running(conn, now)
            if running_count >= max_active_runs:
                conn.execute("COMMIT")
                return None
            rows = conn.execute(
                """
                SELECT research_id, priority, submitted_at
                FROM run_queue
                WHERE status = 'queued'
                """
            ).fetchall()
            if not rows:
                conn.execute("COMMIT")
                return None
            candidate = _pick_candidate(rows, now, aging_seconds)
            updated = conn.execute(
                """
                UPDATE run_queue
                SET status = 'running',
                    owner_id = ?,
                    lease_until = ?,
                    updated_at = ?
                WHERE research_id = ? AND status = 'queued'
                """,
                (owner_id, lease_until, now.isoformat(), candidate),
            ).rowcount
            conn.execute("COMMIT")
        return candidate if updated else None

    def claim(
        self,
        research_id: str,
        *,
        owner_id: str,
        lease_seconds: int,
        max_active_runs: int,
    ) -> bool:
        """Claim a specific queued run."""

        now = datetime.utcnow()
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            running_count = self._count_running(conn, now)
            if running_count >= max_active_runs:
                conn.execute("COMMIT")
                return False
            updated = conn.execute(
                """
                UPDATE run_queue
                SET status = 'running',
                    owner_id = ?,
                    lease_until = ?,
                    updated_at = ?
                WHERE research_id = ? AND status = 'queued'
                """,
                (owner_id, lease_until, now.isoformat(), research_id),
            ).rowcount
            conn.execute("COMMIT")
        return bool(updated)

    def renew_leases(self, owner_id: str, research_ids: List[str], lease_seconds: int) -> None:
        """Renew leases for runs owned by the instance."""

        if not research_ids:
            return
        now = datetime.utcnow()
        now_iso = now.isoformat()
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                """
                UPDATE run_queue
                SET lease_until = ?, updated_at = ?
                WHERE research_id = ? AND owner_id = ? AND status = 'running'
                """,
                [
                    (lease_until, now_iso, research_id, owner_id)
                    for research_id in research_ids
                ],
            )
            conn.execute("COMMIT")

    def list_expired_running(self, now: datetime) -> List[str]:
        """List running runs with expired leases."""

        now_iso = now.isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT research_id
                FROM run_queue
                WHERE status = 'running'
                  AND lease_until IS NOT NULL
                  AND lease_until < ?
                """,
                (now_iso,),
            ).fetchall()
        return [row[0] for row in rows]

    def update_priority(self, research_id: str, priority: int) -> None:
        """Update priority for a queued run."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE run_queue
                SET priority = ?, updated_at = ?
                WHERE research_id = ? AND status = 'queued'
                """,
                (priority, datetime.utcnow().isoformat(), research_id),
            )
            conn.execute("COMMIT")

    def requeue(self, research_id: str, priority: int, submitted_at: str) -> None:
        """Requeue a run after lease expiration."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE run_queue
                SET status = 'queued',
                    priority = ?,
                    submitted_at = ?,
                    updated_at = ?,
                    owner_id = NULL,
                    lease_until = NULL
                WHERE research_id = ?
                """,
                (priority, submitted_at, datetime.utcnow().isoformat(), research_id),
            )
            conn.execute("COMMIT")

    def remove(self, research_id: str) -> None:
        """Remove a run from the queue store."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM run_queue WHERE research_id = ?", (research_id,))
            conn.execute("COMMIT")

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_queue (
                    research_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    submitted_at TEXT,
                    updated_at TEXT,
                    owner_id TEXT,
                    lease_until TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_run_queue_status ON run_queue(status)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=1)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @staticmethod
    def _count_running(conn: sqlite3.Connection, now: datetime) -> int:
        now_iso = now.isoformat()
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM run_queue
            WHERE status = 'running'
              AND (lease_until IS NULL OR lease_until >= ?)
            """,
            (now_iso,),
        ).fetchone()
        return int(row[0]) if row else 0


class RedisRunQueueStore:
    """Persistent queue store based on Redis."""

    _CLAIM_SCRIPT = """
    local queued_key = KEYS[1]
    local running_key = KEYS[2]
    local prefix = ARGV[1]
    local owner_id = ARGV[2]
    local lease_until = tonumber(ARGV[3])
    local max_active = tonumber(ARGV[4])
    local aging = tonumber(ARGV[5])
    local now = tonumber(ARGV[6])

    local running = redis.call("SMEMBERS", running_key)
    local running_count = 0
    for i, run_id in ipairs(running) do
        local run_key = prefix .. ":run:" .. run_id
        local lease = tonumber(redis.call("HGET", run_key, "lease_until_ts")) or 0
        if lease == 0 or lease >= now then
            running_count = running_count + 1
        end
    end
    if running_count >= max_active then
        return nil
    end

    local queued = redis.call("SMEMBERS", queued_key)
    if #queued == 0 then
        return nil
    end

    local best_id = nil
    local best_eff = -1000000
    local best_submitted = nil
    for i, run_id in ipairs(queued) do
        local run_key = prefix .. ":run:" .. run_id
        local status = redis.call("HGET", run_key, "status")
        if status == "queued" then
            local priority = tonumber(redis.call("HGET", run_key, "priority")) or 0
            local submitted_ts = tonumber(redis.call("HGET", run_key, "submitted_at_ts")) or now
            local wait = now - submitted_ts
            local effective = priority
            if aging > 0 and wait > 0 then
                effective = priority + math.floor(wait / aging)
            end
            if best_id == nil or effective > best_eff or (effective == best_eff and submitted_ts < best_submitted) then
                best_id = run_id
                best_eff = effective
                best_submitted = submitted_ts
            end
        end
    end
    if not best_id then
        return nil
    end
    local best_key = prefix .. ":run:" .. best_id
    redis.call("HSET", best_key,
        "status", "running",
        "owner_id", owner_id,
        "lease_until_ts", lease_until,
        "updated_at_ts", now
    )
    redis.call("SREM", queued_key, best_id)
    redis.call("SADD", running_key, best_id)
    return best_id
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        db: int,
        password: Optional[str],
        prefix: str,
    ) -> None:
        """Initialize Redis queue storage."""

        clean_prefix = prefix.strip() or "deep_research:queue"
        self._prefix = clean_prefix.rstrip(":")
        self._queued_key = f"{self._prefix}:queued"
        self._running_key = f"{self._prefix}:running"
        self._redis = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
        )
        self._claim_script = self._redis.register_script(self._CLAIM_SCRIPT)

    def enqueue(self, research_id: str, priority: int, submitted_at: str) -> None:
        """Insert or update a queued run."""

        now = datetime.utcnow()
        submitted_at = submitted_at or now.isoformat()
        submitted_ts = _to_timestamp(submitted_at) or now.timestamp()
        now_ts = now.timestamp()
        run_key = self._run_key(research_id)
        for _ in range(3):
            pipe = self._redis.pipeline()
            try:
                pipe.watch(run_key)
                status = pipe.hget(run_key, "status")
                if status == "running":
                    pipe.unwatch()
                    raise ValueError("Research task already running")
                pipe.multi()
                pipe.hset(
                    run_key,
                    mapping={
                        "status": "queued",
                        "priority": int(priority),
                        "submitted_at": submitted_at,
                        "submitted_at_ts": submitted_ts,
                        "updated_at_ts": now_ts,
                        "owner_id": "",
                        "lease_until_ts": "",
                    },
                )
                pipe.sadd(self._queued_key, research_id)
                pipe.srem(self._running_key, research_id)
                pipe.execute()
                return
            except redis.WatchError:
                continue
        raise RuntimeError("Failed to enqueue run after retries")

    def get_status(self, research_id: str) -> Optional[str]:
        """Return the queue status for a run."""

        return self._redis.hget(self._run_key(research_id), "status")

    def list_running(self) -> List[str]:
        """Return running run ids."""

        return list(self._redis.smembers(self._running_key))

    def list_running_by_owner(self, owner_id: str) -> List[str]:
        """Return running run ids owned by a specific instance."""

        running_ids = self.list_running()
        if not running_ids:
            return []
        pipe = self._redis.pipeline()
        for run_id in running_ids:
            pipe.hget(self._run_key(run_id), "owner_id")
        owners = pipe.execute()
        return [run_id for run_id, owner in zip(running_ids, owners) if owner == owner_id]

    def list_pending(self, aging_seconds: int) -> List[QueueEntry]:
        """Return queued runs sorted by effective priority."""

        queued_ids = list(self._redis.smembers(self._queued_key))
        if not queued_ids:
            return []
        pipe = self._redis.pipeline()
        for run_id in queued_ids:
            pipe.hmget(self._run_key(run_id), "priority", "submitted_at", "submitted_at_ts")
        rows = pipe.execute()
        now = datetime.utcnow()
        entries: List[QueueEntry] = []
        for run_id, row in zip(queued_ids, rows):
            priority_raw, submitted_at, submitted_ts_raw = row
            priority = int(priority_raw) if priority_raw is not None else 0
            submitted_ts = _to_float(submitted_ts_raw)
            if not submitted_at and submitted_ts is not None:
                submitted_at = datetime.utcfromtimestamp(submitted_ts).isoformat()
            wait_seconds = (
                (now - datetime.utcfromtimestamp(submitted_ts)).total_seconds()
                if submitted_ts is not None
                else None
            )
            effective = _compute_effective_priority(priority, wait_seconds, aging_seconds)
            entries.append(
                QueueEntry(
                    research_id=run_id,
                    priority=priority,
                    submitted_at=submitted_at,
                    effective_priority=effective,
                    wait_seconds=wait_seconds,
                )
            )
        entries.sort(key=lambda item: (-item.effective_priority, item.submitted_at or ""))
        return entries

    def count_pending(self) -> int:
        """Return pending queue size."""

        return int(self._redis.scard(self._queued_key))

    def count_running(self, now: datetime) -> int:
        """Return count of running runs with valid leases."""

        now_ts = now.timestamp()
        running_ids = self.list_running()
        if not running_ids:
            return 0
        pipe = self._redis.pipeline()
        for run_id in running_ids:
            pipe.hget(self._run_key(run_id), "lease_until_ts")
        leases = pipe.execute()
        count = 0
        for lease in leases:
            lease_ts = _to_float(lease)
            if lease_ts is None or lease_ts >= now_ts:
                count += 1
        return count

    def claim_next(
        self,
        *,
        owner_id: str,
        lease_seconds: int,
        max_active_runs: int,
        aging_seconds: int,
    ) -> Optional[str]:
        """Claim the next available queued run."""

        now = datetime.utcnow()
        lease_until = now.timestamp() + lease_seconds
        result = self._claim_script(
            keys=[self._queued_key, self._running_key],
            args=[
                self._prefix,
                owner_id,
                lease_until,
                max_active_runs,
                aging_seconds,
                now.timestamp(),
            ],
        )
        return str(result) if result else None

    def claim(
        self,
        research_id: str,
        *,
        owner_id: str,
        lease_seconds: int,
        max_active_runs: int,
    ) -> bool:
        """Claim a specific queued run."""

        now = datetime.utcnow()
        lease_until = now.timestamp() + lease_seconds
        if self.count_running(now) >= max_active_runs:
            return False
        run_key = self._run_key(research_id)
        for _ in range(3):
            pipe = self._redis.pipeline()
            try:
                pipe.watch(run_key)
                status = pipe.hget(run_key, "status")
                if status != "queued":
                    pipe.unwatch()
                    return False
                pipe.multi()
                pipe.hset(
                    run_key,
                    mapping={
                        "status": "running",
                        "owner_id": owner_id,
                        "lease_until_ts": lease_until,
                        "updated_at_ts": now.timestamp(),
                    },
                )
                pipe.srem(self._queued_key, research_id)
                pipe.sadd(self._running_key, research_id)
                pipe.execute()
                return True
            except redis.WatchError:
                continue
        return False

    def renew_leases(self, owner_id: str, research_ids: List[str], lease_seconds: int) -> None:
        """Renew leases for runs owned by the instance."""

        if not research_ids:
            return
        now = datetime.utcnow()
        lease_until = now.timestamp() + lease_seconds
        pipe = self._redis.pipeline()
        for run_id in research_ids:
            run_key = self._run_key(run_id)
            pipe.hget(run_key, "owner_id")
        owners = pipe.execute()
        pipe = self._redis.pipeline()
        has_updates = False
        for run_id, owner in zip(research_ids, owners):
            if owner != owner_id:
                continue
            pipe.hset(
                self._run_key(run_id),
                mapping={
                    "lease_until_ts": lease_until,
                    "updated_at_ts": now.timestamp(),
                },
            )
            has_updates = True
        if has_updates:
            pipe.execute()

    def list_expired_running(self, now: datetime) -> List[str]:
        """List running runs with expired leases."""

        now_ts = now.timestamp()
        running_ids = self.list_running()
        if not running_ids:
            return []
        pipe = self._redis.pipeline()
        for run_id in running_ids:
            pipe.hget(self._run_key(run_id), "lease_until_ts")
        leases = pipe.execute()
        expired: List[str] = []
        for run_id, lease in zip(running_ids, leases):
            lease_ts = _to_float(lease)
            if lease_ts is not None and lease_ts < now_ts:
                expired.append(run_id)
        return expired

    def update_priority(self, research_id: str, priority: int) -> None:
        """Update priority for a queued run."""

        run_key = self._run_key(research_id)
        status = self._redis.hget(run_key, "status")
        if status != "queued":
            return
        self._redis.hset(
            run_key,
            mapping={
                "priority": int(priority),
                "updated_at_ts": datetime.utcnow().timestamp(),
            },
        )

    def requeue(self, research_id: str, priority: int, submitted_at: str) -> None:
        """Requeue a run after lease expiration."""

        now = datetime.utcnow()
        submitted_at = submitted_at or now.isoformat()
        submitted_ts = _to_timestamp(submitted_at) or now.timestamp()
        self._redis.hset(
            self._run_key(research_id),
            mapping={
                "status": "queued",
                "priority": int(priority),
                "submitted_at": submitted_at,
                "submitted_at_ts": submitted_ts,
                "updated_at_ts": now.timestamp(),
                "owner_id": "",
                "lease_until_ts": "",
            },
        )
        self._redis.sadd(self._queued_key, research_id)
        self._redis.srem(self._running_key, research_id)

    def remove(self, research_id: str) -> None:
        """Remove a run from the queue store."""

        pipe = self._redis.pipeline()
        pipe.srem(self._queued_key, research_id)
        pipe.srem(self._running_key, research_id)
        pipe.delete(self._run_key(research_id))
        pipe.execute()

    def _run_key(self, research_id: str) -> str:
        return f"{self._prefix}:run:{research_id}"


def create_queue_store(base_dir: Path) -> SqliteRunQueueStore | RedisRunQueueStore:
    """Create a queue store based on configuration."""

    backend = (settings.QUEUE_BACKEND or "sqlite").strip().lower()
    if backend == "redis":
        return RedisRunQueueStore(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD,
            prefix=settings.REDIS_QUEUE_PREFIX,
        )
    return SqliteRunQueueStore(base_dir)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _compute_effective_priority(
    priority: int, wait_seconds: Optional[float], aging_seconds: int
) -> int:
    if wait_seconds is None or aging_seconds <= 0:
        return max(-10, min(10, priority))
    bonus = int(wait_seconds // aging_seconds)
    effective = priority + bonus
    return max(-10, min(10, effective))


def _pick_candidate(
    rows: List[tuple[str, int, Optional[str]]],
    now: datetime,
    aging_seconds: int,
) -> str:
    candidates: List[QueueEntry] = []
    for research_id, priority, submitted_at in rows:
        submitted_ts = _parse_iso(submitted_at)
        wait_seconds = (now - submitted_ts).total_seconds() if submitted_ts else None
        effective = _compute_effective_priority(priority, wait_seconds, aging_seconds)
        candidates.append(
            QueueEntry(
                research_id=research_id,
                priority=priority,
                submitted_at=submitted_at,
                effective_priority=effective,
                wait_seconds=wait_seconds,
            )
        )
    candidates.sort(
        key=lambda item: (-item.effective_priority, item.submitted_at or "")
    )
    return candidates[0].research_id


def _to_timestamp(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    parsed = _parse_iso(value)
    return parsed.timestamp() if parsed else None


def _to_float(value: Optional[str]) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
