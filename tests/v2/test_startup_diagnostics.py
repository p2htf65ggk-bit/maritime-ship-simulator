# SPDX-License-Identifier: MPL-2.0
from pathlib import Path

from maritime_simulator.application.paths import bundled_resource_path
from maritime_simulator.application.startup import configure_startup_logging


def test_startup_log_records_safe_platform_details_without_credentials(tmp_path):
    diagnostics = configure_startup_logging(tmp_path, "test-version")
    for handler in diagnostics.logger.handlers:
        handler.flush()
    text = diagnostics.log_path.read_text(encoding="utf-8")
    assert "startup version=test-version" in text
    assert "runtime_dependencies=" in text
    assert "client_secret" not in text
    assert "api_key" not in text


def test_source_resource_path_is_workspace_relative():
    path = bundled_resource_path("assets", "app_icon.png")
    assert path.is_file()
    assert path.name == "app_icon.png"
    assert isinstance(path, Path)
