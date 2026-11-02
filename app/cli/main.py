import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

from contracts.validation import KINDS, validate_contract


def main() -> None:
    parser = argparse.ArgumentParser(description="AeroIntel local MLOps")
    parser.add_argument("--home", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate a JSON contract")
    validate.add_argument("kind", choices=sorted(KINDS))
    validate.add_argument("document", type=Path)
    demo = commands.add_parser("demo")
    demo.add_argument(
        "--approve-demo",
        action="store_true",
        help="Explicitly approve only the generated synthetic demo model",
    )
    demo.add_argument("--epochs", type=int, default=12)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("manifest", type=Path)
    ingest.add_argument("--source-root", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("dataset_key")
    run.add_argument("--epochs", type=int, default=12)
    for name in ("resume", "status", "approve", "deploy", "investigate"):
        command = commands.add_parser(name)
        command.add_argument("target")
        if name == "resume":
            command.add_argument("--attempts", type=int, default=3)
        if name == "approve":
            command.add_argument("--confirm-human-approval", action="store_true", required=True)

    rollback_cmd = commands.add_parser(
        "rollback", help="Roll back active deployment to prior model"
    )
    rollback_cmd.add_argument("--request-id", default=None)

    monitor_cmd = commands.add_parser("monitor", help="Run drift and performance monitoring")
    monitor_cmd.add_argument("model_key")
    monitor_cmd.add_argument("--start", default="2020-01-01T00:00:00Z")
    monitor_cmd.add_argument("--end", default=None)

    predict_cmd = commands.add_parser("predict", help="Run change detection on image pair")
    predict_cmd.add_argument("before", type=Path)
    predict_cmd.add_argument("after", type=Path)
    predict_cmd.add_argument("--region", default="default-region")

    args = parser.parse_args()
    try:
        if args.command == "validate":
            validate_contract(args.kind, json.loads(args.document.read_text(encoding="utf-8")))
            print("Contract valid")
            return
        if args.command == "demo":
            from app.cli.demo import run_demo

            home = args.home or Path(os.environ.get("AEROINTEL_HOME", "data/demo"))
            print(json.dumps(run_demo(home, args.approve_demo, args.epochs), indent=2))
            return
        from app.core.database import now
        from app.core.platform import Platform
        from app.core.security import authenticate
        from modules.propertypulse.training.engine import TrainingConfig

        platform = Platform(args.home)
        if args.command == "ingest":
            result = platform.ingestion.register(
                json.loads(args.manifest.read_text()), args.source_root
            )
        elif args.command == "run":
            run_id = platform.pipeline.submit(args.dataset_key, TrainingConfig(epochs=args.epochs))
            result = platform.pipeline.execute(run_id)
        elif args.command in {"resume", "status"}:
            result = (
                platform.pipeline.execute_with_retries(args.target, args.attempts)
                if args.command == "resume"
                else platform.pipeline.status(args.target)
            )
        elif args.command == "investigate":
            from modules.modelops.agent.investigate import investigate

            result = investigate(platform, args.target)
        elif args.command == "rollback":
            actor = authenticate(os.environ.get("AEROINTEL_TOKEN", ""))
            request_id = args.request_id or str(uuid4())
            result = platform.deployment.rollback(actor, request_id)
        elif args.command == "monitor":
            end_time = args.end or now()
            result = platform.monitoring.run(args.model_key, args.start, end_time)
        elif args.command == "predict":
            req_id = str(uuid4())
            raw_res = platform.production.infer(
                args.before.read_bytes(),
                args.after.read_bytes(),
                args.region,
                req_id,
            )
            result = {
                "prediction_id": raw_res["prediction_id"],
                "model_key": raw_res["model_key"],
                "change_percentage": raw_res["change_percentage"],
                "uncertainty": raw_res["uncertainty"],
                "confidence": raw_res["confidence"],
                "review_required": raw_res["review_required"],
                "geojson": raw_res.get("geojson"),
            }
        else:
            actor = authenticate(os.environ.get("AEROINTEL_TOKEN", ""))
            result = (
                platform.registry.approve(args.target, actor, str(uuid4()))
                if args.command == "approve"
                else platform.deployment.deploy(args.target, actor, str(uuid4()))
            )
        print(json.dumps(result, indent=2))
    except Exception as exc:
        parser.exit(1, f"{type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
