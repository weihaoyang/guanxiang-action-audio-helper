# Guanxiang Action Audio Helper

HTTP/WebSocket helper and target contract implementation for Guanxiang Lab action-to-audio and geometry integration.

This repository is intentionally separate from the product repository. The product calls this process over HTTP through `GUANXIANG_PRODUCT_ACTION_AUDIO_HELPER_URL`; it does not import or package this code.

## Runtime Shape

- `product_action_audio_helper.py`: product-facing HTTP adapter with `/health`, `/ready`, `/self-test`, and `/render`.
- `product_action_audio_target.py`: replaceable product target implementing the nine-track action-to-audio contract.
- `product_geometry_helper.py`: product-facing WebSocket helper for 2D geometry, 3D indexed surface mesh, and drag solve.
- `action_audio_contract.py`: shared wire contract constants and identity validation.
- `scripts/run_target_mode_smoke.py`: starts target + helper, renders a 2s dynamic nine-track request, validates WAV/stems/identity/latency, then exits.
- `scripts/start_target_mode.ps1`: starts a persistent local target + helper pair for product UI debugging.
- `scripts/run_geometry_helper_smoke.py`: starts the geometry helper and can validate it with the product repository checker.
- `scripts/start_geometry_helper.ps1`: starts the persistent geometry helper for product UI debugging.

The normal product-debug path is target mode. It keeps the target process warm and is the only local mode that should be used for 800ms Lab latency evidence:

```powershell
python scripts/run_target_mode_smoke.py --max-latency-ms 800
```

For interactive product UI work:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_target_mode.ps1 -Python python
$env:GUANXIANG_PRODUCT_ACTION_AUDIO_HELPER_URL = "http://127.0.0.1:8192/render"
```

Start geometry helper for Lab 2D/3D/drag editing:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_geometry_helper.ps1 -Python python
$env:GUANXIANG_PRODUCT_GEOMETRY_HELPER_WS_URL = "ws://127.0.0.1:8194"
```

To validate the geometry helper against the product repository checker:

```powershell
python scripts/run_geometry_helper_smoke.py --product-repo F:\vocal-app-v3-authoritative
```

Manual target mode is equivalent:

```powershell
$env:GUANXIANG_ACTION_AUDIO_TARGET_URL = "http://127.0.0.1:8193"
python product_action_audio_target.py --host 127.0.0.1 --port 8193
python product_action_audio_helper.py --host 127.0.0.1 --port 8192
$env:GUANXIANG_PRODUCT_ACTION_AUDIO_HELPER_URL = "http://127.0.0.1:8192/render"
```

Command mode is intentionally debug-only. In this mode the helper sends one render request JSON object through stdin and expects one response JSON object on stdout. The helper performs a real render probe before reporting ready, but per-render process startup is not the low-latency product path:

```powershell
$env:GUANXIANG_ACTION_AUDIO_TARGET_COMMAND = "python F:\guanxiang-action-audio-helper\product_action_audio_target.py --stdio"
python product_action_audio_helper.py --host 127.0.0.1 --port 8192
$env:GUANXIANG_PRODUCT_ACTION_AUDIO_HELPER_URL = "http://127.0.0.1:8192/render"
```

## Contract

The target must expose:

- `runtime_kind=product_action_audio_target`
- `claim_tier=product_runtime_contract`
- `product_evidence_allowed=true`
- `fallback_allowed=false`
- `engine_capabilities` containing `nine_track_dynamic_glottis`

Required action tracks:

```text
f0, pressure, x_bottom, x_top, chink_area, lag, rel_amp, pulse_shape, flutter
```

`/self-test` is the local acceptance endpoint. It renders controlled variants for every required track and fails if any track is missing from the target identity path or does not change the generated audio. Product code should keep using `/render`; QA and integration scripts should use `/self-test` before trusting a helper/target pair.

The geometry helper WebSocket accepts:

- `health`
- `geometry_2d`
- `geometry_3d`
- `drag_solve`
- `multi_drag_solve`

It must return `helper_name=tract-reference-renderer`, `runtime=product_tract_geometry_helper_ipc`, `fallback_allowed=false`, `clinical_truth_claim_allowed=false`, product-marked 2D SVG, at least one indexed 3D surface mesh, and a non-blocked 19D `parameter_vector` for supported drag controls.

## Boundary

This repository does not contain VTL/VocalTractLab assets, GPL runtime code, FEM solvers, or training pipelines. Future high-fidelity targets can replace `product_action_audio_target.py` behind the same HTTP contract.

## Local Checks

```powershell
python -m pytest
python scripts/run_target_mode_smoke.py --max-latency-ms 800
python scripts/run_geometry_helper_smoke.py --product-repo F:\vocal-app-v3-authoritative
```

The smoke must report `status=ok`, self-test coverage for all 9 tracks, `runtime_kind=product_action_audio_target`, `claim_tier=product_runtime_contract`, `nine_track_dynamic_glottis`, 48000 Hz, 2 seconds, 96000 samples, non-silent audio, and ready glottal/tract stems.
