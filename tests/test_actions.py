"""Action policy."""

import pytest

from custom_components.custom_device_page_components.actions import action_level


@pytest.mark.parametrize("action", [
    "homeassistant.restart", "homeassistant.stop", "hassio.host_reboot", "hassio.app_uninstall",
    "backup.create", "recorder.purge", "recorder.purge_entities", "shell_command.anything",
    "python_script.x", "zha.remove", "zha.permit", "zwave_js.set_config_parameter",
    "update.install", "lock.clear_usercode", "todo.remove_item", "calendar.delete_event",
    "counter.reset", "esphome.factory_reset", "light", "",
])
def test_blocked(action):
    assert action_level(action) == "blocked"


@pytest.mark.parametrize("action", ["lock.unlock", "alarm_control_panel.alarm_disarm", "cover.open_cover",
                                    "button.press", "switch.turn_off", "automation.turn_off"])
def test_confirm(action):
    assert action_level(action) == "confirm"


@pytest.mark.parametrize("action", ["light.turn_on", "light.toggle", "switch.turn_on", "media_player.media_pause",
                                    "number.set_value", "input_select.select_option"])
def test_allowed(action):
    assert action_level(action) == "allowed"
