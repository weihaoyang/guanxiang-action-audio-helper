from __future__ import annotations

import re
from typing import Any, Mapping

PRODUCT_ACTION_AUDIO_HELPER_BOUNDARY = "product_action_audio_helper_ipc"
PRODUCT_ACTION_AUDIO_HELPER_REQUEST_SCHEMA = "guanxiang.product_action_audio.render.v1"
PRODUCT_ACTION_AUDIO_HELPER_ACTIONS_SCHEMA = "guanxiang.product_action_audio.actions.v1"
PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_SAMPLE_RATE_HZ = 48000
PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_RUNTIME_KIND = "product_action_audio_helper"
PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_CLAIM_TIER = "product_runtime_contract"
PRODUCT_ACTION_AUDIO_TARGET_PROTOCOL = "guanxiang.action_audio.target.v1"
PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_RUNTIME_KIND = "product_action_audio_target"
PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_CLAIM_TIER = "product_runtime_contract"
PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_CAPABILITY = "nine_track_dynamic_glottis"
PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_TRACKS = (
    "f0",
    "pressure",
    "x_bottom",
    "x_top",
    "chink_area",
    "lag",
    "rel_amp",
    "pulse_shape",
    "flutter",
)

_DEVELOPMENT_TOKENS = {"development", "dev", "e2e", "mock", "fixture", "contract_helper"}
_DEVELOPMENT_SUBSTRINGS = _DEVELOPMENT_TOKENS - {"dev"}
_REFERENCE_MARKERS = ("vtl", "vocaltractlab", "vocal_tract_lab", "vocal tract lab")


def _identity_tokens(value: str) -> set[str]:
    normalized = str(value or "").lower().replace("contract helper", "contract_helper")
    return {token for token in re.split(r"[^a-z0-9_]+", normalized) if token}


def classify_action_audio_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    renderer_id = str(payload.get("renderer_id", "") or payload.get("helper_id", "") or "").strip()
    renderer_runtime_kind = str(payload.get("renderer_runtime_kind", "") or payload.get("runtime_kind", "") or "").strip()
    helper_tier = str(payload.get("helper_tier", "") or "").strip()
    claim_tier = str(payload.get("claim_tier", "") or "").strip()
    product_evidence_allowed = payload.get("product_evidence_allowed") is True
    explicit_development_only = bool(
        payload.get("not_for_product_evidence", False)
        or payload.get("development_only", False)
        or payload.get("e2e_only", False)
    )
    identity = " ".join(
        [
            renderer_id,
            renderer_runtime_kind,
            helper_tier,
            claim_tier,
            str(payload.get("renderer_name", "") or ""),
        ]
    ).lower()
    tokens = _identity_tokens(identity)
    reference_runtime_blocked = any(marker in identity for marker in _REFERENCE_MARKERS)
    development_helper = explicit_development_only or bool(tokens & _DEVELOPMENT_TOKENS) or any(
        token in identity for token in _DEVELOPMENT_SUBSTRINGS
    )
    not_for_product_evidence = explicit_development_only or not product_evidence_allowed or reference_runtime_blocked
    return {
        "renderer_id": renderer_id,
        "renderer_runtime_kind": renderer_runtime_kind,
        "helper_tier": helper_tier,
        "claim_tier": claim_tier,
        "runtime_kind_ok": renderer_runtime_kind == PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_RUNTIME_KIND,
        "claim_tier_ok": claim_tier == PRODUCT_ACTION_AUDIO_HELPER_REQUIRED_CLAIM_TIER,
        "development_helper": bool(development_helper),
        "reference_runtime_blocked": bool(reference_runtime_blocked),
        "not_for_product_evidence": bool(not_for_product_evidence),
        "product_evidence_allowed": bool(product_evidence_allowed)
        and not bool(development_helper)
        and not bool(reference_runtime_blocked),
    }


def product_action_audio_target_identity_error(payload: Mapping[str, Any]) -> str:
    target_identity = payload.get("target_identity")
    if not isinstance(target_identity, Mapping):
        return "product action-audio helper response must include target_identity"
    if str(target_identity.get("protocol", "") or "").strip() != PRODUCT_ACTION_AUDIO_TARGET_PROTOCOL:
        return "product action-audio target identity protocol mismatch"
    if str(target_identity.get("runtime_kind", "") or "").strip() != PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_RUNTIME_KIND:
        return "product action-audio target identity runtime kind mismatch"
    if str(target_identity.get("claim_tier", "") or "").strip() != PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_CLAIM_TIER:
        return "product action-audio target identity claim tier mismatch"
    if str(target_identity.get("engine_family", "") or "").strip() == "":
        return "product action-audio target identity must include engine_family"
    capabilities = target_identity.get("engine_capabilities")
    if not isinstance(capabilities, list) or not any(str(item).strip() for item in capabilities):
        return "product action-audio target identity must include engine_capabilities"
    normalized_capabilities = {str(item).strip() for item in capabilities}
    if PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_CAPABILITY not in normalized_capabilities:
        return (
            "product action-audio target identity must include "
            f"{PRODUCT_ACTION_AUDIO_TARGET_REQUIRED_CAPABILITY}"
        )
    if target_identity.get("product_evidence_allowed") is not True:
        return "product action-audio target identity must explicitly allow product evidence"
    if target_identity.get("not_for_product_evidence") is not False:
        return "product action-audio target identity must not be marked not_for_product_evidence"
    if target_identity.get("fallback_allowed") is not False:
        return "product action-audio target identity must disallow fallback"
    identity = " ".join(
        str(target_identity.get(key, "") or "")
        for key in ("id", "target_id", "target_name", "engine_family", "runtime_kind", "claim_tier", "protocol")
    ).lower()
    tokens = _identity_tokens(identity)
    if bool(tokens & _DEVELOPMENT_TOKENS) or any(token in identity for token in _DEVELOPMENT_SUBSTRINGS):
        return "product action-audio target identity cannot be development or fixture"
    if any(marker in identity for marker in _REFERENCE_MARKERS):
        return "product action-audio target identity cannot be a reference runtime"
    return ""


def response_contract_error(payload: Mapping[str, Any]) -> str:
    if str(payload.get("schema_version", "") or "").strip() != PRODUCT_ACTION_AUDIO_HELPER_REQUEST_SCHEMA:
        return "product action-audio helper response schema mismatch"
    if str(payload.get("renderer_contract", "") or "").strip() != "action_to_audio":
        return "product action-audio helper response contract mismatch"
    if str(payload.get("commercial_boundary", "") or "").strip() != PRODUCT_ACTION_AUDIO_HELPER_BOUNDARY:
        return "product action-audio helper response boundary mismatch"
    if payload.get("product_evidence_allowed") is not True:
        return "product action-audio helper response must explicitly allow product evidence"
    identity = classify_action_audio_payload(payload)
    if identity["runtime_kind_ok"] is not True:
        return "product action-audio helper response runtime kind mismatch"
    if identity["claim_tier_ok"] is not True:
        return "product action-audio helper response claim tier mismatch"
    return product_action_audio_target_identity_error(payload)
