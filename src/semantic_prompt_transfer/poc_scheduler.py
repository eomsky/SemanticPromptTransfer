from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Callable, Iterator


@dataclass(frozen=True)
class FairShareState:
    job_id: str
    status: str
    phase: str | None
    runnable_jobs: int
    running_jobs: int
    queue_position: int | None
    fair_share_percent: float
    idle_seconds: float

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value.pop("job_id", None)
        return value


class FairShareReviewScheduler:
    """Round-robin scheduler for expensive review work on one shared accelerator.

    Each request registers a pending quantum. At most ``parallel_quanta`` distinct
    review jobs receive a quantum at the same time. When a quantum ends, a job with
    more pending work goes to the back of the queue. This prevents a long A-E review
    from monopolising the accelerator while still allowing vLLM continuous batching.
    """

    def __init__(
        self,
        *,
        parallel_quanta: int = 2,
        idle_timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if parallel_quanta < 1:
            raise ValueError("parallel_quanta must be positive")
        if idle_timeout_seconds <= 0:
            raise ValueError("idle_timeout_seconds must be positive")
        self.parallel_quanta = int(parallel_quanta)
        self.idle_timeout_seconds = float(idle_timeout_seconds)
        self.clock = clock
        self._condition = threading.Condition(threading.RLock())
        self._queue: deque[str] = deque()
        self._queued: set[str] = set()
        self._running: set[str] = set()
        self._pending: dict[str, int] = {}
        self._last_activity: dict[str, float] = {}
        self._status: dict[str, str] = {}
        self._phase: dict[str, str | None] = {}
        self._suspend_after_quantum: set[str] = set()

    def register(self, job_id: str) -> FairShareState:
        with self._condition:
            now = self.clock()
            self._last_activity[job_id] = now
            self._status[job_id] = "RUNNABLE"
            self._phase[job_id] = None
            self._pending.setdefault(job_id, 0)
            self._condition.notify_all()
            return self._state_locked(job_id)

    def _idle_locked(self, job_id: str) -> float:
        return max(0.0, self.clock() - self._last_activity.get(job_id, self.clock()))

    def _remove_queued_locked(self, job_id: str) -> None:
        if job_id not in self._queued:
            return
        self._queue = deque(value for value in self._queue if value != job_id)
        self._queued.discard(job_id)

    def _enqueue_locked(self, job_id: str) -> None:
        if (
            self._status.get(job_id) == "RUNNABLE"
            and self._pending.get(job_id, 0) > 0
            and job_id not in self._queued
            and job_id not in self._running
        ):
            self._queue.append(job_id)
            self._queued.add(job_id)

    def _expire_idle_locked(self) -> None:
        for job_id, status in list(self._status.items()):
            if status != "RUNNABLE":
                continue
            if self._idle_locked(job_id) < self.idle_timeout_seconds:
                continue
            if job_id in self._running:
                self._suspend_after_quantum.add(job_id)
                continue
            self._status[job_id] = "SUSPENDED"
            self._remove_queued_locked(job_id)

    def touch(self, job_id: str) -> FairShareState:
        with self._condition:
            if job_id not in self._status:
                raise KeyError(job_id)
            self._last_activity[job_id] = self.clock()
            if self._status[job_id] == "SUSPENDED":
                self._status[job_id] = "RUNNABLE"
                self._suspend_after_quantum.discard(job_id)
            self._enqueue_locked(job_id)
            self._condition.notify_all()
            return self._state_locked(job_id)

    def suspend(self, job_id: str) -> FairShareState:
        with self._condition:
            if job_id not in self._status:
                raise KeyError(job_id)
            if job_id in self._running:
                self._suspend_after_quantum.add(job_id)
            else:
                self._status[job_id] = "SUSPENDED"
                self._remove_queued_locked(job_id)
            self._condition.notify_all()
            return self._state_locked(job_id)

    def finish(self, job_id: str) -> None:
        with self._condition:
            self._status[job_id] = "FINISHED"
            self._phase[job_id] = None
            self._pending[job_id] = 0
            self._running.discard(job_id)
            self._remove_queued_locked(job_id)
            self._suspend_after_quantum.discard(job_id)
            self._condition.notify_all()

    def _state_locked(self, job_id: str) -> FairShareState:
        self._expire_idle_locked()
        runnable = [key for key, value in self._status.items() if value == "RUNNABLE"]
        try:
            position = list(self._queue).index(job_id) + 1
        except ValueError:
            position = None
        share = 100.0 / max(1, len(runnable)) if self._status.get(job_id) == "RUNNABLE" else 0.0
        return FairShareState(
            job_id=job_id,
            status=self._status.get(job_id, "UNKNOWN"),
            phase=self._phase.get(job_id),
            runnable_jobs=len(runnable),
            running_jobs=len(self._running),
            queue_position=position,
            fair_share_percent=round(share, 1),
            idle_seconds=round(self._idle_locked(job_id), 1),
        )

    def state(self, job_id: str) -> FairShareState:
        with self._condition:
            if job_id not in self._status:
                raise KeyError(job_id)
            return self._state_locked(job_id)

    def snapshot(self) -> dict[str, object]:
        with self._condition:
            self._expire_idle_locked()
            return {
                "policy": "ROUND_ROBIN_FAIR_SHARE",
                "parallel_quanta": self.parallel_quanta,
                "idle_timeout_seconds": self.idle_timeout_seconds,
                "runnable_jobs": sum(value == "RUNNABLE" for value in self._status.values()),
                "running_jobs": len(self._running),
                "suspended_jobs": sum(value == "SUSPENDED" for value in self._status.values()),
            }

    @contextmanager
    def quantum(self, job_id: str, phase: str) -> Iterator[FairShareState]:
        with self._condition:
            if job_id not in self._status:
                self.register(job_id)
            self._pending[job_id] = self._pending.get(job_id, 0) + 1
            self._enqueue_locked(job_id)
            while True:
                self._expire_idle_locked()
                status = self._status.get(job_id)
                if status == "FINISHED":
                    raise RuntimeError("review job already finished")
                if status == "SUSPENDED":
                    self._condition.wait(timeout=0.25)
                    continue
                at_front = bool(self._queue and self._queue[0] == job_id)
                has_slot = len(self._running) < self.parallel_quanta
                if at_front and has_slot and job_id not in self._running:
                    self._queue.popleft()
                    self._queued.discard(job_id)
                    self._pending[job_id] -= 1
                    self._running.add(job_id)
                    self._phase[job_id] = str(phase)
                    state = self._state_locked(job_id)
                    break
                self._condition.wait(timeout=0.1)
        try:
            yield state
        finally:
            with self._condition:
                self._running.discard(job_id)
                self._phase[job_id] = None
                if job_id in self._suspend_after_quantum or self._idle_locked(job_id) >= self.idle_timeout_seconds:
                    self._suspend_after_quantum.discard(job_id)
                    if self._status.get(job_id) != "FINISHED":
                        self._status[job_id] = "SUSPENDED"
                    self._remove_queued_locked(job_id)
                else:
                    self._enqueue_locked(job_id)
                self._condition.notify_all()
