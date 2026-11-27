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
