"""Compatibility helpers."""

from types import SimpleNamespace

from custom_components.custom_device_page_components.compat import all_devices


def test_all_devices_old_mapping_registry():
    """Releases before 2026.10: `devices` is a mapping that iterates ids."""
    a, b = SimpleNamespace(id="a"), SimpleNamespace(id="b")
    devices = {"a": a, "b": b}
    reg = SimpleNamespace(devices=devices, async_get=devices.get)
    assert all_devices(reg) == [a, b]


def test_all_devices_entry_view():
    """2026.10+: iterating `devices` yields the entries themselves."""
    a = SimpleNamespace(id="a")
    reg = SimpleNamespace(devices=[a], async_get=lambda _id: None)
    assert all_devices(reg) == [a]


def test_all_devices_empty():
    assert all_devices(SimpleNamespace(devices={}, async_get=lambda _id: None)) == []
