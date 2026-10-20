# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from maritime_simulator.domain.enums import TransportKind, UdpMode
from maritime_simulator.protocols.settings import ProtocolOutputSettings, SensorProtocolSettings


XDR_ENVIRONMENT_PROTOCOLS = (
    "XDR_AIR_TEMPERATURE",
    "XDR_RELATIVE_HUMIDITY",
    "XDR_DEW_POINT",
    "XDR_SURFACE_PRESSURE",
)

PROTOCOL_DISPLAY_NAMES = {
    "XDR_AIR_TEMPERATURE": "XDR Air temperature | ID Air#0",
    "XDR_RELATIVE_HUMIDITY": "XDR Relative humidity | ID Air#1",
    "XDR_DEW_POINT": "XDR Dew point | ID Air#2",
    "XDR_SURFACE_PRESSURE": "XDR Surface pressure | ID Baro#0",
}


def protocol_display_name(protocol: str) -> str:
    return PROTOCOL_DISPLAY_NAMES.get(str(protocol), str(protocol))


SENSOR_PROTOCOLS = {
    "GNSS": ("DTM", "GGA", "GLL", "GNS", "RMC", "VTG", "ZDA"),
    "Gyro": ("HDT", "HDG", "HDM", "ROT", "THS"),
    "Log": ("VHW", "VBW", "VLW"),
    "Depth": ("DBT", "DPT"),
    "Wind": ("MWV", "MWD"),
    "Meteorological": ("MDA", "MTW"),
    "XDR Environment": XDR_ENVIRONMENT_PROTOCOLS,
    "Current": ("VDR",),
    "Rudder": ("RSA", "ROR"),
    "AIS": ("VDM", "VDO"),
    "Additional": ("PRC", "TRC", "TRD"),
}


@dataclass(frozen=True, slots=True)
class OutputPathDefinition:
    path_id: str
    label: str
    transport: TransportKind
    removable: bool = False


DEFAULT_OUTPUT_PATHS = (
    OutputPathDefinition("udp", "UDP", TransportKind.UDP),
    OutputPathDefinition("tcp", "TCP", TransportKind.TCP),
    OutputPathDefinition("serial", "Serial", TransportKind.SERIAL),
)


class ProtocolConfigurationStore:
    """Persist protocol settings while preserving parent pushdown semantics."""

    FORMAT_VERSION = 3

    def __init__(self, path: Path | None = None):
        self.path = path
        self._reset_defaults()
        if path is not None and path.exists():
            self.load()

    def _reset_defaults(self) -> None:
        self._output_paths = {item.path_id: item for item in DEFAULT_OUTPUT_PATHS}
        self._extra_sensors: dict[str, dict[str, SensorProtocolSettings]] = {}
        self.sensors = {
            sensor: SensorProtocolSettings(protocols)
            for sensor, protocols in SENSOR_PROTOCOLS.items()
        }
        # Own-ship VDO is available but opt-in to avoid adding a new AIS identity
        # unexpectedly to existing installations.
        for transport in TransportKind:
            self.sensors["AIS"].update_protocol(
                "VDO",
                output_transport=transport,
                enabled=False,
            )
            for protocol in XDR_ENVIRONMENT_PROTOCOLS:
                self.sensors["XDR Environment"].update_protocol(
                    protocol,
                    output_transport=transport,
                    enabled=False,
                )

    def output_paths(self) -> tuple[OutputPathDefinition, ...]:
        return tuple(self._output_paths.values())

    def output_path(self, path_id: str) -> OutputPathDefinition:
        try:
            return self._output_paths[str(path_id)]
        except KeyError as error:
            raise KeyError(f"Unknown output path: {path_id}") from error

    def default_path_id(self, transport: TransportKind) -> str:
        selected = TransportKind(transport)
        return next(
            item.path_id
            for item in DEFAULT_OUTPUT_PATHS
            if item.transport is selected
        )

    def sensors_for_path(self, path_id: str) -> dict[str, SensorProtocolSettings]:
        definition = self.output_path(path_id)
        if not definition.removable:
            return self.sensors
        return self._extra_sensors[definition.path_id]

    def add_output_path(
        self,
        transport: TransportKind,
        *,
        label: str | None = None,
        save: bool = True,
    ) -> OutputPathDefinition:
        selected = TransportKind(transport)
        index = 2
        while f"{selected.value}-{index}" in self._output_paths:
            index += 1
        path_id = f"{selected.value}-{index}"
        display = label.strip() if label and label.strip() else f"{_transport_label(selected)} {index}"
        definition = OutputPathDefinition(path_id, display, selected, True)
        self._install_extra_path(definition, clone_defaults=True)
        if save:
            self.save()
        return definition

    def remove_output_path(self, path_id: str, *, save: bool = True) -> None:
        definition = self.output_path(path_id)
        if not definition.removable:
            raise ValueError("The primary UDP, TCP and Serial output paths cannot be removed")
        del self._output_paths[definition.path_id]
        self._extra_sensors.pop(definition.path_id, None)
        if save:
            self.save()

    def _install_extra_path(
        self,
        definition: OutputPathDefinition,
        *,
        clone_defaults: bool,
    ) -> None:
        if definition.path_id in self._output_paths:
            raise ValueError(f"Duplicate output path: {definition.path_id}")
        if not definition.path_id or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in definition.path_id):
            raise ValueError("Output path ID contains unsupported characters")
        target = {
            sensor: SensorProtocolSettings(protocols)
            for sensor, protocols in SENSOR_PROTOCOLS.items()
        }
        for sensor, settings in target.items():
            for protocol in settings.protocols():
                if clone_defaults:
                    base = self.sensors[sensor].get_protocol(protocol, definition.transport)
                    values = asdict(base)
                    values["enabled"] = False
                    values["transport"] = definition.transport
                    settings.update_protocol(
                        protocol,
                        output_transport=definition.transport,
                        **values,
                    )
                else:
                    settings.update_protocol(
                        protocol,
                        output_transport=definition.transport,
                        enabled=False,
                    )
        self._output_paths[definition.path_id] = definition
        self._extra_sensors[definition.path_id] = target

    def _resolve_path(
        self,
        output_transport: TransportKind,
        output_path_id: str | None,
    ) -> tuple[OutputPathDefinition, dict[str, SensorProtocolSettings]]:
        selected = TransportKind(output_transport)
        path_id = output_path_id or self.default_path_id(selected)
        definition = self.output_path(path_id)
        if definition.transport is not selected:
            raise ValueError("Output path transport does not match the selected transport")
        return definition, self.sensors_for_path(path_id)

    def update_sensor(
        self,
        sensor: str,
        *,
        output_transport: TransportKind = TransportKind.UDP,
        output_path_id: str | None = None,
        **changes,
    ) -> None:
        definition, sensors = self._resolve_path(output_transport, output_path_id)
        sensors[sensor].update_sensor(output_transport=definition.transport, **changes)
        self.save()

    def update_protocol(
        self,
        protocol: str,
        *,
        output_transport: TransportKind = TransportKind.UDP,
        output_path_id: str | None = None,
        **changes,
    ) -> None:
        sensor = self.sensor_for_protocol(protocol)
        definition, sensors = self._resolve_path(output_transport, output_path_id)
        sensors[sensor].update_protocol(
            protocol,
            output_transport=definition.transport,
            **changes,
        )
        self.save()

    def configured_outputs(self):
        for path in self.output_paths():
            for sensor, settings in self.sensors_for_path(path.path_id).items():
                for protocol in settings.protocols():
                    yield path, sensor, protocol, settings.get_protocol(protocol, path.transport)

    def sensor_for_protocol(self, protocol: str) -> str:
        for sensor, settings in self.sensors.items():
            if protocol in settings.protocols():
                return sensor
        raise KeyError(protocol)

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_payload()
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def to_payload(self) -> dict:
        return {
            "format_version": self.FORMAT_VERSION,
            "output_paths": [
                {
                    "id": path.path_id,
                    "label": path.label,
                    "transport": path.transport.value,
                }
                for path in self.output_paths()
            ],
            "protocols": {
                protocol: {
                    path.path_id: _serialize(
                        self.sensors_for_path(path.path_id)[sensor].get_protocol(protocol, path.transport)
                    )
                    for path in self.output_paths()
                }
                for sensor, settings in self.sensors.items()
                for protocol in settings.protocols()
            },
        }

    def apply_payload(self, payload: dict, *, save: bool = True) -> None:
        version = int(payload.get("format_version", -1))
        if version not in {1, 2, self.FORMAT_VERSION}:
            raise ValueError("Unsupported V2 protocol configuration format")
        self._reset_defaults()
        if version == self.FORMAT_VERSION:
            for raw_path in payload.get("output_paths", ()):
                if not isinstance(raw_path, dict):
                    continue
                path_id = str(raw_path.get("id", "")).strip()
                transport = TransportKind(raw_path.get("transport", TransportKind.UDP.value))
                label = str(raw_path.get("label", "")).strip() or _transport_label(transport)
                if path_id in self._output_paths:
                    if self._output_paths[path_id].transport is not transport:
                        raise ValueError("Primary output path transport cannot be changed")
                    continue
                self._install_extra_path(
                    OutputPathDefinition(path_id, label, transport, True),
                    clone_defaults=False,
                )
        for protocol, raw_protocol in payload.get("protocols", {}).items():
            try:
                sensor = self.sensor_for_protocol(protocol)
            except KeyError:
                continue
            if version == 1:
                self._apply_legacy_protocol(sensor, protocol, raw_protocol)
                continue
            if not isinstance(raw_protocol, dict):
                continue
            if version == 2:
                path_items = (
                    (self.default_path_id(transport), transport, raw_protocol.get(transport.value))
                    for transport in TransportKind
                )
            else:
                path_items = (
                    (path.path_id, path.transport, raw_protocol.get(path.path_id))
                    for path in self.output_paths()
                )
            for path_id, transport, raw in path_items:
                if not isinstance(raw, dict):
                    continue
                values = _deserialize(raw)
                values["transport"] = transport
                self.sensors_for_path(path_id)[sensor].update_protocol(
                    protocol,
                    output_transport=transport,
                    **values,
                )
        if save:
            self.save()

    def _apply_legacy_protocol(self, sensor: str, protocol: str, raw: object) -> None:
        if not isinstance(raw, dict):
            return
        values = _deserialize(raw)
        selected = values.get("transport", TransportKind.UDP)
        values.setdefault("enabled", True)
        for transport in TransportKind:
            self.sensors[sensor].update_protocol(
                protocol,
                output_transport=transport,
                enabled=False,
            )
        self.sensors[sensor].update_protocol(
            protocol,
            output_transport=selected,
            **values,
        )

    def load(self) -> None:
        if self.path is None:
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.apply_payload(payload, save=False)


def _serialize(settings: ProtocolOutputSettings) -> dict:
    values = asdict(settings)
    values["transport"] = settings.transport.value
    values["udp_mode"] = settings.udp_mode.value
    return values


def _deserialize(raw: dict) -> dict:
    values = dict(raw)
    values["transport"] = TransportKind(values.get("transport", TransportKind.UDP.value))
    values["udp_mode"] = UdpMode(values.get("udp_mode", UdpMode.AUTO.value))
    return values


def _transport_label(transport: TransportKind) -> str:
    return "Serial" if transport is TransportKind.SERIAL else transport.value.upper()
