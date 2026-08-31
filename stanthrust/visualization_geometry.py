"""Renderer-ready dimensions derived from the solved engine geometry."""

import math
from typing import Dict, List

from stanthrust.design_model import EngineDesign


def _number(values: Dict[str, object], key: str, fallback: float) -> float:
    try:
        return float(values.get(key, fallback))
    except (TypeError, ValueError):
        return fallback


def build_chamber_nozzle_geometry(design: EngineDesign) -> Dict[str, object]:
    """Build the renderer geometry directly from solved engine dimensions."""

    values = dict(design.derived.engineering_values)
    chamber_length_mm = float(design.derived.chamber_length_mm)
    chamber_inner_radius_mm = 0.5 * _number(
        values, "chamber_inner_diameter_mm", design.inputs.chamber_diameter_mm
    )
    chamber_wall_mm = _number(values, "chamber_wall_thickness_mm", 1.0)
    nozzle_wall_mm = _number(values, "nozzle_structural_wall_thickness_mm", 1.0)
    channel_depth_mm = _number(values, "regen_channel_depth_mm", 0.0) if design.inputs.regen_cooling else 0.0
    jacket_wall_mm = _number(values, "regen_outer_jacket_thickness_mm", 0.0) if design.inputs.regen_cooling else 0.0
    inner_wall_mm = _number(values, "regen_inner_wall_thickness_mm", 0.0) if design.inputs.regen_cooling else 0.0
    if inner_wall_mm > 0.0:
        chamber_wall_mm = max(chamber_wall_mm, inner_wall_mm)
        nozzle_wall_mm = max(nozzle_wall_mm, inner_wall_mm)
    channel_count = max(0, int(round(_number(values, "regen_channel_count", 0.0))))
    rib_thickness_mm = _number(values, "regen_rib_thickness_mm", 0.0) if channel_count else 0.0

    profile: List[Dict[str, object]] = []

    def append_station(x_mm: float, inner_radius_mm: float, wall_mm: float, section: str) -> None:
        hot_wall_outer_radius_mm = inner_radius_mm + wall_mm
        channel_outer_radius_mm = hot_wall_outer_radius_mm + channel_depth_mm
        jacket_outer_radius_mm = channel_outer_radius_mm + jacket_wall_mm
        if not design.inputs.regen_cooling:
            jacket_outer_radius_mm = hot_wall_outer_radius_mm
        pitch_mm = (
            2.0 * math.pi * hot_wall_outer_radius_mm / channel_count if channel_count > 0 else 0.0
        )
        profile.append(
            {
                "x_mm": float(x_mm),
                "inner_radius_mm": float(inner_radius_mm),
                "hot_wall_outer_radius_mm": float(hot_wall_outer_radius_mm),
                "channel_outer_radius_mm": float(channel_outer_radius_mm),
                "jacket_outer_radius_mm": float(jacket_outer_radius_mm),
                "wall_thickness_mm": float(wall_mm),
                "channel_depth_mm": float(channel_depth_mm),
                "channel_pitch_mm": float(pitch_mm),
                "channel_width_mm": float(max(0.0, pitch_mm - rib_thickness_mm)),
                "section": section,
            }
        )

    append_station(0.0, chamber_inner_radius_mm, chamber_wall_mm, "chamber")
    append_station(chamber_length_mm, chamber_inner_radius_mm, chamber_wall_mm, "chamber")
    for point in sorted(
        design.derived.nozzle_contour_points,
        key=lambda row: float(row.get("x_mm", 0.0)),
    ):
        nozzle_x_mm = float(point.get("x_mm", 0.0))
        append_station(
            chamber_length_mm + nozzle_x_mm,
            float(point.get("radius_mm", 0.0)),
            nozzle_wall_mm,
            str(point.get("section", "nozzle")),
        )

    deduplicated: List[Dict[str, object]] = []
    for row in sorted(profile, key=lambda item: float(item["x_mm"])):
        if deduplicated and abs(float(row["x_mm"]) - float(deduplicated[-1]["x_mm"])) < 1e-7:
            deduplicated[-1] = row
        else:
            deduplicated.append(row)

    throat_station = min(deduplicated, key=lambda row: float(row["inner_radius_mm"]))
    return {
        "profile": deduplicated,
        "channel_count": channel_count,
        "rib_thickness_mm": rib_thickness_mm,
        "channel_depth_mm": channel_depth_mm,
        "jacket_wall_mm": jacket_wall_mm,
        "chamber_length_mm": chamber_length_mm,
        "nozzle_length_mm": float(design.derived.nozzle_length_mm),
        "total_length_mm": chamber_length_mm + float(design.derived.nozzle_length_mm),
        "throat_x_mm": float(throat_station["x_mm"]),
        "throat_diameter_mm": 2.0 * float(throat_station["inner_radius_mm"]),
        "chamber_inner_diameter_mm": 2.0 * chamber_inner_radius_mm,
        "exit_inner_diameter_mm": 2.0 * float(deduplicated[-1]["inner_radius_mm"]),
    }
