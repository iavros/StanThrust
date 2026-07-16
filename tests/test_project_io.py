from stanthrust.exporter import load_project, save_project


def test_project_round_trip(tmp_path):
    path = tmp_path / "engine.stanthrust.json"
    state = {"fuel_name": "Ethanol", "target_thrust_newtons": 250.0}
    weights = {"thrust": 0.4, "mass": 0.2, "packaging": 0.2, "thermal": 0.2}
    optimizer_result = {"status": "complete", "score": 0.91}

    save_project(path, state, weights, optimizer_result)
    document = load_project(path)

    assert document.version == 1
    assert document.generated_at.endswith("Z")
    assert document.state == state
    assert document.objective_weights == weights
    assert document.ga_result == optimizer_result
