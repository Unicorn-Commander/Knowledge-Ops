from __future__ import annotations

from contextlib import AbstractContextManager
import logging
import signal
import threading
from typing import Any, Protocol

from .config import settings
from .memory_quality import enqueue_due_refresh_jobs, list_refresh_jobs, scan_review_queue
from .operations import (
    claim_deletion_task,
    claim_refresh_run,
    finish_deletion_task,
    finish_refresh_run,
    list_deletion_tasks,
    record_worker_heartbeat,
    release_worker_leases,
    renew_lease,
    sanitize_error,
    update_source_health,
)
from .storage import init_db, record_audit
from .wiki import reconcile_pending_deletions

logger = logging.getLogger("knowledge_ops.worker")


class RefreshAdapter(Protocol):
    """Process-owned connector contract; implementations must be idempotent."""

    def refresh(self, *, source_ref: str, idempotency_key: str) -> dict[str, Any]: ...


class DeletionAdapter(Protocol):
    """Process-owned source deletion contract; implementations must be idempotent."""

    def delete(
        self, *, source_ref: str, deletion_id: str, idempotency_key: str
    ) -> dict[str, Any]: ...


def _error_summary(exc: Exception) -> str:
    return sanitize_error(
        exc, category="adapter_execution_failed", exception_type=type(exc).__name__
    )


class _LeaseKeeper(AbstractContextManager["_LeaseKeeper"]):
    def __init__(self, kind: str, item_id: str, worker_id: str, lease_token: str):
        self.kind = kind
        self.item_id = item_id
        self.worker_id = worker_id
        self.lease_token = lease_token
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_LeaseKeeper":
        interval = max(
            1,
            min(settings.worker_heartbeat_seconds, max(1, settings.worker_lease_seconds // 3)),
        )

        def keep() -> None:
            while not self._stop.wait(interval):
                try:
                    if not renew_lease(
                        self.kind, self.item_id,
                        worker_id=self.worker_id, lease_token=self.lease_token,
                    ):
                        logger.error("lost %s lease for %s", self.kind, self.item_id)
                        return
                    record_worker_heartbeat(
                        self.worker_id,
                        detail={"active_kind": self.kind, "active_id": self.item_id},
                    )
                except Exception as exc:
                    logger.error(
                        "lease renewal failed kind=%s code=%s",
                        self.kind,
                        sanitize_error(
                            exc, category="lease_renewal_failed", exception_type=type(exc).__name__
                        ),
                    )

        self._thread = threading.Thread(target=keep, name="knowledge-ops-lease", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)


class DurableWorker:
    def __init__(
        self,
        *,
        worker_id: str | None = None,
        refresh_adapters: dict[str, RefreshAdapter] | None = None,
        deletion_adapters: dict[str, DeletionAdapter] | None = None,
    ) -> None:
        self.worker_id = (worker_id or settings.worker_id)[:200]
        self.refresh_adapters = dict(refresh_adapters or {})
        self.deletion_adapters = dict(deletion_adapters or {})
        self._started = False

    def start(self) -> None:
        init_db()
        record_worker_heartbeat(
            self.worker_id,
            starting=True,
            detail={
                "refresh_adapters": sorted(self.refresh_adapters),
                "deletion_adapters": sorted(self.deletion_adapters),
            },
        )
        self._started = True

    def _publish_disabled_sources(self) -> None:
        for job in list_refresh_jobs():
            if job["kind"] != "connector_refresh":
                continue
            source_ref = str(job["source_ref"])
            if source_ref not in self.refresh_adapters:
                update_source_health(
                    source_ref,
                    status="disabled",
                    detail="adapter_not_registered",
                )
        for task in list_deletion_tasks(status="pending", limit=500):
            adapter = str(task["adapter"])
            if adapter not in self.deletion_adapters:
                update_source_health(
                    f"deletion:{adapter}",
                    status="disabled",
                    detail="adapter_not_registered",
                )

    def _run_refresh(self, run: dict[str, Any], at: str | None) -> dict[str, Any]:
        source_ref = str(run["source_ref"])
        try:
            with _LeaseKeeper(
                "refresh", str(run["run_id"]), self.worker_id, str(run["lease_token"])
            ):
                if run["kind"] == "review_scan":
                    result = scan_review_queue(at=str(run["scheduled_for"]))
                else:
                    adapter = self.refresh_adapters[source_ref]
                    result = adapter.refresh(
                        source_ref=source_ref,
                        idempotency_key=str(run["idempotency_key"]),
                    )
        except Exception as exc:
            message = _error_summary(exc)
            completed = finish_refresh_run(
                str(run["run_id"]), worker_id=self.worker_id,
                lease_token=str(run["lease_token"]),
                succeeded=False, error=message, at=at,
            )
            update_source_health(source_ref, status="failed", detail=message, at=at)
            record_audit(
                self.worker_id, "worker.refresh", str(run["run_id"]), completed["status"],
                {"job_id": run["job_id"], "source_ref": source_ref, "error": message},
            )
            return completed
        completed = finish_refresh_run(
            str(run["run_id"]), worker_id=self.worker_id,
            lease_token=str(run["lease_token"]),
            succeeded=True, result=result, at=at,
        )
        update_source_health(source_ref, status="healthy", detail="refresh_succeeded", at=at)
        record_audit(
            self.worker_id, "worker.refresh", str(run["run_id"]), "success",
            {"job_id": run["job_id"], "source_ref": source_ref},
        )
        return completed

    def _run_deletion(self, task: dict[str, Any], at: str | None) -> dict[str, Any]:
        adapter_name = str(task["adapter"])
        health_ref = f"deletion:{adapter_name}"
        try:
            with _LeaseKeeper(
                "deletion", str(task["task_id"]), self.worker_id, str(task["lease_token"])
            ):
                result = self.deletion_adapters[adapter_name].delete(
                    source_ref=str(task["source_ref"]),
                    deletion_id=str(task["deletion_id"]),
                    idempotency_key=str(task["task_id"]),
                )
        except Exception as exc:
            message = _error_summary(exc)
            completed = finish_deletion_task(
                str(task["task_id"]), worker_id=self.worker_id,
                lease_token=str(task["lease_token"]),
                succeeded=False, error=message, at=at,
            )
            update_source_health(health_ref, status="failed", detail=message, at=at)
            record_audit(
                self.worker_id, "worker.deletion", str(task["task_id"]), completed["status"],
                {"adapter": adapter_name, "deletion_id": task["deletion_id"], "error": message},
            )
            return completed
        completed = finish_deletion_task(
            str(task["task_id"]), worker_id=self.worker_id,
            lease_token=str(task["lease_token"]),
            succeeded=True, result=result, at=at,
        )
        update_source_health(health_ref, status="healthy", detail="deletion_propagated", at=at)
        record_audit(
            self.worker_id, "worker.deletion", str(task["task_id"]), "success",
            {"adapter": adapter_name, "deletion_id": task["deletion_id"]},
        )
        return completed

    def run_once(self, *, at: str | None = None) -> dict[str, Any]:
        if not self._started:
            self.start()
        scheduled = enqueue_due_refresh_jobs(actor=self.worker_id, at=at)
        local_deletions = reconcile_pending_deletions()
        self._publish_disabled_sources()
        refresh_results: list[dict[str, Any]] = []
        deletion_results: list[dict[str, Any]] = []
        for _ in range(max(1, settings.worker_batch_size)):
            run = claim_refresh_run(
                self.worker_id,
                connector_adapters=set(self.refresh_adapters),
                at=at,
            )
            if run is None:
                break
            refresh_results.append(self._run_refresh(run, at))
        for _ in range(max(1, settings.worker_batch_size)):
            task = claim_deletion_task(
                self.worker_id,
                adapters=set(self.deletion_adapters),
                at=at,
            )
            if task is None:
                break
            deletion_results.append(self._run_deletion(task, at))
        detail = {
            "scheduled": len(scheduled),
            "refresh_processed": len(refresh_results),
            "deletions_processed": len(deletion_results),
            "local_deletions_pending": local_deletions["pending"],
            "refresh_adapters": sorted(self.refresh_adapters),
            "deletion_adapters": sorted(self.deletion_adapters),
        }
        record_worker_heartbeat(self.worker_id, detail=detail, at=at)
        return {
            **detail,
            "refresh_results": refresh_results,
            "deletion_results": deletion_results,
        }

    def stop(self, *, failed: bool = False) -> dict[str, int]:
        if not self._started:
            return {"refresh_runs": 0, "deletion_tasks": 0}
        record_worker_heartbeat(self.worker_id, status="stopping")
        released = release_worker_leases(self.worker_id)
        record_worker_heartbeat(
            self.worker_id,
            status="failed" if failed else "stopped",
            detail={"released": released},
        )
        self._started = False
        return released

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        stop_event = stop_event or threading.Event()
        self.start()
        failed = False
        try:
            while not stop_event.is_set():
                try:
                    self.run_once()
                except Exception as exc:
                    logger.error(
                        "worker loop iteration failed code=%s",
                        sanitize_error(
                            exc, category="worker_iteration_failed", exception_type=type(exc).__name__
                        ),
                    )
                stop_event.wait(max(0.1, settings.worker_poll_seconds))
        except BaseException:
            failed = True
            raise
        finally:
            self.stop(failed=failed)


def run_worker(*, once: bool = False) -> dict[str, Any] | None:
    """Start the credential-free worker. Live adapters must be wired explicitly."""
    worker = DurableWorker()
    if once:
        try:
            return worker.run_once()
        finally:
            worker.stop()

    stop_event = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    worker.run_forever(stop_event)
    return None
