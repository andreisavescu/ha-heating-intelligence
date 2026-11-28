from custom_components.heating_intelligence.building_audits import (
    build_house_audit,
    build_measurement_audit,
    build_thermal_readiness_audit,
)


def test_house_audit_keeps_glass_frame_and_gross_areas_separate():
    options = {
        "rooms": [{"id": "living", "name": "Living"}],
        "house_assessment": {"window_area_east_m2": 3.0},
    }
    windows = [{
        "id": "east_1", "name": "East living", "room_id": "living",
        "orientation": "east", "glass_area_m2": 2.5, "frame_area_m2": 0.5,
    }]
    result = build_house_audit(options, {}, windows)

    assert result["individual_windows_glass_total_m2"] == 2.5
    assert result["individual_windows_known_frame_area_total_m2"] == 0.5
    assert result["individual_windows_gross_total_m2"] == 3.0
    assert result["window_area_by_orientation"]["east"]["glass_difference_m2"] == -0.5
    assert result["window_area_by_orientation"]["east"]["reference_area_basis"] == "glass"


def test_house_audit_flags_unknown_room_and_incomplete_geometry():
    result = build_house_audit(
        {"rooms": [{"id": "living"}]},
        {},
        [{"id": "w1", "name": "Window 1", "room_id": "missing", "orientation": "unknown"}],
    )
    assert result["windows_with_unknown_room"] == ["Window 1"]
    assert result["windows_missing_geometry_or_orientation"] == ["Window 1"]
    assert result["status"] == "review_required"


def test_thermal_readiness_does_not_invent_conductances():
    options = {"rooms": [{"id": "bedroom", "name": "Bedroom", "floor": "upstairs", "area_m2": 12}]}
    result = build_thermal_readiness_audit(options, {}, {}, {})
    assert result["rooms_without_outdoor_surface_records"] == ["bedroom"]
    assert result["upstairs_rooms_missing_attic_surface_records"] == ["bedroom"]
    assert result["room_parameters"]["bedroom"]["volume_m3"] is None


def test_measurement_audit_treats_return_temperature_as_optional_gap():
    options = {
        "rooms": [{"id": "living", "temperature_entity": "sensor.living_temp"}],
        "water_temperature_entity": "sensor.supply",
    }
    result = build_measurement_audit(options, {}, {"heat_delivery_calibration": {}})
    assert result["water_side_measurement"]["supply_temperature_configured"] is True
    assert result["water_side_measurement"]["return_temperature_configured"] is False
    assert "not treated as prerequisites" in result["measurement_interpretation"]



def test_missing_frame_area_is_unknown_not_zero():
    result = build_house_audit(
        {"rooms": [{"id": "living"}], "house_assessment": {"window_area_east_m2": 3.0}},
        {},
        [{"id": "east_1", "room_id": "living", "orientation": "east", "glass_area_m2": 2.5}],
    )
    east = result["window_area_by_orientation"]["east"]
    assert east["gross_m2"] is None
    assert east["glass_difference_m2"] == -0.5
    assert result["individual_windows_gross_total_m2"] is None



def test_thermal_readiness_ignores_legacy_room_and_attic_conductances():
    options = {
        "rooms": [
            {"id": "upstairs", "name": "Upstairs", "floor": "upstairs",
             "area_m2": 12, "volume_m3": 30, "envelope_conductance_w_per_k": 4.2},
        ],
        "house_assessment": {
            "attic_floor_room_conductances_w_per_k": {"upstairs": 2.1},
        },
    }
    result = build_thermal_readiness_audit(options, {}, {}, {})
    room = result["room_parameters"]["upstairs"]
    assert room["room_to_outdoors_surface_calculated_w_per_k"] is None
    assert room["room_to_attic_surface_calculated_w_per_k"] is None
    assert result["upstairs_rooms_missing_attic_surface_records"] == ["upstairs"]
    assert "legacy_room_level_conductances" in result["boundary_interpretation"]


def test_thermal_readiness_reports_outdoor_and_attic_paths_separately():
    options = {
        "rooms": [
            {"id": "upstairs", "name": "Upstairs", "floor": "upstairs",
             "area_m2": 12, "volume_m3": 30, "envelope_conductance_w_per_k": 4.2},
        ],
        "house_assessment": {
            "attic_floor_room_conductances_w_per_k": {"upstairs": 2.1},
        },
    }
    envelope = {
        "status": "configured",
        "rooms": {
            "upstairs": {
                "conductance_by_boundary_w_per_k": {"outdoor": 3.8, "attic": 2.1},
                "configured_surface_count": 3,
            },
        },
    }
    result = build_thermal_readiness_audit(options, {}, {}, {}, envelope)
    room = result["room_parameters"]["upstairs"]
    assert room["room_to_outdoors_surface_calculated_w_per_k"] == 3.8
    assert room["room_to_attic_surface_calculated_w_per_k"] == 2.1
    assert room["surface_conductance_by_boundary_w_per_k"]["attic"] == 2.1



def test_thermal_readiness_exposes_missing_inter_room_connection_configuration():
    options = {
        "rooms": [{"id": "living", "floor": "ground_floor", "area_m2": 20,
                   "volume_m3": 50, "envelope_conductance_w_per_k": 5}],
        "house_assessment": {},
    }
    result = build_thermal_readiness_audit(options, {}, {}, {})
    assert result["inter_room_connection_status"] == "not_configured"
    assert result["inter_room_connections_required_for_multizone_model"] is True
    assert result["status"] == "incomplete"
