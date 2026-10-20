# SPDX-License-Identifier: MPL-2.0
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import performance_benchmark_v2 as benchmark
from scripts import prebuild_v2 as prebuild


@pytest.mark.parametrize("fails", [False, True])
def test_ui_benchmark_isolates_configuration_and_closes_before_deletion(monkeypatch, fails):
    from maritime_simulator.ui import main_window

    events = []
    directories = []

    class Timer:
        def stop(self):
            events.append("stop")

    class Window:
        def __init__(self, service, *, config_dir):
            assert config_dir.is_dir()
            directories.append(config_dir)
            self.timer = Timer()
            self.service = service

        def _refresh(self):
            if fails:
                raise RuntimeError("refresh failed")

        def close(self):
            assert directories[0].is_dir()
            events.append("close")
            self.service.close()

        def deleteLater(self):
            events.append("delete")

    monkeypatch.setattr(main_window, "V2MainWindow", Window)
    if fails:
        with pytest.raises(RuntimeError, match="refresh failed"):
            benchmark.benchmark_ui(0.05)
    else:
        result = benchmark.benchmark_ui(0.05)
        assert result["case"] == "ui_headless"
        assert result["refreshes"] > 0
    assert events == ["stop", "close", "delete"]
    assert not directories[0].exists()


def test_failed_performance_gate_still_saves_measurements(tmp_path, monkeypatch):
    def result(*_args):
        return {"case": "test", "metrics": {}}

    for name in ("benchmark_simulation", "benchmark_track", "benchmark_protocols", "benchmark_ui"):
        monkeypatch.setattr(benchmark, name, result)
    monkeypatch.setattr(benchmark, "benchmark_memory", lambda *_: {
        "case": "memory_long_run", "retained_growth_kib": 5000.0,
    })
    output = tmp_path / "performance.json"
    monkeypatch.setattr(sys, "argv", ["benchmark", "--assert-gates", "--output", str(output)])
    with pytest.raises(SystemExit, match="memory retained growth"):
        benchmark.main()
    payload = json.loads(output.read_text())
    assert payload["status"] == "failed"
    assert len(payload["results"]) == 10
    assert "5000.0" in payload["failure"]


def test_partial_performance_results_survive_a_later_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "benchmark_simulation", lambda *args: {"case": f"ship_{args[1]}"})

    def fail(*_):
        raise RuntimeError("Track failed")

    monkeypatch.setattr(benchmark, "benchmark_track", fail)
    output = tmp_path / "performance.json"
    monkeypatch.setattr(sys, "argv", ["benchmark", "--output", str(output)])
    with pytest.raises(RuntimeError, match="Track failed"):
        benchmark.main()
    payload = json.loads(output.read_text())
    assert payload["status"] == "running"
    assert len(payload["results"]) == 2


def test_prebuild_failure_logs_stdout_and_stderr_and_enables_fault_handler(tmp_path):
    log = tmp_path / "performance.log"
    code = "import os, sys; print(os.environ['PYTHONFAULTHANDLER']); print('native details', file=sys.stderr); sys.exit(7)"
    with pytest.raises(SystemExit, match="performance gate failed.*exit code 7"):
        prebuild.run("performance gate", [sys.executable, "-c", code], log_path=log)
    text = log.read_text()
    assert "native details" in text and "1" in text


@pytest.mark.parametrize("platform, returncode, reason", [
    ("linux", -11, "SIGSEGV"),
    ("linux", -9, "out-of-memory kill"),
    ("linux", -123456, "process status -123456"),
    ("win32", -9, "Windows process status -9"),
    ("win32", -11, "Windows process status -11"),
    ("win32", -1073741819, "0xC0000005"),
    ("win32", 3221225477, "0xC0000005"),
    ("win32", 7, "exit code 7"),
])
def test_prebuild_reports_native_process_failure_without_losing_its_status(monkeypatch, platform, returncode, reason):
    monkeypatch.setattr(prebuild.sys, "platform", platform)

    def posix_signal(number):
        # Make the POSIX branch testable even on a Windows host without SIGKILL.
        if number not in (9, 11):
            raise ValueError(number)
        return SimpleNamespace(name="SIGKILL" if number == 9 else "SIGSEGV")

    def windows_signal(number):
        raise AssertionError("Windows status must not be decoded as a POSIX signal")

    monkeypatch.setattr(prebuild.signal, "Signals", windows_signal if platform == "win32" else posix_signal)
    monkeypatch.setattr(prebuild.subprocess, "run", lambda command, **_: subprocess.CompletedProcess(command, returncode))
    with pytest.raises(SystemExit, match=reason) as failure:
        prebuild.run("performance gate", [sys.executable])
    if platform == "win32":
        assert "out-of-memory" not in str(failure.value)
        assert "SIGKILL" not in str(failure.value)


def test_ui_smoke_uses_temporary_settings_and_an_explicit_shutdown(monkeypatch):
    calls = []
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    monkeypatch.setattr(prebuild, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    prebuild.ui_smoke()
    (label, command), kwargs = calls[0]
    assert label == "headless UI smoke"
    assert "config_dir=Path(directory)" in command[-1]
    assert "window.close()" in command[-1]
    assert "QEvent.DeferredDelete" in command[-1]
    assert kwargs["environment"]["QT_QPA_PLATFORM"] == "offscreen"
