from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from action_audio_contract import (  # noqa: E402
    PRODUCT_ACTION_AUDIO_HELPER_ACTIONS_SCHEMA,
    PRODUCT_ACTION_AUDIO_HELPER_BOUNDARY,
    PRODUCT_ACTION_AUDIO_HELPER_REQUEST_SCHEMA,
    PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_CLAIM_TIER,
    PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_RUNTIME_KIND,
    PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_SAMPLE_RATE_HZ,
    PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS,
    PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_CLAIM_TIER,
    PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_RUNTIME_KIND,
    response_contract_error,
)


def _fetch_json(url: str, timeout_s: float) -> tuple[int, dict[str, Any]]:
    try:
        with urlopen(url, timeout=timeout_s) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return int(error.code), json.loads(error.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urlopen(request, timeout=timeout_s) as response:
        loaded = json.loads(response.read().decode("utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("render response must be a JSON object")
    return loaded


def _wait_ready(url: str, deadline_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(1.0, deadline_s)
    last_error = ""
    while time.monotonic() < deadline:
        try:
            status, payload = _fetch_json(url, timeout_s=2.0)
            if status == 200 and payload.get("ready") is True:
                return payload
            last_error = json.dumps(payload, ensure_ascii=False)
        except (OSError, URLError, ValueError, json.JSONDecodeError) as error:
            last_error = str(error)
        time.sleep(0.25)
    raise TimeoutError(f"{url} did not become ready: {last_error}")


def _build_payload(duration_ms: int, output_file: Path | None) -> dict[str, Any]:
    frame_step_ms = 20
    frames: list[dict[str, Any]] = []
    for time_ms in range(0, duration_ms + frame_step_ms, frame_step_ms):
        ratio = min(1.0, time_ms / max(1, duration_ms))
        wobble = math.sin(ratio * math.tau)
        frames.append(
            {
                "time_ms": time_ms,
                "track_values": {
                    "f0": 130.0 + 85.0 * ratio,
                    "pressure": 8200.0 + 2600.0 * max(0.0, wobble),
                    "x_bottom": 0.02 + 0.025 * ratio,
                    "x_top": 0.018 + 0.018 * (1.0 - ratio),
                    "chink_area": 0.025 + 0.035 * abs(wobble),
                    "lag": 1.08 + 0.22 * ratio,
                    "rel_amp": 0.82 + 0.16 * ratio,
                    "pulse_shape": -0.25 + 0.5 * ratio,
                    "flutter": 10.0 + 14.0 * abs(wobble),
                },
            }
        )
    return {
        "schema_version": PRODUCT_ACTION_AUDIO_HELPER_REQUEST_SCHEMA,
        "renderer_contract": "action_to_audio",
        "commercial_boundary": PRODUCT_ACTION_AUDIO_HELPER_BOUNDARY,
        "product_evidence_required": True,
        "fallback_allowed": False,
        "sample_rate_hz": PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_SAMPLE_RATE_HZ,
        "duration_ms": duration_ms,
        "render_context": {
            "project_id": "target_mode_smoke",
            "source_route": "external_helper_target_mode_smoke",
            "lab_forward_parameter_bundle": {
                "tract_params": [0.5, 0.4, 0.6, 0.58, 0.32, 0.5, 0.48, 0.5, 0.5, 0.46, 0.5, 0.52, 0.5, 0.62],
            },
        },
        "actions": {
            "schema_version": PRODUCT_ACTION_AUDIO_HELPER_ACTIONS_SCHEMA,
            "source": "target_mode_smoke",
            "duration_ms": duration_ms,
            "coverage_end_ms": duration_ms,
            "tracks": list(PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS),
            "gestures": [],
            "frames": frames,
        },
        "output_file": str(output_file) if output_file else "",
    }


def _validate_render(payload: dict[str, Any], *, duration_ms: int, max_latency_ms: float, expect_stems: bool) -> dict[str, Any]:
    contract_error = response_contract_error(payload)
    if contract_error:
        raise ValueError(contract_error)
    if payload.get("renderer_runtime_kind") != PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_RUNTIME_KIND:
        raise ValueError("renderer_runtime_kind mismatch")
    if payload.get("claim_tier") != PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_CLAIM_TIER:
        raise ValueError("claim_tier mismatch")
    target_identity = payload.get("target_identity")
    if not isinstance(target_identity, dict):
        raise ValueError("missing target_identity")
    if target_identity.get("runtime_kind") != PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_RUNTIME_KIND:
        raise ValueError("target runtime_kind mismatch")
    if target_identity.get("claim_tier") != PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_CLAIM_TIER:
        raise ValueError("target claim_tier mismatch")
    if "nine_track_dynamic_glottis" not in [str(item) for item in target_identity.get("engine_capabilities", [])]:
        raise ValueError("target does not advertise nine_track_dynamic_glottis")
    expected_samples = round(PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_SAMPLE_RATE_HZ * duration_ms / 1000)
    audio = payload.get("audio")
    if not isinstance(audio, list) or len(audio) != expected_samples:
        raise ValueError(f"audio sample count mismatch: {len(audio) if isinstance(audio, list) else 'not_list'}")
    values = [float(value) for value in audio]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("audio contains non-finite samples")
    peak_abs = max((abs(value) for value in values), default=0.0)
    rms = math.sqrt(sum(value * value for value in values) / max(1, len(values)))
    if peak_abs <= 1.0e-5 or rms <= 1.0e-8:
        raise ValueError("audio is silent")
    elapsed_ms = float(payload.get("render_elapsed_ms", 0.0) or 0.0)
    if elapsed_ms > max_latency_ms:
        raise ValueError(f"render exceeded latency budget: {elapsed_ms:.3f}ms > {max_latency_ms:.3f}ms")
    stems = payload.get("stems") if isinstance(payload.get("stems"), dict) else {}
    if expect_stems:
        for name in ("glottal_source", "tract_filtered"):
            stem = stems.get(name)
            if not isinstance(stem, dict) or stem.get("ready") is not True:
                raise ValueError(f"stem not ready: {name}")
            path = Path(str(stem.get("file_path", "") or ""))
            if not path.exists() or path.stat().st_size <= 44:
                raise ValueError(f"stem file is missing or empty: {path}")
    return {
        "duration_ms": duration_ms,
        "sample_rate_hz": PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_SAMPLE_RATE_HZ,
        "num_samples": expected_samples,
        "peak_abs": peak_abs,
        "rms": rms,
        "render_elapsed_ms": elapsed_ms,
        "target_identity": target_identity,
    }


def _terminate(process: subprocess.Popen[str] | None) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the external helper in product target mode and validate audio output.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--helper-port", type=int, default=8192)
    parser.add_argument("--target-port", type=int, default=8193)
    parser.add_argument("--duration-ms", type=int, default=2000)
    parser.add_argument("--ready-deadline-s", type=float, default=20.0)
    parser.add_argument("--timeout-s", type=float, default=30.0)
    parser.add_argument("--max-latency-ms", type=float, default=800.0)
    parser.add_argument("--output", default="data/tmp/target_mode_smoke.wav")
    parser.add_argument("--keep-running", action="store_true")
    args = parser.parse_args()

    target_url = f"http://{args.host}:{args.target_port}"
    helper_url = f"http://{args.host}:{args.helper_port}"
    output_file = (REPO_ROOT / args.output).resolve()
    target_process: subprocess.Popen[str] | None = None
    helper_process: subprocess.Popen[str] | None = None
    env = os.environ.copy()
    env["GUANXIANG_ACTION_AUDIO_TARGET_URL"] = target_url
    env.pop("GUANXIANG_ACTION_AUDIO_TARGET_COMMAND", None)
    env.pop("GUANXIANG_ACTION_AUDIO_ALLOW_TEST_TARGET", None)

    try:
        target_process = subprocess.Popen(
            [sys.executable, str(REPO_ROOT / "product_action_audio_target.py"), "--host", args.host, "--port", str(args.target_port)],
            cwd=str(REPO_ROOT),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _wait_ready(f"{target_url}/ready", args.ready_deadline_s)
        helper_process = subprocess.Popen(
            [sys.executable, str(REPO_ROOT / "product_action_audio_helper.py"), "--host", args.host, "--port", str(args.helper_port)],
            cwd=str(REPO_ROOT),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        ready = _wait_ready(f"{helper_url}/ready", args.ready_deadline_s)
        render_payload = _build_payload(args.duration_ms, output_file)
        render = _post_json(f"{helper_url}/render", render_payload, args.timeout_s)
        observed = _validate_render(
            render,
            duration_ms=args.duration_ms,
            max_latency_ms=args.max_latency_ms,
            expect_stems=True,
        )
        print(
            json.dumps(
                {
                    "status": "ok",
                    "helper_url": f"{helper_url}/render",
                    "target_url": target_url,
                    "ready": {
                        "gate_status": ready.get("gate_status"),
                        "target_probe_ok": ready.get("target_probe_ok"),
                    },
                    "render": observed,
                    "output_file": str(output_file),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if args.keep_running:
            while True:
                time.sleep(1.0)
    finally:
        if not args.keep_running:
            _terminate(helper_process)
            _terminate(target_process)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
