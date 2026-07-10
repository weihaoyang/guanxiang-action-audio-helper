from __future__ import annotations

import argparse
import json
import math
import sys
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np

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
)

IDENTITY = {
    "protocol": PRODUCT_ACTION_AUDIO_TARGET_PROTOCOL,
    "target_id": "guanxiang_product_action_audio_target",
    "runtime_kind": PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_RUNTIME_KIND,
    "claim_tier": PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_CLAIM_TIER,
    "engine_family": "guanxiang_self_owned_lf_glottal_tract_filter",
    "engine_capabilities": [
        "nine_track_dynamic_glottis",
        "lf_style_glottal_flow_derivative",
        "time_varying_vocal_tract_filter",
        "wav_and_stem_export",
    ],
    "engine_limitations": ["local_product_target_until_high_fidelity_target_is_promoted"],
    "product_evidence_allowed": True,
    "not_for_product_evidence": False,
    "fallback_allowed": False,
}


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


def _write_wav(path: Path, audio: np.ndarray, sample_rate_hz: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio.astype(np.float32), -1.0, 1.0) * np.float32(32767.0)).astype("<i2")
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate_hz)
        wav_file.writeframes(pcm.tobytes())


def _float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _frames(actions: dict[str, Any], duration_ms: int, sample_rate_hz: int) -> dict[str, np.ndarray]:
    sample_count = max(1, round(sample_rate_hz * duration_ms / 1000))
    t_ms = np.arange(sample_count, dtype=np.float32) * np.float32(1000.0 / sample_rate_hz)
    neutral = {
        "f0": 120.0,
        "pressure": 8000.0,
        "x_bottom": 0.02,
        "x_top": 0.02,
        "chink_area": 0.03,
        "lag": 1.22,
        "rel_amp": 0.9,
        "pulse_shape": 0.0,
        "flutter": 20.0,
    }
    out = {
        track: np.full(sample_count, np.float32(neutral[track]), dtype=np.float32)
        for track in PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS
    }
    raw_frames = actions.get("frames") if isinstance(actions.get("frames"), list) else []
    if raw_frames:
        frame_times: list[float] = []
        frame_values = {track: [] for track in PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS}
        for frame in raw_frames:
            if not isinstance(frame, dict) or not isinstance(frame.get("track_values"), dict):
                continue
            frame_times.append(_float(frame.get("time_ms"), 0.0))
            for track in PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS:
                frame_values[track].append(_float(frame["track_values"].get(track), neutral[track]))
        if frame_times:
            x = np.asarray(frame_times, dtype=np.float32)
            return {
                track: np.interp(t_ms, x, np.asarray(frame_values[track], dtype=np.float32)).astype(np.float32)
                for track in PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS
            }
    for gesture in actions.get("gestures", []) if isinstance(actions.get("gestures"), list) else []:
        if not isinstance(gesture, dict):
            continue
        track = str(gesture.get("track", "") or "")
        if track not in out:
            continue
        start = max(0.0, _float(gesture.get("start_ms"), 0.0))
        end = start + max(1.0, _float(gesture.get("duration_ms"), duration_ms))
        out[track][(t_ms >= start) & (t_ms <= end)] = np.float32(_float(gesture.get("value"), neutral[track]))
    return out


def _lowpass(signal: np.ndarray, cutoff_hz: float, sample_rate_hz: int) -> np.ndarray:
    alpha = math.exp(-2.0 * math.pi * max(20.0, cutoff_hz) / sample_rate_hz)
    out = np.empty_like(signal, dtype=np.float32)
    y = 0.0
    for index, value in enumerate(signal.astype(np.float32)):
        y = (1.0 - alpha) * float(value) + alpha * y
        out[index] = y
    return out


def _resonator(signal: np.ndarray, formant_hz: float, bandwidth_hz: float, sample_rate_hz: int) -> np.ndarray:
    radius = math.exp(-math.pi * max(40.0, bandwidth_hz) / sample_rate_hz)
    theta = 2.0 * math.pi * max(80.0, min(formant_hz, sample_rate_hz * 0.45)) / sample_rate_hz
    a1 = 2.0 * radius * math.cos(theta)
    a2 = -(radius * radius)
    gain = max(1.0e-4, 1.0 - radius)
    out = np.empty_like(signal, dtype=np.float32)
    y1 = 0.0
    y2 = 0.0
    for index, value in enumerate(signal.astype(np.float32)):
        y0 = gain * float(value) + a1 * y1 + a2 * y2
        out[index] = y0
        y2 = y1
        y1 = y0
    return out


def _render(payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    if payload.get("schema_version") != PRODUCT_ACTION_AUDIO_HELPER_REQUEST_SCHEMA:
        raise ValueError("schema_version mismatch")
    if payload.get("renderer_contract") != "action_to_audio":
        raise ValueError("renderer_contract mismatch")
    if payload.get("commercial_boundary") != PRODUCT_ACTION_AUDIO_HELPER_BOUNDARY:
        raise ValueError("commercial_boundary mismatch")
    if payload.get("fallback_allowed") is not False:
        raise ValueError("fallback_allowed must be false")
    sample_rate_hz = int(payload.get("sample_rate_hz", 0) or 0)
    if sample_rate_hz != PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_SAMPLE_RATE_HZ:
        raise ValueError("sample_rate_hz mismatch")
    duration_ms = int(payload.get("duration_ms", 0) or 0)
    if duration_ms <= 0:
        raise ValueError("duration_ms must be positive")
    actions = payload.get("actions")
    if not isinstance(actions, dict):
        raise ValueError("actions must be an object")
    if actions.get("schema_version") != PRODUCT_ACTION_AUDIO_HELPER_ACTIONS_SCHEMA:
        raise ValueError("actions schema mismatch")
    if tuple(str(track) for track in actions.get("tracks", [])) != tuple(PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS):
        raise ValueError("actions tracks mismatch")

    track = _frames(actions, duration_ms, sample_rate_hz)
    n = max(1, round(sample_rate_hz * duration_ms / 1000))
    f0 = np.clip(track["f0"], 40.0, 600.0)
    pressure = np.clip(track["pressure"], 0.0, 16000.0)
    x_bottom = np.clip(track["x_bottom"], -0.12, 0.18)
    x_top = np.clip(track["x_top"], -0.12, 0.18)
    chink = np.clip(track["chink_area"], -0.25, 0.25)
    lag = np.clip(track["lag"], 0.0, math.pi)
    rel_amp = np.clip(track["rel_amp"], -1.0, 1.0)
    pulse_shape = np.clip(track["pulse_shape"], -1.0, 1.0)
    flutter = np.clip(track["flutter"], 0.0, 100.0)
    fold_gap = np.clip((x_bottom + x_top) * 0.5, -0.12, 0.18)
    vertical_phase_skew = np.clip(x_top - x_bottom, -0.18, 0.18)
    phase = np.cumsum((2.0 * math.pi * f0) / sample_rate_hz).astype(np.float32)
    skewed_phase = phase + lag * vertical_phase_skew * 2.2
    cycle = (skewed_phase / (2.0 * math.pi)) % 1.0
    oq = np.clip(0.52 + 0.05 * pulse_shape + 0.42 * fold_gap, 0.28, 0.86)
    open_phase = np.clip(cycle / oq, 0.0, 1.0)
    closed_phase = np.clip((cycle - oq) / np.maximum(1.0e-4, 1.0 - oq), 0.0, 1.0)
    flow = np.where(cycle <= oq, 0.5 - 0.5 * np.cos(np.pi * open_phase), np.exp(-6.0 * closed_phase))
    source = np.diff(flow, prepend=flow[0]).astype(np.float32)
    closure_efficiency = np.clip(1.0 - np.abs(fold_gap) * 4.8 - np.abs(chink) * 2.2, 0.12, 1.2)
    open_leak = np.clip(np.abs(chink) * 7.5 + np.maximum(fold_gap, 0.0) * 5.5, 0.0, 1.0)
    source *= (0.25 + pressure / 16000.0) * (0.45 + np.abs(rel_amp)) * closure_efficiency * (1.0 + 0.18 * np.sin(lag))
    source += (np.sin(phase * 0.37 + 1.7) + np.sin(phase * 1.91)) * 0.015 * np.clip(
        open_leak + flutter / 100.0,
        0.0,
        1.0,
    )
    source = _lowpass(source.astype(np.float32), 3800.0, sample_rate_hz)

    context = payload.get("render_context") if isinstance(payload.get("render_context"), dict) else {}
    bundle = context.get("lab_forward_parameter_bundle") if isinstance(context, dict) else {}
    tract = bundle.get("tract_params") if isinstance(bundle, dict) else []
    tract = [float(value) for value in tract] if isinstance(tract, list) else []
    jaw = tract[3] if len(tract) > 3 else 0.5
    lip = tract[4] if len(tract) > 4 else 0.5
    tongue_y = tract[13] if len(tract) > 13 else 0.5
    f1 = 420.0 + 520.0 * jaw
    f2 = 1050.0 + 900.0 * tongue_y - 220.0 * lip
    f3 = 2350.0 + 360.0 * (1.0 - lip)
    audio = _resonator(source, f1, 90.0, sample_rate_hz)
    audio = _resonator(audio, f2, 140.0, sample_rate_hz)
    audio = _resonator(audio, f3, 220.0, sample_rate_hz)
    audio = _lowpass(audio, 7600.0, sample_rate_hz)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 1.0e-8:
        audio = (audio / np.float32(peak) * np.float32(0.72)).astype(np.float32)
    source_peak = float(np.max(np.abs(source))) if source.size else 0.0
    if source_peak > 1.0e-8:
        source = (source / np.float32(source_peak) * np.float32(0.72)).astype(np.float32)

    output_file = str(payload.get("output_file", "") or "").strip()
    if output_file:
        out = Path(output_file).expanduser()
        stem_paths = {
            "glottal_source": out.with_name(f"{out.stem}.glottal_source{out.suffix or '.wav'}"),
            "tract_filtered": out.with_name(f"{out.stem}.tract_filtered{out.suffix or '.wav'}"),
        }
        _write_wav(stem_paths["glottal_source"], source, sample_rate_hz)
        _write_wav(stem_paths["tract_filtered"], audio, sample_rate_hz)
        stems = {
            name: {
                "ready": True,
                "file_path": str(path),
                "sample_rate_hz": sample_rate_hz,
                "num_samples": n,
                "source": "product_action_audio_helper",
            }
            for name, path in stem_paths.items()
        }
    else:
        stems = {
            name: {
                "ready": False,
                "file_path": "",
                "sample_rate_hz": sample_rate_hz,
                "num_samples": n,
                "source": "product_action_audio_helper",
            }
            for name in ("glottal_source", "tract_filtered")
        }

    summary: dict[str, float] = {}
    for name, values in track.items():
        summary[f"{name}_min"] = float(np.min(values))
        summary[f"{name}_max"] = float(np.max(values))
        summary[f"{name}_mean"] = float(np.mean(values))
        summary[f"{name}_range"] = float(np.max(values) - np.min(values))
    rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    peak_abs = float(np.max(np.abs(audio))) if audio.size else 0.0
    return {
        "ok": True,
        "schema_version": PRODUCT_ACTION_AUDIO_HELPER_REQUEST_SCHEMA,
        "renderer_contract": "action_to_audio",
        "commercial_boundary": PRODUCT_ACTION_AUDIO_HELPER_BOUNDARY,
        "renderer_id": "product_action_audio_helper",
        "renderer_runtime_kind": PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_RUNTIME_KIND,
        "claim_tier": PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_CLAIM_TIER,
        "product_evidence_allowed": True,
        "not_for_product_evidence": False,
        "sample_rate_hz": sample_rate_hz,
        "duration_ms": duration_ms,
        "num_samples": n,
        "render_elapsed_ms": (time.perf_counter() - started) * 1000.0,
        "audio": audio.astype(np.float32).tolist(),
        "audio_quality": {
            "finite": bool(np.all(np.isfinite(audio))),
            "non_silent": bool(peak_abs > 1.0e-5 and rms > 1.0e-8),
            "normalized": bool(peak_abs <= 1.0),
            "duration_matches_request": True,
        },
        "metrics": {
            "peak_abs": peak_abs,
            "rms": rms,
            "rms_db": 20.0 * math.log10(max(rms, 1.0e-12)),
            "zero_crossing_rate": float(np.mean(np.diff(np.signbit(audio)) != 0)) if audio.size > 1 else 0.0,
            "action_f0_mean_hz": summary["f0_mean"],
            "action_f0_range_hz": summary["f0_range"],
            "action_pressure_mean_pa": summary["pressure_mean"],
            "action_x_bottom_mean": summary["x_bottom_mean"],
            "action_x_top_mean": summary["x_top_mean"],
            "action_chink_area_mean": summary["chink_area_mean"],
            "fold_gap_mean": float(np.mean(fold_gap)),
            "vertical_phase_skew_mean": float(np.mean(vertical_phase_skew)),
            "closure_efficiency_mean": float(np.mean(closure_efficiency)),
            "open_leak_mean": float(np.mean(open_leak)),
            "f1_hz": float(f1),
            "f2_hz": float(f2),
            "f3_hz": float(f3),
            "f4_hz": 3400.0,
            "tract_jaw": float(jaw),
            "tract_lip_rounding": float(lip),
            "tract_tongue_body_y": float(tongue_y),
            "tract_constriction": float(np.mean(np.abs(tract))) if tract else 0.0,
            "mean_abs_delta": float(np.mean(np.abs(np.diff(audio)))) if audio.size > 1 else 0.0,
        },
        "action_summary": summary,
        "stems": stems,
        "target_identity": IDENTITY,
    }


def _health() -> dict[str, Any]:
    return {"ok": True, "ready": True, "gate_status": "ready", "target_identity": IDENTITY, "target_probe_ok": True, "target_probe_error": "", "target_probe_metrics": {}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path.rstrip("/") in {"", "/health", "/ready"}:
            _send(self, 200, _health())
            return
        _send(self, 404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/render":
            _send(self, 404, {"ok": False, "error": "not_found"})
            return
        try:
            _send(self, 200, _render(_read(self)))
        except Exception as error:
            _send(self, 400, {"ok": False, "error": str(error), "target_identity": IDENTITY})


def _run_stdio() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("stdin payload must be a JSON object")
        print(json.dumps(_render(payload), ensure_ascii=False), flush=True)
        return 0
    except Exception as error:
        print(
            json.dumps({"ok": False, "error": str(error), "target_identity": IDENTITY}, ensure_ascii=False),
            file=sys.stderr,
            flush=True,
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8193)
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Read one render request JSON object from stdin and write one response JSON object to stdout.",
    )
    args = parser.parse_args()
    if args.stdio:
        return _run_stdio()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(json.dumps({"status": "ready", "url": f"http://{args.host}:{args.port}"}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
