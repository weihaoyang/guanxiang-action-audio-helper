from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from action_audio_contract import (
    PRODUCT_ACTION_AUDIO_HELPER_ACTIONS_SCHEMA,
    PRODUCT_ACTION_AUDIO_HELPER_BOUNDARY,
    PRODUCT_ACTION_AUDIO_HELPER_REQUEST_SCHEMA,
    PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_CLAIM_TIER,
    PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_RUNTIME_KIND,
    PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_SAMPLE_RATE_HZ,
    PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS,
    PRODUCT_ACTION_AUDIO_TARGET_PROTOCOL,
    PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_CLAIM_TIER,
    PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_RUNTIME_KIND,
    product_action_audio_target_identity_error,
    response_contract_error,
)

_PROBE_CACHE_LOCK = Lock()
_PROBE_CACHE: dict[str, Any] = {"expires_at": 0.0, "key": "", "payload": None}
_PROBE_CACHE_TTL_S = 2.0


def _send(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    raw = handler.rfile.read(int(handler.headers.get("Content-Length", "0") or "0")).decode("utf-8")
    payload = json.loads(raw) if raw.strip() else {}
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def _target_url() -> str:
    return str(os.getenv("GUANXIANG_ACTION_AUDIO_TARGET_URL", "") or "").strip().rstrip("/")


def _target_command() -> str:
    return str(os.getenv("GUANXIANG_ACTION_AUDIO_TARGET_COMMAND", "") or "").strip()


def _fetch(url: str, timeout_s: float = 1.5) -> tuple[int, dict[str, Any]]:
    try:
        with urlopen(url, timeout=timeout_s) as response:
            status = int(response.status)
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        status = int(error.code)
        payload = json.loads(error.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("target response must be a JSON object")
    return status, payload


def _post(url: str, payload: dict[str, Any], timeout_s: float = 20.0) -> dict[str, Any]:
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    with urlopen(request, timeout=timeout_s) as response:
        loaded = json.loads(response.read().decode("utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("target render response must be a JSON object")
    return loaded


def _run_command(payload: dict[str, Any]) -> dict[str, Any]:
    command = _target_command()
    if command == "":
        raise RuntimeError("no action-audio target URL or command is configured")
    completed = subprocess.run(command, input=json.dumps(payload, ensure_ascii=False), text=True, capture_output=True, timeout=20.0, shell=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "target command failed").strip())
    loaded = json.loads(completed.stdout)
    if not isinstance(loaded, dict):
        raise ValueError("target command response must be a JSON object")
    return loaded


def _command_probe_payload() -> dict[str, Any]:
    duration_ms = 100
    track_values = {
        "f0": 132.0,
        "pressure": 9200.0,
        "x_bottom": 0.03,
        "x_top": 0.02,
        "chink_area": 0.02,
        "lag": 1.12,
        "rel_amp": 1.0,
        "pulse_shape": 0.2,
        "flutter": 18.0,
    }
    return {
        "schema_version": PRODUCT_ACTION_AUDIO_HELPER_REQUEST_SCHEMA,
        "renderer_contract": "action_to_audio",
        "commercial_boundary": PRODUCT_ACTION_AUDIO_HELPER_BOUNDARY,
        "product_evidence_required": True,
        "fallback_allowed": False,
        "sample_rate_hz": PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_SAMPLE_RATE_HZ,
        "duration_ms": duration_ms,
        "render_context": {
            "project_id": "product_action_audio_helper_command_probe",
            "source_route": "product_action_audio_helper",
        },
        "actions": {
            "schema_version": PRODUCT_ACTION_AUDIO_HELPER_ACTIONS_SCHEMA,
            "source": "product_action_audio_helper_command_probe",
            "duration_ms": duration_ms,
            "coverage_end_ms": duration_ms,
            "tracks": list(PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS),
            "gestures": [
                {
                    "track": track,
                    "start_ms": 0,
                    "duration_ms": duration_ms,
                    "value": track_values[track],
                    "motion_source": "runtime_health_probe",
                    "truth_tier": "product_runtime_contract",
                    "confidence": 1.0,
                }
                for track in PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS
            ],
            "frames": [],
        },
        "output_file": "",
    }


def _probe_command_target() -> dict[str, Any]:
    try:
        response = _run_command(_command_probe_payload())
        contract_error = response_contract_error(response)
        if contract_error:
            return {
                "ready": False,
                "target_probe_ok": False,
                "target_probe_error": contract_error,
                "target_identity": {},
                "target_probe_metrics": {},
            }
        audio = response.get("audio")
        if not isinstance(audio, list) or len(audio) == 0:
            target_identity = response.get("target_identity")
            return {
                "ready": False,
                "target_probe_ok": False,
                "target_probe_error": "target command probe returned no audio",
                "target_identity": target_identity if isinstance(target_identity, dict) else {},
                "target_probe_metrics": {},
            }
        target_identity = response.get("target_identity")
        metrics = response.get("metrics")
        return {
            "ready": True,
            "target_probe_ok": True,
            "target_probe_error": "",
            "target_identity": target_identity if isinstance(target_identity, dict) else {},
            "target_probe_metrics": metrics if isinstance(metrics, dict) else {},
        }
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        return {
            "ready": False,
            "target_probe_ok": False,
            "target_probe_error": str(error),
            "target_identity": {},
            "target_probe_metrics": {},
        }


def _probe_url_target(target_url: str) -> dict[str, Any]:
    try:
        status, payload = _fetch(f"{target_url}/ready")
        identity = payload.get("target_identity") if isinstance(payload.get("target_identity"), dict) else {}
        identity_error = product_action_audio_target_identity_error({"target_identity": identity})
        if status != 200 or payload.get("ready") is not True or identity_error:
            return {
                "ready": False,
                "target_probe_ok": False,
                "target_probe_error": identity_error or str(payload.get("target_probe_error", "") or "target not ready"),
                "target_identity": identity,
                "target_probe_metrics": payload.get("target_probe_metrics") if isinstance(payload.get("target_probe_metrics"), dict) else {},
            }
        render_response = _post(f"{target_url}/render", _command_probe_payload(), timeout_s=5.0)
        contract_error = response_contract_error(render_response)
        if contract_error:
            return {
                "ready": False,
                "target_probe_ok": False,
                "target_probe_error": contract_error,
                "target_identity": identity,
                "target_probe_metrics": {},
            }
        audio = render_response.get("audio")
        if not isinstance(audio, list) or len(audio) == 0:
            return {
                "ready": False,
                "target_probe_ok": False,
                "target_probe_error": "target render probe returned no audio",
                "target_identity": identity,
                "target_probe_metrics": {},
            }
        metrics = render_response.get("metrics")
        return {
            "ready": True,
            "target_probe_ok": True,
            "target_probe_error": "",
            "target_identity": render_response.get("target_identity") if isinstance(render_response.get("target_identity"), dict) else identity,
            "target_probe_metrics": metrics if isinstance(metrics, dict) else {},
        }
    except (OSError, URLError, ValueError, json.JSONDecodeError) as error:
        return {"ready": False, "target_probe_ok": False, "target_probe_error": str(error), "target_identity": {}, "target_probe_metrics": {}}


def _probe() -> dict[str, Any]:
    target_url = _target_url()
    target_command = _target_command()
    probe_key = f"url:{target_url}" if target_url else f"command:{target_command}"
    now = time.monotonic()
    with _PROBE_CACHE_LOCK:
        if (
            _PROBE_CACHE.get("key") == probe_key
            and float(_PROBE_CACHE.get("expires_at", 0.0) or 0.0) > now
            and isinstance(_PROBE_CACHE.get("payload"), dict)
        ):
            return dict(_PROBE_CACHE["payload"])
    if target_url == "" and target_command == "":
        return {"ready": False, "target_probe_ok": False, "target_probe_error": "GUANXIANG_ACTION_AUDIO_TARGET_URL or GUANXIANG_ACTION_AUDIO_TARGET_COMMAND is required", "target_identity": {}, "target_probe_metrics": {}}
    if target_url:
        result = _probe_url_target(target_url)
    else:
        result = _probe_command_target()
    with _PROBE_CACHE_LOCK:
        _PROBE_CACHE.update({"key": probe_key, "expires_at": time.monotonic() + _PROBE_CACHE_TTL_S, "payload": dict(result)})
    return result


def _health() -> dict[str, Any]:
    probe = _probe()
    ready = bool(probe["ready"] and probe["target_probe_ok"])
    return {
        "ok": ready,
        "ready": ready,
        "gate_status": "ready" if ready else "target_not_ready",
        "renderer_id": "product_action_audio_helper",
        "renderer_runtime_kind": PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_RUNTIME_KIND,
        "renderer_contract": "action_to_audio",
        "commercial_boundary": PRODUCT_ACTION_AUDIO_HELPER_BOUNDARY,
        "accepted_request_schema": PRODUCT_ACTION_AUDIO_HELPER_REQUEST_SCHEMA,
        "accepted_actions_schema": PRODUCT_ACTION_AUDIO_HELPER_ACTIONS_SCHEMA,
        "supported_sample_rates_hz": [PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_SAMPLE_RATE_HZ],
        "supported_tracks": list(PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS),
        "claim_tier": PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_CLAIM_TIER,
        "not_for_product_evidence": False,
        "product_evidence_allowed": True,
        "target_protocol": PRODUCT_ACTION_AUDIO_TARGET_PROTOCOL,
        "target_required_runtime_kind": PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_RUNTIME_KIND,
        "target_required_claim_tier": PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_CLAIM_TIER,
        "test_target_allowed": False,
        "target_command_blocked": False,
        "target_command_block_reason": "",
        **probe,
    }


def _render(payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    health = _health()
    if health["ready"] is not True:
        raise RuntimeError(str(health.get("target_probe_error", "") or "target is not ready"))
    response = _post(f"{_target_url()}/render", payload) if _target_url() else _run_command(payload)
    error = response_contract_error(response)
    if error:
        raise ValueError(error)
    response["render_elapsed_ms"] = float(response.get("render_elapsed_ms", 0.0) or 0.0) + (time.perf_counter() - started) * 1000.0
    return response


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            _send(self, 200, _health())
            return
        if self.path.rstrip("/") in {"", "/ready"}:
            payload = _health()
            _send(self, 200 if payload["ready"] else 503, payload)
            return
        _send(self, 404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/render":
            _send(self, 404, {"ok": False, "error": "not_found"})
            return
        try:
            _send(self, 200, _render(_read(self)))
        except Exception as error:
            _send(self, 400, {"ok": False, "error": {"message": str(error)}})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8192)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(json.dumps({"status": "ready", "url": f"http://{args.host}:{args.port}/render"}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
