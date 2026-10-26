# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path

from PySide6.QtCore import QTime, QTimer, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from maritime_simulator.application.startup_profile import (
    STARTUP_GROUP_LABELS,
    STARTUP_GROUPS,
    StartupProfileStore,
)

from .controls import format_coordinate
from .dialogs import ensure_text_button_fits, show_warning


class StartupProfilePanel(QWidget):
    """Operator-facing Fixed/Last-saved startup policy editor."""

    _COLUMNS = (
        "Group",
        "Startup Source",
        "Current",
        "Last Saved",
        "Fixed",
        "Save Current",
    )

    def __init__(
        self,
        store: StartupProfileStore,
        current_configuration: Callable[[], dict],
        last_saved_changed: Callable[[dict], None],
        save_configuration: Callable[[], None] | None = None,
        load_configuration: Callable[[], None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.store = store
        self.current_configuration = current_configuration
        self.last_saved_changed = last_saved_changed
        self.save_configuration = save_configuration
        self.load_configuration = load_configuration
        self._updating = False
        self._last_refresh_signature = ""
        self._modes: dict[str, QComboBox] = {}
        self._items: dict[str, dict[str, QTableWidgetItem]] = {}

        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Choose the source restored for each group at application startup. Last Saved is updated only "
            "by Close and Save or the explicit save actions below. Fixed remains unchanged until captured again."
        )
        explanation.setObjectName("secondaryText")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        distinction = QLabel(
            "Configuration files apply a complete operating snapshot immediately. A Startup Setup controls "
            "which saved values are restored on future launches."
        )
        distinction.setObjectName("secondaryText")
        distinction.setWordWrap(True)
        layout.addWidget(distinction)

        self.table = QTableWidget(len(STARTUP_GROUPS), len(self._COLUMNS))
        self.table.setObjectName("startupProfileTable")
        self.table.setHorizontalHeaderLabels(self._COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setShowGrid(True)
        self.table.setMinimumHeight(420)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)

        for row, group in enumerate(STARTUP_GROUPS):
            self.table.setItem(
                row, 0, self._read_only_item(STARTUP_GROUP_LABELS[group])
            )

            mode = QComboBox()
            mode.addItem("Last Saved", "last")
            mode.addItem("Fixed", "fixed")
            mode.currentIndexChanged.connect(
                lambda _index, selected=group: self._mode_changed(selected)
            )
            self.table.setCellWidget(row, 1, mode)
            self._modes[group] = mode

            row_items: dict[str, QTableWidgetItem] = {}
            for column, key in ((2, "current"), (3, "last"), (4, "fixed")):
                item = self._read_only_item("")
                self.table.setItem(row, column, item)
                row_items[key] = item
            self._items[group] = row_items

            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(4)
            save_last = QPushButton("Last")
            ensure_text_button_fits(save_last, horizontal_padding=18, vertical_padding=12)
            save_last.setToolTip(
                f"Save the current {STARTUP_GROUP_LABELS[group]} values as Last Saved."
            )
            save_last.clicked.connect(
                lambda _checked=False, selected=group: self._capture_last_saved(
                    selected
                )
            )
            save_fixed = QPushButton("Fixed")
            ensure_text_button_fits(save_fixed, horizontal_padding=18, vertical_padding=12)
            save_fixed.setToolTip(
                f"Save the current {STARTUP_GROUP_LABELS[group]} values as Fixed."
            )
            save_fixed.clicked.connect(
                lambda _checked=False, selected=group: self._capture_fixed(selected)
            )
            action_layout.addWidget(save_last)
            action_layout.addWidget(save_fixed)
            self.table.setCellWidget(row, 5, actions)

        self.table.resizeRowsToContents()
        layout.addWidget(self.table, 1)

        self.action_grid = QGridLayout()
        self.action_grid.setHorizontalSpacing(12)
        self.action_grid.setVerticalSpacing(8)
        self.action_buttons: list[QPushButton] = []
        use_all_last = QPushButton("Use Last Saved for All")
        use_all_last.clicked.connect(lambda: self._set_all_modes("last"))
        use_all_fixed = QPushButton("Use Fixed for All")
        use_all_fixed.clicked.connect(lambda: self._set_all_modes("fixed"))
        save_all_last = QPushButton("Save All as Last Saved")
        save_all_last.setToolTip(
            "Replace the complete Last Saved startup snapshot without changing the selected startup sources."
        )
        save_all_last.clicked.connect(self._capture_all_last_saved)
        save_all_fixed = QPushButton("Save All as Fixed")
        save_all_fixed.setToolTip(
            "Replace every Fixed snapshot without changing the selected startup sources."
        )
        save_all_fixed.clicked.connect(self._capture_all_fixed)
        export_button = QPushButton("Export Startup Setup...")
        export_button.setToolTip(
            "Export startup-source choices plus Last Saved and Fixed snapshots as one portable file."
        )
        export_button.clicked.connect(self._export_profile)
        import_button = QPushButton("Import Startup Setup...")
        import_button.setToolTip(
            "Import startup-source choices plus Last Saved and Fixed snapshots for future launches."
        )
        import_button.clicked.connect(self._import_profile)
        save_config = QPushButton("Save Configuration As...")
        save_config.setToolTip(
            "Save route, I/O, AIS, instruments and simulator settings to one portable JSON file."
        )
        save_config.setEnabled(self.save_configuration is not None)
        save_config.clicked.connect(
            lambda: self.save_configuration and self.save_configuration()
        )
        load_config = QPushButton("Load Configuration...")
        load_config.setToolTip("Load a complete Maritime Ship Simulator V2 configuration file.")
        load_config.setEnabled(self.load_configuration is not None)
        load_config.clicked.connect(
            lambda: self.load_configuration and self.load_configuration()
        )
        action_rows = (
            (use_all_last, use_all_fixed, save_all_last, save_all_fixed),
            (export_button, import_button, save_config, load_config),
        )
        for row, buttons in enumerate(action_rows):
            for column, button in enumerate(buttons):
                ensure_text_button_fits(button)
                button.setMinimumHeight(max(36, button.minimumHeight()))
                button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                self.action_grid.addWidget(button, row, column)
                self.action_buttons.append(button)
        common_button_width = max(
            button.minimumWidth() for button in self.action_buttons
        )
        for button in self.action_buttons:
            button.setMinimumWidth(common_button_width)
        for column in range(4):
            self.action_grid.setColumnStretch(column, 1)
        layout.addLayout(self.action_grid)

        self.feedback = QLabel("")
        self.feedback.setObjectName("secondaryText")
        layout.addWidget(self.feedback)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(750)
        self.refresh_timer.timeout.connect(self.refresh_if_changed)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self.refresh_timer.start()

    def hideEvent(self, event) -> None:
        self.refresh_timer.stop()
        super().hideEvent(event)

    @staticmethod
    def _read_only_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemIsEnabled)
        return item

    def refresh(self) -> None:
        self._updating = True
        try:
            current_configuration = self.current_configuration()
            for group in STARTUP_GROUPS:
                mode = self.store.mode(group)
                combo = self._modes[group]
                combo.setCurrentIndex(max(0, combo.findData(mode)))

                payloads = {
                    "last": self.store.last_saved_group(group),
                    "current": self.store.group_fragment(group, current_configuration),
                    "fixed": self.store.fixed_group(group),
                }
                for key, payload in payloads.items():
                    item = self._items[group][key]
                    item.setText(_summary(group, payload) if payload else "Not saved")
                    item.setToolTip(
                        _details(group, payload)
                        if payload
                        else "No snapshot has been saved."
                    )
                    font = item.font()
                    font.setBold(key == mode)
                    item.setFont(font)
                    if key == mode:
                        item.setForeground(QBrush(QColor("#edf6fb")))
                        item.setBackground(QBrush(QColor("#294558")))
                    elif key == "current" and payload != payloads.get(mode):
                        item.setForeground(QBrush(QColor("#e1bd62")))
                        item.setBackground(QBrush())
                        item.setToolTip(
                            item.toolTip()
                            + "\n\n" + _differences(payload, payloads.get(mode) or {})
                        )
                    else:
                        item.setForeground(QBrush(QColor("#9eb0bd")))
                        item.setBackground(QBrush())
            self._last_refresh_signature = self._refresh_signature(
                current_configuration
            )
        finally:
            self._updating = False

    def refresh_if_changed(self) -> None:
        if self._updating:
            return
        current = self.current_configuration()
        if self._refresh_signature(current) != self._last_refresh_signature:
            self.refresh()

    def _refresh_signature(self, current: dict) -> str:
        return json.dumps(
            {"current": current, "startup": self.store.to_payload()},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    def _mode_changed(self, group: str) -> None:
        if self._updating:
            return
        try:
            self.store.set_mode(
                group,
                str(self._modes[group].currentData()),
                self.current_configuration(),
            )
        except (OSError, TypeError, ValueError) as error:
            show_warning(self, "Startup setting could not be changed", str(error))
            self.refresh()
            return
        self._saved_feedback("Startup source")
        self.refresh()

    def _capture_fixed(self, group: str) -> None:
        try:
            self.store.capture_fixed(group, self.current_configuration())
        except (OSError, TypeError, ValueError) as error:
            show_warning(self, "Fixed startup setting could not be saved", str(error))
            return
        self._saved_feedback(f"{STARTUP_GROUP_LABELS[group]} Fixed")
        self.refresh()

    def _capture_last_saved(self, group: str) -> None:
        try:
            self.store.capture_last_saved(group, self.current_configuration())
            self._notify_last_saved_changed()
        except (OSError, TypeError, ValueError) as error:
            show_warning(
                self, "Last Saved startup setting could not be saved", str(error)
            )
            return
        self._saved_feedback(f"{STARTUP_GROUP_LABELS[group]} Last Saved")
        self.refresh()

    def _capture_all_fixed(self) -> None:
        try:
            self.store.capture_all_fixed(self.current_configuration())
        except (OSError, TypeError, ValueError) as error:
            show_warning(self, "Fixed startup settings could not be saved", str(error))
            return
        self._saved_feedback("All Fixed snapshots")
        self.refresh()

    def _capture_all_last_saved(self) -> None:
        try:
            self.store.capture_all_last_saved(self.current_configuration())
            self._notify_last_saved_changed()
        except (OSError, TypeError, ValueError) as error:
            show_warning(
                self, "Last Saved startup settings could not be saved", str(error)
            )
            return
        self._saved_feedback("Complete Last Saved snapshot")
        self.refresh()

    def _set_all_modes(self, mode: str) -> None:
        try:
            self.store.set_all_modes(mode, self.current_configuration())
        except (OSError, TypeError, ValueError) as error:
            show_warning(self, "Startup sources could not be changed", str(error))
            return
        self._saved_feedback("All startup sources")
        self.refresh()

    def _notify_last_saved_changed(self) -> None:
        payload = self.store.last_saved_configuration
        if isinstance(payload, dict):
            self.last_saved_changed(payload)

    def _saved_feedback(self, subject: str) -> None:
        self.feedback.setText(
            f"{subject} saved at {QTime.currentTime().toString('HH:mm:ss')}"
        )

    def _export_profile(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "Export startup setup",
            str(Path.home() / "Maritime-Ship-Simulator-Startup-Setup.json"),
            "Startup setup (*.json)",
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".json":
            target = target.with_suffix(".json")
        try:
            self.store.export_to(target)
        except OSError as error:
            show_warning(self, "Startup setup could not be exported", str(error))
            return
        self._saved_feedback("Startup Setup export")

    def _import_profile(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Import startup setup",
            "",
            "Startup setup (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            self.store.import_from(Path(path))
            self._notify_last_saved_changed()
        except (OSError, TypeError, ValueError) as error:
            show_warning(self, "Startup setup could not be imported", str(error))
            return
        self._saved_feedback("Startup Setup import")
        self.refresh()


def _number(value, suffix: str = "") -> str:
    try:
        return f"{float(value):.1f}{suffix}"
    except (TypeError, ValueError):
        return "--"


def _summary(group: str, payload: dict) -> str:
    if group == "offline":
        values = payload.get("offline", {})
        return f"{'Offline' if values.get('enabled') else 'Online'}; {values.get('water_file') or 'built-in water mask'}"
    if group == "map":
        values = payload.get("offline", {})
        source = {"auto": "Automatic", "online": "OpenSeaMap", "protomaps": "Protomaps", "s57": "S-57 (Experimental)"}.get(values.get("map_source", "auto"), "Automatic")
        layers = values.get("enc_layers")
        display = f"Base ({_number(values.get('enc_safety_contour_m', 30), ' m')} contour)" if values.get("enc_base") else "All layers" if layers is None else f"{len(layers)} selected layer(s)"
        return f"{source}; {values.get('map_file') or 'no saved raster package'}; {display}"
    app_state = payload.get("app_state", {}) if isinstance(payload, dict) else {}
    simulation = app_state.get("simulation", {}) if isinstance(app_state, dict) else {}
    if group == "mode":
        mode = (
            str(simulation.get("control_mode", "not captured"))
            .replace("_", " ")
            .title()
        )
        return f"{mode}; {_number(simulation.get('time_scale'), 'x')}"
    if group == "vessel_models":
        profile_id = str(simulation.get("vessel_profile_id", "--"))
        profiles = payload.get("vessel_profiles", [])
        display_name = profile_id.replace("-", " ").title()
        if isinstance(profiles, list):
            selected = next(
                (
                    profile
                    for profile in profiles
                    if isinstance(profile, dict)
                    and str(profile.get("profile_id", "")) == profile_id
                ),
                None,
            )
            if selected is not None:
                display_name = str(selected.get("display_name", profile_id))
            return f"{display_name}; {len(profiles)} customized/added model(s)"
        return display_name
    if group == "control":
        control = simulation.get("control", {})
        return (
            f"Speed {_number(control.get('speed_order_kn'), ' kn')}; "
            f"rudder {_number(control.get('rudder_order_deg'), '°')}; "
            f"ROT {_number(app_state.get('control_turn_rate_deg_min'), '°/min')}; "
            f"radius {_number(app_state.get('control_turn_radius_nm'), ' NM')}"
        )
    if group == "position_heading":
        position = simulation.get("position", {})
        try:
            latitude = format_coordinate(float(position["latitude_deg"]), latitude=True)
            longitude = format_coordinate(
                float(position["longitude_deg"]), latitude=False
            )
        except (KeyError, TypeError, ValueError):
            latitude, longitude = "--", "--"
        return (
            f"{latitude}; {longitude}; HDG {_number(position.get('heading_deg'), '°')}"
        )
    if group == "environment":
        environment = simulation.get("environment", {})
        weather_source = (
            "live at own ship"
            if environment.get("weather_source") == "open-meteo"
            else "manual weather"
        )
        return (
            f"Wind {_number(environment.get('wind_speed_kn'), ' kn')} / "
            f"{_number(environment.get('wind_from_deg'), '°')} ({environment.get('wind_source', 'manual')}); current "
            f"{_number(environment.get('current_speed_kn'), ' kn')} / "
            f"{_number(environment.get('current_from_deg'), '°')} ({environment.get('current_source', 'manual')}); {weather_source}; "
            f"depth {environment.get('depth_source', 'manual')}"
        )
    if group == "ais":
        ais = app_state.get("ais", {})
        live = ais.get("live", {}) if isinstance(ais, dict) else {}
        provider = live.get("provider", "") if isinstance(live, dict) else ""
        filter_text = "All output targets"
        if ais.get("output_filter_mode") == "sog_dependent":
            filter_text = f"Output SOG > {_number(ais.get('output_min_sog_kn', 0.5), ' kn')}"
        return (
            f"{str(ais.get('mode', 'not captured')).title()}"
            f"{f'; {provider}' if provider else ''}; {filter_text}"
            f"; Avoid Land {'on' if ais.get('avoid_land', True) else 'off'}"
        )
    if group == "sensors":
        values = simulation.get("sensor_values", {})
        auxiliary = simulation.get("auxiliary_sensors", {})
        return (
            f"COG {_number(values.get('cog_deg'), '°')}; SOG {_number(values.get('sog_kn'), ' kn')}; "
            f"STW {_number(values.get('stw_kn'), ' kn')}; dual rudder "
            f"{'on' if auxiliary.get('dual_rudder', values.get('dual_rudder', False)) else 'off'}"
        )
    if group == "noise":
        values = simulation.get("sensor_noise", {})
        active = sum(
            bool(value[0])
            for value in values.values()
            if isinstance(value, (list, tuple)) and value
        )
        return f"{active} enabled channel(s)"
    if group == "protocol_output":
        output = payload.get("protocol_output", {})
        paths = output.get("output_paths", []) if isinstance(output, dict) else []
        protocols = output.get("protocols", {}) if isinstance(output, dict) else {}
        enabled = sum(
            bool(settings.get("enabled"))
            for per_protocol in protocols.values()
            if isinstance(per_protocol, dict)
            for settings in per_protocol.values()
            if isinstance(settings, dict)
        )
        return f"{len(paths)} path(s); {enabled} enabled protocol(s)"
    if group == "external_rudder_input":
        value = payload.get("external_rudder_input", {})
        policy = str(value.get("input_type", "not captured")).upper()
        rudder = {
            "ROR": "ROR",
            "RSA": "RSA",
            "PRIO_RSA_ROR": "Prio RSA, ROR",
            "PRIO_ROR_RSA": "Prio ROR, RSA",
        }.get(policy, policy.replace("_", " ").title())
        return (f"Rudder: {rudder}; Bow: {value.get('bow_thruster', {}).get('policy', 'MANUAL')}; "
                f"Engine: {value.get('engine', {}).get('policy', 'MANUAL')}")
    if group == "instruments":
        value = payload.get("instrument_cluster")
        if not isinstance(value, dict):
            return "Default layout"
        widgets = value.get("widgets")
        return (
            f"Custom layout; {len(widgets)} widget(s)"
            if isinstance(widgets, list)
            else "Custom layout"
        )
    if group == "advanced":
        value = payload.get("advanced_output", {})
        rows = (
            tuple(value.get("protocols", ())) + tuple(value.get("free_text", ()))
            if isinstance(value, dict)
            else ()
        )
        cycles = sum(
            bool(row.get("cycle_active")) for row in rows if isinstance(row, dict)
        )
        return f"{len(rows)} row(s); {cycles} active cycle(s)"
    if group == "route":
        route = app_state.get("route", [])
        end_action = app_state.get("route_end_action", "--")
        return f"{len(route)} waypoint(s); end {end_action}"
    return "Captured"


_SENSITIVE_FIELDS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
}

_UNIT_SUFFIXES = {
    "deg": "°",
    "deg_min": "°/min",
    "kn": " kn",
    "m": " m",
    "nm": " NM",
    "percent": "%",
    "s": " s",
}


def _details(group: str, payload: dict) -> str:
    if group == "map":
        values = payload.get("offline", {})
        source = {"auto": "Automatic", "online": "OpenSeaMap (Online)", "protomaps": "Protomaps (Offline)", "s57": "S-57 (Experimental)"}.get(values.get("map_source", "auto"), "Automatic")
        layers = values.get("enc_layers")
        display = "Base" if values.get("enc_base") else "All layers" if layers is None else f"{len(layers)} selected layers"
        cells = values.get("enc_cells")
        selection = "Existing library selection" if cells is None else (
            f"{len(cells):,} selected" if cells else "None")
        return (f"Map & Layers\nSource: {source}\nRaster package: {Path(values['map_file']).name if values.get('map_file') else 'None'}\nS57 display: {display}\nS57 cells: {selection}"
                f"\nSafety contour: {_number(values.get('enc_safety_contour_m', 30), ' m')} (or next deeper)"
                f"\nSafety depth: {_number(values.get('enc_safety_depth_m', 30), ' m')}")
    if group == "position_heading":
        position = (
            payload.get("app_state", {}).get("simulation", {}).get("position", {})
        )
        try:
            latitude = format_coordinate(float(position["latitude_deg"]), latitude=True)
            longitude = format_coordinate(
                float(position["longitude_deg"]), latitude=False
            )
        except (KeyError, TypeError, ValueError):
            latitude, longitude = "Not set", "Not set"
        return (
            "Position & Heading\n"
            f"Latitude: {latitude}\n"
            f"Longitude: {longitude}\n"
            f"Heading: {_number(position.get('heading_deg'), '°')}"
        )
    app = payload.get("app_state", {})
    simulation = app.get("simulation", {})
    if group == "protocol_output":
        output = payload.get("protocol_output", {})
        lines = []
        for path in output.get("output_paths", [])[:4]:
            path_id = path.get("id", "")
            enabled = sum(bool(paths.get(path_id, {}).get("enabled")) for paths in output.get("protocols", {}).values() if isinstance(paths, dict))
            lines.append(f"{path.get('label', path_id)}: {enabled} enabled protocols")
        return "Protocol Output\n" + _summary(group, payload) + ("\n" + "\n".join(lines) if lines else "")
    if group == "external_rudder_input":
        values = payload.get(group, {})
        lines = [f"Control source: {_summary(group, payload)}"]
        for channel in ("ror", "rsa"):
            endpoint = values.get(channel, {})
            transport = endpoint.get("transport", "UDP")
            address = (f"{endpoint.get('serial_device', '--')} / {endpoint.get('baud', 4800)} baud"
                       if transport.upper() == "SERIAL" else f"{endpoint.get('host', '0.0.0.0')}:{endpoint.get('port', '--')}")
            lines.append(f"{channel.upper()}: {transport} {address}")
        return "External Input\n" + "\n".join(lines)
    if group == "ais":
        values = app.get("ais", {})
        live = values.get("live", {})
        lines = [_summary(group, payload)]
        if live:
            lines += [f"Provider: {live.get('provider', '--')}",
                      f"Subscription range: {_number(live.get('subscription_range_nm'), ' NM')}"]
        for key in ("generated_target_count", "visible_limit", "output_limit", "active_range_nm"):
            if key in values:
                lines.append(f"{_detail_label((key,))}: {_detail_value(values[key], (key,))}")
        return "AIS\n" + "\n".join(lines)
    if group in {"route", "instruments", "advanced", "vessel_models", "noise"}:
        return STARTUP_GROUP_LABELS[group] + "\n" + _summary(group, payload)
    relevant = simulation.get("environment", {}) if group == "environment" else payload
    if group == "environment":
        keys = ("depth_source", "depth_m", "wind_source", "current_source", "wind_speed_kn", "wind_from_deg", "current_speed_kn", "current_from_deg", "weather_source",
                "air_temperature_c", "relative_humidity_percent", "surface_pressure_hpa")
        relevant = {key: relevant[key] for key in keys if key in relevant}
    lines: list[str] = []
    _append_detail_lines(relevant, (), lines)
    if not lines:
        return f"{STARTUP_GROUP_LABELS[group]}: no saved values"
    essential = [line if len(line) <= 110 else line[:107] + "..." for line in lines[:7]]
    if len(lines) > 7:
        essential.append(f"+ {len(lines) - 7} other saved values")
    return f"{STARTUP_GROUP_LABELS[group]}\n" + "\n".join(essential)


def _differences(current, saved):
    current_lines, saved_lines = [], []
    _append_detail_lines(current, (), current_lines)
    _append_detail_lines(saved, (), saved_lines)
    saved_values = {line.partition(": ")[0]: line.partition(": ")[2] for line in saved_lines}
    changes = []
    for line in current_lines:
        label, _, value = line.partition(": ")
        if value != saved_values.get(label):
            short_label = label.split(" > ")[-1]
            text = f"{short_label}: {saved_values.get(label, 'Not saved')} → {value}"
            changes.append(text if len(text) <= 100 else text[:97] + "...")
    if not changes:
        return "Current differs from the selected startup snapshot."
    return "Changes from startup:\n" + "\n".join(changes[:3]) + (f"\n+ {len(changes) - 3} other changes" if len(changes) > 3 else "")


def _append_detail_lines(value, path: tuple[str, ...], lines: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            sensitive_parts = set(
                normalized.replace("-", "_").replace(" ", "_").split("_")
            )
            if (
                normalized in _SENSITIVE_FIELDS
                or normalized.endswith("_api_key")
                or sensitive_parts
                & {
                    "authorization",
                    "credential",
                    "credentials",
                    "password",
                    "secret",
    "token",
    "client_id",
                }
                or normalized in {"format", "format_version"}
            ):
                continue
            next_path = (
                path if normalized in {"app_state", "simulation"} else (*path, str(key))
            )
            _append_detail_lines(child, next_path, lines)
        return
    if isinstance(value, (list, tuple)):
        label = _detail_label(path)
        if not value:
            lines.append(f"{label}: None")
        elif all(not isinstance(item, (dict, list, tuple)) for item in value):
            lines.append(
                f"{label}: {', '.join(_detail_value(item, path) for item in value)}"
            )
        elif path and path[-1] in {"route", "vessel_profiles"}:
            lines.append(f"{label}: {len(value)} entries")
        else:
            for index, item in enumerate(value, start=1):
                _append_detail_lines(item, (*path, str(index)), lines)
        return
    lines.append(f"{_detail_label(path)}: {_detail_value(value, path)}")


def _detail_label(path: tuple[str, ...]) -> str:
    acronyms = {
        "ais",
        "cog",
        "hdg",
        "id",
        "ip",
        "ror",
        "rot",
        "rsa",
        "rtz",
        "sog",
        "stw",
        "tcp",
        "udp",
    }
    parts = []
    for raw in path:
        cleaned = str(raw)
        suffix = next(
            (unit for unit in _UNIT_SUFFIXES if cleaned.endswith(f"_{unit}")), None
        )
        if suffix is not None:
            cleaned = cleaned[: -(len(suffix) + 1)]
        words = cleaned.replace("_", " ").strip().split()
        parts.append(
            " ".join(
                word.upper() if word.lower() in acronyms else word.title()
                for word in words
            )
        )
    return " > ".join(parts) if parts else "Value"


def _detail_value(value, path: tuple[str, ...]) -> str:
    if isinstance(value, bool):
        return "On" if value else "Off"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        suffix = ""
        last = path[-1] if path else ""
        for unit, display in sorted(
            _UNIT_SUFFIXES.items(), key=lambda item: -len(item[0])
        ):
            if str(last).endswith(f"_{unit}"):
                suffix = display
                break
        return f"{float(value):.1f}{suffix}"
    if value is None:
        return "Not set"
    return str(value).replace("_", " ")
