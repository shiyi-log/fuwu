"""Bounded background execution boundary for release operations.

The HTTP API defaults to synchronous completion so local callers receive
deterministic validation errors. Deployments that need queued jobs can submit
the same ReleaseService operation to this worker and poll the audit-backed
status endpoint; credentials and staging paths never enter the queue payload.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Mapping

from .core import ReleaseService


class ReleaseWorker:
    def __init__(self, service: ReleaseService, *, max_workers: int = 2) -> None:
        self.service = service
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="fuwu-release")

    def submit_publish(self, request: Mapping[str, Any], *, actor: str, request_id: str) -> Future[tuple[int, dict[str, Any]]]:
        return self.executor.submit(self.service.publish, dict(request), actor=actor, request_id=request_id)

    def submit_rollback(self, request: Mapping[str, Any], *, actor: str, request_id: str) -> Future[tuple[int, dict[str, Any]]]:
        return self.executor.submit(self.service.rollback, dict(request), actor=actor, request_id=request_id)

    def shutdown(self, *, wait: bool = True) -> None:
        self.executor.shutdown(wait=wait)
