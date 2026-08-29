#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

from production_control import ProductionError, ProductionService


def read_json_file(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_parser():
    parser = argparse.ArgumentParser(description="cinematic-director 本地制片控制内核")
    subparsers = parser.add_subparsers(dest="command", required=True)

    command = subparsers.add_parser("init", help="初始化制片项目")
    command.add_argument("project")
    command.add_argument("--title", required=True)
    command.add_argument("--project-id", required=True)

    command = subparsers.add_parser("ingest", help="登记项目输入")
    command.add_argument("project")
    command.add_argument("source")
    command.add_argument("--input-id", required=True)
    command.add_argument("--role", required=True)
    command.add_argument("--metadata", default="{}")

    command = subparsers.add_parser("plan", help="导入制片计划")
    command.add_argument("project")
    command.add_argument("plan_file")

    command = subparsers.add_parser("next", help="领取下一个可执行任务")
    command.add_argument("project")
    command.add_argument("--executor", default="codex")
    command.add_argument("--lease-seconds", type=int, default=900)

    command = subparsers.add_parser("submit", help="回填模型候选结果")
    command.add_argument("project")
    command.add_argument("--run-id", type=int, required=True)
    command.add_argument("--artifact", required=True)
    command.add_argument("--metadata", required=True)

    command = subparsers.add_parser("review", help="评审候选")
    command.add_argument("project")
    command.add_argument("--candidate-id", type=int, required=True)
    command.add_argument("--decision", choices=["approve", "reject"], required=True)
    command.add_argument("--reviewer", required=True)
    command.add_argument("--notes", default="")

    command = subparsers.add_parser("approve-gate", help="批准阶段审批门")
    command.add_argument("project")
    command.add_argument("gate")
    command.add_argument("--reviewer", required=True)
    command.add_argument("--notes", default="")
    command.add_argument(
        "--human-confirmed",
        action="store_true",
        help="仅在用户已明确批准此审批门时记录",
    )

    command = subparsers.add_parser("retry", help="重做任务")
    command.add_argument("project")
    command.add_argument("task_id")
    command.add_argument("--reason", required=True)

    command = subparsers.add_parser("status", help="查看项目状态")
    command.add_argument("project")

    command = subparsers.add_parser("recover", help="恢复过期运行")
    command.add_argument("project")
    command.add_argument("--now")

    command = subparsers.add_parser("export", help="导出最终交付清单")
    command.add_argument("project")
    return parser


def dispatch(args):
    if args.command == "init":
        service = ProductionService.create(args.project, args.title, args.project_id)
        return service.status()

    service = ProductionService(args.project)
    if args.command == "ingest":
        return service.ingest_input(
            args.input_id, args.source, args.role, json.loads(args.metadata)
        )
    if args.command == "plan":
        return service.import_plan(read_json_file(args.plan_file))
    if args.command == "next":
        return service.next_task(args.executor, args.lease_seconds)
    if args.command == "submit":
        return service.submit_candidate(args.run_id, args.artifact, json.loads(args.metadata))
    if args.command == "review":
        return service.review_candidate(
            args.candidate_id, args.decision, args.reviewer, args.notes
        )
    if args.command == "approve-gate":
        return service.approve_gate(
            args.gate, args.reviewer, args.notes, human_confirmed=args.human_confirmed
        )
    if args.command == "retry":
        return service.retry_task(args.task_id, args.reason)
    if args.command == "status":
        return service.status()
    if args.command == "recover":
        return service.recover(args.now)
    if args.command == "export":
        return service.export_delivery()
    raise RuntimeError(f"未处理命令：{args.command}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = dispatch(args)
    except ProductionError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": str(exc)}}, ensure_ascii=False))
        return 1
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": {"code": "invalid_input", "message": str(exc)}},
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
