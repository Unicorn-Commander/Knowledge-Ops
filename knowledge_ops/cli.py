from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from . import federation
from .authorization import system_principal
from .file_index import deep_read, find_file, index_root
from .evaluation import evaluate_memory
from .memory_quality import run_due_refresh_jobs
from .metrics import record_metric, render_metric_chart
from .maintenance import create_backup, reindex_wiki, restore_and_verify
from .operations import operations_snapshot, worker_readiness
from .retag_legacy import retag_legacy
from .storage import init_db
from .worker import run_worker
from .wiki import get_doc, history, ingest_marketing_docs, list_docs, upsert_doc

_LOCAL = system_principal()


def print_json(obj: object) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=lambda o: o.__dict__))


async def demo() -> None:
    init_db()
    ingest_marketing_docs()
    index_root(Path.home() / "Documents", owner=_LOCAL.subject, limit=80)
    record_metric("qwen.orchestrator.plan_seconds", 3.6, {"model": "Qwen3.6-35B-A3B", "host": "bigboy", "gpu": "3090"})
    res = await federation.search("What is Magic Unicorn?", scope="global", k=8, principal=_LOCAL)
    print(res.answer)
    print("\nSources used:", ", ".join(sorted({p.source for p in res.passages})))


def main() -> None:
    parser = argparse.ArgumentParser(prog="knowledge-ops")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("ingest-marketing")
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--scope")
    p.add_argument("--k", type=int, default=8)
    p = sub.add_parser("ground")
    p.add_argument("claim")
    p.add_argument("--scope")
    p.add_argument("--k", type=int, default=8)
    p = sub.add_parser("upsert-doc")
    p.add_argument("path")
    p.add_argument("file")
    p.add_argument("--scope", default="global")
    p = sub.add_parser("get-doc")
    p.add_argument("id")
    sub.add_parser("list-docs")
    p = sub.add_parser("history")
    p.add_argument("id")
    p = sub.add_parser("index-files")
    p.add_argument("root")
    p.add_argument("--limit", type=int)
    p = sub.add_parser("find-file")
    p.add_argument("query")
    p.add_argument("--k", type=int, default=8)
    p = sub.add_parser("deep-read")
    p.add_argument("path")
    p.add_argument("--query", default="")
    p.add_argument("--k", type=int, default=8)
    p = sub.add_parser("record-metric")
    p.add_argument("name")
    p.add_argument("value", type=float)
    p.add_argument("--tags", default="{}")
    p = sub.add_parser("chart")
    p.add_argument("name")
    p.add_argument("--out", default="")
    sub.add_parser("demo")
    p = sub.add_parser("refresh-due")
    p.add_argument("--at")
    sub.add_parser("evaluate-memory")
    p = sub.add_parser("worker")
    p.add_argument("--once", action="store_true")
    sub.add_parser("operations-status")
    sub.add_parser("worker-health")
    p = sub.add_parser("backup")
    p.add_argument("--out", required=True)
    p = sub.add_parser("restore-verify")
    p.add_argument("archive")
    p.add_argument("--target", required=True)
    sub.add_parser("reindex-wiki")
    p = sub.add_parser("retag-legacy")
    p.add_argument("--workspace", required=True)
    p.add_argument("--legal-project")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.cmd == "init":
        init_db()
        print("initialized")
    elif args.cmd == "ingest-marketing":
        print_json({"ids": ingest_marketing_docs()})
    elif args.cmd == "search":
        res = asyncio.run(federation.search(args.query, scope=args.scope, k=args.k, principal=_LOCAL))
        print_json({"answer": res.answer, "passages": res.passages})
    elif args.cmd == "ground":
        print_json(asyncio.run(federation.ground(args.claim, scope=args.scope, k=args.k, principal=_LOCAL)))
    elif args.cmd == "upsert-doc":
        md = Path(args.file).expanduser().read_text(encoding="utf-8", errors="ignore")
        print_json({"id": upsert_doc(args.path, md, args.scope)})
    elif args.cmd == "get-doc":
        print_json(get_doc(args.id))
    elif args.cmd == "list-docs":
        print_json(list_docs())
    elif args.cmd == "history":
        print_json(history(args.id))
    elif args.cmd == "index-files":
        print_json({"chunks": index_root(Path(args.root).expanduser(), owner=_LOCAL.subject, limit=args.limit)})
    elif args.cmd == "find-file":
        print_json(find_file(args.query, args.k, principal=_LOCAL))
    elif args.cmd == "deep-read":
        print_json(deep_read(args.path, args.query, args.k, principal=_LOCAL))
    elif args.cmd == "record-metric":
        print_json({"id": record_metric(args.name, args.value, json.loads(args.tags))})
    elif args.cmd == "chart":
        svg = render_metric_chart(args.name)
        if args.out:
            Path(args.out).write_text(svg, encoding="utf-8")
            print(args.out)
        else:
            print(svg)
    elif args.cmd == "demo":
        asyncio.run(demo())
    elif args.cmd == "refresh-due":
        print_json({"runs": run_due_refresh_jobs(actor=_LOCAL.subject, at=args.at)})
    elif args.cmd == "evaluate-memory":
        result = evaluate_memory()
        print_json(result)
        if not result["passed"]:
            raise SystemExit(1)
    elif args.cmd == "worker":
        result = run_worker(once=args.once)
        if result is not None:
            print_json(result)
    elif args.cmd == "operations-status":
        print_json(operations_snapshot())
    elif args.cmd == "worker-health":
        result = worker_readiness()
        print_json(result)
        if not result["ready"]:
            raise SystemExit(1)
    elif args.cmd == "backup":
        print_json(create_backup(Path(args.out)))
    elif args.cmd == "restore-verify":
        result = restore_and_verify(Path(args.archive), Path(args.target))
        print_json(result)
        if not result["verification"]["passed"]:
            raise SystemExit(1)
    elif args.cmd == "reindex-wiki":
        print_json(reindex_wiki())
    elif args.cmd == "retag-legacy":
        try:
            print_json(retag_legacy(
                args.workspace,
                args.legal_project,
                apply=args.apply,
            ))
        except (ValueError, RuntimeError) as exc:
            raise SystemExit(f"retag-legacy: {exc}") from exc


if __name__ == "__main__":
    main()
