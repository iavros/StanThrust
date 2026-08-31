"""Tests for the renderer-ready geometry derived from the solved design."""

import math

from stanthrust.design_model import create_engine_design
from stanthrust.visualization_geometry import build_chamber_nozzle_geometry


def test_render_geometry_uses_exact_solved_inner_contour():
    design = create_engine_design({"regen_cooling": True})
    geometry = build_chamber_nozzle_geometry(design)
    profile = geometry["profile"]
    chamber_length = geometry["chamber_length_mm"]
    nozzle_profile = [row for row in profile if float(row["x_mm"]) >= chamber_length]

    assert len(nozzle_profile) == len(design.derived.nozzle_contour_points)
    for rendered, solved in zip(nozzle_profile, design.derived.nozzle_contour_points):
        assert math.isclose(
            float(rendered["inner_radius_mm"]),
            float(solved["radius_mm"]),
            rel_tol=0.0,
            abs_tol=1e-7,
        )


def test_render_geometry_builds_local_regen_stack_from_calculated_dimensions():
    design = create_engine_design({"regen_cooling": True})
    geometry = build_chamber_nozzle_geometry(design)
    profile = geometry["profile"]

    assert geometry["channel_count"] > 0
    assert all(float(row["channel_width_mm"]) > 0.0 for row in profile)
    for row in profile:
        assert math.isclose(
            float(row["channel_outer_radius_mm"]) - float(row["hot_wall_outer_radius_mm"]),
            float(geometry["channel_depth_mm"]),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
        assert math.isclose(
            float(row["jacket_outer_radius_mm"]) - float(row["channel_outer_radius_mm"]),
            float(geometry["jacket_wall_mm"]),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
