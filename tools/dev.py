"""Cross-platform setup/demo/check driver. Every failing command stops the run."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*command: str) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "demo", "check", "serve", "infra"])
    parser.add_argument("--approve-demo", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("UV_CACHE_DIR", str(ROOT / ".cache/uv"))
    os.environ.setdefault("UV_PYTHON_INSTALL_DIR", str(ROOT / ".cache/python"))
    os.environ.setdefault("DO_NOT_TRACK", "1")
    os.environ.setdefault("MLFLOW_DISABLE_TELEMETRY", "true")
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    if args.action == "setup":
        run(
            "uv",
            "sync",
            "--locked",
            "--extra",
            "dev",
            "--extra",
            "ml",
            "--extra",
            "tracking",
            "--extra",
            "monitoring",
            "--extra",
            "mcp",
            "--extra",
            "aws",
        )
        return
    uv = ("uv", "run", "--frozen", "--no-sync")
    if args.action == "demo":
        command = [*uv, "aerointel", "--home", "data/demo", "demo"]
        if args.approve_demo:
            command.append("--approve-demo")
        run(*command)
    elif args.action == "serve":
        run(*uv, "uvicorn", "app.api.main:app", "--host", "127.0.0.1", "--port", "8000")
    elif args.action == "check":
        run(*uv, "ruff", "check", ".")
        run(*uv, "ruff", "format", "--check", ".")
        run(*uv, "mypy")
        run(*uv, "pytest")
        run("uv", "build")
        run(*uv, "python", "tools/verify_package.py")
        run(*uv, "python", "-c", "from app.core.config import load_settings; load_settings()")
        if shutil.which("docker"):
            run("docker", "compose", "--profile", "tracking", "config", "--quiet")
        if shutil.which("terraform") or (ROOT / ".cache/terraform/terraform.exe").exists():
            run(sys.executable, "tools/dev.py", "infra")
    elif args.action == "infra":
        terraform = shutil.which("terraform") or str(ROOT / ".cache/terraform/terraform.exe")
        run(terraform, "-chdir=infrastructure/terraform", "fmt", "-check", "-recursive")
        run(terraform, "-chdir=infrastructure/terraform", "init", "-backend=false", "-input=false")
        run(terraform, "-chdir=infrastructure/terraform", "validate")


if __name__ == "__main__":
    main()
