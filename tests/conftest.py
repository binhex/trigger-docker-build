"""Shared fixtures for TriggerDockerBuild tests."""

import importlib.util
import logging
import os
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(scope="session")
def tdb_module():
    """Import the TriggerDockerBuild module once per session."""
    spec = importlib.util.spec_from_file_location(
        "tdb", os.path.join(os.path.dirname(__file__), "..", "TriggerDockerBuild.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tdb(tdb_module, tmp_path):
    """Set up the module with required globals for each test."""
    # Create a real but minimal configobj for tests
    import configobj

    config_path = str(tmp_path / "config.ini")

    # Basic config with defaults needed by most functions
    cfg = configobj.ConfigObj(
        config_path,
        list_values=False,
        write_empty_values=True,
        encoding="UTF-8",
        default_encoding="UTF-8",
    )
    cfg["general"] = {
        "log_level": "INFO",
        "target_repo_owner": "github_repo",
        "target_access_token": "test-token-123",
        "schedule_check_mins": 30,
        "last_check": "",
    }
    cfg["monitor_sites"] = {
        "site_list": [],
    }
    cfg["results"] = {}
    cfg["notification"] = {
        "email_to": "test@example.com",
        "email_username": "testuser",
        "email_password": "testpass",
        "kodi_username": "kodi",
        "kodi_password": "kodi",
        "kodi_hostname": "localhost",
        "kodi_port": "80",
        "email_notification": True,
        "kodi_notification": False,
    }

    tdb_module.config_obj = cfg
    tdb_module.config_ini = config_path
    tdb_module.app_log_file = str(tmp_path / "app.log")

    # Set up a logger
    logger = logging.getLogger("test_tdb")
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    tdb_module.app_logger_instance = logger

    # Notification globals
    tdb_module.email_notification = True
    tdb_module.email_to = "test@example.com"
    tdb_module.email_username = "testuser"
    tdb_module.email_password = "testpass"
    tdb_module.kodi_notification = False
    tdb_module.kodi_password = "kodi"
    tdb_module.target_access_token = "test-token-123"

    return tdb_module


@pytest.fixture
def mock_http(monkeypatch):
    """Mock requests.Session to avoid real HTTP calls."""
    mock_session = MagicMock()
    mock_session_instance = MagicMock()
    mock_session.return_value = mock_session_instance

    monkeypatch.setattr("requests.Session", mock_session)
    return mock_session_instance


@pytest.fixture
def mock_yagmail(tdb, monkeypatch):
    """Mock yagmail.SMTP to avoid real email sending."""
    mock_smtp = MagicMock()
    monkeypatch.setattr("yagmail.SMTP", mock_smtp)
    return mock_smtp


@pytest.fixture(autouse=True)
def mock_time_sleep(monkeypatch):
    """Globally mock time.sleep to avoid retry-loop delays in tests."""
    import time

    mock_sleep = MagicMock()
    monkeypatch.setattr(time, "sleep", mock_sleep)
    return mock_sleep


@pytest.fixture
def sample_datetime():
    """Provide datetime for time-based tests."""
    import datetime

    return datetime
