from custom_components.heating_intelligence.attic_thermal import build_attic_thermal_analysis


def test_attic_temperature_gradients_are_reported_without_assumptions():
    result = build_attic_thermal_analysis(15.0, 65.0, 21.0, 5.0)

    assert result["attic_to_outdoor_delta_c"] == 10.0
    assert result["upstairs_to_attic_delta_c"] == 6.0
    assert result["estimated_roof_heat_flow_w"] is None
    assert result["estimated_ceiling_heat_flow_w"] is None
    assert result["heat_flow_status"] == "temperature_gradients_only"


def test_attic_heat_flow_uses_explicit_u_values_and_areas():
    result = build_attic_thermal_analysis(
        15.0, 65.0, 21.0, 5.0,
        {
            "roof_u_value": 0.2,
            "roof_area_m2": 100,
            "attic_floor_u_value": 0.3,
            "attic_floor_area_m2": 80,
        },
    )

    assert result["estimated_roof_heat_flow_w"] == 200.0
    assert result["estimated_ceiling_heat_flow_w"] == 144.0
    assert result["heat_flow_status"] == "estimates_available"


def test_reverse_temperature_gradient_keeps_signed_heat_flow():
    result = build_attic_thermal_analysis(
        2.0, 70.0, 18.0, 5.0,
        {"roof_u_value": 0.2, "roof_area_m2": 100},
    )

    assert result["attic_to_outdoor_delta_c"] == -3.0
    assert result["estimated_roof_heat_flow_w"] == -60.0
