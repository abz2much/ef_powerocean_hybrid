"""Setting up a system with Modbus, the cloud, or both, and signing in again."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid import config_flow, const
from custom_components.ef_powerocean_hybrid.cloud import coordinator as cloud_module

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

SERIAL = "HJ31ZAS2TEST0001"
SINGLE = "powerocean_single_phase"
LOGIN = {CONF_EMAIL: "owner@example.com", CONF_PASSWORD: "secret"}


@pytest.fixture
def device_settings() -> Iterator[AsyncMock]:
    with patch.object(
        config_flow,
        "async_read_device_settings",
        new=AsyncMock(return_value={config_flow.DEVICE_SERIAL_NUMBER: SERIAL}),
    ) as read:
        yield read


@pytest.fixture
def cloud_check() -> Iterator[AsyncMock]:
    """The cloud login check; passes unless a test says otherwise."""
    with (
        patch.object(
            config_flow, "async_check_cloud", new=AsyncMock(return_value=None)
        ) as check,
        patch.object(cloud_module, "create_api_for", new=Mock()),
    ):
        yield check


@pytest.fixture(autouse=True)
def no_setup() -> Iterator[None]:
    with patch(
        "custom_components.ef_powerocean_hybrid.async_setup_entry", return_value=True
    ):
        yield


async def _menu(hass: HomeAssistant) -> Any:
    return await hass.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def _to_parameters(hass: HomeAssistant) -> Any:
    result = await _menu(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "modbus"}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_HOST: "192.0.2.10", const.CONF_PORT: 502}
    )


async def test_the_first_step_offers_modbus_or_the_cloud(hass: HomeAssistant) -> None:
    result = await _menu(hass)

    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["modbus", "cloud"]


@pytest.mark.usefixtures("device_settings", "cloud_check")
async def test_a_single_phase_is_offered_the_cloud(hass: HomeAssistant) -> None:
    result = await _to_parameters(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_INVERTER_MODEL: SINGLE}
    )

    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["cloud_login", "finish"]


@pytest.mark.usefixtures("device_settings", "cloud_check")
async def test_a_single_phase_with_the_cloud_keeps_both(hass: HomeAssistant) -> None:
    result = await _to_parameters(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_INVERTER_MODEL: SINGLE}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud_login"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == SERIAL
    assert result["data"] == {
        const.CONF_HOST: "192.0.2.10",
        const.CONF_PORT: 502,
        **LOGIN,
        const.CONF_CLOUD_SERIAL: SERIAL,
    }


@pytest.mark.usefixtures("device_settings", "cloud_check")
async def test_a_single_phase_can_go_without_the_cloud(hass: HomeAssistant) -> None:
    result = await _to_parameters(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_INVERTER_MODEL: SINGLE}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {const.CONF_HOST: "192.0.2.10", const.CONF_PORT: 502}
    assert result["options"][const.CONF_INVERTER_MODEL] == SINGLE


@pytest.mark.usefixtures("device_settings", "cloud_check")
async def test_other_models_are_not_offered_the_cloud(hass: HomeAssistant) -> None:
    result = await _to_parameters(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_INVERTER_MODEL: "powerocean_three_phase"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_EMAIL not in result["data"]


@pytest.mark.usefixtures("cloud_check")
async def test_an_inverter_reporting_three_phase_cannot_be_set_up_as_single_phase(
    hass: HomeAssistant, device_settings: AsyncMock
) -> None:
    device_settings.return_value = {
        config_flow.DEVICE_SERIAL_NUMBER: SERIAL,
        const.CONF_INVERTER_MODEL: "powerocean_three_phase",
    }
    result = await _to_parameters(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_INVERTER_MODEL: SINGLE}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "model_mismatch"}


@pytest.mark.usefixtures("cloud_check")
async def test_an_ocean_2_single_phase_reading_is_accepted_as_single_phase(
    hass: HomeAssistant, device_settings: AsyncMock
) -> None:
    """The product registers cannot tell the two single phases apart."""
    device_settings.return_value = {
        config_flow.DEVICE_SERIAL_NUMBER: SERIAL,
        const.CONF_INVERTER_MODEL: "ocean_2_single_phase",
    }
    result = await _to_parameters(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_INVERTER_MODEL: SINGLE}
    )

    assert result["type"] is FlowResultType.MENU


@pytest.mark.usefixtures("device_settings")
@pytest.mark.parametrize(
    "error", ("invalid_auth", "cloud_cannot_connect", "cloud_unknown_device")
)
async def test_a_failed_cloud_login_is_shown(
    hass: HomeAssistant, cloud_check: AsyncMock, error: str
) -> None:
    cloud_check.return_value = error
    result = await _to_parameters(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_INVERTER_MODEL: SINGLE}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud_login"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


@pytest.mark.usefixtures("cloud_check")
async def test_a_cloud_only_system(hass: HomeAssistant) -> None:
    result = await _menu(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud"}
    )
    assert result["step_id"] == "cloud"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**LOGIN, const.CONF_CLOUD_SERIAL: SERIAL, const.CONF_INVERTER_MODEL: SINGLE},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == SERIAL
    assert result["data"] == {**LOGIN, const.CONF_CLOUD_SERIAL: SERIAL}
    assert result["options"] == {const.CONF_INVERTER_MODEL: SINGLE}


@pytest.mark.usefixtures("cloud_check")
async def test_the_cloud_only_form_offers_only_the_single_phase(
    hass: HomeAssistant,
) -> None:
    result = await _menu(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud"}
    )

    schema = result["data_schema"]
    assert schema is not None
    selector = dict(schema.schema)[const.CONF_INVERTER_MODEL]
    assert selector.config["options"] == [SINGLE]


@pytest.mark.usefixtures("cloud_check")
async def test_a_cloud_only_system_already_set_up_is_refused(
    hass: HomeAssistant,
) -> None:
    MockConfigEntry(domain=const.DOMAIN, unique_id=SERIAL).add_to_hass(hass)
    result = await _menu(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**LOGIN, const.CONF_CLOUD_SERIAL: SERIAL, const.CONF_INVERTER_MODEL: SINGLE},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("cloud_check")
async def test_a_cloud_only_login_failure_is_shown(
    hass: HomeAssistant, cloud_check: AsyncMock
) -> None:
    cloud_check.return_value = "invalid_auth"
    result = await _menu(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**LOGIN, const.CONF_CLOUD_SERIAL: SERIAL, const.CONF_INVERTER_MODEL: SINGLE},
    )

    assert result["errors"] == {"base": "invalid_auth"}


def _cloud_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        title="EcoFlow PowerOcean (cloud)",
        unique_id=SERIAL,
        data={**LOGIN, const.CONF_CLOUD_SERIAL: SERIAL},
        options={const.CONF_INVERTER_MODEL: SINGLE},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.usefixtures("cloud_check")
async def test_signing_in_again_saves_the_new_password(hass: HomeAssistant) -> None:
    entry = _cloud_entry(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new secret"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new secret"
    assert entry.data[CONF_EMAIL] == "owner@example.com"


async def test_signing_in_again_with_a_wrong_password_shows_an_error(
    hass: HomeAssistant, cloud_check: AsyncMock
) -> None:
    cloud_check.return_value = "invalid_auth"
    entry = _cloud_entry(hass)
    result = await entry.start_reauth_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "still wrong"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_PASSWORD] == "secret"


# Review fix 3: never match the cloud to an address instead of a serial.


@pytest.mark.usefixtures("cloud_check")
async def test_the_cloud_is_refused_when_the_serial_cannot_be_read(
    hass: HomeAssistant, device_settings: AsyncMock, cloud_check: AsyncMock
) -> None:
    device_settings.return_value = {}
    result = await _to_parameters(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_INVERTER_MODEL: SINGLE}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud_login"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "serial_unreadable"}
    cloud_check.assert_not_awaited()


# Review fix 2: the options of entries with the cloud.


def _hybrid_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        unique_id=SERIAL,
        data={
            const.CONF_HOST: "192.0.2.10",
            const.CONF_PORT: 502,
            **LOGIN,
            const.CONF_CLOUD_SERIAL: SERIAL,
        },
        options={const.CONF_INVERTER_MODEL: SINGLE},
    )
    entry.add_to_hass(hass)
    return entry


async def test_a_cloud_only_entry_has_no_settings_yet(hass: HomeAssistant) -> None:
    entry = _cloud_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_settings"


async def test_an_entry_with_the_cloud_keeps_the_single_phase_model(
    hass: HomeAssistant,
) -> None:
    entry = _hybrid_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    schema = result["data_schema"]
    assert schema is not None
    selector = dict(schema.schema)[const.CONF_INVERTER_MODEL]
    assert selector.config["options"] == [SINGLE]


# Review fix 1: reconfigure adds Modbus to a cloud-only entry.


async def _reconfigure(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    result = await entry.start_reconfigure_flow(hass)
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_HOST: "192.0.2.10", const.CONF_PORT: 502}
    )


async def test_reconfigure_shows_its_form_on_a_cloud_only_entry(
    hass: HomeAssistant,
) -> None:
    entry = _cloud_entry(hass)

    result = await entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"


@pytest.mark.usefixtures("device_settings")
async def test_reconfigure_adds_modbus_to_a_cloud_only_entry(
    hass: HomeAssistant,
) -> None:
    entry = _cloud_entry(hass)

    result = await _reconfigure(hass, entry)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {
        **LOGIN,
        const.CONF_CLOUD_SERIAL: SERIAL,
        const.CONF_HOST: "192.0.2.10",
        const.CONF_PORT: 502,
    }
    assert entry.title == "EcoFlow PowerOcean (192.0.2.10)"


async def test_reconfigure_refuses_another_inverter_for_a_cloud_entry(
    hass: HomeAssistant, device_settings: AsyncMock
) -> None:
    device_settings.return_value = {
        config_flow.DEVICE_SERIAL_NUMBER: "HJ31ZAS2TEST0009"
    }
    entry = _cloud_entry(hass)

    result = await _reconfigure(hass, entry)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert const.CONF_HOST not in entry.data


async def test_reconfigure_refuses_an_unreadable_serial_for_a_cloud_entry(
    hass: HomeAssistant, device_settings: AsyncMock
) -> None:
    device_settings.return_value = {}
    entry = _cloud_entry(hass)

    result = await _reconfigure(hass, entry)

    assert result["errors"] == {"base": "serial_unreadable"}
    assert const.CONF_HOST not in entry.data


async def test_reconfigure_refuses_a_three_phase_reading_for_a_cloud_entry(
    hass: HomeAssistant, device_settings: AsyncMock
) -> None:
    device_settings.return_value = {
        config_flow.DEVICE_SERIAL_NUMBER: SERIAL,
        const.CONF_INVERTER_MODEL: "powerocean_three_phase",
    }
    entry = _cloud_entry(hass)

    result = await _reconfigure(hass, entry)

    assert result["errors"] == {"base": "model_mismatch"}


@pytest.mark.usefixtures("device_settings")
async def test_the_modbus_path_sends_a_cloud_only_inverter_to_reconfigure(
    hass: HomeAssistant,
) -> None:
    entry = _cloud_entry(hass)

    result = await _to_parameters(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "use_reconfigure"
    assert const.CONF_HOST not in entry.data


# Reconfigure adds the cloud to a Modbus-only PowerOcean Single Phase.


def _modbus_entry(hass: HomeAssistant, model: str = SINGLE) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        title="EcoFlow PowerOcean (192.0.2.10)",
        unique_id=SERIAL,
        data={const.CONF_HOST: "192.0.2.10", const.CONF_PORT: 502},
        options={const.CONF_INVERTER_MODEL: model},
    )
    entry.add_to_hass(hass)
    return entry


async def _add_cloud(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "reconfigure_cloud"}
    )
    return await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)


async def test_reconfigure_offers_the_cloud_to_a_modbus_single_phase(
    hass: HomeAssistant,
) -> None:
    entry = _modbus_entry(hass)

    result = await entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["reconfigure_address", "reconfigure_cloud"]


@pytest.mark.parametrize("model", ("powerocean_three_phase", "ocean_2_single_phase"))
async def test_reconfigure_of_other_models_shows_only_the_address(
    hass: HomeAssistant, model: str
) -> None:
    entry = _modbus_entry(hass, model)

    result = await entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"


async def test_reconfigure_of_an_entry_with_the_cloud_shows_only_the_address(
    hass: HomeAssistant,
) -> None:
    entry = _hybrid_entry(hass)

    result = await entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.FORM


@pytest.mark.usefixtures("device_settings")
async def test_reconfigure_can_still_move_the_inverter(hass: HomeAssistant) -> None:
    entry = _modbus_entry(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "reconfigure_address"}
    )
    assert result["step_id"] == "reconfigure_address"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_HOST: "192.0.2.20", const.CONF_PORT: 502}
    )

    assert result["reason"] == "reconfigure_successful"
    assert entry.data[const.CONF_HOST] == "192.0.2.20"


@pytest.mark.usefixtures("device_settings", "cloud_check")
async def test_adding_the_cloud_saves_the_login(hass: HomeAssistant) -> None:
    entry = _modbus_entry(hass)

    result = await _add_cloud(hass, entry)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {
        const.CONF_HOST: "192.0.2.10",
        const.CONF_PORT: 502,
        **LOGIN,
        const.CONF_CLOUD_SERIAL: SERIAL,
    }


@pytest.mark.usefixtures("device_settings")
async def test_adding_the_cloud_shows_a_failed_login(
    hass: HomeAssistant, cloud_check: AsyncMock
) -> None:
    cloud_check.return_value = "invalid_auth"
    entry = _modbus_entry(hass)

    result = await _add_cloud(hass, entry)

    assert result["errors"] == {"base": "invalid_auth"}
    assert CONF_EMAIL not in entry.data


@pytest.mark.usefixtures("cloud_check")
@pytest.mark.parametrize(
    ("settings", "error"),
    (
        (None, "cannot_connect"),
        ({}, "serial_unreadable"),
        (
            {
                config_flow.DEVICE_SERIAL_NUMBER: SERIAL,
                const.CONF_INVERTER_MODEL: "powerocean_three_phase",
            },
            "model_mismatch",
        ),
    ),
)
async def test_adding_the_cloud_checks_the_inverter(
    hass: HomeAssistant,
    device_settings: AsyncMock,
    cloud_check: AsyncMock,
    settings: dict[str, str] | None,
    error: str,
) -> None:
    device_settings.return_value = settings
    entry = _modbus_entry(hass)

    result = await _add_cloud(hass, entry)

    assert result["errors"] == {"base": error}
    cloud_check.assert_not_awaited()


@pytest.mark.usefixtures("cloud_check")
async def test_adding_the_cloud_refuses_another_inverter(
    hass: HomeAssistant, device_settings: AsyncMock
) -> None:
    device_settings.return_value = {
        config_flow.DEVICE_SERIAL_NUMBER: "HJ31ZAS2TEST0009"
    }
    entry = _modbus_entry(hass)

    result = await _add_cloud(hass, entry)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert CONF_EMAIL not in entry.data
