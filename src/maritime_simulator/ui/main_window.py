# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from pathlib import Path
from dataclasses import asdict, replace
import json
import sqlite3
import html
import sys
import time

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from maritime_simulator.application.ais import AISRepository
from maritime_simulator.application.configuration import ProtocolConfigurationStore
from maritime_simulator.application.fault_injection import FaultInjectionController
from maritime_simulator.application.paths import bundled_resource_path, user_config_dir
from maritime_simulator.application.service import SimulatorService
from maritime_simulator.application.startup_profile import StartupProfileStore
from maritime_simulator.domain.commands import (
    SeekRoute,
    SelectVesselProfile,
    SetAuxiliarySensors,
    SetControlMode,
    SetEnvironment,
    SetPosition,
    SetRudderOrder,
    SetRoute,
    SetRouteOptions,
    SetSensorCoupling,
    SetSensorNoise,
    SetSensorValues,
    SetSpeedOrder,
    SetBowThrusterOrder,
    SetTimeScale,
    UpdateRoute,
)
from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.domain.state import RouteWaypoint, sensor_noise_settings
from maritime_simulator.simulation.route_geometry import RouteGeometry, bearing_deg
from maritime_simulator.simulation.route_io import load_rtz
from maritime_simulator.transport.live_weather import LiveWeatherClient, LiveWeatherReading
from maritime_simulator.protocols.sensors import SensorProjector
from maritime_simulator.visual.water_mask import OSMNavigableWaterMask
from maritime_simulator.visual.offline import MBTiles, OfflineSettings, OfflineTileServer
from maritime_simulator.visual.offline_areas import AREAS_FILE
from maritime_simulator.visual.s57 import BASE_LAYERS, S57Store, layer_descriptions
from ui.webengine_profile import set_webengine_offline
from ui.widgets.map_widget import MapWidget

from .about_dialog import AboutV2Dialog
from .advanced_dialog import AdvancedProtocolDialog
from .instruments import SnapshotInstrumentCluster
from .input_runtime import InputRuntime
from .memory_monitor import MemoryMonitor
from .advanced_sensors import AdvancedSensorsPanel, SensorNoisePanel
from .controls import format_coordinate
from .dialogs import (
    CloseConfirmationDialog,
    OpaqueDialog,
    fit_dialog_to_available_screen,
    show_warning,
)
from .forward_view import (
    ForwardViewDialog,
    normalize_visual_renderer_mode,
    visual_settings_payload,
)
from .fault_injection import FaultInjectionPanel
from .live_ais import AISControlPanel
from .mode_panels import CompactModeControls, EnvironmentPanel, ModeControlStack
from .output_runtime import OutputRuntime
from .performance_panel import PerformancePanel
from .status_strip import StatusStrip
from .protocol_output_dialog import ProtocolOutputDialog
from .settings_dialog import ProtocolSettingsDialog
from .s57_layers import S57LayerMenu
from .route_planner import RoutePlannerPanel
from .user_guide import UserGuideDialog
from .window_geometry import WindowGeometryGuard, usable_screen_area


class _DetachedMapDialog(OpaqueDialog):
    restore_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fullscreen_shortcut = QShortcut(QKeySequence("F11"), self)
        self._fullscreen_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._fullscreen_shortcut.activated.connect(self.toggle_fullscreen)
        self._leave_fullscreen_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._leave_fullscreen_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._leave_fullscreen_shortcut.activated.connect(self.leave_fullscreen)

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def leave_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()

    def closeEvent(self, event) -> None:
        self.restore_requested.emit()
        event.accept()


class V2MainWindow(QMainWindow):
    """Responsive V2 command/snapshot shell with mode-specific workflows."""

    def __init__(self, service: SimulatorService | None = None, *, config_dir: Path | None = None):
        super().__init__()
        self.config_dir = config_dir or user_config_dir()
        self.startup_profile = StartupProfileStore(self.config_dir / "startup_profile_v2.json")
        self.service = service or SimulatorService(
            profile_file=self.config_dir / "vessel_profiles_v2.json"
        )
        self.fault_injector = FaultInjectionController()
        self.offline_settings = OfflineSettings()
        set_webengine_offline(False)
        self.offline_tiles = None
        self.s57_store = S57Store(self.config_dir / "offline-packages" / "s57")
        self._s57_layers_menu = None
        self.sensor_projector = SensorProjector(self.fault_injector)
        self.water_mask = OSMNavigableWaterMask(
            bundled_resource_path("assets", "geodata", "osm-simplified-land-polygons-3857.zip"),
            self.config_dir / "geodata-cache",
            allow_network=QApplication.platformName().lower() != "offscreen",
        )
        self.ais = AISRepository(
            water_checker=(
                None
                if QApplication.platformName().lower() == "offscreen"
                else self.water_mask.is_water
            ),
            water_sampler=(
                None
                if QApplication.platformName().lower() == "offscreen"
                else self.water_mask.sample_water_position
            ),
            water_preparer=(
                None if QApplication.platformName().lower() == "offscreen"
                else self.water_mask.prepare_ais_region
            ),
        )
        self.protocol_configuration = ProtocolConfigurationStore(self.config_dir / "protocol_output.json")
        self.output_runtime = OutputRuntime(
            self.service,
            self.ais,
            self.protocol_configuration,
            self.config_dir / "advanced_ports_v2.json",
            self,
            fault_injector=self.fault_injector,
        )
        self.input_runtime = InputRuntime(self.service, self.config_dir / "ror_input.json", self)
        self.live_weather = LiveWeatherClient(self)
        self.setWindowTitle("Maritime Ship Simulator V2")
        self.setMinimumSize(960, 640)
        self._fit_to_screen()
        self._last_ui_revision = -1
        self._last_map_revision = -1
        self._last_ais_revision = -1
        self._last_ais_map_refresh_s = 0.0
        self._last_route_signature = None
        self._map_route_source = None
        self._map_route_signature_cache = ()
        self._route_scroll_guard = False
        self.instrument_dialog: QDialog | None = None
        self.performance_dialog: QDialog | None = None
        self.settings_dialog: ProtocolSettingsDialog | None = None
        self.protocol_dialog: ProtocolOutputDialog | None = None
        self.advanced_dialog: AdvancedProtocolDialog | None = None
        self.about_dialog: AboutV2Dialog | None = None
        self.user_guide_dialog: UserGuideDialog | None = None
        self.map_dialog: _DetachedMapDialog | None = None
        self.forward_view_dialog: ForwardViewDialog | None = None
        self._map_detached = False
        self._forward_view_embedded = False
        self._keyboard_control_active = False
        self._history_reset_token = 0
        self._force_map_center_once = False
        self._configuration_baseline: dict | None = None
        self._current_configuration_path: Path | None = None
        self._close_dialog_active = False
        self.memory_monitor = None

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        outer.addWidget(self._build_status_bar())
        outer.addWidget(self._build_navigation_bar())

        self.workspace = QSplitter(Qt.Horizontal)
        self.workspace.setChildrenCollapsible(False)
        self.workspace.addWidget(self._build_left_panel())
        self.map = MapWidget()
        self.map.performance_monitor = self.service.performance_monitor
        self.map.route_event.connect(self._map_route_event)
        self.map.map_command.connect(self._map_command)
        self.map.setMinimumSize(440, 240)

        self.navigation_panel = QFrame()
        self.navigation_panel.setObjectName("panel")
        navigation_layout = QVBoxLayout(self.navigation_panel)
        navigation_layout.setContentsMargins(0, 0, 0, 0)
        navigation_layout.setSpacing(0)
        view_selector = QFrame()
        selector_layout = QHBoxLayout(view_selector)
        selector_layout.setContentsMargins(7, 5, 7, 5)
        selector_layout.setSpacing(5)
        selector_label = QLabel("Navigation View")
        selector_label.setObjectName("secondaryText")
        selector_layout.addWidget(selector_label)
        selector_layout.addStretch(1)
        self.navigation_view_group = QButtonGroup(self)
        self.navigation_view_group.setExclusive(True)
        self.navigation_view_buttons: dict[str, QPushButton] = {}
        for index, (view_name, title) in enumerate((("map", "Map"), ("3d", "3D"))):
            button = QPushButton(title)
            button.setCheckable(True)
            button.setMinimumWidth(62)
            button.setToolTip(
                "Show the interactive route map" if view_name == "map"
                else "Show the Google 3D navigation view in this area"
            )
            button.clicked.connect(
                lambda _checked=False, selected=view_name: self._show_navigation_view(selected)
            )
            self.navigation_view_group.addButton(button, index)
            self.navigation_view_buttons[view_name] = button
            selector_layout.addWidget(button)
        self.navigation_view_buttons["map"].setChecked(True)
        navigation_layout.addWidget(view_selector)
        self.navigation_stack = QStackedWidget()
        self.navigation_stack.addWidget(self.map)
        navigation_layout.addWidget(self.navigation_stack, 1)
        self.workspace.addWidget(self.navigation_panel)
        self.workspace.setStretchFactor(0, 0)
        self.workspace.setStretchFactor(1, 1)
        self.workspace.setSizes([340, 1080])
        outer.addWidget(self.workspace, 1)

        self._map_placeholder = QFrame()
        self._map_placeholder.setObjectName("panel")
        placeholder_layout = QVBoxLayout(self._map_placeholder)
        placeholder_layout.addStretch(1)
        placeholder_label = QLabel("Map is open in a separate window")
        placeholder_label.setObjectName("secondaryText")
        placeholder_label.setAlignment(Qt.AlignCenter)
        placeholder_layout.addWidget(placeholder_label)
        restore_map = QPushButton("Restore Map")
        restore_map.clicked.connect(self._restore_map)
        placeholder_layout.addWidget(restore_map, 0, Qt.AlignCenter)
        placeholder_layout.addStretch(1)

        self.route_panel = self._build_route_table()
        outer.addWidget(self.route_panel)
        self.setCentralWidget(root)
        self._install_keyboard_shortcuts()

        loaded_last_configuration = self._load_last_configuration()
        if not loaded_last_configuration:
            self._load_initial_route()
        self._apply_offline_settings(self.offline_settings)
        if self.ais.mode == "generated":
            self.ais.use_generated(self.service.snapshot())
        self.output_runtime.update_snapshot(self.service.snapshot())
        self.output_runtime.start()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start(50)
        self._refresh(force=True)
        self._configuration_baseline = self._configuration_payload()
        if not loaded_last_configuration:
            try:
                self._persist_configuration_payload(self._configuration_baseline)
            except OSError:
                # Read-only/headless smoke environments may intentionally deny
                # the platform user-config directory. Normal saves still report errors.
                pass
        self.memory_monitor = MemoryMonitor(self.water_mask, self.s57_store, self)
        self.memory_monitor.set_tile_server(self.offline_tiles)
        self.memory_monitor.sampled.connect(self._memory_sampled)
        if QApplication.platformName().lower() != "offscreen":
            self.memory_monitor.start()
        self.window_geometry_guard = WindowGeometryGuard(
            self, pending_initial_fit=self._waiting_for_display
        )

    def _memory_sampled(self, status, budget, usage):
        monitor = self.service.performance_monitor
        for name, value in {
            "memory.available_mb": status.available_mb,
            "memory.total_mb": status.total_mb,
            "memory.current_rss_mb": status.tree_rss_mb,
            "memory.main_rss_mb": status.process_rss_mb,
            "memory.children_rss_mb": status.children_rss_mb,
            "memory.swap_used_mb": status.swap_used_mb,
            "memory.paging_active": budget.paging_active,
            "memory.pressure": budget.pressure,
            "memory.cache_budget_mb": budget.cache_mb,
            "memory.cache_bytes": usage,
        }.items():
            monitor.set_gauge(name, value)
        for widget in (self.map, self.forward_view_dialog):
            page = getattr(widget, "memory_page", None)
            if page is not None:
                page.discard_if_idle(budget.pressure)
        if self.forward_view_dialog is not None:
            self.forward_view_dialog._refresh_visual_quality(memory=status)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange and hasattr(self, "map"):
            self.map._set_page_active(not self.isMinimized() and self.map.isVisible())
            if self.forward_view_dialog is not None and self._forward_view_embedded:
                self.forward_view_dialog._set_page_active(not self.isMinimized() and self.forward_view_dialog.isVisible())

    def _fit_to_screen(self) -> None:
        available = usable_screen_area(QApplication.primaryScreen())
        self._waiting_for_display = not available.isValid()
        if self._waiting_for_display:
            self.resize(1440, 860)
            return
        usable_width = max(1, available.width() - 32)
        usable_height = max(1, available.height() - 32)
        self.setMinimumSize(min(960, usable_width), min(640, usable_height))
        width = min(1500, usable_width)
        height = min(920, usable_height)
        self.resize(width, height)

    def _build_status_bar(self) -> QFrame:
        frame = StatusStrip()
        frame.setObjectName("statusBar")
        frame.setMinimumHeight(54)
        self.status_strip = frame
        self.status_fields: dict[str, QLabel] = {}
        time_cell = QFrame()
        time_cell.setObjectName("statusCell")
        time_layout = QVBoxLayout(time_cell)
        time_layout.setContentsMargins(7, 2, 7, 2)
        time_layout.setSpacing(1)
        time_header = QLabel("SIM TIME")
        time_header.setObjectName("statusHeader")
        time_header.setAlignment(Qt.AlignCenter)
        time_layout.addWidget(time_header)
        self.time_scale = QComboBox()
        self.time_scale.setObjectName("statusTimeScale")
        for value in (1, 2, 5, 10, 30):
            self.time_scale.addItem(f"{value}x", float(value))
        self.time_scale.currentIndexChanged.connect(self._time_scale_changed)
        time_layout.addWidget(self.time_scale)
        frame.add_cell(time_cell)
        definitions = (
            ("POSITION", "POSITION"),
            ("HDG", "HDG"),
            ("COG", "COG"),
            ("SOG", "SOG"),
            ("STW", "STW"),
            ("ROT", "ROT"),
            ("XTE", "XTE"),
            ("RUDDER", "RUDDER ORDER/RESPONSE"),
        )
        for column, (name, title) in enumerate(definitions, start=1):
            cell = QFrame()
            cell.setObjectName("statusCell")
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(7, 2, 7, 2)
            cell_layout.setSpacing(1)
            header = QLabel(title)
            header.setObjectName("statusHeader")
            header.setAlignment(Qt.AlignCenter)
            value = QLabel("--")
            value.setObjectName("statusValue")
            value.setAlignment(Qt.AlignCenter)
            value.setMinimumWidth(64 if name != "POSITION" else 190)
            cell_layout.addWidget(header)
            cell_layout.addWidget(value)
            frame.add_cell(cell, weight=2 if name == "POSITION" else 1, track_only=name == "XTE")
            self.status_fields[name] = value
        return frame

    def _build_navigation_bar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(5)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons = {}
        modes = (
            ("Sensor", ControlMode.SENSOR, "Sensor Control: publish exact test values"),
            ("Ship", ControlMode.SHIP, "Ship Control: control vessel model with engine and rudder"),
            ("Track", ControlMode.TRACK, "Track Control: follow the active route"),
            ("External", ControlMode.EXTERNAL_RUDDER, "External Control: independent rudder, thruster and engine sources"),
        )
        for index, (text, mode, tooltip) in enumerate(modes):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setToolTip(tooltip)
            button.clicked.connect(lambda _checked=False, value=mode: self._request_mode(value))
            self.mode_group.addButton(button, index)
            self.mode_buttons[mode] = button
            layout.addWidget(button, 1)

        self.fault_badge = QLabel("FAULT ACTIVE")
        self.fault_badge.setObjectName("faultBadge")
        self.fault_badge.setToolTip("One or more sensor or transmission faults are active.")
        self.fault_badge.hide()
        layout.addWidget(self.fault_badge)

        separator = QFrame()
        separator.setFrameShape(QFrame.VLine)
        layout.addWidget(separator)
        for text, tooltip, callback in (
            ("Instruments", "Open the snapshot-driven instrument cluster", self._open_instruments),
            ("3D Window", "Open the Google 3D navigation view in a separate window", self._open_forward_view),
            ("Output", "Open protocol output monitor and port load", self._open_protocol_output),
            ("Advanced", "Build and send validated advanced IEC sentences", self._open_advanced),
            ("Settings", "Configure sensor protocols and output ports", self._open_settings),
            ("Guide", "Open the Maritime Ship Simulator V2 user guide", self._open_user_guide),
            ("About", "Program information and performance monitor", self._open_about),
        ):
            button = QPushButton(text)
            button.setObjectName("toolButton")
            button.setToolTip(tooltip)
            button.clicked.connect(callback)
            layout.addWidget(button)
        return frame

    def _build_left_panel(self) -> QFrame:
        frame = QFrame()
        self.left_panel = frame
        frame.setObjectName("panel")
        frame.setMinimumWidth(300)
        frame.setMaximumWidth(400)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        panel_header = QHBoxLayout()
        panel_header.setContentsMargins(8, 5, 8, 0)
        panel_title = QLabel("Controls")
        panel_title.setObjectName("sectionTitle")
        panel_header.addWidget(panel_title)
        panel_header.addStretch(1)
        self.left_collapse = QToolButton()
        self.left_collapse.setArrowType(Qt.LeftArrow)
        self.left_collapse.setToolTip("Collapse to key controls.")
        self.left_collapse.clicked.connect(self._toggle_left_panel)
        panel_header.addWidget(self.left_collapse)
        layout.addLayout(panel_header)
        self.control_tabs = QTabWidget()
        self.control_tabs.setObjectName("controlTabs")
        self.control_tabs.setDocumentMode(True)
        self.mode_controls = ModeControlStack(self.service)
        self.mode_controls.command_submitted.connect(self._submit_ui_command)
        self.mode_controls.keyboard_control_changed.connect(self._set_keyboard_control)
        self.sensor_fields = self.mode_controls.sensor_fields
        self.environment_controls = EnvironmentPanel()
        self.environment_controls.command_submitted.connect(self._submit_ui_command)
        self.environment_controls.weather_source_changed.connect(self._set_weather_source)
        self.live_weather.status_changed.connect(self.environment_controls.set_weather_status)
        self.live_weather.reading_received.connect(self._apply_live_weather)
        self.ais_controls = AISControlPanel(
            self.ais,
            self.service.snapshot,
            self.config_dir / "live_ais_v2.json",
            water_status=lambda: self.water_mask.status,
        )
        self.ais_controls.output_selection_changed.connect(
            self.output_runtime.refresh_ais_selection
        )
        self.ais_controls.output_selection_changed.connect(
            lambda: self._refresh(force=True)
        )
        self.advanced_sensors = AdvancedSensorsPanel()
        self.advanced_sensors.command_submitted.connect(self._submit_ui_command)
        self.sensor_noise = SensorNoisePanel()
        self.sensor_noise.command_submitted.connect(self._submit_ui_command)
        self.fault_panel = FaultInjectionPanel(self.fault_injector)
        self.fault_panel.state_changed.connect(self._fault_state_changed)
        self.control_tabs.addTab(self._scroll(self.mode_controls), "Control")
        self.control_tabs.addTab(self._scroll(self.environment_controls), "Environment")
        self.control_tabs.addTab(self._scroll(self.ais_controls), "AIS")
        noise_index = self.control_tabs.addTab(self._scroll(self.sensor_noise), "Noise")
        other_index = self.control_tabs.addTab(self._scroll(self.advanced_sensors), "Other")
        fault_index = self.control_tabs.addTab(self._scroll(self.fault_panel), "Fault Injection")
        self.control_tabs.setTabToolTip(noise_index, "Sensor Noise")
        self.control_tabs.setTabToolTip(other_index, "Other Sensors")
        self.control_tabs.setTabToolTip(fault_index, "Sensor and transmission fault scenarios")
        layout.addWidget(self.control_tabs)
        self.compact_controls = CompactModeControls(self.service)
        self.compact_controls.command_submitted.connect(self._submit_ui_command)
        self.compact_controls.hide()
        layout.addWidget(self.compact_controls, 1)
        self._left_collapsed = False
        return frame

    def _toggle_left_panel(self) -> None:
        self._left_collapsed = not self._left_collapsed
        self.control_tabs.setVisible(not self._left_collapsed)
        self.compact_controls.setVisible(self._left_collapsed)
        self.left_collapse.setArrowType(Qt.RightArrow if self._left_collapsed else Qt.LeftArrow)
        self.left_collapse.setToolTip(
            "Expand all control tabs." if self._left_collapsed else "Collapse to key controls."
        )
        self.left_panel.setMinimumWidth(112 if self._left_collapsed else 300)
        self.left_panel.setMaximumWidth(124 if self._left_collapsed else 400)
        sizes = self.workspace.sizes()
        compact_width = 118
        self.workspace.setSizes([
            compact_width if self._left_collapsed else 340,
            max(440, sum(sizes) - compact_width),
        ])

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        widget.setMinimumWidth(0)
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        scroll.setWidget(widget)
        return scroll

    def _sensor_value_changed(self, name: str) -> None:
        """Compatibility entry used by boundary tests and scripted UI drivers."""
        self.mode_controls._sensor_changed(name)

    def _fault_state_changed(self) -> None:
        self._last_ui_revision = -1
        self.output_runtime.update_snapshot(self.service.snapshot())
        self._refresh(force=True)

    def _build_route_table(self) -> QFrame:
        planner = RoutePlannerPanel()
        planner.command_submitted.connect(self._submit_ui_command)
        planner.import_requested.connect(self._import_route)
        planner.default_requested.connect(self._load_default_route)
        self.route_table = planner.table
        self.route_status = planner.status
        self.auto_scroll = planner.auto_scroll
        self.route_planner = planner
        return planner

    def _load_initial_route(self) -> None:
        path = self.config_dir / "app_state_v2.json"
        data = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            raw_route = data.get("route") or []
            route = tuple(RouteWaypoint(**item) for item in raw_route)
            if len(route) >= 2:
                self._set_route(route)
            else:
                self._load_default_route()
        except (OSError, TypeError, ValueError):
            data = {}
            self._load_default_route()
        self._apply_app_state(data)

    def _load_last_configuration(self) -> bool:
        legacy_last_saved = self._read_json(
            self.config_dir / "last_configuration_v2.json", None
        )
        if (
            self.startup_profile.last_saved_configuration is None
            and isinstance(legacy_last_saved, dict)
        ):
            try:
                self.startup_profile.capture_all_last_saved(legacy_last_saved)
            except (OSError, TypeError, ValueError):
                pass
        last_saved = self.startup_profile.last_saved_configuration or legacy_last_saved
        has_fixed = any(mode == "fixed" for mode in self.startup_profile.modes.values())
        if not isinstance(last_saved, dict) and not has_fixed:
            return False
        try:
            payload = self.startup_profile.resolve(last_saved, self._configuration_payload())
            self._apply_configuration_payload(payload)
        except (KeyError, OSError, TypeError, ValueError):
            return False
        return True

    def _apply_app_state(self, data: dict) -> None:
        simulation = data.get("simulation", {}) if isinstance(data, dict) else {}
        weather_source = "manual"
        try:
            try:
                desired_mode = ControlMode(
                    str(simulation.get("control_mode", ControlMode.SENSOR.value))
                )
            except ValueError:
                desired_mode = ControlMode.SENSOR
            profile_id = simulation.get("vessel_profile_id")
            if profile_id:
                self._submit_ui_command(SelectVesselProfile(str(profile_id)))
            snapshot = self.service.snapshot()
            self._submit_ui_command(SetRouteOptions(
                use_planned_speed=bool(data.get("use_planned_speed", snapshot.use_planned_speed)),
                use_planned_turns=bool(data.get("use_planned_turns", snapshot.use_planned_turns)),
                control_turn_mode=str(data.get("control_turn_mode", "fixed_rot")),
                control_turn_rate_deg_min=float(data.get("control_turn_rate_deg_min", 10.0)),
                control_turn_radius_nm=float(data.get("control_turn_radius_nm", 0.10)),
                end_action=str(data.get("route_end_action", snapshot.route_end_action)),
            ))
            environment = simulation.get("environment", {})
            if environment:
                weather_source = str(environment.get("weather_source", "manual"))
                self._submit_ui_command(SetEnvironment(
                    wind_from_deg=float(environment.get("wind_from_deg", 0.0)),
                    wind_speed_kn=float(environment.get("wind_speed_kn", 0.0)),
                    current_from_deg=float(environment.get("current_from_deg", 0.0)),
                    current_speed_kn=float(environment.get("current_speed_kn", 0.0)),
                    depth_m=float(environment.get("depth_m", 18.0)),
                ))
                xdr_fields = SetAuxiliarySensors.__dataclass_fields__
                xdr_values = {
                    name: float(environment[name])
                    for name in (
                        "xdr_air_temperature_c",
                        "xdr_relative_humidity_percent",
                        "xdr_dew_point_c",
                        "xdr_surface_pressure_bar",
                    )
                    if name in environment and name in xdr_fields
                }
                if xdr_values:
                    self._submit_ui_command(SetAuxiliarySensors(**xdr_values))
            if "time_scale" in simulation:
                self._submit_ui_command(SetTimeScale(float(simulation["time_scale"])))
            if "dual_rudder" in simulation:
                self._submit_ui_command(SetAuxiliarySensors(
                    dual_rudder=bool(simulation["dual_rudder"])
                ))
            position = simulation.get("position", {})
            if isinstance(position, dict) and {"latitude_deg", "longitude_deg"} <= set(position):
                self._submit_ui_command(SetPosition(
                    latitude_deg=float(position["latitude_deg"]),
                    longitude_deg=float(position["longitude_deg"]),
                    heading_deg=(
                        None
                        if position.get("heading_deg") is None
                        else float(position["heading_deg"])
                    ),
                ))
            control = simulation.get("control", {})
            if isinstance(control, dict):
                if "speed_order_kn" in control:
                    self._submit_ui_command(SetSpeedOrder(float(control["speed_order_kn"])))
                if "rudder_order_deg" in control:
                    self._submit_ui_command(SetRudderOrder(float(control["rudder_order_deg"])))
                if "bow_thruster_percent" in control:
                    self._submit_ui_command(SetBowThrusterOrder(float(control["bow_thruster_percent"])))
            coupling = simulation.get("sensor_coupling", {})
            if isinstance(coupling, dict) and coupling:
                self._submit_ui_command(SetSensorCoupling(
                    cog_follows_heading=_optional_bool(coupling.get("cog_follows_heading")),
                    heading_follows_rot=_optional_bool(coupling.get("heading_follows_rot")),
                ))
            sensor_values = simulation.get("sensor_values", {})
            if isinstance(sensor_values, dict) and sensor_values:
                self._submit_ui_command(SetControlMode(ControlMode.SENSOR))
                allowed = SetSensorValues.__dataclass_fields__
                self._submit_ui_command(SetSensorValues(**{
                    name: value for name, value in sensor_values.items() if name in allowed
                }))
            auxiliary = simulation.get("auxiliary_sensors", {})
            if isinstance(auxiliary, dict) and auxiliary:
                allowed = SetAuxiliarySensors.__dataclass_fields__
                self._submit_ui_command(SetAuxiliarySensors(**{
                    name: value for name, value in auxiliary.items() if name in allowed
                }))
            for name, value in simulation.get("sensor_noise", {}).items():
                enabled, amplitude, period_s = sensor_noise_settings(value)
                self._submit_ui_command(SetSensorNoise(
                    name=str(name),
                    enabled=bool(enabled),
                    amplitude=float(amplitude),
                    period_s=period_s,
                ))
            ais_state = data.get("ais")
            if isinstance(ais_state, dict):
                self.ais_controls.apply_state(ais_state)
            fault_state = data.get("fault_injection")
            if isinstance(fault_state, dict):
                self.fault_panel.apply_state(fault_state)
            self._set_weather_source(weather_source)
            self._submit_ui_command(SetControlMode(desired_mode))
            self.service.advance_wall_time(0.0)
        except (KeyError, TypeError, ValueError):
            # A partial user file must not prevent startup; valid sections above remain applied.
            pass

    def _load_default_route(self) -> None:
        root = Path(__file__).resolve().parents[3]
        route_path = Path(getattr(sys, "_MEIPASS", root)) / "routes" / "default_route.rtz"
        if not route_path.exists():
            route_path = root / "routes" / "default_route.rtz"
        self._set_route(load_rtz(route_path))
        self._submit_ui_command(SetRouteOptions(
            use_planned_speed=True,
            use_planned_turns=True,
            end_action="Reverse",
        ))
        self.auto_scroll.setChecked(True)

    def _set_route(self, route) -> None:
        self._submit_ui_command(SetRoute(self._normalize_route_for_planner(route)))

    def _update_route(self, route) -> None:
        normalized = self._normalize_route_for_planner(route)
        self._submit_ui_command(UpdateRoute(normalized))
        self._last_route_signature = None

    def _populate_route_table(self, route) -> None:
        self.route_planner.set_route(tuple(route))

    @staticmethod
    def _normalize_route_for_planner(route) -> tuple[RouteWaypoint, ...]:
        """Apply UI defaults without changing lower-level/Golden-Master route semantics."""
        normalized = []
        for waypoint in route:
            mode = waypoint.turn_mode if waypoint.turn_mode in {"fixed_rot", "fixed_radius"} else "fixed_rot"
            if mode == "fixed_radius" and waypoint.turn_radius_nm is None:
                mode = "fixed_rot"
            normalized.append(replace(
                waypoint,
                speed_kn=10.0 if waypoint.speed_kn is None else float(waypoint.speed_kn),
                turn_mode=mode,
                turn_rate_deg_min=(
                    10.0 if mode == "fixed_rot" and waypoint.turn_rate_deg_min is None
                    else waypoint.turn_rate_deg_min
                ),
            ))
        return tuple(normalized)

    def _request_mode(self, mode: ControlMode) -> None:
        if mode is ControlMode.TRACK and not self.service.snapshot().route_waypoints:
            self._load_default_route()
        self.mode_controls.set_mode(mode)
        self.route_panel.setVisible(mode is ControlMode.TRACK)
        self._last_route_signature = None
        self._submit_ui_command(SetControlMode(mode))
        self._update_keyboard_shortcuts()

    def _time_scale_changed(self) -> None:
        value = self.time_scale.currentData()
        if value is not None:
            self._set_time_scale_highlight(float(value))
            self._submit_ui_command(SetTimeScale(float(value)))

    def _set_time_scale_highlight(self, value: float) -> None:
        accelerated = abs(float(value) - 1.0) > 1e-9
        if self.time_scale.property("accelerated") != accelerated:
            self.time_scale.setProperty("accelerated", accelerated)
            self.time_scale.style().unpolish(self.time_scale)
            self.time_scale.style().polish(self.time_scale)

    def _set_weather_source(self, source: str) -> None:
        normalized = "open-meteo" if str(source).strip().lower() == "open-meteo" else "manual"
        live = normalized == "open-meteo"
        self.environment_controls.set_weather_source(normalized)
        if live:
            snapshot = self.service.snapshot()
            self.live_weather.update_position(snapshot.latitude_deg, snapshot.longitude_deg)
        self.live_weather.set_enabled(live and not self.offline_settings.enabled)
        if self.offline_settings.enabled and live:
            self.environment_controls.set_weather_status("Offline: live weather paused; current values retained")

    def _apply_offline_settings(self, settings: OfflineSettings) -> None:
        settings = OfflineSettings.from_payload(settings.payload())
        if settings.enc_cells is not None and settings.enc_cells != self.offline_settings.enc_cells:
            try:
                self.s57_store.set_enabled_cells(settings.enc_cells)
            except (OSError, sqlite3.Error) as error:
                show_warning(self, "S57 cells", f"Cannot restore the selected chart cells: {error}")
                settings = replace(settings, enc_cells=self.offline_settings.enc_cells)
        directory = self.config_dir / "offline-packages"
        if not settings.map_file and (directory / AREAS_FILE).is_file():
            settings = replace(settings, map_file=AREAS_FILE, water_file=AREAS_FILE)
        water = None
        if settings.water_file:
            try:
                water = self._offline_package(directory / settings.water_file, water_mask=True)
            except (OSError, ValueError, sqlite3.Error):
                pass
        self.offline_settings = settings
        self.water_mask.set_offline(settings.enabled, water)
        if QApplication.platformName().lower() == "offscreen":
            self.water_mask.allow_network = False
        set_webengine_offline(settings.enabled)
        self.ais_controls.client.set_offline(settings.enabled)
        weather = self.environment_controls.weather_source.currentData() or "manual"
        self._set_weather_source(weather)
        package = None
        if settings.map_file:
            try:
                package = self._offline_package(directory / settings.map_file)
            except (OSError, ValueError, sqlite3.Error):
                pass
        try:
            charts = self.s57_store.has_enabled_cells()
        except (OSError, ValueError, sqlite3.Error):
            charts = False
        available = [
            {"id": "online", "label": "OpenSeaMap (Online)", "enabled": not settings.enabled},
            {"id": "protomaps", "label": "Protomaps (Offline)", "enabled": package is not None},
            {"id": "s57", "label": "S-57 (Experimental)", "enabled": charts},
        ]
        selected = settings.map_source
        if selected == "auto" or not any(row["id"] == selected and row["enabled"] for row in available):
            selected = "online" if not settings.enabled else "protomaps" if package else "s57" if charts else "schematic"
        self.water_mask.set_s57(self.s57_store if selected == "s57" else None)
        source = {"offline": False, "id": selected, "available": available}
        if selected != "online":
            if self.offline_tiles is None:
                self.offline_tiles = OfflineTileServer(self.water_mask, self.s57_store)
                if self.memory_monitor is not None:
                    self.memory_monitor.set_tile_server(self.offline_tiles)
            self.offline_tiles.select(None if selected == "s57" else package)
            source.update({
                "offline": True, "url": self.offline_tiles.url,
                "minZoom": package.min_zoom if package else 0,
                "maxZoom": package.max_zoom if package else 12,
                "attribution": (
                    html.escape(str(package.metadata.get("attribution") or "Offline raster map - verify source licence"))
                    if package else "Schematic coastlines | OpenStreetMap contributors / ODbL | not a navigation chart"
                ),
            })
            if selected == "s57":
                source.update({"url": self.offline_tiles.url, "encUrl": self.offline_tiles.enc_url,
                               "minZoom": 0, "maxZoom": 12,
                               "layers": settings.enc_layers, "base": settings.enc_base,
                               "safetyContour": settings.enc_safety_contour_m, "safetyDepth": settings.enc_safety_depth_m,
                               "attribution": "Schematic coastlines | OpenStreetMap contributors / ODbL | not a navigation chart"})
        if settings.enabled:
            if self.forward_view_dialog is not None:
                self.forward_view_dialog.hide()
                self._show_navigation_view("map")
        self.map.set_map_source(source)
        self._last_ais_revision = -1

    def _offline_package(self, path, *, water_mask=False):
        cache = getattr(self, "_offline_package_cache", None)
        if cache is None:
            cache = self._offline_package_cache = {}
        info = path.stat()
        signature = (info.st_size, info.st_mtime_ns)
        package = cache.get(water_mask)
        if package is None or package.path != path.resolve() or package.signature != signature:
            package = MBTiles(path, water_mask=water_mask)
            cache[water_mask] = package
        return package

    def _show_s57_chart(self, bounds):
        self._apply_offline_settings(replace(self.offline_settings, map_source="s57"))
        self._show_navigation_view("map")
        self.map.fit_chart_bounds(bounds)
        if self.settings_dialog is not None:
            self.settings_dialog.hide()

    def _choose_s57_layers(self):
        try:
            available = self.s57_store.layers()
            descriptions = layer_descriptions() if available else {}
        except (OSError, ValueError, sqlite3.Error, ImportError) as error:
            show_warning(self, "S57 layers", f"Cannot read S57 chart layers: {error}")
            return
        if not available:
            show_warning(self, "S57 layers", "Import or enable an S57 chart first.")
            return
        if self._s57_layers_menu is not None:
            self._s57_layers_menu.hide()
            self._s57_layers_menu.deleteLater()
        selected = set(available) & BASE_LAYERS if self.offline_settings.enc_base else set(available) if self.offline_settings.enc_layers is None else set(self.offline_settings.enc_layers)
        menu = self._s57_layers_menu = S57LayerMenu(available, descriptions, selected, self.offline_settings.enc_base, self,
            safety_contour=self.offline_settings.enc_safety_contour_m, safety_depth=self.offline_settings.enc_safety_depth_m)
        menu.selection_changed.connect(self._apply_s57_selection)
        menu.safety_changed.connect(lambda contour, depth: self._apply_offline_settings(
            replace(self.offline_settings, enc_safety_contour_m=contour, enc_safety_depth_m=depth)))
        menu.popup(QCursor.pos())

    def _apply_s57_selection(self, layers, base=False):
        try:
            self._apply_offline_settings(replace(self.offline_settings, enc_layers=layers, enc_base=base))
        except ValueError as error:
            menu = self._s57_layers_menu
            if menu is not None:
                selected = set(menu.available) & BASE_LAYERS if self.offline_settings.enc_base else (
                    set(menu.available) if self.offline_settings.enc_layers is None else set(self.offline_settings.enc_layers))
                menu.set_selection(selected, self.offline_settings.enc_base)
            show_warning(self, "S57 layers", f"Cannot apply the chart layer selection: {error}")

    def _set_s57_layer(self, layer, enabled):
        selected = set(self.s57_store.layers()) & BASE_LAYERS if self.offline_settings.enc_base else set(self.s57_store.layers()) if self.offline_settings.enc_layers is None else set(self.offline_settings.enc_layers)
        if enabled:
            selected.add(layer)
        else:
            selected.discard(layer)
        self._apply_s57_selection(tuple(sorted(selected)))

    def _apply_live_weather(self, reading: LiveWeatherReading) -> None:
        if not self.live_weather.enabled:
            return
        self._submit_ui_command(
            SetAuxiliarySensors(
                xdr_surface_pressure_bar=reading.surface_pressure_bar,
                xdr_air_temperature_c=reading.air_temperature_c,
                xdr_relative_humidity_percent=reading.relative_humidity_percent,
                xdr_dew_point_c=reading.dew_point_c,
            )
        )
        self.service.advance_wall_time(0.0)
        self._refresh()

    def _open_instruments(self) -> None:
        if self.instrument_dialog is None:
            self.instrument_dialog = OpaqueDialog(self)
            self.instrument_dialog.setWindowTitle("Maritime Ship Simulator V2 - Instrument Cluster")
            self.instrument_dialog.resize(1180, 760)
            layout = QVBoxLayout(self.instrument_dialog)
            controls = QHBoxLayout()
            hint = QLabel("Customize, then drag instruments to reorder or use X to remove them.")
            hint.setObjectName("secondaryText")
            controls.addWidget(hint)
            controls.addStretch(1)
            customize = QPushButton("Customize")
            customize.clicked.connect(self._customize_instruments)
            controls.addWidget(customize)
            reset = QPushButton("Reset Layout")
            reset.clicked.connect(self._reset_instruments)
            controls.addWidget(reset)
            layout.addLayout(controls)
            self.instrument_cluster = SnapshotInstrumentCluster(
                profile_lookup=self.service.profiles.get,
                ais_targets=self.ais.map_targets,
            )
            state_path = self.config_dir / "instrument_cluster_v2.json"
            if state_path.exists():
                try:
                    self.instrument_cluster.apply_state(json.loads(state_path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    pass
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(self.instrument_cluster)
            layout.addWidget(scroll, 1)
            if (
                self._configuration_baseline is not None
                and self._configuration_baseline.get("instrument_cluster") is None
            ):
                self._configuration_baseline["instrument_cluster"] = self.instrument_cluster.export_state()
        self.instrument_cluster.update_snapshot(self.service.snapshot())
        self._show_dialog(self.instrument_dialog)

    def _ensure_forward_view(self) -> ForwardViewDialog:
        if self.forward_view_dialog is None:
            self.forward_view_dialog = ForwardViewDialog(
                self.service.snapshot,
                self.ais.map_targets,
                self.config_dir / "visual_cache",
                self,
                self.service.profiles.get,
                settings_path=self.config_dir / "visual_settings_v2.json",
            )
            if not QApplication.instance().windowIcon().isNull():
                self.forward_view_dialog.setWindowIcon(QApplication.instance().windowIcon())
            self.forward_view_dialog.fullscreen_requested.connect(
                self._open_forward_view_fullscreen
            )
        return self.forward_view_dialog

    def _show_navigation_view(self, view_name: str) -> None:
        if view_name != "map" and self.offline_settings.enabled:
            show_warning(self, "3D requires Internet", "Offline mode is active. Use the local 2D map or disable Offline mode in Settings.")
            return
        if view_name == "map":
            target = self._map_placeholder if self._map_detached else self.map
            if self.navigation_stack.indexOf(target) < 0:
                self.navigation_stack.addWidget(target)
            self.navigation_stack.setCurrentWidget(target)
            self.navigation_view_buttons["map"].setChecked(True)
            return

        dialog = self._ensure_forward_view()
        if not self._forward_view_embedded:
            dialog.hide()
            dialog.setParent(self.navigation_stack, Qt.Widget)
            dialog.setWindowFlags(Qt.Widget)
            dialog.setMinimumSize(0, 0)
            self.navigation_stack.addWidget(dialog)
            self._forward_view_embedded = True
        self.navigation_stack.setCurrentWidget(dialog)
        self.navigation_view_buttons["3d"].setChecked(True)
        dialog.show()

    def _open_forward_view(self) -> None:
        if self.offline_settings.enabled:
            show_warning(self, "3D requires Internet", "Disable Offline mode in Settings to use Google 3D.")
            return
        dialog = self._ensure_forward_view()
        if self._forward_view_embedded:
            dialog.hide()
            self.navigation_stack.removeWidget(dialog)
            dialog.setParent(
                self,
                Qt.Dialog
                | Qt.WindowTitleHint
                | Qt.WindowMinMaxButtonsHint
                | Qt.WindowCloseButtonHint,
            )
            dialog.setWindowFlags(
                Qt.Dialog
                | Qt.WindowTitleHint
                | Qt.WindowMinMaxButtonsHint
                | Qt.WindowCloseButtonHint
            )
            dialog.setMinimumSize(760, 480)
            self._forward_view_embedded = False
            self._show_navigation_view("map")
        self._show_dialog(dialog)

    def _open_forward_view_fullscreen(self) -> None:
        dialog = self._ensure_forward_view()
        if self._forward_view_embedded:
            self._open_forward_view()
        dialog.showFullScreen()

    def _customize_instruments(self) -> None:
        if hasattr(self, "instrument_cluster"):
            self.instrument_cluster.enable_edit_mode()

    def _reset_instruments(self) -> None:
        if hasattr(self, "instrument_cluster"):
            self.instrument_cluster.reset_to_defaults()

    def _open_performance(self) -> None:
        if self.performance_dialog is None:
            self.performance_dialog = OpaqueDialog(self)
            self.performance_dialog.setWindowTitle("Maritime Ship Simulator V2 - Performance")
            self.performance_dialog.resize(720, 560)
            layout = QVBoxLayout(self.performance_dialog)
            layout.addWidget(PerformancePanel(self.service.performance_monitor, serial_loads=self.output_runtime.measured_serial_loads))
        self._show_dialog(self.performance_dialog)

    def _open_settings(self) -> None:
        if self.settings_dialog is None:
            self.settings_dialog = ProtocolSettingsDialog(
                self.protocol_configuration,
                self._protocol_settings_changed,
                self.input_runtime,
                self,
                save_configuration=self._save_configuration_as,
                load_configuration=self._load_configuration,
                profiles=self.service.profiles,
                active_profile_id=lambda: self.service.snapshot().vessel_profile_id,
                profiles_changed=self._vessel_profiles_changed,
                startup_profile=self.startup_profile,
                current_configuration=self._configuration_payload,
                startup_last_saved_changed=self._startup_last_saved_changed,
                offline_current=lambda: self.offline_settings,
                offline_changed=self._apply_offline_settings,
                offline_directory=self.config_dir / "offline-packages",
                offline_select_area=self._begin_offline_area_selection,
                offline_show_chart=self._show_s57_chart,
                offline_choose_layers=self._choose_s57_layers,
            )
        self.settings_dialog.refresh()
        self._show_dialog(self.settings_dialog)

    def _open_protocol_output(self) -> None:
        if self.protocol_dialog is None:
            self.protocol_dialog = ProtocolOutputDialog(
                output_running=self.output_runtime.is_running,
                configured_ports=self.output_runtime.configured_ports,
                output_events=self.output_runtime.events,
                clear_events=self.output_runtime.clear_events,
                serial_load=self.output_runtime.serial_load_percent,
                parent=self,
            )
        self._show_dialog(self.protocol_dialog)

    def _open_advanced(self) -> None:
        dialog = self._ensure_advanced_dialog()
        dialog.set_ports(self.output_runtime.configured_ports())
        self._show_dialog(dialog)

    def _ensure_advanced_dialog(self) -> AdvancedProtocolDialog:
        if self.advanced_dialog is None:
            self.advanced_dialog = AdvancedProtocolDialog(
                snapshot=self.service.snapshot,
                send_sentence=self.output_runtime.send_advanced,
                add_output_port=self.output_runtime.add_advanced_port,
                remove_output_port=self.output_runtime.remove_advanced_port,
                state_path=self.config_dir / "advanced_output_v2.json",
                parent=self,
            )
            if (
                self._configuration_baseline is not None
                and self._configuration_baseline.get("advanced_output") is None
            ):
                self._configuration_baseline["advanced_output"] = self.advanced_dialog.export_state()
        return self.advanced_dialog

    def _open_about(self) -> None:
        if self.about_dialog is None:
            self.about_dialog = AboutV2Dialog(self._open_performance, self)
        self._show_dialog(self.about_dialog)

    def _open_user_guide(self) -> None:
        if self.user_guide_dialog is None:
            self.user_guide_dialog = UserGuideDialog(self)
        self._show_dialog(self.user_guide_dialog)

    def _set_keyboard_control(self, active: bool) -> None:
        self._keyboard_control_active = bool(active)
        self._update_keyboard_shortcuts()
        if active:
            self.setFocus(Qt.OtherFocusReason)

    def _install_keyboard_shortcuts(self) -> None:
        commands = (
            (Qt.Key_Up, "engine", 1),
            (Qt.Key_Down, "engine", -1),
            (Qt.Key_Left, "rudder", -1),
            (Qt.Key_Right, "rudder", 1),
        )
        self._keyboard_shortcuts: list[QShortcut] = []
        for key, command, direction in commands:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(
                lambda action=command, step=direction: self._handle_keyboard_command(action, step)
            )
            shortcut.setEnabled(False)
            self._keyboard_shortcuts.append(shortcut)

    def _update_keyboard_shortcuts(self) -> None:
        if not hasattr(self, "_keyboard_shortcuts"):
            return
        enabled = (
            self._keyboard_control_active
            and self.service.snapshot().control_mode is ControlMode.SHIP
        )
        for shortcut in self._keyboard_shortcuts:
            shortcut.setEnabled(enabled)

    def _handle_keyboard_command(self, command: str, direction: int) -> None:
        if (
            not self._keyboard_control_active
            or self.service.snapshot().control_mode is not ControlMode.SHIP
        ):
            return
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QAbstractSpinBox, QComboBox)):
            return
        if command == "engine":
            self.mode_controls.step_engine(direction)
        elif command == "rudder":
            self.mode_controls.step_rudder(direction)

    def _protocol_settings_changed(self) -> None:
        self.output_runtime.restart_if_running()

    def _vessel_profiles_changed(self, profile_id: str) -> None:
        active = self.service.snapshot().vessel_profile_id
        available = {profile.profile_id for profile in self.service.profiles.all_profiles()}
        selected = active if active in available else profile_id
        if selected != active:
            self._submit_ui_command(SelectVesselProfile(selected))
            self.service.advance_wall_time(0.0)
        controller = self.service.track_controller
        if controller is not None:
            controller.reset()
        self.mode_controls.refresh_profiles(selected)
        self._last_route_signature = None
        self._refresh(force=True)

    @staticmethod
    def _show_dialog(dialog: QDialog) -> None:
        fit_dialog_to_available_screen(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _import_route(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Import RTZ route", "", "RTZ route (*.rtz);;All files (*)")
        if not path:
            return
        try:
            self._set_route(load_rtz(Path(path)))
        except (OSError, ValueError) as error:
            show_warning(self, "Route import failed", str(error))

    def _reverse_route(self) -> None:
        route = self.service.snapshot().route_waypoints
        if len(route) >= 2:
            self._update_route(tuple(reversed(route)))

    def _submit_ui_command(self, command) -> None:
        self.service.submit(command)
        if isinstance(command, (SeekRoute, SetPosition)):
            self._history_reset_token += 1
        if isinstance(command, SeekRoute):
            self._force_map_center_once = True
        if isinstance(command, (SetRoute, UpdateRoute)):
            self._populate_route_table(command.waypoints)
            self._last_route_signature = None
        if isinstance(command, (SeekRoute, SetPosition, SetRoute, UpdateRoute, SetRouteOptions, SetControlMode, SetTimeScale)):
            self.service.advance_wall_time(0.0)
            if isinstance(command, (SeekRoute, SetPosition)):
                self.ais_controls.client.own_ship_position_changed()
            self._refresh()

    def _map_command(self, command: dict) -> None:
        action = str(command.get("action", ""))
        if action == "map_source":
            selected = str(command.get("source", ""))
            if selected in {"online", "protomaps", "s57"}:
                self._apply_offline_settings(replace(self.offline_settings, map_source=selected))
            return
        if action == "s57_layers":
            self._choose_s57_layers()
            return
        if action in {"offline_area", "offline_area_cancel"}:
            if self.settings_dialog is None:
                self._open_settings()
            if self.settings_dialog is not None:
                self.settings_dialog.show()
                self.settings_dialog.raise_()
            if action == "offline_area":
                try:
                    from maritime_simulator.visual.offline_areas import normalize_area, selection_tile_count
                    selections = [normalize_area(item) for item in command["areas"]]
                    selection_tile_count(selections)
                    self.settings_dialog.offline_panel.manager.queue_areas(selections)
                except (KeyError, ValueError, TypeError) as error:
                    show_warning(self, "Offline area", str(error))
                    return
            return
        if action == "synthetic_add" and self.ais.mode == "synthetic":
            self.ais_controls.synthetic_panel.add_target_at(
                float(command["lat"]),
                float(command["lon"]),
            )
            return
        if action == "synthetic_move" and self.ais.mode == "synthetic":
            self.ais_controls.synthetic_panel.move_target(
                int(command["mmsi"]),
                float(command["lat"]),
                float(command["lon"]),
            )
            return
        if action == "synthetic_vector" and self.ais.mode == "synthetic":
            self.ais_controls.synthetic_panel.change_vector(
                int(command["mmsi"]),
                float(command["course"]),
                float(command["speed"]),
            )
            return
        if action != "expand":
            return
        if self._map_detached:
            assert self.map_dialog is not None
            self.map_dialog.toggle_fullscreen()
            return
        self._detach_map()

    def _begin_offline_area_selection(self):
        if self.settings_dialog is not None and self.settings_dialog.offline_panel.manager.job is not None:
            self.settings_dialog.show()
            self.settings_dialog.raise_()
            return
        if self.map.view is None or not self.map.loaded:
            show_warning(self, "Offline area", "The 2D map must be loaded before selecting an area.")
            return
        self._show_navigation_view("map")
        if self.settings_dialog is not None:
            self.settings_dialog.hide()
        if self._map_detached and self.map_dialog is not None:
            self.map_dialog.show()
            self.map_dialog.raise_()
        from maritime_simulator.visual.offline_areas import saved_areas
        self.map.begin_offline_area_selection(saved_areas(self.config_dir / "offline-packages"))

    def _detach_map(self) -> None:
        if self._map_detached:
            return
        map_index = self.navigation_stack.indexOf(self.map)
        if map_index < 0:
            return
        if self.map_dialog is None:
            self.map_dialog = _DetachedMapDialog(self)
            self.map_dialog.setWindowTitle("Maritime Ship Simulator V2 - Navigation Map")
            self.map_dialog.resize(1180, 820)
            self.map_dialog.restore_requested.connect(self._restore_map)
            dialog_layout = QVBoxLayout(self.map_dialog)
            dialog_layout.setContentsMargins(8, 8, 8, 8)
        self.navigation_stack.removeWidget(self.map)
        self.navigation_stack.insertWidget(map_index, self._map_placeholder)
        self.navigation_stack.setCurrentWidget(self._map_placeholder)
        self.map_dialog.layout().addWidget(self.map)
        self._map_placeholder.show()
        self.map.show()
        self._map_detached = True
        self.map.set_detached(True)
        self.navigation_view_buttons["map"].setChecked(True)
        self._show_dialog(self.map_dialog)

    def _restore_map(self) -> None:
        if not self._map_detached:
            return
        if self.map_dialog is not None:
            self.map_dialog.layout().removeWidget(self.map)
        placeholder_index = self.navigation_stack.indexOf(self._map_placeholder)
        if placeholder_index < 0:
            placeholder_index = 0
            self.navigation_stack.insertWidget(placeholder_index, self.map)
        else:
            self.navigation_stack.removeWidget(self._map_placeholder)
            self.navigation_stack.insertWidget(placeholder_index, self.map)
        self.navigation_stack.setCurrentWidget(self.map)
        self._map_placeholder.hide()
        self._map_placeholder.setParent(None)
        self.map.show()
        self.workspace.setStretchFactor(0, 0)
        self.workspace.setStretchFactor(1, 1)
        self._map_detached = False
        self.map.set_detached(False)
        self.navigation_view_buttons["map"].setChecked(True)

    def _map_route_event(self, event: dict) -> None:
        action = str(event.get("action", ""))
        snapshot = self.service.snapshot()
        route = list(snapshot.route_waypoints)
        try:
            if action == "move":
                index = int(event.get("index", -1))
                if 0 <= index < len(route):
                    route[index] = replace(
                        route[index],
                        latitude_deg=float(event["lat"]),
                        longitude_deg=float(event["lon"]),
                    )
                    self._update_route(route)
            elif action in {"append", "insert"}:
                index = len(route) if action == "append" else int(event.get("index", len(route)))
                index = max(0, min(len(route), index))
                route.insert(
                    index,
                    RouteWaypoint(
                        name=str(len(route) + 1),
                        latitude_deg=float(event["lat"]),
                        longitude_deg=float(event["lon"]),
                    ),
                )
                self._update_route(route)
            elif action == "delete":
                index = int(event.get("index", -1))
                if len(route) > 2 and 0 <= index < len(route):
                    route.pop(index)
                    self._update_route(route)
            elif action == "set_ownship":
                self._submit_ui_command(
                    SetPosition(float(event["lat"]), float(event["lon"]))
                )
            elif action == "set_heading":
                origin = RouteWaypoint(
                    "ownship",
                    snapshot.latitude_deg,
                    snapshot.longitude_deg,
                )
                target = RouteWaypoint("target", float(event["lat"]), float(event["lon"]))
                self._submit_ui_command(
                    SetPosition(
                        snapshot.latitude_deg,
                        snapshot.longitude_deg,
                        bearing_deg(origin, target),
                    )
                )
        except (KeyError, TypeError, ValueError) as error:
            show_warning(self, "Map action failed", str(error))

    def _route_scrolled(self, _value: int) -> None:
        if not self._route_scroll_guard and self.auto_scroll.isChecked():
            self.auto_scroll.setChecked(False)

    def _auto_scroll_changed(self, active: bool) -> None:
        if active:
            self._scroll_to_current_leg(self.service.snapshot().next_waypoint_index)

    def _scroll_to_current_leg(self, row: int) -> None:
        if not self.auto_scroll.isChecked() or not 0 <= row < self.route_table.rowCount():
            return
        item = self.route_table.item(row, 0)
        if item is None:
            return
        self._route_scroll_guard = True
        try:
            self.route_table.selectRow(row)
            self.route_table.scrollToItem(item, QTableWidget.PositionAtCenter)
        finally:
            self._route_scroll_guard = False

    def _tick(self) -> None:
        self.service.tick()
        self._refresh()

    def _refresh(self, force: bool = False) -> None:
        with self.service.performance_monitor.measure("ui_refresh"):
            self._refresh_from_snapshot(force)

    def _refresh_from_snapshot(self, force: bool = False) -> None:
        snapshot = self.service.snapshot()
        self.output_runtime.update_snapshot(snapshot)
        sensor_snapshot = self.sensor_projector.project(snapshot)
        display_snapshot = self.fault_injector.display_snapshot(snapshot, sensor_snapshot)
        ui_revision = (snapshot.revision, self.fault_injector.revision)
        if force or ui_revision != self._last_ui_revision:
            self._last_ui_revision = ui_revision
            fields = self.status_fields
            fields["POSITION"].setText(
                f"{format_coordinate(display_snapshot.latitude_deg, latitude=True)}  "
                f"{format_coordinate(display_snapshot.longitude_deg, latitude=False)}"
            )
            fields["HDG"].setText(f"{display_snapshot.heading_deg:05.1f}°")
            fields["COG"].setText(f"{display_snapshot.cog_deg:05.1f}°")
            fields["SOG"].setText(f"{display_snapshot.sog_kn:+.1f} kn")
            fields["STW"].setText(f"{display_snapshot.stw_kn:+.1f} kn")
            fields["ROT"].setText(f"{display_snapshot.rate_of_turn_deg_min:+.1f}°/min")
            self.status_strip.set_track_visible(snapshot.control_mode is ControlMode.TRACK)
            fields["XTE"].setText(f"{snapshot.xte_nm:+.3f} NM")
            fields["RUDDER"].setText(
                f"{snapshot.rudder_order_starboard_deg:+.1f}° / "
                f"{display_snapshot.rudder_response_starboard_deg:+.1f}°"
            )
            active_faults = self.fault_injector.active_faults()
            self.fault_badge.setVisible(bool(active_faults))
            self.fault_badge.setToolTip(
                "Active faults: " + ", ".join(active_faults)
                if active_faults else "No active faults"
            )
            time_index = self.time_scale.findData(float(snapshot.time_scale))
            if time_index >= 0 and self.time_scale.currentIndex() != time_index:
                self.time_scale.blockSignals(True)
                self.time_scale.setCurrentIndex(time_index)
                self.time_scale.blockSignals(False)
            self._set_time_scale_highlight(snapshot.time_scale)
            external_kind = snapshot.external_rudder_input_type.upper()
            external_button = self.mode_buttons[ControlMode.EXTERNAL_RUDDER]
            external_button.setText("External")
            external_button.setToolTip(
                f"External Control. Rudder: {external_kind}; independent engine and bow-thruster sources in Settings."
            )
            for mode, button in self.mode_buttons.items():
                button.setChecked(snapshot.control_mode is mode)
            self._update_keyboard_shortcuts()
            self.mode_controls.update_snapshot(display_snapshot)
            self.compact_controls.update_snapshot(display_snapshot)
            self.environment_controls.update_snapshot(display_snapshot)
            self.live_weather.update_position(snapshot.latitude_deg, snapshot.longitude_deg)
            self.advanced_sensors.update_snapshot(display_snapshot)
            self.sensor_noise.update_snapshot(snapshot)
            self.fault_panel.refresh()
            self.ais_controls.update_snapshot()
            self.route_planner.update_snapshot(snapshot)
            self.route_panel.setVisible(snapshot.control_mode is ControlMode.TRACK)
            if self.instrument_dialog is not None and self.instrument_dialog.isVisible():
                self.instrument_cluster.update_snapshot(display_snapshot)

        map_snapshot_changed = force or snapshot.revision != self._last_map_revision
        if map_snapshot_changed:
            self._last_map_revision = snapshot.revision
            self.ais.update_generated(snapshot)
            center_map = force or self._force_map_center_once
            self.map.update_ship(
                snapshot.latitude_deg,
                snapshot.longitude_deg,
                snapshot.heading_deg,
                center=center_map,
                sog=snapshot.sog_kn,
                cog=snapshot.cog_deg,
                rot=snapshot.rate_of_turn_deg_min,
                history_reset_token=self._history_reset_token,
                time_scale=snapshot.time_scale,
            )
            if center_map and self.map.loaded:
                self._force_map_center_once = False
            if snapshot.route_waypoints is not self._map_route_source:
                self._map_route_source = snapshot.route_waypoints
                self._map_route_signature_cache = tuple(
                    (
                        item.name, item.latitude_deg, item.longitude_deg, item.speed_kn,
                        item.turn_mode, item.turn_rate_deg_min, item.turn_radius_nm,
                    )
                    for item in snapshot.route_waypoints
                )
            route_signature = self._map_route_signature_cache
            visible_route = (
                route_signature
                + ((
                    snapshot.vessel_profile_id,
                    round(snapshot.speed_order_kn, 2),
                    snapshot.use_planned_speed,
                    snapshot.use_planned_turns,
                    snapshot.control_turn_mode,
                    round(snapshot.control_turn_rate_deg_min, 3),
                    round(snapshot.control_turn_radius_nm, 3),
                ),)
                if snapshot.control_mode is ControlMode.TRACK else ()
            )
            if self.map.loaded and visible_route != self._last_route_signature:
                wheel_over, turn_arcs = self.service.route_visuals() if visible_route else ([], [])
                self.map.set_route_geometry(wheel_over, turn_arcs)
                self.map.update_route(
                    [
                        {"name": item.name, "lat": item.latitude_deg, "lon": item.longitude_deg}
                        for item in snapshot.route_waypoints
                    ] if visible_route else [],
                )
                self._last_route_signature = visible_route
            self.map.set_context_mode(snapshot.control_mode.value)
            self.map.set_ais_edit_mode(self.ais.mode == "synthetic")

        now = time.monotonic()
        ais_revision = self.ais.revision
        live_refresh_due = self.ais.mode == "live" and now - self._last_ais_map_refresh_s >= 0.25
        if (
            (force or ais_revision != self._last_ais_revision or live_refresh_due)
            and self.map.ais_update_due(now, force=force)
        ):
            if self.map.update_ais(self.ais.map_targets(snapshot), force=force):
                self._last_ais_revision = self.ais.revision
                self._last_ais_map_refresh_s = now

    def closeEvent(self, event) -> None:
        if self._close_dialog_active:
            event.ignore()
            return
        current = self._configuration_payload()
        has_unsaved_changes = (
            self._configuration_baseline is not None
            and current != self._configuration_baseline
        )
        if self._interactive_close_confirmation_enabled():
            self._close_dialog_active = True
            try:
                selection = self._request_close_confirmation(has_unsaved_changes)
                if selection is None:
                    event.ignore()
                    return
                if selection == "save":
                    self._save_all_current_state()
                elif selection == "save_as":
                    if not self._save_configuration_as():
                        event.ignore()
                        return
                elif (
                    selection == "close"
                    and has_unsaved_changes
                    and self._configuration_baseline is not None
                ):
                    advanced = self._configuration_baseline.get("advanced_output")
                    if self.advanced_dialog is not None and isinstance(advanced, dict):
                        # The Advanced dialog persists itself when it closes. Restore its
                        # baseline first so Close really means close without saving.
                        self.advanced_dialog.apply_state(advanced)
                    self._persist_configuration_payload(
                        self._configuration_baseline,
                        update_last_saved=False,
                    )
            finally:
                self._close_dialog_active = False
        self.timer.stop()
        self.window_geometry_guard.close()
        if self.memory_monitor is not None:
            self.memory_monitor.stop()
        self._restore_map()
        self.ais_controls.client.disconnect_live()
        if hasattr(self, "instrument_cluster"):
            self.instrument_cluster.timer.stop()
        if self.forward_view_dialog is not None:
            self.forward_view_dialog.shutdown()
        self.live_weather.close()
        if self.settings_dialog is not None and self.settings_dialog.offline_panel is not None:
            self.settings_dialog.offline_panel.shutdown()
        if self.offline_tiles is not None:
            self.offline_tiles.close()
        self.output_runtime.close()
        self.input_runtime.close()
        self.water_mask.close()
        self.service.close()
        event.accept()

    def _interactive_close_confirmation_enabled(self) -> bool:
        return QApplication.platformName().lower() != "offscreen"

    def _request_close_confirmation(self, has_unsaved_changes: bool) -> str | None:
        return CloseConfirmationDialog.request(
            self,
            has_unsaved_changes=has_unsaved_changes,
        )

    def _save_session_state(self) -> None:
        self._write_json(self.config_dir / "app_state_v2.json", self._app_state_payload())

    def _app_state_payload(self) -> dict:
        snapshot = self.service.snapshot()
        return {
            "route": self._route_payload(snapshot.route_waypoints),
            "use_planned_speed": snapshot.use_planned_speed,
            "use_planned_turns": snapshot.use_planned_turns,
            "control_turn_mode": snapshot.control_turn_mode,
            "control_turn_rate_deg_min": snapshot.control_turn_rate_deg_min,
            "control_turn_radius_nm": snapshot.control_turn_radius_nm,
            "route_end_action": snapshot.route_end_action,
            "simulation": {
                "control_mode": snapshot.control_mode.value,
                "vessel_profile_id": snapshot.vessel_profile_id,
                "time_scale": snapshot.time_scale,
                "dual_rudder": snapshot.dual_rudder,
                "position": {
                    "latitude_deg": snapshot.latitude_deg,
                    "longitude_deg": snapshot.longitude_deg,
                    "heading_deg": snapshot.heading_deg,
                },
                "control": {
                    "speed_order_kn": snapshot.speed_order_kn,
                    "rudder_order_deg": snapshot.rudder_order_starboard_deg,
                    "bow_thruster_percent": snapshot.bow_thruster_order_percent,
                },
                "sensor_coupling": {
                    "cog_follows_heading": snapshot.cog_follows_heading,
                    "heading_follows_rot": snapshot.heading_follows_rot,
                },
                "sensor_values": {
                    "heading_deg": snapshot.heading_deg,
                    "cog_deg": snapshot.cog_deg,
                    "sog_kn": snapshot.sog_kn,
                    "stw_kn": snapshot.stw_kn,
                    "rot_deg_min": snapshot.rate_of_turn_deg_min,
                    "rudder_order_starboard_deg": snapshot.rudder_order_starboard_deg,
                    "rudder_response_starboard_deg": snapshot.rudder_response_starboard_deg,
                    "rudder_order_port_deg": snapshot.rudder_order_port_deg,
                    "rudder_response_port_deg": snapshot.rudder_response_port_deg,
                    "dual_rudder": snapshot.dual_rudder,
                    "depth_m": snapshot.depth_m,
                },
                "auxiliary_sensors": {
                    "dual_rudder": snapshot.dual_rudder,
                    "pitch_deg": snapshot.pitch_deg,
                    "roll_deg": snapshot.roll_deg,
                    "water_distance_nm": snapshot.water_distance_nm,
                    "ground_distance_nm": snapshot.ground_distance_nm,
                    "propeller_lever_demand_percent": snapshot.propeller_lever_demand_percent,
                    "thruster_number": snapshot.thruster_number,
                    "thruster_rpm_demand_percent": snapshot.thruster_rpm_demand_percent,
                    "thruster_pitch_demand_percent": snapshot.thruster_pitch_demand_percent,
                    "thruster_azimuth_demand_deg": snapshot.thruster_azimuth_demand_deg,
                    "thruster_rpm_response_percent": snapshot.thruster_rpm_response_percent,
                    "thruster_pitch_response_percent": snapshot.thruster_pitch_response_percent,
                    "thruster_azimuth_response_deg": snapshot.thruster_azimuth_response_deg,
                    "barometric_pressure_bar": snapshot.barometric_pressure_bar,
                    "air_temperature_c": snapshot.air_temperature_c,
                    "water_temperature_c": snapshot.water_temperature_c,
                    "relative_humidity_percent": snapshot.relative_humidity_percent,
                    "absolute_humidity_percent": snapshot.absolute_humidity_percent,
                    "dew_point_c": snapshot.dew_point_c,
                },
                "sensor_noise": {
                    name: list(sensor_noise_settings(settings))
                    for name, settings in snapshot.sensor_noise
                },
                "environment": {
                    "wind_from_deg": snapshot.wind_from_deg,
                    "wind_speed_kn": snapshot.wind_speed_kn,
                    "current_from_deg": snapshot.current_from_deg,
                    "current_speed_kn": snapshot.current_speed_kn,
                    "depth_m": snapshot.depth_m,
                    "xdr_air_temperature_c": snapshot.xdr_air_temperature_c,
                    "xdr_relative_humidity_percent": snapshot.xdr_relative_humidity_percent,
                    "xdr_dew_point_c": snapshot.xdr_dew_point_c,
                    "xdr_surface_pressure_bar": snapshot.xdr_surface_pressure_bar,
                    "weather_source": str(
                        self.environment_controls.weather_source.currentData() or "manual"
                    ),
                },
            },
            "ais": self.ais_controls.export_state(),
            "fault_injection": self.fault_panel.export_state(),
        }

    @staticmethod
    def _route_payload(route) -> list[dict]:
        return [
            {
                "name": item.name,
                "latitude_deg": item.latitude_deg,
                "longitude_deg": item.longitude_deg,
                "speed_kn": item.speed_kn,
                "turn_rate_deg_min": item.turn_rate_deg_min,
                "turn_radius_nm": item.turn_radius_nm,
                "turn_mode": item.turn_mode,
            }
            for item in route
        ]

    def _configuration_payload(self) -> dict:
        advanced_path = self.config_dir / "advanced_output_v2.json"
        instruments_path = self.config_dir / "instrument_cluster_v2.json"
        advanced = (
            self.advanced_dialog.export_state()
            if self.advanced_dialog is not None
            else self._read_json(advanced_path, None)
        )
        instruments = (
            self.instrument_cluster.export_state()
            if hasattr(self, "instrument_cluster")
            else self._read_json(instruments_path, None)
        )
        visual_settings = (
            self.forward_view_dialog.export_settings()
            if self.forward_view_dialog is not None
            else visual_settings_payload(self.config_dir / "visual_settings_v2.json")
        )
        return {
            "format": "maritime-ship-simulator-v2-configuration",
            "format_version": 1,
            "offline": self.offline_settings.payload(),
            "app_state": self._app_state_payload(),
            "protocol_output": self.protocol_configuration.to_payload(),
            "external_rudder_input": asdict(self.input_runtime.settings),
            "advanced_output": advanced,
            "advanced_ports": self.output_runtime.advanced_ports_payload(),
            "instrument_cluster": instruments,
            "visual_settings": visual_settings,
            "vessel_profiles": self.service.profiles.to_payload(),
        }

    def _save_all_current_state(self) -> None:
        payload = self._configuration_payload()
        self._persist_configuration_payload(payload)
        self._configuration_baseline = payload

    def _save_configuration_as(self) -> bool:
        suggested = str(
            self._current_configuration_path
            or (Path.home() / "Maritime-Ship-Simulator-V2-Configuration.json")
        )
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "Save complete simulator configuration",
            suggested,
            "Simulator configuration (*.json)",
        )
        if not path:
            return False
        target = Path(path)
        if target.suffix.lower() != ".json":
            target = target.with_suffix(".json")
        try:
            payload = self._configuration_payload()
            self._write_json(target, payload)
        except (OSError, TypeError, ValueError) as error:
            show_warning(self, "Configuration could not be saved", str(error))
            return False
        self._current_configuration_path = target
        self._configuration_baseline = payload
        return True

    def _load_configuration(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Load complete simulator configuration",
            "",
            "Simulator configuration (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            self._apply_configuration_payload(payload)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            show_warning(self, "Configuration could not be loaded", str(error))
            return
        self._current_configuration_path = Path(path)
        self._configuration_baseline = self._configuration_payload()
        if self.settings_dialog is not None:
            self.settings_dialog.refresh()
            self.settings_dialog._load_input_fields()
        self._refresh(force=True)

    def _apply_configuration_payload(self, payload: dict) -> None:
        if payload.get("format") != "maritime-ship-simulator-v2-configuration":
            raise ValueError("The selected file is not a Maritime Ship Simulator V2 configuration")
        if int(payload.get("format_version", -1)) != 1:
            raise ValueError("Unsupported simulator configuration version")
        self._apply_offline_settings(OfflineSettings.from_payload(payload.get("offline")))
        protocol_payload = payload.get("protocol_output")
        if not isinstance(protocol_payload, dict):
            raise ValueError("Configuration has no protocol-output section")
        self.protocol_configuration.apply_payload(protocol_payload)
        vessel_profiles = payload.get("vessel_profiles")
        if isinstance(vessel_profiles, list):
            self.service.profiles.apply_payload(vessel_profiles)
            self.mode_controls.refresh_profiles()
        self.input_runtime.stop()
        input_settings = payload.get("external_rudder_input", {})
        if isinstance(input_settings, dict):
            self.input_runtime.update(**input_settings)
        self.input_runtime.ensure_started()
        app_state = payload.get("app_state", {})
        if not isinstance(app_state, dict):
            raise ValueError("Configuration has no app-state section")
        raw_route = app_state.get("route", [])
        route = tuple(RouteWaypoint(**item) for item in raw_route)
        if len(route) >= 2:
            self._set_route(route)
        else:
            self._load_default_route()
        self._apply_app_state(app_state)
        advanced = payload.get("advanced_output")
        advanced_ports = payload.get("advanced_ports", [])
        if isinstance(advanced_ports, list):
            self.output_runtime.apply_advanced_ports_payload(advanced_ports)
        if isinstance(advanced, dict):
            self._write_json(self.config_dir / "advanced_output_v2.json", advanced)
            if self.advanced_dialog is not None or _advanced_has_active_cycles(advanced):
                dialog = self._ensure_advanced_dialog()
                dialog.set_ports(self.output_runtime.configured_ports())
                dialog.apply_state(advanced)
        instruments = payload.get("instrument_cluster")
        if isinstance(instruments, dict):
            self._write_json(self.config_dir / "instrument_cluster_v2.json", instruments)
            if hasattr(self, "instrument_cluster"):
                self.instrument_cluster.apply_state(instruments)
        visual_settings = payload.get("visual_settings")
        if isinstance(visual_settings, dict):
            if self.forward_view_dialog is not None:
                self.forward_view_dialog.apply_settings(visual_settings)
            else:
                self._write_json(
                    self.config_dir / "visual_settings_v2.json",
                    {
                        "google_tiles_api_key": str(
                            visual_settings.get("google_tiles_api_key", "")
                        ).strip(),
                        "visual_quality_mode": str(
                            visual_settings.get("visual_quality_mode", "auto")
                        ).strip().lower(),
                        "renderer_mode": normalize_visual_renderer_mode(
                            visual_settings.get("renderer_mode", "auto")
                        ),
                    },
                )
        self.ais_controls.settings_dialog.save_settings()
        self.output_runtime.update_snapshot(self.service.snapshot())
        self.output_runtime.start()
        self._save_session_state()

    def _persist_configuration_payload(
        self,
        payload: dict,
        *,
        update_last_saved: bool = True,
    ) -> None:
        if payload.get("format") != "maritime-ship-simulator-v2-configuration":
            raise ValueError("Invalid simulator configuration payload")
        app_state = payload.get("app_state")
        protocol_output = payload.get("protocol_output")
        external_input = payload.get("external_rudder_input")
        if not all(isinstance(section, dict) for section in (app_state, protocol_output, external_input)):
            raise ValueError("Simulator configuration is incomplete")
        if update_last_saved:
            self.startup_profile.capture_all_last_saved(payload)
        self._write_json_if_changed(self.config_dir / "app_state_v2.json", app_state)
        self._write_json_if_changed(self.config_dir / "protocol_output.json", protocol_output)
        self._write_json_if_changed(self.config_dir / "ror_input.json", external_input)
        ais = app_state.get("ais", {})
        live = ais.get("live") if isinstance(ais, dict) else None
        if isinstance(live, dict):
            self._write_json_if_changed(self.config_dir / "live_ais_v2.json", live)
        advanced = payload.get("advanced_output")
        if isinstance(advanced, dict):
            self._write_json_if_changed(self.config_dir / "advanced_output_v2.json", advanced)
        advanced_ports = payload.get("advanced_ports")
        if isinstance(advanced_ports, list):
            self._write_json_if_changed(
                self.config_dir / "advanced_ports_v2.json",
                advanced_ports,
            )
        instruments = payload.get("instrument_cluster")
        if isinstance(instruments, dict):
            self._write_json_if_changed(self.config_dir / "instrument_cluster_v2.json", instruments)
        visual_settings = payload.get("visual_settings")
        if isinstance(visual_settings, dict):
            self._write_json_if_changed(
                self.config_dir / "visual_settings_v2.json",
                visual_settings,
            )
        if update_last_saved:
            self._write_json_if_changed(
                self.config_dir / "last_configuration_v2.json",
                payload,
            )

    def _startup_last_saved_changed(self, payload: dict) -> None:
        if payload.get("format") != "maritime-ship-simulator-v2-configuration":
            raise ValueError("Invalid Last Saved startup configuration")
        self._write_json_if_changed(
            self.config_dir / "last_configuration_v2.json",
            payload,
        )

    @staticmethod
    def _read_json(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
        except (OSError, ValueError):
            return default

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _write_json_if_changed(path: Path, payload: dict) -> bool:
        serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        try:
            if path.exists() and path.read_text(encoding="utf-8") == serialized:
                return False
        except OSError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(path)
        return True


def _optional_bool(value) -> bool | None:
    return None if value is None else bool(value)


def _advanced_has_active_cycles(state: dict) -> bool:
    return any(
        isinstance(row, dict) and bool(row.get("cycle_active"))
        for key in ("protocols", "free_text")
        for row in state.get(key, ())
    )
