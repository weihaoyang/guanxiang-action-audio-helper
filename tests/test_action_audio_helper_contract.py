from __future__ import annotations

from pathlib import Path

from action_audio_contract import (
    PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_SAMPLE_RATE_HZ,
    PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS,
    product_action_audio_target_identity_error,
    response_contract_error,
)
from product_action_audio_helper import _command_probe_payload
from product_action_audio_target import _render


def test_target_renders_product_contract_audio() -> None:
    payload = _command_probe_payload()
    payload["duration_ms"] = 200
    payload["actions"]["duration_ms"] = 200
    payload["actions"]["coverage_end_ms"] = 200
    for gesture in payload["actions"]["gestures"]:
        gesture["duration_ms"] = 200

    response = _render(payload)

    assert response_contract_error(response) == ""
    assert response["sample_rate_hz"] == PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_SAMPLE_RATE_HZ
    assert response["num_samples"] == 9600
    assert len(response["audio"]) == 9600
    assert response["audio_quality"]["finite"] is True
    assert response["audio_quality"]["non_silent"] is True
    assert response["target_identity"]["runtime_kind"] == "product_action_audio_target"
    assert "nine_track_dynamic_glottis" in response["target_identity"]["engine_capabilities"]
    for track in PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS:
        assert f"{track}_mean" in response["action_summary"]


def test_target_writes_glottal_and_filtered_stems(tmp_path: Path) -> None:
    payload = _command_probe_payload()
    payload["output_file"] = str(tmp_path / "main.wav")

    response = _render(payload)

    assert response_contract_error(response) == ""
    stems = response["stems"]
    for name in ("glottal_source", "tract_filtered"):
        stem = stems[name]
        assert stem["ready"] is True
        path = Path(stem["file_path"])
        assert path.exists()
        assert path.stat().st_size > 44


def test_target_identity_requires_nine_track_dynamic_glottis_capability() -> None:
    response = _render(_command_probe_payload())
    target_identity = dict(response["target_identity"])
    target_identity["engine_capabilities"] = ["wav_and_stem_export"]

    assert (
        product_action_audio_target_identity_error({"target_identity": target_identity})
        == "product action-audio target identity must include nine_track_dynamic_glottis"
    )
