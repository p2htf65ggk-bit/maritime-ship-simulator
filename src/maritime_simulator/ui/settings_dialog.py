# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtSerialPort import QSerialPortInfo
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from maritime_simulator.application.configuration import (
    OutputPathDefinition,
    ProtocolConfigurationStore,
    protocol_display_name,
)
from maritime_simulator.application.startup_profile import StartupProfileStore
from maritime_simulator.domain.enums import TransportKind, UdpMode
from maritime_simulator.domain.enums import ExternalRudderControlPolicy
from maritime_simulator.domain.profiles import VesselProfileStore

from .input_runtime import InputRuntime
from .input_monitor_dialog import ExternalInputMonitorDialog
from .dialogs import OpaqueDialog, ask_question, request_text, reserve_label_lines, show_warning
from .startup_profile_panel import StartupProfilePanel
from .offline_panel import OfflinePanel
from .rudder_endpoint import RudderEndpointEditor
from .network_help import LOCAL_BIND_HELP, REMOTE_TCP_HELP


@dataclass(slots=True)
class _RowWidgets:
    enabled: QCheckBox
    destination: QLineEdit | QComboBox
    rate: QLineEdit
    port: QLineEdit | None = None
    mode: QComboBox | None = None
    ttl: QLineEdit | None = None
    interface: QLineEdit | None = None
    baud: QComboBox | None = None
    parity: QComboBox | None = None
    stop_bits: QComboBox | None = None


class VesselProfileEditor(QWidget):
    """Editable profile overrides with immutable built-in identities."""

    _GENERAL_FIELDS = (
        ("length_m", "Length", 1.0, 600.0, 1, " m"),
        ("beam_m", "Beam", 1.0, 100.0, 1, " m"),
        ("draft_m", "Draft", 0.1, 40.0, 1, " m"),
        ("pivot_fraction_from_bow", "Pivot from bow", 0.05, 0.95, 3, ""),
        ("max_speed_kn", "Maximum ahead speed", 0.1, 100.0, 1, " kn"),
        ("max_astern_kn", "Maximum astern speed", 0.1, 50.0, 1, " kn"),
    )
    _DYNAMIC_FIELDS = (
        ("acceleration_kn_s", "Acceleration", 0.001, 10.0, 3, " kn/s"),
        ("deceleration_kn_s", "Deceleration", 0.001, 10.0, 3, " kn/s"),
        ("max_rot_deg_min", "Maximum rate of turn", 0.1, 180.0, 1, " °/min"),
        ("rot_time_constant_s", "ROT time constant", 0.1, 600.0, 1, " s"),
        ("max_rudder_deg", "Maximum rudder angle", 1.0, 60.0, 1, "°"),
        ("rudder_rate_deg_s", "Rudder speed", 0.1, 30.0, 2, " °/s"),
        ("rudder_effectiveness", "Rudder effectiveness", 0.1, 5.0, 2, ""),
        ("windage", "Windage", 0.0, 2.0, 4, ""),
        ("wind_lateral_gain", "Wind lateral gain", 0.0, 10.0, 3, ""),
        ("wind_forward_gain", "Wind forward gain", 0.0, 10.0, 3, ""),
        ("wind_damping_floor", "Wind damping floor", 0.0, 1.0, 3, ""),
        ("turn_slip", "Turn slip", 0.0, 5.0, 3, ""),
        ("sway_time_constant_s", "Sway time constant", 0.1, 600.0, 1, " s"),
    )
    _THRUSTER_FIELDS = (
        ("bow_thruster_power_kw", "Rated power (0 = not fitted)", 0.0, 20000.0, 1, " kW"),
        ("bow_thruster_rated_rpm", "Rated RPM (TRC mode R)", 1.0, 10000.0, 0, " RPM"),
        ("bow_thruster_ramp_s", "Zero to full RPM time", 0.1, 120.0, 1, " s"),
        ("bow_thruster_half_effect_speed_kn", "Speed at 50% effectiveness", 0.1, 20.0, 1, " kn STW"),
        ("bow_thruster_thrust_kn_per_kw", "Static thrust per kW", 0.001, 1.0, 3, " kN/kW"),
        ("bow_thruster_fraction_from_bow", "Position from bow (fraction of length)", 0.0, 0.9, 3, ""),
        ("displacement_t", "Displacement (0 = estimate)", 0.0, 1000000.0, 1, " t"),
    )

    def __init__(
        self,
        profiles: VesselProfileStore,
        active_profile_id: Callable[[], str],
        changed: Callable[[str], None],
        parent=None,
    ):
        super().__init__(parent)
        self.profiles = profiles
        self.active_profile_id = active_profile_id
        self.changed = changed
        self._updating = False
        self._fields: dict[str, QDoubleSpinBox] = {}
        self._track_fields: dict[str, QDoubleSpinBox] = {}

        root = QVBoxLayout(self)
        selector = QHBoxLayout()
        selector.addWidget(QLabel("Vessel"))
        self.profile = QComboBox()
        self.profile.currentIndexChanged.connect(self.refresh)
        selector.addWidget(self.profile, 1)
        self.kind = QLabel()
        self.kind.setObjectName("secondaryText")
        selector.addWidget(self.kind)
        self.add_button = QPushButton("Add Vessel...")
        self.add_button.clicked.connect(self._add_profile)
        selector.addWidget(self.add_button)
        self.delete_button = QPushButton("Delete Vessel")
        self.delete_button.clicked.connect(self._delete_profile)
        selector.addWidget(self.delete_button)
        self.reset_button = QPushButton("Reset This Vessel")
        self.reset_button.clicked.connect(self._reset_profile)
        selector.addWidget(self.reset_button)
        root.addLayout(selector)

        name_form = QFormLayout()
        self.name = QLineEdit()
        self.name.editingFinished.connect(self._save_name)
        name_form.addRow("Display name", self.name)
        root.addLayout(name_form)

        self.parameter_tabs = QTabWidget()
        # A literal ampersand must be escaped in Qt tab captions; otherwise it
        # is treated as a mnemonic and rendered as an underscore on some OSes.
        self.parameter_tabs.addTab(self._fields_page(self._GENERAL_FIELDS), "Dimensions && Speed")
        self.parameter_tabs.addTab(self._fields_page(self._DYNAMIC_FIELDS), "Dynamics")
        self.parameter_tabs.addTab(self._fields_page(self._THRUSTER_FIELDS), "Bow Thruster")
        self.parameter_tabs.addTab(self._track_page(), "Track Controller")
        root.addWidget(self.parameter_tabs, 1)
        note = QLabel(
            "Built-in vessels may be renamed and tuned, but cannot be deleted. "
            "Reset restores their shipped values. New vessels are copied from the selected model."
        )
        note.setObjectName("secondaryText")
        note.setWordWrap(True)
        root.addWidget(note)
        self.refresh_profiles(self.active_profile_id())

    def _fields_page(self, definitions) -> QWidget:
        content = QWidget()
        form = QFormLayout(content)
        for key, label, minimum, maximum, decimals, suffix in definitions:
            field = QDoubleSpinBox()
            field.setRange(minimum, maximum)
            field.setDecimals(decimals)
            field.setKeyboardTracking(False)
            field.setSuffix(suffix)
            if key == "bow_thruster_power_kw":
                self.thruster_power_unit = QComboBox()
                self.thruster_power_unit.addItems(["kW", "MW"])
                self.thruster_power_unit.currentTextChanged.connect(self._power_unit_changed)
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.addWidget(field, 1)
                row_layout.addWidget(self.thruster_power_unit)
                field.setSuffix("")
                editor = row
            else:
                editor = field
            if key == "rudder_effectiveness":
                field.setToolTip(
                    "Partial-rudder response curve. 1.0 preserves the linear shipped behaviour; "
                    "higher values increase and lower values reduce partial-rudder effect."
                )
            field.editingFinished.connect(lambda name=key: self._save_field(name))
            form.addRow(label, editor)
            self._fields[key] = field
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        return scroll

    def _track_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        actions = QHBoxLayout()
        self.calculate_track_button = QPushButton("Calculate Values")
        self.calculate_track_button.setToolTip(
            "Estimate a conservative controller baseline from the tuned built-in vessel models. "
            "Test runs are still recommended for final tuning."
        )
        self.calculate_track_button.clicked.connect(self._calculate_track_values)
        actions.addWidget(self.calculate_track_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.track_content = QWidget()
        self.track_form = QFormLayout(self.track_content)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.track_content)
        layout.addWidget(scroll)
        return page

    def refresh_profiles(self, selected_id: str | None = None) -> None:
        selected = selected_id or self.profile.currentData() or self.active_profile_id()
        self.profile.blockSignals(True)
        self.profile.clear()
        for profile in self.profiles.all_profiles():
            self.profile.addItem(profile.display_name, profile.profile_id)
        index = self.profile.findData(selected)
        self.profile.setCurrentIndex(max(0, index))
        self.profile.blockSignals(False)
        self.refresh()

    def refresh(self, *_args) -> None:
        profile_id = self.profile.currentData()
        if not profile_id:
            return
        profile = self.profiles.get(str(profile_id))
        self._updating = True
        try:
            self.name.setText(profile.display_name)
            built_in = self.profiles.is_factory_profile(profile.profile_id)
            self.kind.setText("Built-in" if built_in else "Custom")
            self.delete_button.setEnabled(not built_in)
            for key, field in self._fields.items():
                value = float(getattr(profile, key))
                if key == "bow_thruster_power_kw" and self.thruster_power_unit.currentText() == "MW":
                    value /= 1000.0
                field.setValue(value)
            while self.track_form.count():
                item = self.track_form.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            self._track_fields.clear()
            for key, value in sorted(profile.track_parameters.items()):
                field = QDoubleSpinBox()
                field.setRange(-10000.0, 10000.0)
                field.setDecimals(6)
                field.setKeyboardTracking(False)
                field.setValue(float(value))
                field.editingFinished.connect(lambda name=key: self._save_track_field(name))
                self.track_form.addRow(key.replace("track_", "").replace("_", " ").title(), field)
                self._track_fields[key] = field
        finally:
            self._updating = False

    def _save_name(self) -> None:
        if self._updating:
            return
        value = self.name.text().strip()
        if not value:
            self.refresh()
            return
        self._commit(display_name=value)

    def _power_unit_changed(self, unit):
        field = self._fields["bow_thruster_power_kw"]
        field.setDecimals(3 if unit == "MW" else 1)
        field.setMaximum(20.0 if unit == "MW" else 20000.0)
        self.refresh()

    def _save_field(self, name: str) -> None:
        if not self._updating:
            value = self._fields[name].value()
            if name == "bow_thruster_power_kw" and self.thruster_power_unit.currentText() == "MW":
                value *= 1000.0
            self._commit(**{name: value})

    def _save_track_field(self, name: str) -> None:
        if self._updating:
            return
        profile = self.profiles.get(str(self.profile.currentData()))
        values = dict(profile.track_parameters)
        values[name] = self._track_fields[name].value()
        self._commit(track_parameters=values)

    def _calculate_track_values(self) -> None:
        profile_id = str(self.profile.currentData())
        if not profile_id:
            return
        if ask_question(
            self,
            "Calculate Track Controller Values",
            "Replace the current track-controller values with a conservative estimate based on the built-in vessels?",
        ) != QMessageBox.Yes:
            return
        self._commit(track_parameters=dict(self.profiles.calculate_track_parameters(profile_id)))

    def _commit(self, **changes) -> None:
        profile_id = str(self.profile.currentData())
        try:
            self.profiles.update_profile(profile_id, **changes)
        except (KeyError, TypeError, ValueError) as error:
            show_warning(self, "Vessel profile could not be updated", str(error))
            self.refresh()
            return
        self.refresh_profiles(profile_id)
        self.changed(profile_id)

    def _add_profile(self) -> None:
        base_id = str(self.profile.currentData())
        display_name, accepted = request_text(self, "Add Vessel", "Vessel name")
        display_name = display_name.strip()
        if not accepted or not display_name:
            return
        stem = "-".join(part for part in "".join(
            character.lower() if character.isalnum() else " " for character in display_name
        ).split()) or "custom-vessel"
        profile_id = stem
        suffix = 2
        existing = {profile.profile_id for profile in self.profiles.all_profiles()}
        while profile_id in existing:
            profile_id = f"{stem}-{suffix}"
            suffix += 1
        try:
            self.profiles.create_user_profile(base_id, profile_id, display_name)
        except (KeyError, TypeError, ValueError) as error:
            show_warning(self, "Vessel could not be added", str(error))
            return
        self.refresh_profiles(profile_id)
        self.changed(profile_id)

    def _delete_profile(self) -> None:
        profile_id = str(self.profile.currentData())
        if self.profiles.is_factory_profile(profile_id):
            return
        if ask_question(
            self,
            "Delete Vessel",
            f"Delete {self.profiles.get(profile_id).display_name}?",
        ) != QMessageBox.Yes:
            return
        base_id = self.profiles.get(profile_id).factory_profile_id or next(iter(self.profiles.factory_profiles))
        self.profiles.delete_user_profile(profile_id)
        self.refresh_profiles(base_id)
        self.changed(base_id)

    def _reset_profile(self) -> None:
        profile_id = str(self.profile.currentData())
        if ask_question(
            self,
            "Reset Vessel",
            "Restore all parameters for this vessel from its factory model?",
        ) != QMessageBox.Yes:
            return
        try:
            self.profiles.reset_user_profile(profile_id)
        except (KeyError, ValueError) as error:
            show_warning(self, "Vessel could not be reset", str(error))
            return
        self.refresh_profiles(profile_id)
        self.changed(profile_id)


class ProtocolSettingsDialog(OpaqueDialog):
    """Protocol settings editor with explicit sensor pushdown semantics."""

    def __init__(
        self,
        store: ProtocolConfigurationStore,
        on_changed: Callable[[], None],
        input_runtime: InputRuntime,
        parent=None,
        *,
        save_configuration: Callable[[], None] | None = None,
        load_configuration: Callable[[], None] | None = None,
        profiles: VesselProfileStore | None = None,
        active_profile_id: Callable[[], str] | None = None,
        profiles_changed: Callable[[str], None] | None = None,
        startup_profile: StartupProfileStore | None = None,
        current_configuration: Callable[[], dict] | None = None,
        startup_last_saved_changed: Callable[[dict], None] | None = None,
        offline_current=None,
        offline_changed=None,
        offline_directory=None,
        offline_select_area=None,
        offline_show_chart=None,
        offline_choose_layers=None,
    ):
        super().__init__(parent)
        self.store = store
        self.on_changed = on_changed
        self.input_runtime = input_runtime
        self.save_configuration = save_configuration
        self.load_configuration = load_configuration
        self._updating = False
        self._path_rows: dict[str, dict[tuple[str, str | None], _RowWidgets]] = {}
        self._transport_rows: dict[TransportKind, dict[tuple[str, str | None], _RowWidgets]] = {}
        self.output_trees: dict[str, QTreeWidget] = {}
        self._serial_port_options = self._available_serial_ports()
        self.setWindowTitle("Maritime Ship Simulator V2 - Settings")
        self.resize(1260, 700)

        layout = QVBoxLayout(self)
        heading = QLabel("Settings")
        heading.setObjectName("dialogTitle")
        layout.addWidget(heading)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.startup_panel = None
        if (
            startup_profile is not None
            and current_configuration is not None
            and startup_last_saved_changed is not None
        ):
            self.startup_panel = StartupProfilePanel(
                startup_profile,
                current_configuration,
                startup_last_saved_changed,
                save_configuration=save_configuration,
                load_configuration=load_configuration,
            )
            self.tabs.addTab(self.startup_panel, "Start-up")

        self.offline_panel = None
        if offline_current is not None:
            self.offline_panel = OfflinePanel(offline_current, offline_changed, offline_directory, offline_select_area,
                                              show_chart=offline_show_chart, choose_layers=offline_choose_layers)
            self.tabs.addTab(self.offline_panel, "Offline")

        output_page = QWidget()
        output_layout = QVBoxLayout(output_page)
        explanation = QLabel(
            "Each tab is an independent output path. Sensor-row edits are pushed to every protocol below that "
            "sensor in the selected transport only. "
            "A blank or mixed sensor field means its protocol rows differ; editing a protocol never rewrites its siblings."
        )
        explanation.setObjectName("secondaryText")
        explanation.setWordWrap(True)
        output_layout.addWidget(explanation)

        self.output_tabs = QTabWidget()
        self.output_tabs.currentChanged.connect(self._update_output_path_actions)
        output_corner = QWidget()
        corner_layout = QHBoxLayout(output_corner)
        corner_layout.setContentsMargins(0, 0, 0, 0)
        corner_layout.setSpacing(3)
        self.output_add = QToolButton()
        self.output_add.setText("+")
        self.output_add.setToolTip("Add another independent UDP, TCP or Serial output path.")
        self.output_add.setPopupMode(QToolButton.InstantPopup)
        add_menu = QMenu(self.output_add)
        for transport, label in (
            (TransportKind.UDP, "UDP output"),
            (TransportKind.TCP, "TCP output"),
            (TransportKind.SERIAL, "Serial output"),
        ):
            action = add_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, selected=transport: self._add_output_path(selected)
            )
        self.output_add.setMenu(add_menu)
        self.output_remove = QToolButton()
        self.output_remove.setText("×")
        self.output_remove.setToolTip("Remove the selected additional output path.")
        self.output_remove.clicked.connect(self._remove_current_output_path)
        corner_layout.addWidget(self.output_add)
        corner_layout.addWidget(self.output_remove)
        self.output_tabs.setCornerWidget(output_corner, Qt.TopRightCorner)
        self._rebuild_output_tabs()
        output_layout.addWidget(self.output_tabs, 1)
        self.tabs.addTab(output_page, "Protocol Output")
        self._input_tab_index = self.tabs.addTab(self._build_input_page(), "External Input")
        self.input_monitor = ExternalInputMonitorDialog(
            input_running=self.input_runtime.is_listening,
            endpoint_description=self.input_runtime.monitor_description,
            input_events=self.input_runtime.events,
            clear_events=self.input_runtime.clear_events,
            parent=self,
        )
        self.profile_editor = None
        if profiles is not None and active_profile_id is not None and profiles_changed is not None:
            self.profile_editor = VesselProfileEditor(profiles, active_profile_id, profiles_changed)
            self.tabs.addTab(self.profile_editor, "Vessel Models")

        if self.startup_panel is None:
            configuration_actions = QHBoxLayout()
            save_profile = QPushButton("Save Configuration As...")
            save_profile.setToolTip("Save route, I/O, AIS, instruments and simulator settings to one portable JSON file.")
            save_profile.clicked.connect(lambda: self.save_configuration and self.save_configuration())
            load_profile = QPushButton("Load Configuration...")
            load_profile.setToolTip("Load a complete Maritime Ship Simulator V2 configuration file.")
            load_profile.clicked.connect(lambda: self.load_configuration and self.load_configuration())
            configuration_actions.addWidget(save_profile)
            configuration_actions.addWidget(load_profile)
            configuration_actions.addStretch(1)
            layout.addLayout(configuration_actions)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.refresh()
        self.input_timer = QTimer(self)
        self.input_timer.timeout.connect(self._refresh_input_status)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.startup_panel is not None:
            self.startup_panel.refresh()
        self._refresh_serial_port_choices()
        self._load_input_fields()
        self.input_timer.start(500)

    def hideEvent(self, event) -> None:
        self.input_timer.stop()
        super().hideEvent(event)

    def _rebuild_output_tabs(self, selected_path_id: str | None = None) -> None:
        if selected_path_id is None and self.output_tabs.count():
            selected_path_id = self.output_tabs.tabBar().tabData(self.output_tabs.currentIndex())
        signal_blocker = QSignalBlocker(self.output_tabs)
        self.output_tabs.setUpdatesEnabled(False)
        try:
            while self.output_tabs.count():
                page = self.output_tabs.widget(0)
                self.output_tabs.removeTab(0)
                page.deleteLater()
            self._path_rows.clear()
            self.output_trees.clear()
            selected_index = 0
            for path in self.store.output_paths():
                index = self.output_tabs.addTab(self._build_output_transport_page(path), path.label)
                self.output_tabs.tabBar().setTabData(index, path.path_id)
                if path.path_id == selected_path_id:
                    selected_index = index
            self._build_tree()
            self.output_tabs.setCurrentIndex(selected_index)
        finally:
            self.output_tabs.setUpdatesEnabled(True)
            del signal_blocker
        self._transport_rows = {
            transport: self._path_rows[self.store.default_path_id(transport)]
            for transport in TransportKind
        }
        # Compatibility aliases for the original primary UDP path.
        self.tree = self.output_trees[self.store.default_path_id(TransportKind.UDP)]
        self._rows = self._transport_rows[TransportKind.UDP]
        self._update_output_path_actions()

    def _current_output_path_id(self) -> str | None:
        index = self.output_tabs.currentIndex()
        if index < 0:
            return None
        value = self.output_tabs.tabBar().tabData(index)
        return str(value) if value else None

    def _add_output_path(self, transport: TransportKind) -> None:
        try:
            path = self.store.add_output_path(transport)
        except (OSError, TypeError, ValueError) as error:
            show_warning(self, "Output path could not be added", str(error))
            return
        self._rebuild_output_tabs(path.path_id)
        self.refresh()
        self.on_changed()

    def _remove_current_output_path(self) -> None:
        path_id = self._current_output_path_id()
        if path_id is None:
            return
        path = self.store.output_path(path_id)
        if not path.removable:
            return
        answer = ask_question(
            self,
            "Remove output path",
            f"Remove {path.label} and all of its protocol settings?",
            buttons=QMessageBox.Yes | QMessageBox.Cancel,
            default=QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        fallback = self.store.default_path_id(path.transport)
        selected_index = self.output_tabs.currentIndex()
        page = self.output_tabs.widget(selected_index)
        signal_blocker = QSignalBlocker(self.output_tabs)
        try:
            self.store.remove_output_path(path.path_id)
            self.output_tabs.removeTab(selected_index)
            self._path_rows.pop(path.path_id, None)
            self.output_trees.pop(path.path_id, None)
            fallback_index = self._output_path_tab_index(fallback)
            self.output_tabs.setCurrentIndex(max(0, fallback_index))
        except (OSError, TypeError, ValueError, KeyError) as error:
            show_warning(self, "Output path could not be removed", str(error))
            return
        finally:
            del signal_blocker
        if page is not None:
            page.deleteLater()
        self._update_output_path_actions()
        self.refresh()
        self.on_changed()

    def _update_output_path_actions(self, _index: int = -1) -> None:
        path_id = self._current_output_path_id()
        try:
            removable = bool(path_id and self.store.output_path(path_id).removable)
        except KeyError:
            removable = False
        self.output_remove.setEnabled(removable)

    def _output_path_tab_index(self, path_id: str) -> int:
        for index in range(self.output_tabs.count()):
            if self.output_tabs.tabBar().tabData(index) == path_id:
                return index
        return -1

    def _build_output_transport_page(self, path: OutputPathDefinition) -> QWidget:
        transport = path.transport
        page = QWidget()
        layout = QVBoxLayout(page)
        tree = QTreeWidget()
        tree.setObjectName("settingsTree")
        headers = {
            TransportKind.UDP: (
                "Sensor / Protocol", "On", "Destination host", "Port", "Rate (Hz)",
                "UDP mode", "TTL", "Interface IP",
            ),
            TransportKind.TCP: (
                "Sensor / Protocol", "On", "TCP mode", "Host / bind address", "Port", "Rate (Hz)",
            ),
            TransportKind.SERIAL: (
                "Sensor / Protocol", "On", "Serial device", "Baud", "Parity", "Stop bits", "Rate (Hz)",
            ),
        }[transport]
        tree.setColumnCount(len(headers))
        tree.setHeaderLabels(headers)
        tree.setRootIsDecorated(True)
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.setSelectionMode(QAbstractItemView.NoSelection)
        self.output_trees[path.path_id] = tree
        self._path_rows[path.path_id] = {}
        layout.addWidget(tree, 1)

        hints = {
            TransportKind.UDP: (
                "Each protocol has an independent UDP rate and destination. AUTO derives unicast, "
                "broadcast or multicast from the destination address. TTL applies only to multicast; "
                "1 normally keeps packets on the local network."
            ),
            TransportKind.TCP: (
                "Client connects to a remote TCP server. Server listens on the bind address and sends "
                "to all connected clients; 0.0.0.0 listens on every local IPv4 adapter. TCP protocols "
                "are disabled by default."
            ),
            TransportKind.SERIAL: (
                "Each protocol may use its own serial port settings and rate. Detected operating-system ports "
                "are listed; the field remains editable for custom device paths. Serial protocols are disabled "
                "by default."
            ),
        }
        hint = QLabel(hints[transport])
        hint.setObjectName("secondaryText")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return page

    def _build_input_page(self) -> QWidget:
        from .external_input import ActuatorInputPanel

        page = QWidget()
        layout = QVBoxLayout(page)
        self.input_groups = QTabWidget()
        def scrollable(content):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            return scroll
        self.input_groups.addTab(scrollable(self._build_rudder_input_page()), "Rudder")
        self.actuator_input_panels = {}
        for key, title in (("bow_thruster", "Bow Thruster"), ("engine", "Engine Telegraph")):
            panel = ActuatorInputPanel(self.input_runtime, key, self.on_changed, self._show_input_monitor)
            self.actuator_input_panels[key] = panel
            self.input_groups.addTab(scrollable(panel), title)
        layout.addWidget(self.input_groups)
        return page

    def _build_rudder_input_page(self) -> QWidget:
        from .control_sources import ControlSourceSelector, SOURCE_LABELS
        from .rudder_xdr import RudderXdrEditor
        from .content_tabs import ContentSizedTabs
        from maritime_simulator.domain.external_control import RUDDER_SOURCES
        page = QWidget()
        layout = QVBoxLayout(page)
        explanation = QLabel(
            "All inputs are monitored. Main has priority; fallback takes over after 2.5 s without valid main data. "
            "Main resumes on its next valid sentence. On Input Loss applies when neither source is current."
        )
        explanation.setObjectName("secondaryText")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.input_type = QComboBox()
        self.input_type.addItem("Manual", ExternalRudderControlPolicy.MANUAL.value)
        self.input_type.addItem("ROR", ExternalRudderControlPolicy.ROR.value)
        self.input_type.addItem("RSA", ExternalRudderControlPolicy.RSA.value)
        self.input_type.addItem(
            "Prio RSA, ROR",
            ExternalRudderControlPolicy.PREFER_RSA.value,
        )
        self.input_type.addItem(
            "Prio ROR, RSA",
            ExternalRudderControlPolicy.PREFER_ROR.value,
        )
        self.input_type.setToolTip(
            "Fixed sources use only that protocol. Priority modes fall back after 2.5 seconds without a valid "
            "preferred value and return as soon as a valid preferred sentence arrives."
        )
        self.input_type.currentTextChanged.connect(self._input_type_changed)
        for policy in ExternalRudderControlPolicy:
            if self.input_type.findData(policy.value) < 0:
                self.input_type.addItem(policy.value.replace("_", " "), policy.value)
        self.input_type.setParent(page)
        self.input_type.hide()
        self.rudder_sources = ControlSourceSelector(RUDDER_SOURCES)
        self.rudder_sources.changed.connect(self._rudder_sources_changed)
        layout.addWidget(self.rudder_sources)
        self.input_endpoints = ContentSizedTabs()
        self.input_editors = {}
        for channel, title in (("ROR", "ROR - Rudder Order"), ("RSA", "RSA - Rudder Response")):
            editor = RudderEndpointEditor()
            editor.changed.connect(lambda channel=channel: self._save_input_fields(channel))
            self.input_editors[channel] = editor
            self.input_endpoints.addTab(editor, title)
        for channel, title in (("XDR_ORDER", "XDR - Rudder Order"), ("XDR_RESPONSE", "XDR - Rudder Response")):
            editor = RudderXdrEditor()
            editor.changed.connect(lambda channel=channel: self._save_input_fields(channel))
            self.input_editors[channel] = editor
            self.input_endpoints.addTab(editor, title)
        layout.addWidget(self.input_endpoints)
        signal_title = QLabel("Rudder input indications")
        signal_title.setObjectName("sectionTitle")
        layout.addWidget(signal_title)
        self.input_lamps: dict[tuple[str, str], QLabel] = {}
        signal_grid = QGridLayout()
        signal_grid.setHorizontalSpacing(0)
        signal_grid.setVerticalSpacing(6)
        signal_grid.setColumnMinimumWidth(1, 10)
        signal_grid.setColumnMinimumWidth(3, 28)
        signal_grid.setColumnMinimumWidth(5, 10)
        signal_grid.setColumnStretch(7, 1)
        for row, input_type in enumerate(RUDDER_SOURCES):
            for index, side in enumerate(("PORT", "STBD")):
                lamp = QLabel()
                lamp.setObjectName("rudderInputLamp")
                lamp.setProperty("signalState", "alarm")
                lamp.setAccessibleName(f"{input_type} {side} input indication")
                lamp.setToolTip(f"{input_type} {side}: never received")
                base_column = 0 if index == 0 else 4
                signal_grid.addWidget(lamp, row, base_column, Qt.AlignCenter)
                signal_grid.addWidget(QLabel(f"{SOURCE_LABELS[input_type]} {side}"), row, base_column + 2, Qt.AlignLeft | Qt.AlignVCenter)
                self.input_lamps[(input_type, side)] = lamp
        layout.addLayout(signal_grid)
        signal_legend = QLabel(
            "Green: valid 0-5 s | Grey: sentence received, field empty | "
            "Yellow: >5-30 s | Red: >30 s or never received"
        )
        signal_legend.setObjectName("secondaryText")
        signal_legend.setWordWrap(True)
        layout.addWidget(signal_legend)
        self.input_status = QLabel("Input stopped")
        self.input_status.setObjectName("dataReadout")
        self.input_status.setWordWrap(True)
        reserve_label_lines(self.input_status, 4, padding=20)
        layout.addWidget(self.input_status)
        actions = QHBoxLayout()
        self.input_view = QPushButton("View Port")
        self.input_view.setToolTip("Open a live monitor showing the exact sentences received on this input port.")
        self.input_view.clicked.connect(self._show_input_monitor)
        actions.addWidget(self.input_view)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addStretch(1)
        return page

    def _load_input_fields(self) -> None:
        settings = self.input_runtime.settings
        with QSignalBlocker(self.input_type):
            self.input_type.setCurrentIndex(max(0, self.input_type.findData(settings.input_type)))
        self.rudder_sources.load(settings.input_type, settings.on_loss)
        for channel, editor in self.input_editors.items():
            editor.load(getattr(settings, channel.lower()))
        for panel in getattr(self, "actuator_input_panels", {}).values():
            panel.load()
        self._update_input_labels()
        self._refresh_input_status()

    def _input_type_changed(self, _value: str) -> None:
        self._update_input_labels()
        self._save_input_fields()

    def _rudder_sources_changed(self):
        policy, _loss = self.rudder_sources.values()
        with QSignalBlocker(self.input_type):
            self.input_type.setCurrentIndex(self.input_type.findData(policy))
        self._save_input_fields()

    def _update_input_labels(self) -> None:
        policy = ExternalRudderControlPolicy.parse(
            self.input_type.currentData() or "ROR"
        )
        self.tabs.setTabText(self._input_tab_index, "External Input")
        self.input_type.setToolTip(
            "Manual rudder control in External mode." if policy is ExternalRudderControlPolicy.MANUAL else
            f"ROR, RSA and both rudder XDR inputs are monitored. {policy.preferred_source} has "
            + ("priority with automatic fallback." if policy.allows_fallback else "exclusive model control.")
        )

    def _save_input_fields(self, channel=None) -> bool:
        try:
            changes = {channel.lower(): self.input_editors[channel].values()} if channel else {}
            self.input_runtime.stop()
            self.input_runtime.update(
                input_type=self.input_type.currentData() or "ROR",
                on_loss=self.rudder_sources.values()[1],
                **changes,
            )
            self.input_runtime.ensure_started()
        except (OSError, TypeError, ValueError) as error:
            show_warning(self, "Invalid external rudder input setting", str(error))
            self._load_input_fields()
            self.input_runtime.ensure_started()
            return False
        self._load_input_fields()
        self._refresh_input_status()
        self.on_changed()
        return True

    def _show_input_monitor(self) -> None:
        self.input_monitor.show()
        self.input_monitor.raise_()
        self.input_monitor.activateWindow()
        self.input_monitor.refresh(force=True)

    def _refresh_input_status(self) -> None:
        for panel in getattr(self, "actuator_input_panels", {}).values():
            panel.refresh()
        policy = ExternalRudderControlPolicy.parse(
            self.input_runtime.settings.input_type
        )
        snapshot = self.input_runtime.simulator.snapshot()
        active = snapshot.external_rudder_active_source or (
            "Manual" if self.input_runtime.settings.input_type == "MANUAL" else f"Waiting - {self.input_runtime.settings.on_loss.title()}"
        )
        statuses = self.input_runtime.statuses()
        if not statuses:
            detail = "Listener not running"
        else:
            parts = []
            for status in statuses:
                age = "--" if status.last_sentence_age_s is None else f"{status.last_sentence_age_s:.1f} s"
                label = status.source_id.removeprefix("external-").upper()
                parts.append(
                    f"{label}: {'running' if status.running else 'stopped'}, "
                    f"{status.sentences_received} sentences, age {age}"
                )
            detail = " | ".join(parts)
        detections = self.input_runtime.detections()
        detected = " | ".join(
            f"{item.input_type}: {item.topology} ({item.count}, {item.last_seen_age_s:.1f} s ago)"
            for item in detections
        ) or "No valid rudder input detected"
        error = self.input_runtime.last_error()
        self.input_status.setText(
            f"Policy: {self.input_type.currentText()} | Active source: {active} | {detail}\nDetected: {detected}"
            + (f"\nLast error: {error}" if error else "")
        )
        self.input_status.setToolTip(self.input_status.text())
        for channel in self.input_runtime.channel_statuses():
            lamp = self.input_lamps[(channel.input_type, channel.side)]
            if lamp.property("signalState") != channel.state:
                lamp.setProperty("signalState", channel.state)
                lamp.style().unpolish(lamp)
                lamp.style().polish(lamp)
                lamp.update()
            age_text = "never received" if channel.last_seen_age_s is None else f"{channel.last_seen_age_s:.1f} s ago"
            if channel.state == "empty":
                age_text = "sentence received; field empty"
            lamp.setToolTip(f"{channel.input_type} {channel.side}: {age_text}")

    def _build_tree(self) -> None:
        widths = {
            TransportKind.UDP: (285, 60, 210, 80, 90, 120, 70, 150),
            TransportKind.TCP: (285, 60, 190, 220, 80, 90),
            TransportKind.SERIAL: (285, 60, 220, 110, 80, 80, 90),
        }
        for path in self.store.output_paths():
            transport = path.transport
            tree = self.output_trees[path.path_id]
            tree.clear()
            rows = self._path_rows[path.path_id]
            rows.clear()
            for sensor, settings in self.store.sensors_for_path(path.path_id).items():
                parent_item = QTreeWidgetItem((sensor,))
                parent_item.setData(0, Qt.UserRole, (sensor, None, path.path_id))
                tree.addTopLevelItem(parent_item)
                self._install_row(tree, parent_item, path, sensor, None)
                for protocol in settings.protocols():
                    child = QTreeWidgetItem((protocol_display_name(protocol),))
                    child.setData(0, Qt.UserRole, (sensor, protocol, path.path_id))
                    parent_item.addChild(child)
                    self._install_row(tree, child, path, sensor, protocol)
                parent_item.setExpanded(True)
            for column, width in enumerate(widths[transport]):
                tree.setColumnWidth(column, width)

    def _install_row(
        self,
        tree: QTreeWidget,
        item: QTreeWidgetItem,
        path: OutputPathDefinition,
        sensor: str,
        protocol: str | None,
    ) -> None:
        output_transport = path.transport
        output_path_id = path.path_id
        enabled = QCheckBox()
        enabled.setTristate(protocol is None)
        enabled.clicked.connect(
            lambda checked, transport=output_transport, path_id=output_path_id, sensor_name=sensor, protocol_name=protocol: self._row_changed(
                transport, path_id, sensor_name, protocol_name, "enabled", bool(checked)
            )
        )
        enabled_box = QWidget()
        enabled_layout = QHBoxLayout(enabled_box)
        enabled_layout.setContentsMargins(8, 0, 0, 0)
        enabled_layout.addWidget(enabled)
        enabled_layout.addStretch(1)
        tree.setItemWidget(item, 1, enabled_box)

        if output_transport is TransportKind.SERIAL:
            destination = self._serial_port_editor(output_path_id, sensor, protocol)
        else:
            destination = QLineEdit()
            destination.setPlaceholderText("Mixed" if protocol is None else "")
            destination.editingFinished.connect(
                lambda transport=output_transport, path_id=output_path_id, sensor_name=sensor, protocol_name=protocol, field=destination: self._text_changed(
                    transport, path_id, sensor_name, protocol_name, "host", field.text()
                )
            )

        rate = QLineEdit()
        rate.setPlaceholderText("Mixed" if protocol is None else "Hz")
        rate.setMaximumWidth(110)
        rate.editingFinished.connect(
            lambda transport=output_transport, path_id=output_path_id, sensor_name=sensor, protocol_name=protocol, field=rate: self._numeric_changed(
                transport, path_id, sensor_name, protocol_name, "rate_hz", field.text(), float
            )
        )
        port = None
        mode = None
        ttl = None
        interface = None
        baud = None
        parity = None
        stop_bits = None

        if output_transport is TransportKind.UDP:
            tree.setItemWidget(item, 2, destination)
            port = self._port_editor(output_transport, output_path_id, sensor, protocol)
            tree.setItemWidget(item, 3, port)
            tree.setItemWidget(item, 4, rate)
            mode = QComboBox()
            if protocol is None:
                mode.addItem("Mixed", None)
            for value in UdpMode:
                mode.addItem(value.value.title(), value.value)
            mode.currentIndexChanged.connect(
                lambda _index, path_id=output_path_id, sensor_name=sensor, protocol_name=protocol, field=mode: self._row_changed(
                    TransportKind.UDP, path_id, sensor_name, protocol_name, "udp_mode", field.currentData()
                )
            )
            tree.setItemWidget(item, 5, mode)
            ttl = QLineEdit()
            ttl.setPlaceholderText("Mixed" if protocol is None else "0-255")
            ttl.editingFinished.connect(
                lambda path_id=output_path_id, sensor_name=sensor, protocol_name=protocol, field=ttl: self._numeric_changed(
                    TransportKind.UDP, path_id, sensor_name, protocol_name, "multicast_ttl", field.text(), int
                )
            )
            tree.setItemWidget(item, 6, ttl)
            interface = QLineEdit()
            interface.setPlaceholderText("Mixed" if protocol is None else "Optional")
            interface.editingFinished.connect(
                lambda path_id=output_path_id, sensor_name=sensor, protocol_name=protocol, field=interface: self._text_changed(
                    TransportKind.UDP, path_id, sensor_name, protocol_name, "interface_ip", field.text(), allow_empty=True
                )
            )
            tree.setItemWidget(item, 7, interface)
        elif output_transport is TransportKind.TCP:
            mode = QComboBox()
            if protocol is None:
                mode.addItem("Mixed", None)
            mode.addItem("Client", False)
            mode.addItem("Server", True)
            mode.currentIndexChanged.connect(
                lambda _index, path_id=output_path_id, sensor_name=sensor, protocol_name=protocol, field=mode: self._row_changed(
                    TransportKind.TCP, path_id, sensor_name, protocol_name, "tcp_server", field.currentData()
                )
            )
            tree.setItemWidget(item, 2, mode)
            tree.setItemWidget(item, 3, destination)
            port = self._port_editor(output_transport, output_path_id, sensor, protocol)
            tree.setItemWidget(item, 4, port)
            tree.setItemWidget(item, 5, rate)
        else:
            tree.setItemWidget(item, 2, destination)
            baud = self._choice_editor(output_transport, output_path_id, sensor, protocol, "baud", (4800, 9600, 19200, 38400, 57600, 115200))
            parity = self._choice_editor(output_transport, output_path_id, sensor, protocol, "parity", ("N", "E", "O"))
            stop_bits = self._choice_editor(output_transport, output_path_id, sensor, protocol, "stop_bits", (1, 2))
            tree.setItemWidget(item, 3, baud)
            tree.setItemWidget(item, 4, parity)
            tree.setItemWidget(item, 5, stop_bits)
            tree.setItemWidget(item, 6, rate)

        self._path_rows[output_path_id][(sensor, protocol)] = _RowWidgets(
            enabled=enabled,
            destination=destination,
            rate=rate,
            port=port,
            mode=mode,
            ttl=ttl,
            interface=interface,
            baud=baud,
            parity=parity,
            stop_bits=stop_bits,
        )

    def _serial_port_editor(self, output_path_id: str, sensor: str, protocol: str | None) -> QComboBox:
        editor = QComboBox()
        editor.setEditable(True)
        editor.setInsertPolicy(QComboBox.NoInsert)
        editor.setPlaceholderText("Mixed" if protocol is None else "Select or enter a port")
        self._populate_serial_port_editor(editor, "")
        editor.activated.connect(
            lambda _index, path_id=output_path_id, sensor_name=sensor, protocol_name=protocol, field=editor: self._text_changed(
                TransportKind.SERIAL,
                path_id,
                sensor_name,
                protocol_name,
                "serial_device",
                field.currentText(),
            )
        )
        if editor.lineEdit() is not None:
            editor.lineEdit().editingFinished.connect(
                lambda path_id=output_path_id, sensor_name=sensor, protocol_name=protocol, field=editor: self._text_changed(
                    TransportKind.SERIAL,
                    path_id,
                    sensor_name,
                    protocol_name,
                    "serial_device",
                    field.currentText(),
                )
            )
        return editor

    @staticmethod
    def _available_serial_ports() -> tuple[tuple[str, str], ...]:
        ports = []
        for info in QSerialPortInfo.availablePorts():
            name = info.portName().strip()
            if not name:
                continue
            description = info.description().strip() or info.manufacturer().strip()
            ports.append((name, description))
        return tuple(sorted(set(ports), key=lambda item: item[0].lower()))

    def _populate_serial_port_editor(self, editor: QComboBox, current: str) -> None:
        editor.blockSignals(True)
        editor.clear()
        available = self._serial_port_options
        names = {name for name, _description in available}
        if current and current not in names:
            editor.addItem(current, current)
            editor.setItemData(0, "Configured port is not currently reported by the operating system.", Qt.ToolTipRole)
        for name, description in available:
            editor.addItem(name, name)
            if description:
                editor.setItemData(editor.count() - 1, description, Qt.ToolTipRole)
        editor.setEditText(current)
        editor.blockSignals(False)

    def _refresh_serial_port_choices(self) -> None:
        self._serial_port_options = self._available_serial_ports()
        for path in self.store.output_paths():
            if path.transport is not TransportKind.SERIAL:
                continue
            for row in self._path_rows.get(path.path_id, {}).values():
                editor = row.destination
                if isinstance(editor, QComboBox):
                    self._populate_serial_port_editor(editor, editor.currentText().strip())

    def _port_editor(
        self,
        output_transport: TransportKind,
        output_path_id: str,
        sensor: str,
        protocol: str | None,
    ) -> QLineEdit:
        editor = QLineEdit()
        editor.setPlaceholderText("Mixed" if protocol is None else "1-65535")
        editor.setMaximumWidth(100)
        editor.editingFinished.connect(
            lambda transport=output_transport, path_id=output_path_id, sensor_name=sensor, protocol_name=protocol, field=editor: self._numeric_changed(
                transport, path_id, sensor_name, protocol_name, "port", field.text(), int
            )
        )
        return editor

    def _choice_editor(
        self,
        output_transport: TransportKind,
        output_path_id: str,
        sensor: str,
        protocol: str | None,
        name: str,
        values: tuple,
    ) -> QComboBox:
        editor = QComboBox()
        if protocol is None:
            editor.addItem("Mixed", None)
        for value in values:
            editor.addItem(str(value), value)
        editor.currentIndexChanged.connect(
            lambda _index, transport=output_transport, path_id=output_path_id, sensor_name=sensor, protocol_name=protocol, field=editor, field_name=name: self._row_changed(
                transport, path_id, sensor_name, protocol_name, field_name, field.currentData()
            )
        )
        return editor

    def refresh(self) -> None:
        if self.offline_panel is not None:
            self.offline_panel.refresh()
        if self.startup_panel is not None:
            self.startup_panel.refresh()
        expected_path_ids = tuple(path.path_id for path in self.store.output_paths())
        if tuple(self._path_rows) != expected_path_ids:
            self._rebuild_output_tabs(self._current_output_path_id())
        self._updating = True
        try:
            for path in self.store.output_paths():
                output_transport = path.transport
                rows = self._path_rows[path.path_id]
                for sensor, settings in self.store.sensors_for_path(path.path_id).items():
                    self._set_widgets(
                        rows[(sensor, None)],
                        output_transport,
                        enabled=settings.aggregate("enabled", path.transport),
                        host=settings.aggregate("host", path.transport),
                        serial_device=settings.aggregate("serial_device", path.transport),
                        port=settings.aggregate("port", path.transport),
                        rate_hz=settings.aggregate("rate_hz", path.transport),
                        udp_mode=settings.aggregate("udp_mode", path.transport),
                        multicast_ttl=settings.aggregate("multicast_ttl", path.transport),
                        interface_ip=settings.aggregate("interface_ip", path.transport),
                        baud=settings.aggregate("baud", path.transport),
                        parity=settings.aggregate("parity", path.transport),
                        stop_bits=settings.aggregate("stop_bits", path.transport),
                        tcp_server=settings.aggregate("tcp_server", path.transport),
                    )
                    for protocol in settings.protocols():
                        current = settings.get_protocol(protocol, path.transport)
                        self._set_widgets(
                            rows[(sensor, protocol)],
                            output_transport,
                            enabled=current.enabled,
                            host=current.host,
                            serial_device=current.serial_device,
                            port=current.port,
                            rate_hz=current.rate_hz,
                            udp_mode=current.udp_mode,
                            multicast_ttl=current.multicast_ttl,
                            interface_ip=current.interface_ip,
                            baud=current.baud,
                            parity=current.parity,
                            stop_bits=current.stop_bits,
                            tcp_server=current.tcp_server,
                        )
        finally:
            self._updating = False
        if self.profile_editor is not None:
            self.profile_editor.refresh_profiles(self.profile_editor.active_profile_id())

    def _set_widgets(
        self,
        row: _RowWidgets,
        output_transport: TransportKind,
        *,
        enabled,
        host,
        serial_device,
        port,
        rate_hz,
        udp_mode,
        multicast_ttl,
        interface_ip,
        baud,
        parity,
        stop_bits,
        tcp_server,
    ) -> None:
        row.enabled.setCheckState(
            Qt.PartiallyChecked if enabled is None else Qt.Checked if enabled else Qt.Unchecked
        )
        destination = serial_device if output_transport is TransportKind.SERIAL else host
        destination_text = "" if destination is None else str(destination)
        if isinstance(row.destination, QComboBox):
            if destination_text and row.destination.findData(destination_text) < 0:
                row.destination.addItem(destination_text, destination_text)
                row.destination.setItemData(
                    row.destination.count() - 1,
                    "Configured port is not currently reported by the operating system.",
                    Qt.ToolTipRole,
                )
            row.destination.setEditText(destination_text)
        else:
            row.destination.setText(destination_text)
        if row.port is not None:
            row.port.setText("" if port is None else str(port))
        row.rate.setText("" if rate_hz is None else f"{float(rate_hz):g}")
        if row.mode is not None:
            self._set_combo(row.mode, udp_mode if output_transport is TransportKind.UDP else tcp_server)
        if row.ttl is not None:
            row.ttl.setText("" if multicast_ttl is None else str(multicast_ttl))
        if row.interface is not None:
            row.interface.setText("" if interface_ip is None else str(interface_ip))
        for combo, value in ((row.baud, baud), (row.parity, parity), (row.stop_bits, stop_bits)):
            if combo is not None:
                self._set_combo(combo, value)
        if output_transport is TransportKind.TCP:
            if tcp_server is None:
                row.destination.setToolTip("TCP modes differ across this sensor's protocols.\nServer: the simulator's local address.\nClient: the remote TCP server's address.")
            elif tcp_server:
                row.destination.setToolTip(LOCAL_BIND_HELP)
            else:
                row.destination.setToolTip(REMOTE_TCP_HELP)

    @staticmethod
    def _set_combo(combo: QComboBox, value) -> None:
        lookup = value.value if isinstance(value, UdpMode) else value
        index = combo.findData(lookup)
        combo.setCurrentIndex(max(0, index))

    def _text_changed(
        self,
        output_transport: TransportKind,
        output_path_id: str,
        sensor: str,
        protocol: str | None,
        name: str,
        value: str,
        *,
        allow_empty: bool = False,
    ) -> None:
        if self._updating or (not allow_empty and not value.strip()):
            return
        self._apply(
            sensor,
            protocol,
            output_transport=output_transport,
            output_path_id=output_path_id,
            **{name: value.strip()},
        )

    def _numeric_changed(
        self,
        output_transport: TransportKind,
        output_path_id: str,
        sensor: str,
        protocol: str | None,
        name: str,
        text: str,
        converter,
    ) -> None:
        if self._updating or not text.strip():
            return
        try:
            value = converter(text.replace(",", "."))
            self._apply(
                sensor,
                protocol,
                output_transport=output_transport,
                output_path_id=output_path_id,
                **{name: value},
            )
        except (TypeError, ValueError) as error:
            show_warning(self, "Invalid output setting", str(error))
            self.refresh()

    def _row_changed(
        self,
        output_transport: TransportKind,
        output_path_id: str,
        sensor: str,
        protocol: str | None,
        name: str,
        value,
    ) -> None:
        if self._updating or value is None:
            return
        if name == "udp_mode" and not isinstance(value, UdpMode):
            value = UdpMode(str(value))
        self._apply(
            sensor,
            protocol,
            output_transport=output_transport,
            output_path_id=output_path_id,
            **{name: value},
        )

    def _apply(
        self,
        sensor: str,
        protocol: str | None,
        *,
        output_transport: TransportKind = TransportKind.UDP,
        output_path_id: str | None = None,
        **changes,
    ) -> None:
        try:
            path_id = output_path_id or self.store.default_path_id(output_transport)
            if changes.get("enabled") and output_transport is TransportKind.SERIAL:
                current = self.store.sensors_for_path(path_id)[sensor]
                devices = (
                    [current.get_protocol(protocol, output_transport).serial_device]
                    if protocol is not None
                    else [current.get_protocol(name, output_transport).serial_device for name in current.protocols()]
                )
                if any(not device for device in devices):
                    raise ValueError("Configure a serial device before enabling this output")
            if protocol is None:
                self.store.update_sensor(
                    sensor,
                    output_transport=output_transport,
                    output_path_id=path_id,
                    **changes,
                )
            else:
                self.store.update_protocol(
                    protocol,
                    output_transport=output_transport,
                    output_path_id=path_id,
                    **changes,
                )
        except (TypeError, ValueError) as error:
            show_warning(self, "Invalid output setting", str(error))
        self.refresh()
        self.on_changed()
