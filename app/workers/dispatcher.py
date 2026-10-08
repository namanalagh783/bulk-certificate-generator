"""JobDispatcher protocol and its two implementations (ADR-003, ADR-012, ADR-018).

Workers receive only a `job_id`; the database is the single source of truth. Kept free of
FastAPI imports (ARCHITECTURE §2).
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

logger = logging.getLogger(__name__)


class JobDispatcher(Protocol):
    """Hands a job id to a worker. `shutdown()` takes no arguments (ADR-018)."""

    def submit(self, job_id: str) -> None: ...

    def shutdown(self) -> None: ...


class InlineDispatcher:
    """Runs `process_job` synchronously on the calling thread. Deterministic; used in tests."""

    def __init__(self, process_job: Callable[[str], None]) -> None:
        self._process_job = process_job

    def submit(self, job_id: str) -> None:
        self._process_job(job_id)

    def shutdown(self) -> None:
        """Nothing to shut down: there is no background executor."""


class ThreadPoolDispatcher:
    """Bounded `ThreadPoolExecutor` running jobs in the background (ARCHITECTURE §8, ADR-003)."""

    def __init__(self, process_job: Callable[[str], None], max_workers: int) -> None:
        self._process_job = process_job
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="certgen-worker"
        )

    def submit(self, job_id: str) -> None:
        self._executor.submit(self._run, job_id)

    def _run(self, job_id: str) -> None:
        """Wrap the call so an exception is logged; a bare future swallows it otherwise."""
        try:
            self._process_job(job_id)
        except Exception:  # noqa: BLE001 - must not kill the pool or be silently lost
            logger.exception("Unhandled error processing job %s", job_id)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
