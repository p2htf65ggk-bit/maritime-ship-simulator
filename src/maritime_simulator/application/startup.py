# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import platform
import sys
import tempfile
import threading

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import qVersion
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True, slots=True)
class StartupDiagnostics:
    logger: logging.Logger
    log_path: Path


def configure_startup_logging(config_dir: Path, version: str) -> StartupDiagnostics:
    """Create a small rotating startup log without recording configuration values."""
    preferred = config_dir / "logs"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        log_path = preferred / "startup.log"
        handler = RotatingFileHandler(log_path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "maritime-ship-simulator-v2"
        fallback.mkdir(parents=True, exist_ok=True)
        log_path = fallback / "startup.log"
        handler = RotatingFileHandler(log_path, maxBytes=512 * 1024, backupCount=1, encoding="utf-8")

    logger = logging.getLogger("maritime_simulator.startup")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.info("startup version=%s", version)
    logger.info("platform=%s os_release=%s architecture=%s", sys.platform, platform.release(), platform.machine())
    logger.info("python=%s bundled=%s", platform.python_version(), bool(getattr(sys, "frozen", False)))
    logger.info("pyside=%s qt=%s", pyside_version, qVersion())
    logger.info("executable=%s", Path(sys.executable).resolve())
    logger.info("startup_directory=%s", Path.cwd().resolve())
    logger.info("runtime_dependencies=Python/PySide6/Qt bundled; Git/Bash/winget not required")
    return StartupDiagnostics(logger, log_path)


def install_exception_logging(diagnostics: StartupDiagnostics) -> None:
    handling = False

    def report_exception(exc_type, exc_value, exc_traceback):
        nonlocal handling
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        diagnostics.logger.critical(
            "unhandled application exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        if handling:
            return
        handling = True
        try:
            app = QApplication.instance()
            if app is not None and app.platformName().lower() != "offscreen":
                from maritime_simulator.ui.dialogs import show_critical

                show_critical(
                    None,
                    "Maritime Ship Simulator V2",
                    "The application encountered an unexpected problem and could not continue.\n\n"
                    f"Diagnostic log:\n{diagnostics.log_path}",
                )
        finally:
            handling = False

    def report_thread_exception(args):
        diagnostics.logger.critical(
            "unhandled background exception in %s",
            getattr(args.thread, "name", "unknown"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = report_exception
    threading.excepthook = report_thread_exception


def show_startup_failure(diagnostics: StartupDiagnostics, error: BaseException) -> None:
    diagnostics.logger.critical(
        "fatal startup failure",
        exc_info=(type(error), error, error.__traceback__),
    )
    app = QApplication.instance()
    if app is not None and app.platformName().lower() != "offscreen":
        from maritime_simulator.ui.dialogs import show_critical

        show_critical(
            None,
            "Maritime Ship Simulator V2 could not start",
            "A required packaged component or application resource could not be initialized.\n\n"
            f"Diagnostic log:\n{diagnostics.log_path}",
        )
