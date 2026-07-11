from __future__ import annotations

from pathlib import Path

from action_audio_contract import (
    PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_SAMPLE_RATE_HZ,
    PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS,
    product_action_audio_target_identity_error,
    response_contract_error,
)
import product_action_audio_helper
from product_action_audio_helper import _command_probe_payload
from product_action_audio_target import _render, _self_test as _target_self_test


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


def test_target_identity_rejects_incomplete_target_limitation_marker() -> None:
    response = _render(_command_probe_payload())
    target_identity = dict(response["target_identity"])
    target_identity["engine_limitations"] = ["local_product_target_until_high_fidelity_target_is_promoted"]

    assert (
        product_action_audio_target_identity_error({"target_identity": target_identity})
        == "product action-audio target identity cannot advertise incomplete target limitations"
    )


def test_glottal_position_tracks_change_audio() -> None:
    baseline = _command_probe_payload()
    variant = _command_probe_payload()
    for gesture in variant["actions"]["gestures"]:
        if gesture["track"] == "x_bottom":
            gesture["value"] = 0.12
        if gesture["track"] == "x_top":
            gesture["value"] = -0.08

    baseline_audio = _render(baseline)["audio"]
    variant_audio = _render(variant)["audio"]

    mean_abs_delta = sum(abs(float(left) - float(right)) for left, right in zip(baseline_audio, variant_audio)) / len(baseline_audio)
    assert mean_abs_delta > 1.0e-4


def test_target_self_test_proves_all_required_tracks_are_coupled() -> None:
    response = _target_self_test()

    assert response["ok"] is True
    assert response["target_probe_ok"] is True
    coupling = response["track_coupling"]
    assert set(coupling) == set(PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS)
    for track in PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS:
        assert coupling[track]["coupled"] is True
        assert coupling[track]["audio_mean_abs_delta"] > 1.0e-5


def test_helper_self_test_rejects_uncoupled_target_track(monkeypatch) -> None:
    target_identity = _render(_command_probe_payload())["target_identity"]

    def fake_health() -> dict:
        return {
            "ok": True,
            "ready": True,
            "target_probe_ok": True,
            "target_probe_error": "",
            "target_identity": target_identity,
            "target_probe_metrics": {},
        }

    def fake_target_url() -> str:
        return "http://target.example"

    def fake_fetch(url: str, timeout_s: float = 1.5) -> tuple[int, dict]:
        assert url == "http://target.example/self-test"
        return 200, {
            "ok": True,
            "ready": True,
            "target_probe_ok": True,
            "target_probe_error": "",
            "target_identity": target_identity,
            "track_coupling": {
                track: {
                    "coupled": track != "x_top",
                    "audio_mean_abs_delta": 0.02 if track != "x_top" else 0.0,
                    "action_mean_delta": 1.0,
                }
                for track in PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS
            },
        }

    monkeypatch.setattr(product_action_audio_helper, "_health", fake_health)
    monkeypatch.setattr(product_action_audio_helper, "_target_url", fake_target_url)
    monkeypatch.setattr(product_action_audio_helper, "_fetch", fake_fetch)

    response = product_action_audio_helper._self_test()

    assert response["ok"] is False
    assert response["gate_status"] == "self_test_failed"
    assert "uncoupled tracks: x_top" in response["self_test_error"]


def test_url_target_probe_requires_real_contract_render(monkeypatch) -> None:
    render_calls: list[dict] = []

    def fake_fetch(url: str, timeout_s: float = 1.5) -> tuple[int, dict]:
        assert url == "http://target.example/ready"
        return 200, {"ready": True, "target_identity": _render(_command_probe_payload())["target_identity"], "target_probe_ok": True}

    def fake_post(url: str, payload: dict, timeout_s: float = 20.0) -> dict:
        assert url == "http://target.example/render"
        render_calls.append(payload)
        return _render(payload)

    monkeypatch.setattr(product_action_audio_helper, "_fetch", fake_fetch)
    monkeypatch.setattr(product_action_audio_helper, "_post", fake_post)

    response = product_action_audio_helper._probe_url_target("http://target.example")

    assert response["ready"] is True
    assert response["target_probe_ok"] is True
    assert response["target_probe_error"] == ""
    assert len(render_calls) == 1
