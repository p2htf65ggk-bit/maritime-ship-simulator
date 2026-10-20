# SPDX-License-Identifier: MPL-2.0
from dataclasses import replace

import pytest

from maritime_simulator.application.fault_injection import FaultInjectionController
from maritime_simulator.application.service import SimulatorService
from maritime_simulator.protocols.iec61162 import IEC61162Codec, encode_protocol
from maritime_simulator.protocols.sensors import SensorProjector


def test_sensor_fault_changes_projection_and_display_but_not_vessel_truth():
    service = SimulatorService()
    truth = replace(service.snapshot(), heading_deg=42.0)
    faults = FaultInjectionController()
    faults.set_active("heading_bias", True)

    projected = SensorProjector(faults).project(truth)
    displayed = faults.display_snapshot(truth, projected)

    assert truth.heading_deg == pytest.approx(42.0)
    assert projected.heading_deg == pytest.approx(52.0)
    assert displayed.heading_deg == pytest.approx(52.0)
    service.close()


def test_gnss_jamming_reports_invalid_protocol_status_without_gui_interaction():
    service = SimulatorService()
    faults = FaultInjectionController()
    faults.set_active("gnss_jamming", True)
    sensor = SensorProjector(faults).project(service.snapshot())

    gga = IEC61162Codec.decode(encode_protocol("GGA", sensor))
    rmc = IEC61162Codec.decode(encode_protocol("RMC", sensor))
    assert sensor.gnss_valid is False
    assert gga.fields[5] == "0"
    assert rmc.fields[1] == "V"
    service.close()


def test_saved_fault_selection_never_activates_automatically():
    faults = FaultInjectionController()
    faults.apply_state({"configured_faults": ["gnss_jamming", "packet_loss"]})
    assert faults.configured_faults() == ("gnss_jamming", "packet_loss")
    assert faults.active_faults() == ()

    faults.activate_configured()
    assert faults.active_faults() == ("gnss_jamming", "packet_loss")
    faults.clear()
    assert faults.active_faults() == ()
    assert faults.configured_faults() == ("gnss_jamming", "packet_loss")


def test_transmission_faults_drop_and_corrupt_at_deterministic_intervals():
    faults = FaultInjectionController()
    faults.set_active("packet_loss", True)
    outcomes = [faults.transform_sentence("HDT", "$HEHDT,10.0,T*00") for _ in range(5)]
    assert outcomes[:4] == ["$HEHDT,10.0,T*00"] * 4
    assert outcomes[4] is None

    faults.clear()
    faults.set_active("checksum_error", True)
    outcomes = [faults.transform_sentence("HDT", "$HEHDT,10.0,T*AA") for _ in range(10)]
    assert outcomes[-1] != "$HEHDT,10.0,T*AA"
