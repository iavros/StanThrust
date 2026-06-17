import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


PROJECT_SCHEMA_VERSION = 1


@dataclass
class ProjectDocument:
    version: int
    generated_at: str
    state: Dict[str, object]
    objective_weights: Dict[str, float]
    ga_result: Optional[Dict[str, object]] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "state": self.state,
            "objective_weights": self.objective_weights,
            "ga_result": self.ga_result,
        }


def build_project_document(
    state: Dict[str, object],
    objective_weights: Dict[str, float],
    ga_result: Optional[Dict[str, object]] = None,
) -> ProjectDocument:
    return ProjectDocument(
        version=PROJECT_SCHEMA_VERSION,
        generated_at=datetime.utcnow().isoformat() + "Z",
        state=state,
        objective_weights=objective_weights,
        ga_result=ga_result,
    )


def save_project(
    path: Path,
    state: Dict[str, object],
    objective_weights: Dict[str, float],
    ga_result: Optional[Dict[str, object]] = None,
) -> None:
    document = build_project_document(state, objective_weights, ga_result)
    path.write_text(json.dumps(document.as_dict(), indent=2), encoding="utf-8")


def load_project(path: Path) -> ProjectDocument:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ProjectDocument(
        version=int(raw.get("version", PROJECT_SCHEMA_VERSION)),
        generated_at=str(raw.get("generated_at", "")),
        state=dict(raw.get("state", {})),
        objective_weights=dict(raw.get("objective_weights", {})),
        ga_result=raw.get("ga_result"),
    )
