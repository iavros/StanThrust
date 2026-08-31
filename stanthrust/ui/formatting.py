"""Display formatting for solver values, option names, and version strings."""

from __future__ import annotations

from typing import List, Optional, Tuple

#: Internal option values mapped to the labels shown in the interface.
INJECTOR_DISPLAY_NAMES = {
    "impinging": "Impinging",
    "pintle": "Pintle",
}

FLOW_MODEL_DISPLAY_NAMES = {
    "viscous": "Viscous Quasi-1D",
    "fast": "Fast Preview",
    "refined": "Refined Solve",
}

NOZZLE_EXPANSION_DISPLAY_NAMES = {
    "pressure_matched": "Pressure Matched",
    "underexpanded": "Underexpanded",
    "overexpanded": "Overexpanded",
}

PRESSURE_MODE_DISPLAY_NAMES = {
    "design": "Design Sizing",
    "analysis": "Hardware Analysis",
}

_SOLVER_STAGE_PREFIXES = (
    "stage-2-nozzle-loss-",
    "stage-3-pressure-root-shock-feedback-",
    "pressure-root-shock-feedback-",
)

#: Placeholder shown wherever a value has not been solved yet.
EMPTY = "--"


def display_option_name(value: object) -> str:
    """Turn a lower-case option token into title-cased display text."""
    text = str(value or "")
    if text and text == text.lower():
        return text.replace("_", " ").replace("-", " ").title()
    return text


def display_injector_name(value: object) -> str:
    """Return the interface label for an injector family token."""
    normalized = str(value or "").strip().lower()
    return INJECTOR_DISPLAY_NAMES.get(normalized, str(value or ""))


def display_solver_stage(value: object, flow_model_label: object = "") -> str:
    """Collapse an internal solver stage token into readable stage text."""
    text = str(value or "").strip()
    if text.startswith(_SOLVER_STAGE_PREFIXES):
        if flow_model_label:
            return str(flow_model_label)
        flow_model = text
        for prefix in _SOLVER_STAGE_PREFIXES:
            flow_model = flow_model.replace(prefix, "")
        return "{0} solver".format(display_option_name(flow_model.strip()))
    return display_option_name(text) if text else EMPTY


def as_float(value: object, fallback: float = 0.0) -> float:
    """Coerce ``value`` to ``float``, falling back on non-numeric input."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def safe_float(value: object, fallback: Optional[float] = None) -> Optional[float]:
    """Coerce ``value`` to ``float`` or return ``fallback`` when impossible."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def format_number(value: object, decimals: int = 1) -> str:
    """Format a solver value for display, preserving non-numeric placeholders."""
    if value in (None, "", EMPTY):
        return EMPTY
    try:
        return "{0:.{1}f}".format(float(value), decimals)
    except (TypeError, ValueError):
        return str(value)


def format_measure(value: object, decimals: int, unit: str = "") -> str:
    """Format a value with a trailing unit, skipping the unit when unsolved."""
    text = format_number(value, decimals)
    if not unit or text == EMPTY:
        return text
    return "{0} {1}".format(text, unit)


def format_percent(value: object, target: object, decimals: int = 0) -> str:
    """Return ``value`` as a percentage of ``target``."""
    numerator = safe_float(value)
    denominator = safe_float(target)
    if numerator is None or not denominator:
        return EMPTY
    return "{0:.{1}f}%".format(numerator / denominator * 100.0, decimals)


def version_parts(version: object) -> Tuple[int, ...]:
    """Split a version string into a comparable tuple of integers."""
    cleaned = str(version or "").strip().lstrip("vV")
    parts: List[int] = []
    for token in cleaned.replace("-", ".").replace("_", ".").split("."):
        digits = "".join(char for char in token if char.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts or [0])


def is_newer_version(candidate: object, current: object) -> bool:
    """Return ``True`` when ``candidate`` is a later version than ``current``."""
    candidate_parts = list(version_parts(candidate))
    current_parts = list(version_parts(current))
    width = max(len(candidate_parts), len(current_parts))
    candidate_parts.extend([0] * (width - len(candidate_parts)))
    current_parts.extend([0] * (width - len(current_parts)))
    return tuple(candidate_parts) > tuple(current_parts)
