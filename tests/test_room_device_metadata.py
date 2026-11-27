import ast
from pathlib import Path


SENSOR_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "heating_intelligence"
    / "sensor.py"
)


def test_all_room_device_entities_store_room_name():
    tree = ast.parse(SENSOR_PATH.read_text())
    target_classes = {
        "GasReconciledRoomEnergySensor",
        "RoomConductanceSensor",
    }
    found = set()

    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name not in target_classes:
            continue
        found.add(node.name)
        initializer = next(
            (
                item for item in node.body
                if isinstance(item, ast.FunctionDef) and item.name == "__init__"
            ),
            None,
        )
        assert initializer is not None, f"{node.name} must define __init__"
        assignments = [
            item
            for item in ast.walk(initializer)
            if isinstance(item, ast.Assign)
            and any(
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and target.attr == "room_name"
                for target in item.targets
            )
        ]
        assert assignments, f"{node.name} must set self.room_name"

    assert found == target_classes



def test_device_registry_uses_current_via_device_id_api():
    devices_path = (
        Path(__file__).parents[1]
        / "custom_components"
        / "heating_intelligence"
        / "devices.py"
    )
    tree = ast.parse(devices_path.read_text())
    source = devices_path.read_text()

    assert "via_device=" not in source
    assert "via_device_id=" in source

    # Both DeviceInfo and device-registry creation must use the current API.
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "DeviceInfo")
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "async_get_or_create"
            )
        )
    ]
    via_keywords = [
        keyword.arg
        for call in calls
        for keyword in call.keywords
        if keyword.arg in {"via_device", "via_device_id"}
    ]
    assert via_keywords
    assert "via_device" not in via_keywords
    assert "via_device_id" in via_keywords



def test_device_registry_parent_uses_registered_device_id():
    devices_path = (
        Path(__file__).parents[1]
        / "custom_components"
        / "heating_intelligence"
        / "devices.py"
    )
    source = devices_path.read_text()
    tree = ast.parse(source)

    # DeviceInfo identifiers are not device registry IDs. Parent relationships
    # are created only after the house device exists, using house.id.
    assert "via_device_id=_house_identifier(coordinator)" not in source
    assert "via_device_id=house.id" in source
    assert "via_device=" not in source

    registry_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "async_get_or_create"
    ]
    parent_values = [
        keyword.value for call in registry_calls for keyword in call.keywords
        if keyword.arg == "via_device_id"
    ]
    assert len(parent_values) == 3
    assert all(
        isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id == "house"
        and value.attr == "id"
        for value in parent_values
    )


def test_device_repair_clears_only_config_entry_disabled_devices():
    devices_path = (
        Path(__file__).parents[1]
        / "custom_components"
        / "heating_intelligence"
        / "devices.py"
    )
    source = devices_path.read_text()
    tree = ast.parse(source)
    helper = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_enable_config_entry_disabled_device"
    )
    helper_source = ast.get_source_segment(source, helper)
    assert "dr.DeviceEntryDisabler.CONFIG_ENTRY" in helper_source
    assert "disabled_by=None" in helper_source
    assert "async_update_device" in helper_source
    assert "device.disabled_by" in helper_source
    assert "for device in (house, *room_devices.values(), *pump_devices.values(), audit_device)" in source
