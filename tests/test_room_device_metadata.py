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
