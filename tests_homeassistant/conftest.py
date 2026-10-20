"""Skutečné fixture HA; vlastní integraci načítáme z tohoto repozitáře."""

import pytest


@pytest.fixture
def mock_recorder_before_hass(request):
    """Připravit testovací databázi před vytvořením HA pro test registrů.

    Recorder je deklarovaná závislost integrace, proto jej potřebuje také
    načtení ConfigFlow. Fixture dodává testovací plugin HA; žádná data ani
    spojení provozního Home Assistantu se zde nepoužívají.
    """
    if "recorder_mock" in request.fixturenames:
        request.getfixturevalue("recorder_db_url")


@pytest.fixture(autouse=True)
def enable_watermeter(enable_custom_integrations, recorder_mock):
    """Povolit skutečný ConfigFlow a platformy lokální vlastní integrace."""
    yield
