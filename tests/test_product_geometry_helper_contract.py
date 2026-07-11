from __future__ import annotations

from product_geometry_helper import handle_request


def _request(request_type: str, **extra):
    payload = {
        "type": request_type,
        "request_id": f"test-{request_type}",
        "current_tract_params": [0.5] * 19,
        "parameter_schema": "owned_product_normalized_19d",
    }
    payload.update(extra)
    return handle_request(payload)


def test_health_uses_product_geometry_identity() -> None:
    response = _request("health")

    assert response["status"] == "ok"
    assert response["helper_name"] == "tract-reference-renderer"
    assert response["runtime"] == "product_tract_geometry_helper_ipc"
    assert response["clinical_truth_claim_allowed"] is False


def test_geometry_2d_returns_product_svg_and_controls() -> None:
    response = _request("geometry_2d", width_px=640, height_px=640)

    assert response["status"] == "ok"
    svg = response["current_svg"]
    assert "data-helper='tract-reference-renderer'" in svg
    assert "data-role='product-2d-vocal-tract'" in svg
    assert "class='product-airway-lumen'" in svg
    assert response["current_control_points"]
    assert response["geometry_provenance"]["fallback_allowed"] is False


def test_geometry_3d_returns_indexed_surface_mesh() -> None:
    response = _request("geometry_3d")

    assert response["status"] == "ok"
    surface = response["surfaces"][0]
    assert surface["vertices"]
    assert surface["indices"]
    assert len(surface["indices"]) % 3 == 0
    assert response["geometry_provenance"]["runtime"] == "product_tract_geometry_helper_ipc"
    assert response["clinical_truth_claim_allowed"] is False


def test_drag_solve_returns_changed_19d_vector() -> None:
    response = _request("drag_solve", control_id="jaw", x_cm=1.0, y_cm=2.0)

    assert response["status"] == "ok"
    solve = response["solve"]
    assert solve["blocked"] is False
    assert len(solve["parameter_vector"]) == 19
    assert solve["parameter_vector"] != [0.5] * 19
    assert solve["geometry_provenance"]["fallback_allowed"] is False
