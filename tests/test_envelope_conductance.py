from custom_components.heating_intelligence.envelope_conductance import build_envelope_conductance


def test_surface_conductance_is_area_times_u_value_and_boundary_separated():
    result = build_envelope_conductance({
        "rooms": [{"id": "bedroom"}],
        "envelope_surfaces": [
            {"id": "wall", "name": "External wall", "room_id": "bedroom",
             "boundary": "outdoor", "area_m2": 10, "u_value_w_m2k": 0.2},
            {"id": "ceiling", "name": "Attic floor", "room_id": "bedroom",
             "boundary": "attic", "area_m2": 12, "u_value_w_m2k": 0.18},
        ],
    })
    room = result["rooms"]["bedroom"]
    assert room["conductance_by_boundary_w_per_k"]["outdoor"] == 2.0
    assert room["conductance_by_boundary_w_per_k"]["attic"] == 2.16
    assert room["known_conductance_w_per_k"] == 4.16


def test_window_requires_frame_area_and_whole_window_uw():
    result = build_envelope_conductance({
        "rooms": [{"id": "living"}],
        "windows": [{"id": "w", "room_id": "living", "glass_area_m2": 2,
                     "frame_area_m2": 0.4, "whole_window_u_value_w_m2k": 0.9}],
    })
    assert result["rooms"]["living"]["conductance_by_boundary_w_per_k"]["outdoor"] == 2.16
    missing = build_envelope_conductance({
        "rooms": [{"id": "living"}],
        "windows": [{"id": "w", "room_id": "living", "glass_area_m2": 2,
                     "whole_window_u_value_w_m2k": 0.9}],
    })
    assert missing["known_surface_count"] == 0
    assert missing["windows"] == []
    assert missing["incomplete_records"] == ["w"]


def test_unknown_u_value_is_not_inferred_and_adjacent_space_is_separate():
    result = build_envelope_conductance({
        "rooms": [{"id": "room"}],
        "envelope_surfaces": [
            {"id": "unknown", "name": "Unknown wall", "room_id": "room",
             "boundary": "outdoor", "area_m2": 10},
            {"id": "shared", "name": "Shared wall", "room_id": "room",
             "boundary": "adjacent_heated", "area_m2": 8, "u_value_w_m2k": 0.5},
        ],
    })
    assert result["rooms"]["room"]["conductance_by_boundary_w_per_k"]["outdoor"] is None
    assert result["rooms"]["room"]["conductance_by_boundary_w_per_k"]["adjacent_heated"] == 4.0
    assert result["incomplete_records"] == ["Unknown wall"]
