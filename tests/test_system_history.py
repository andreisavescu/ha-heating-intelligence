from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "heating_intelligence" / "system_history.py"
SPEC = spec_from_file_location("system_history", MODULE_PATH)
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
record_system_sample = MODULE.record_system_sample


def test_system_history_samples_every_five_minutes_and_keeps_circuit_and_room_values():
    history = {}
    now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    snapshot = {
        "thermal_power_w": 1250,
        "active_flow_l_min": 2.0,
        "circuits": {"loop_1": {"name": "Living loop", "flow_active": True, "active_configured_flow_l_min": 2.0, "estimated_thermal_power_w": 1250}},
        "rooms": {"living": {"name": "Living", "temperature": 21.2, "estimated_thermal_power_w": 1250, "demand": True}},
    }
    first = record_system_sample(history, now, snapshot)
    same_minute = record_system_sample(history, now + timedelta(seconds=60), snapshot)
    next_sample = record_system_sample(history, now + timedelta(seconds=300), snapshot)
    assert len(first) == 1
    assert len(same_minute) == 1
    assert len(next_sample) == 2
    assert next_sample[-1]["circuits"]["loop_1"]["active"] is True
    assert next_sample[-1]["rooms"]["living"]["temperature_c"] == 21.2


def test_system_history_prunes_samples_older_than_thirty_days():
    history = {}
    now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    record_system_sample(history, now - timedelta(days=31), {})
    record_system_sample(history, now, {})
    assert len(history["system_samples"]) == 1
    assert history["system_samples"][0]["time"] == now.isoformat()
