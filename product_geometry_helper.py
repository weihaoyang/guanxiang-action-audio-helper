from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

from websockets.sync.server import serve


HELPER_NAME = "tract-reference-renderer"
HELPER_RUNTIME = "product_tract_geometry_helper_ipc"
PROTOCOL_VERSION = "guanxiang.product_geometry_helper.v1"
GEOMETRY_3D_PROTOCOL_VERSION = "guanxiang.product_geometry_helper.geometry_3d.v1"
PARAMETER_SCHEMA = "owned_product_normalized_19d"
HELPER_VERSION = "2026.07.product-geometry-helper.1"
PARAM_COUNT = 19
CONTROL_TO_INDEX = {
    "jaw": 0,
    "jaw_handle": 0,
    "hyoid": 1,
    "hyoid_handle": 1,
    "lips": 2,
    "lip_handle": 2,
    "lip_aperture": 3,
    "lip_aperture_handle": 3,
    "tongue_center": 7,
    "tongue_center_handle": 7,
    "tongue_body": 8,
    "tongue_body_handle": 8,
    "tongue_tip": 9,
    "tongue_tip_handle": 9,
    "tongue_root": 10,
    "tongue_root_handle": 10,
    "velum": 12,
    "velum_handle": 12,
    "pharynx": 14,
    "pharynx_handle": 14,
    "larynx": 15,
    "larynx_handle": 15,
    "glottis": 16,
    "glottis_handle": 16,
    "left_vocal_fold": 17,
    "left_vocal_fold_handle": 17,
    "right_vocal_fold": 18,
    "right_vocal_fold_handle": 18,
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _params(raw: Any) -> list[float]:
    if not isinstance(raw, list):
        raw = []
    values: list[float] = []
    for item in raw[:PARAM_COUNT]:
        try:
            value = float(item)
        except (TypeError, ValueError):
            value = 0.5
        values.append(_clamp(value))
    while len(values) < PARAM_COUNT:
        values.append(0.5)
    return values


def _provenance(*, schema: str = PROTOCOL_VERSION, lineage: str = "external_product_geometry_helper_ipc") -> dict[str, Any]:
    return {
        "runtime": HELPER_RUNTIME,
        "lineage": lineage,
        "parameter_schema": PARAMETER_SCHEMA,
        "ipc_boundary": "localhost_websocket",
        "external_product_helper": True,
        "fallback_allowed": False,
        "helper_version": HELPER_VERSION,
        "protocol_version": schema,
        "clinical_truth_claim_allowed": False,
    }


def _medical_gate(schema: str) -> dict[str, Any]:
    return {
        "schema_version": schema,
        "status": "reference_only",
        "medical_anatomy_product_allowed": False,
        "clinical_truth_claim_allowed": False,
        "blockers": ["external_reference_visualization_only"],
    }


def _centerline(params: list[float], width_px: int, height_px: int) -> list[tuple[float, float]]:
    x0 = width_px * 0.16
    x1 = width_px * 0.84
    center_y = height_px * (0.50 + 0.10 * (params[0] - 0.5) - 0.08 * (params[15] - 0.5))
    curve = 0.10 * (params[7] - 0.5) - 0.06 * (params[10] - 0.5)
    points: list[tuple[float, float]] = []
    for index in range(28):
        t = index / 27.0
        x = x0 + (x1 - x0) * t
        arch = math.sin((t - 0.08) * math.pi)
        lip_lift = 0.08 * (params[2] - 0.5) * t * t
        tongue_bulge = curve * math.sin(t * math.pi * 1.4)
        y = center_y - height_px * (0.10 * arch + tongue_bulge - lip_lift)
        points.append((x, y))
    return points


def _radius_profile(params: list[float], width_px: int, height_px: int) -> list[float]:
    base = min(width_px, height_px) * 0.055
    pharynx = 1.0 + 0.75 * (params[14] - 0.5)
    tongue = 1.0 - 0.45 * (params[8] - 0.5)
    lips = 1.0 + 0.90 * (params[3] - 0.5)
    glottis = 0.75 + 0.55 * (params[16] - 0.5)
    radii: list[float] = []
    for index in range(28):
        t = index / 27.0
        blend = (
            pharynx * (1.0 - t) ** 2
            + tongue * math.sin(t * math.pi)
            + lips * t**2
            + 0.25 * glottis * (1.0 - t)
        )
        radii.append(max(18.0, min(74.0, base * blend)))
    return radii


def _svg_path(points: list[tuple[float, float]]) -> str:
    if not points:
        return ""
    head, *tail = points
    return "M " + f"{head[0]:.2f} {head[1]:.2f} " + " ".join(f"L {x:.2f} {y:.2f}" for x, y in tail) + " Z"


def _geometry_2d(payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    width_px = int(payload.get("width_px") or 640)
    height_px = int(payload.get("height_px") or 640)
    params = _params(payload.get("current_tract_params"))
    center = _centerline(params, width_px, height_px)
    radii = _radius_profile(params, width_px, height_px)
    upper = [(x, y - r) for (x, y), r in zip(center, radii)]
    lower = [(x, y + r) for (x, y), r in reversed(list(zip(center, radii)))]
    airway = upper + lower
    control_points = _control_points(params, width_px, height_px)
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width_px}' height='{height_px}' "
        f"viewBox='0 0 {width_px} {height_px}' data-helper='{HELPER_NAME}' "
        "data-role='product-2d-vocal-tract'>"
        "<rect x='0' y='0' width='100%' height='100%' fill='#0b1018'/>"
        f"<path class='product-airway-lumen' data-role='product-2d-airway' d='{_svg_path(airway)}' "
        "fill='#d8eef2' stroke='#6bb8c6' stroke-width='3'/>"
        f"<path class='product-centerline' data-role='product-2d-centerline' d='{_svg_path(center)}' "
        "fill='none' stroke='#2f7788' stroke-width='2'/>"
        + "".join(
            f"<circle data-control-id='{point['id']}' cx='{point['x_px']:.2f}' cy='{point['y_px']:.2f}' r='7' fill='#f7c948'/>"
            for point in control_points
        )
        + "</svg>"
    )
    return {
        "status": "ok",
        "request_id": payload.get("request_id", ""),
        "helper_name": HELPER_NAME,
        "helper_version": HELPER_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "runtime": HELPER_RUNTIME,
        "current_svg": svg,
        "current_control_points": control_points,
        "width_px": width_px,
        "height_px": height_px,
        "diagnostics": {
            "elapsed_ms": (time.perf_counter() - started) * 1000.0,
            "parameter_schema": PARAMETER_SCHEMA,
            "point_count": len(center),
        },
        "geometry_provenance": _provenance(),
        "clinical_truth_claim_allowed": False,
        "medical_anatomy_product_allowed": False,
        "medical_anatomy_gate": _medical_gate(PROTOCOL_VERSION),
    }


def _control_points(params: list[float], width_px: int, height_px: int) -> list[dict[str, Any]]:
    center = _centerline(params, width_px, height_px)
    anchors = {
        "jaw_handle": center[4],
        "tongue_root_handle": center[8],
        "tongue_body_handle": center[13],
        "tongue_center_handle": center[15],
        "tongue_tip_handle": center[20],
        "velum_handle": center[9],
        "pharynx_handle": center[6],
        "larynx_handle": center[2],
        "lip_handle": center[25],
        "lip_aperture_handle": center[27],
        "glottis_handle": center[1],
    }
    return [
        {
            "id": control_id,
            "label": control_id.replace("_handle", ""),
            "x_px": x,
            "y_px": y,
            "x_cm": x / 64.0,
            "y_cm": y / 64.0,
            "product_runtime_allowed": True,
        }
        for control_id, (x, y) in anchors.items()
    ]


def _geometry_3d(payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    params = _params(payload.get("current_tract_params"))
    width_px = 640
    height_px = 640
    center = _centerline(params, width_px, height_px)
    radii = _radius_profile(params, width_px, height_px)
    radial_segments = 14
    vertices: list[list[float]] = []
    for section, ((x, y), radius_px) in enumerate(zip(center, radii)):
        z_cm = section * 0.18
        radius_cm = radius_px / 96.0
        for side in range(radial_segments):
            angle = math.tau * side / radial_segments
            vertices.append([
                (x - width_px * 0.5) / 96.0,
                (y - height_px * 0.5) / 96.0 + math.cos(angle) * radius_cm,
                z_cm + math.sin(angle) * radius_cm,
            ])
    indices: list[int] = []
    for section in range(len(center) - 1):
        for side in range(radial_segments):
            a = section * radial_segments + side
            b = section * radial_segments + (side + 1) % radial_segments
            c = (section + 1) * radial_segments + side
            d = (section + 1) * radial_segments + (side + 1) % radial_segments
            indices.extend([a, c, b, b, c, d])
    return {
        "status": "ok",
        "request_id": payload.get("request_id", ""),
        "helper_name": HELPER_NAME,
        "helper_version": HELPER_VERSION,
        "protocol_version": GEOMETRY_3D_PROTOCOL_VERSION,
        "surfaces": [
            {
                "id": "product_vocal_tract_lumen",
                "label": "product vocal tract lumen",
                "vertices": vertices,
                "indices": indices,
                "material": {"color": "#78c7d8", "opacity": 0.72},
            }
        ],
        "paths": [],
        "geometry_provenance": _provenance(schema=GEOMETRY_3D_PROTOCOL_VERSION, lineage="external_product_geometry_helper_ipc_3d"),
        "product_geometry_helper_diagnostics": {
            "elapsed_ms": (time.perf_counter() - started) * 1000.0,
            "vertex_count": len(vertices),
            "triangle_count": len(indices) // 3,
        },
        "license_status": "external_product_geometry_helper_ipc",
        "truth_tier": "reference_visualization_not_patient_truth",
        "clinical_truth_claim_allowed": False,
        "medical_anatomy_product_allowed": False,
        "medical_anatomy_gate": _medical_gate(GEOMETRY_3D_PROTOCOL_VERSION),
    }


def _drag_solve(payload: dict[str, Any]) -> dict[str, Any]:
    params = _params(payload.get("current_tract_params"))
    control_id = str(payload.get("control_id") or "").strip()
    index = CONTROL_TO_INDEX.get(control_id)
    if index is None:
        return _blocked_drag(payload, f"unsupported_control:{control_id}")
    try:
        x_cm = float(payload.get("x_cm"))
        y_cm = float(payload.get("y_cm"))
    except (TypeError, ValueError):
        return _blocked_drag(payload, "invalid_drag_target")
    next_params = list(params)
    target = _clamp(0.5 + (x_cm - 1.0) * 0.10 + (2.0 - y_cm) * 0.08)
    if abs(target - next_params[index]) < 1.0e-6:
        target = _clamp(target + 0.08)
    next_params[index] = target
    if index in {7, 8, 9, 10}:
        for neighbor in {7, 8, 9, 10} - {index}:
            next_params[neighbor] = _clamp(next_params[neighbor] + (target - params[index]) * 0.18)
    solve = {
        "schema_version": "product-geometry-helper.drag_solve.v1",
        "status": "ok",
        "blocked": False,
        "control_id": control_id,
        "parameter_vector": next_params,
        "parameter_schema": PARAMETER_SCHEMA,
        "product_runtime_allowed": True,
        "geometry_provenance": _provenance(lineage="external_product_geometry_helper_ipc_drag_solve"),
        "truth_tier": "reference_visualization_not_patient_truth",
        "clinical_truth_claim_allowed": False,
        "medical_anatomy_product_allowed": False,
    }
    return {
        "status": "ok",
        "request_id": payload.get("request_id", ""),
        "helper_name": HELPER_NAME,
        "helper_version": HELPER_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "solve": solve,
    }


def _blocked_drag(payload: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "request_id": payload.get("request_id", ""),
        "helper_name": HELPER_NAME,
        "helper_version": HELPER_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "solve": {
            "schema_version": "product-geometry-helper.drag_solve.v1",
            "status": "blocked",
            "blocked": True,
            "blocked_reason": reason,
            "parameter_vector": _params(payload.get("current_tract_params")),
            "product_runtime_allowed": False,
            "geometry_provenance": _provenance(lineage="external_product_geometry_helper_ipc_drag_solve"),
            "truth_tier": "blocked_invalid_drag_request",
            "clinical_truth_claim_allowed": False,
            "medical_anatomy_product_allowed": False,
        },
    }


def _multi_drag_solve(payload: dict[str, Any]) -> dict[str, Any]:
    next_params = _params(payload.get("current_tract_params"))
    targets = payload.get("drag_targets")
    if not isinstance(targets, list) or not targets:
        return _blocked_drag(payload, "missing_drag_targets")
    last_response: dict[str, Any] | None = None
    for target in targets:
        if not isinstance(target, dict):
            continue
        target_payload = {**payload, **target, "current_tract_params": next_params}
        last_response = _drag_solve(target_payload)
        solve = last_response.get("solve") if isinstance(last_response, dict) else None
        if not isinstance(solve, dict) or solve.get("blocked") is True:
            return last_response if isinstance(last_response, dict) else _blocked_drag(payload, "invalid_drag_targets")
        next_params = _params(solve.get("parameter_vector"))
    response = last_response or _blocked_drag(payload, "missing_drag_targets")
    response["request_id"] = payload.get("request_id", "")
    return response


def _health(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "ok",
        "ready": True,
        "request_id": (payload or {}).get("request_id", ""),
        "helper_name": HELPER_NAME,
        "helper_version": HELPER_VERSION,
        "runtime": HELPER_RUNTIME,
        "protocol_version": PROTOCOL_VERSION,
        "supported_request_types": ["health", "geometry_2d", "geometry_3d", "drag_solve", "multi_drag_solve"],
        "parameter_schema": PARAMETER_SCHEMA,
        "fallback_allowed": False,
        "clinical_truth_claim_allowed": False,
        "medical_anatomy_product_allowed": False,
    }


def handle_request(payload: dict[str, Any]) -> dict[str, Any]:
    request_type = str(payload.get("type") or payload.get("request_type") or "")
    if request_type == "health":
        return _health(payload)
    if request_type == "geometry_2d":
        return _geometry_2d(payload)
    if request_type == "geometry_3d":
        return _geometry_3d(payload)
    if request_type == "drag_solve":
        return _drag_solve(payload)
    if request_type == "multi_drag_solve":
        return _multi_drag_solve(payload)
    return {"status": "error", "error": {"message": f"unsupported request type: {request_type}"}}


def _handler(websocket: Any) -> None:
    for raw_message in websocket:
        try:
            payload = json.loads(raw_message)
            if not isinstance(payload, dict):
                raise ValueError("request must be a JSON object")
            response = handle_request(payload)
        except Exception as error:
            response = {"status": "error", "error": {"message": str(error)}}
        websocket.send(json.dumps(response, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Guanxiang external product geometry WebSocket helper.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8194)
    parser.add_argument("--target-file", default="")
    args = parser.parse_args()
    ws_url = f"ws://{args.host}:{args.port}"
    if args.target_file:
        target_file = Path(args.target_file)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(ws_url, encoding="utf-8")
    print(json.dumps({"status": "ready", "ws_url": ws_url, "helper_name": HELPER_NAME}, ensure_ascii=False), flush=True)
    with serve(_handler, args.host, args.port) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
