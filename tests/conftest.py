"""Fixtures."""

import traceback

import pytest
from homeassistant.helpers import device_registry as dr

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations."""
    yield


@pytest.fixture(autouse=True)
def no_deprecated_device_registry_access(monkeypatch):
    """Fail any test that uses ``DeviceRegistry.devices`` as a mapping.

    Home Assistant 2026.10 only logs this for custom integrations (and stops
    supporting it in 2027.9), so it would otherwise pass unnoticed.
    """
    view = getattr(dr, "_DeprecatedDeviceRegistryItemsView", None)
    if view is None:  # releases before the deprecation
        yield
        return
    calls: list[str] = []
    original = view._report_deprecated_use

    def record(self):
        calls.append("".join(traceback.format_stack(limit=8)))
        original(self)

    monkeypatch.setattr(view, "_report_deprecated_use", record)
    yield
    assert not calls, f"deprecated device_registry.devices access:\n{calls[0]}"
