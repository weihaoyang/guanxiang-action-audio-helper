from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from websockets.sync.client import connect

REPO_ROOT = Path(__file__).resolve().parents[1]


def _wait_ws(url: str, deadline_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + deadline_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with connect(url, open_timeout=1.0, close_timeout=0.2) as websocket:
                websocket.send(json.dumps({"type": "health", "request_id": "geometry-smoke-health"}))
                payload = json.loads(websocket.recv(timeout=2.0))
                if isinstance(payload, dict) and payload.get("status") == "ok":
                    return payload
        except Exception as error:
            last_error = str(error)
            time.sleep(0.2)
    raise TimeoutError(f"{url} did not become ready: {last_error}")


def _terminate(process: subprocess.Popen[str] | None) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the external product geometry helper and validate its WebSocket contract.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8194)
    parser.add_argument("--ready-deadline-s", type=float, default=10.0)
    parser.add_argument("--product-repo", default="")
    parser.add_argument("--keep-running", action="store_true")
    args = parser.parse_args()
    ws_url = f"ws://{args.host}:{args.port}"
    process: subprocess.Popen[str] | None = None
    with tempfile.TemporaryDirectory() as tmp_dir:
        target_file = Path(tmp_dir) / "geometry-helper-target.txt"
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(REPO_ROOT / "product_geometry_helper.py"),
                    "--host",
                    args.host,
                    "--port",
                    str(args.port),
                    "--target-file",
                    str(target_file),
                ],
                cwd=str(REPO_ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            health = _wait_ws(ws_url, args.ready_deadline_s)
            product_repo = Path(args.product_repo).resolve() if args.product_repo else None
            checker_payload: dict[str, Any] | None = None
            if product_repo:
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(product_repo / "scripts" / "check_product_geometry_helper.py"),
                        "--target-file",
                        str(target_file),
                    ],
                    cwd=str(product_repo),
                    text=True,
                    capture_output=True,
                    timeout=20.0,
                    check=False,
                )
                if completed.returncode != 0:
                    raise RuntimeError((completed.stderr or completed.stdout or "product checker failed").strip())
                checker_payload = json.loads(completed.stdout)
            result = {
                "status": "ok",
                "ws_url": ws_url,
                "health": {
                    "helper_name": health.get("helper_name"),
                    "runtime": health.get("runtime"),
                    "protocol_version": health.get("protocol_version"),
                },
                "product_checker": checker_payload,
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if args.keep_running:
                while True:
                    time.sleep(1.0)
        finally:
            if not args.keep_running:
                _terminate(process)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
