# SPDX-License-Identifier: MPL-2.0
"""Maritime Ship Simulator V2 application package."""

from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.enums import ControlMode

__all__ = ["ControlMode", "SimulatorService"]

__version__ = "2.3.1-beta.hud.1"
