"""Human-operated cloud plan/apply/cleanup helper. Never called by the local demo."""

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["plan", "destroy-plan", "apply", "inventory", "delete-endpoint"]
    )
    parser.add_argument("--vars", type=Path)
    parser.add_argument("--plan", type=Path, default=ROOT / ".cache/aws.tfplan")
    parser.add_argument("--confirm-paid-aws", action="store_true")
    parser.add_argument("--endpoint")
    args = parser.parse_args()
    if args.action in {"apply", "delete-endpoint"} and not args.confirm_paid_aws:
        parser.error("Mutations require --confirm-paid-aws after explicit cost and resource review")
    if args.action in {"inventory", "delete-endpoint"}:
        import boto3

        client = boto3.client("sagemaker")
        if args.action == "inventory":
            for page in client.get_paginator("list_endpoints").paginate(NameContains="aerointel"):
                for endpoint in page["Endpoints"]:
                    print(endpoint["EndpointName"], endpoint["EndpointStatus"])
            return
        if not args.endpoint or not args.endpoint.startswith("aerointel-"):
            parser.error("An exact endpoint beginning aerointel- is required")
        client.delete_endpoint(EndpointName=args.endpoint)
        client.get_waiter("endpoint_deleted").wait(EndpointName=args.endpoint)
        print("Endpoint removed. Review/delete unused endpoint configs and models separately.")
        return
    terraform = shutil.which("terraform") or str(ROOT / ".cache/terraform/terraform.exe")
    args.plan.parent.mkdir(parents=True, exist_ok=True)
    command = [terraform, f"-chdir={ROOT / 'infrastructure/terraform'}"]
    if args.action in {"plan", "destroy-plan"}:
        if not args.vars:
            parser.error("A reviewed --vars file is required")
        command += [
            "plan",
            "-input=false",
            f"-var-file={args.vars.resolve()}",
            f"-out={args.plan.resolve()}",
        ]
        if args.action == "destroy-plan":
            command.append("-destroy")
    else:
        if not args.plan.is_file():
            parser.error("Review and save a plan before applying")
        command += ["apply", "-input=false", str(args.plan.resolve())]
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
