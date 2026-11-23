"""Tests for room-level UFH topology construction."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "multizone_thermal_model",
    ROOT / "custom_components" / "heating_intelligence" / "multizone_thermal_model.py",
)
MODEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODEL)


def test_multiple_circuits_map_to_one_room_slab_node():
    result = MODEL.build_room_ufh_topology(
        {"living": {"name": "Living", "floor": "ground_floor"}},
        [
            {"id": "living_a", "served_rooms": ["living"], "pipe_length_m": 72},
            {"id": "living_b", "served_rooms": ["living"], "pipe_length_m": 64},
        ],
    )
    slab = result["room_slab_nodes"]["slab:living"]
    assert slab["circuit_ids"] == ["living_a", "living_b"]
    assert slab["circuit_count"] == 2
    assert slab["temperature_c"] is None
    assert slab["capacity_wh_per_k"] is None


def test_one_circuit_can_serve_multiple_room_slab_nodes():
    result = MODEL.build_room_ufh_topology(
        {
            "bathroom": {"floor": "upstairs"},
            "hall": {"floor": "upstairs"},
        },
        [{"id": "shared", "served_rooms": ["bathroom", "hall"], "pipe_length_m": 58}],
    )
    assert result["room_slab_nodes"]["slab:bathroom"]["circuit_ids"] == ["shared"]
    assert result["room_slab_nodes"]["slab:hall"]["circuit_ids"] == ["shared"]
    assert result["circuits"]["shared"]["served_room_ids"] == ["bathroom", "hall"]


def test_legacy_room_id_and_demand_room_fallbacks_are_explicit():
    result = MODEL.build_room_ufh_topology(
        {"office": {}, "bedroom": {}},
        [
            {"id": "legacy_single", "room_id": "office"},
            {"id": "legacy_multi", "demand_rooms": ["bedroom"]},
        ],
    )
    assert result["circuits"]["legacy_single"]["served_room_ids"] == ["office"]
    assert result["circuits"]["legacy_single"]["source"] == "legacy_room_id"
    assert result["circuits"]["legacy_multi"]["served_room_ids"] == ["bedroom"]
    assert result["circuits"]["legacy_multi"]["source"] == "legacy_demand_rooms"


def test_unconfigured_room_assignments_are_reported_not_created():
    result = MODEL.build_room_ufh_topology(
        {"living": {}},
        [{"id": "bad", "served_rooms": ["living", "missing"]}],
    )
    assert result["room_slab_nodes"]["slab:living"]["circuit_ids"] == ["bad"]
    assert result["circuits"]["bad"]["unresolved_room_ids"] == ["missing"]
    assert result["unresolved_assignments"] == [{
        "circuit_id": "bad",
        "reason": "served_room_not_configured",
        "room_ids": ["missing"],
    }]


def test_rooms_without_circuits_remain_visible_and_no_heat_is_invented():
    result = MODEL.build_room_ufh_topology(
        {"living": {}, "bedroom": {}},
        [{"id": "unassigned"}],
    )
    assert result["room_slab_nodes"]["slab:living"]["status"] == "no_configured_ufh_circuit"
    assert result["circuits"]["unassigned"]["thermal_output_w"] is None
    assert result["parameter_status"]["configured_flow"] == "not_used_as_thermal_power"


def test_duplicate_or_missing_circuit_ids_are_reported():
    result = MODEL.build_room_ufh_topology(
        {"living": {}},
        [
            {"id": "c1", "served_rooms": ["living"]},
            {"id": "c1", "served_rooms": ["living"]},
            {"served_rooms": ["living"]},
        ],
    )
    assert list(result["circuits"]) == ["c1"]
    assert result["coverage"]["configured_circuits"] == 3
    assert result["unresolved_assignments"] == [
        {"circuit_id": "c1", "reason": "duplicate_circuit_id"},
        {"index": 2, "reason": "missing_circuit_id"},
    ]


def test_each_room_gets_air_node_with_observed_temperature_and_air_only_capacity():
    result = MODEL.build_room_ufh_topology(
        {
            "living": {
                "name": "Living",
                "floor": "ground_floor",
                "temperature": 21.5,
                "temperature_entity": "sensor.living_temperature",
                "volume_m3": 105.0,
            },
            "bedroom": {
                "floor": "upstairs",
                "temperature": None,
                "volume_m3": 42.0,
            },
        },
        [],
    )
    living = result["room_air_nodes"]["room:living"]
    assert living["temperature_c"] == 21.5
    assert living["temperature_source"] == "configured_sensor"
    assert living["air_only_capacity_wh_per_k"] == 35.175
    assert living["effective_room_capacity_wh_per_k"] is None
    bedroom = result["room_air_nodes"]["room:bedroom"]
    assert bedroom["temperature_c"] is None
    assert bedroom["temperature_source"] == "unavailable"


def test_room_air_and_slab_nodes_are_connected_by_unidentified_topology_edge():
    result = MODEL.build_room_ufh_topology(
        {"living": {"temperature": 21.0}},
        [{"id": "living_loop", "served_rooms": ["living"]}],
    )
    assert set(result["room_air_nodes"]) == {"room:living"}
    assert set(result["room_slab_nodes"]) == {"slab:living"}
    assert result["room_slab_couplings"] == [{
        "from": "slab:living",
        "to": "room:living",
        "path": "ufh_slab_to_room_air",
        "conductance_w_per_k": None,
        "status": "conductance_not_identified",
        "note": "Topology edge only; excluded from RC solver until conductance is identified.",
    }]


def test_missing_or_invalid_room_volume_does_not_create_air_capacity():
    result = MODEL.build_room_ufh_topology(
        {
            "missing": {"temperature": 20.0},
            "invalid": {"temperature": 19.0, "volume_m3": -4},
        },
        [],
    )
    assert result["room_air_nodes"]["room:missing"]["air_only_capacity_wh_per_k"] is None
    assert result["room_air_nodes"]["room:invalid"]["air_only_capacity_wh_per_k"] is None
