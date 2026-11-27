from custom_components.heating_intelligence.house_assessment import build_house_assessment


def test_empty_profile_reports_missing_information_without_inventing_performance():
    result = build_house_assessment({}, {}, {})

    assert result["profile_completeness_percent"] == 0
    assert result["performance_status"] == "collecting_data"
    assert result["measured_performance"] == {}
    assert result["room_coverage"]["configured_room_area_m2"] == 0
    assert any("Attic-floor U-value" in item for item in result["observations"])


def test_known_values_increase_completeness_and_are_reported():
    profile = {
        "wall_construction": "brick",
        "wall_insulation_cm": 15,
        "window_glazing": "double",
        "ventilation_type": "natural",
    }
    result = build_house_assessment(profile, {}, {})

    assert result["profile_completeness_percent"] > 0
    assert result["sections"]["envelope"]["known_fields"] == 3
    assert any("15 cm" in item for item in result["observations"])
    assert any("double" in item for item in result["observations"])


def test_unknown_values_do_not_count_as_known():
    result = build_house_assessment(
        {"wall_construction": "unknown", "roof_insulation_cm": None},
        {},
        {},
    )

    assert result["sections"]["envelope"]["known_fields"] == 0


def test_performance_is_kept_separate_and_uses_existing_estimates():
    efficiency = {
        "heat_loss_u_value_7d": 0.42,
        "heat_loss_u_value_30d": None,
        "thermal_model_boiler_efficiency_assumption": 0.9,
    }
    result = build_house_assessment({}, efficiency, {})

    assert result["performance_status"] == "7_day_estimate_available"
    assert result["measured_performance"] == {"heat_loss_u_value_7d": 0.42}
    assert "energy-efficiency score" in result["interpretation"]


def test_room_area_coverage_uses_only_configured_positive_areas():
    rooms = {
        "living": {"area": 35.5},
        "bedroom": {"area": None},
        "hall": {"area": 0},
    }
    result = build_house_assessment({}, {}, rooms)

    coverage = result["room_coverage"]
    assert coverage["configured_rooms"] == 3
    assert coverage["rooms_with_area"] == 1
    assert coverage["configured_room_area_m2"] == 35.5
    assert coverage["room_area_coverage_percent"] == 33
    assert coverage["rooms_with_measured_ach"] == 0
    assert coverage["average_measured_ach"] is None
    assert coverage["minimum_measured_ach"] is None
    assert coverage["maximum_measured_ach"] is None


def test_attic_floor_insulation_is_reported_separately_from_roof_insulation():
    result = build_house_assessment(
        {
            "roof_construction": "Timber, membrane and concrete tiles",
            "roof_insulation_cm": 0,
            "attic_floor_insulation_cm": 20,
            "attic_floor_insulation_material": "Mineral wool",
            "attic_floor_u_value": 0.183,
            "attic_floor_area_m2": 72,
        },
        {},
        {},
    )
    characteristics = result["building_characteristics"]
    assert characteristics["roof_construction"] == "Timber, membrane and concrete tiles"
    assert characteristics["attic_floor_insulation_cm"] == 20
    assert characteristics["attic_floor_insulation_material"] == "Mineral wool"
    assert characteristics["attic_floor_u_value_w_m2k"] == 0.183
    assert any("Attic-floor insulation thickness recorded as 20 cm" in item for item in result["observations"])
