# Guanxiang Action Audio Helper

HTTP helper and target contract implementation for Guanxiang Lab action-to-audio integration.

This repository is intentionally separate from the product repository. The product calls this process over HTTP through `GUANXIANG_PRODUCT_ACTION_AUDIO_HELPER_URL`; it does not import or package this code.

## Runtime Shape

- `product_action_audio_helper.py`: product-facing HTTP adapter with `/health`, `/ready`, and `/render`.
- `product_action_audio_target.py`: replaceable product target implementing the nine-track action-to-audio contract.
- `action_audio_contract.py`: shared wire contract constants and identity validation.

The helper refuses fallback behavior and forwards render requests to an explicit target:

```powershell
$env:GUANXIANG_ACTION_AUDIO_TARGET_URL = "http://127.0.0.1:8193"
python product_action_audio_target.py --host 127.0.0.1 --port 8193
python product_action_audio_helper.py --host 127.0.0.1 --port 8192
```

The target can also run as a command target. In this mode the helper sends one
render request JSON object through stdin and expects one response JSON object on
stdout. The helper performs a real render probe before reporting ready:

```powershell
$env:GUANXIANG_ACTION_AUDIO_TARGET_COMMAND = "python F:\guanxiang-action-audio-helper\product_action_audio_target.py --stdio"
python product_action_audio_helper.py --host 127.0.0.1 --port 8192
```

Then point the product service at:

```powershell
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

## Boundary

This repository does not contain VTL/VocalTractLab assets, GPL runtime code, FEM solvers, or training pipelines. Future high-fidelity targets can replace `product_action_audio_target.py` behind the same HTTP contract.
