# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import replace
import math

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QToolButton,
    QWidget,
)

from maritime_simulator.domain.commands import SeekRoute, SetRouteOptions, UpdateRoute
from ui.widgets.slider import ResetSlider
from maritime_simulator.domain.state import RouteWaypoint, SimulationSnapshot
from maritime_simulator.simulation.route_geometry import (
    RouteGeometry,
    bearing_deg,
    radius_from_rot,
    signed_angle_delta,
)
from .controls import format_controller_phase, format_coordinate, parse_coordinate
from .dialogs import show_warning


class RoutePlanTable(QTableWidget):
    manual_navigation = Signal()
    paste_requested = Signal(int, int, object)

    def wheelEvent(self, event) -> None:
        self.manual_navigation.emit()
        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.Copy):
            self._copy_selection()
            return
        if event.matches(QKeySequence.Paste):
            self._paste_selection()
            return
        if event.key() in {Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End}:
            self.manual_navigation.emit()
        super().keyPressEvent(event)

    def _copy_selection(self) -> None:
        ranges = self.selectedRanges()
        if not ranges:
            return
        selection = ranges[0]
        lines = []
        for row in range(selection.topRow(), selection.bottomRow() + 1):
            lines.append("\t".join(
                self.item(row, column).text() if self.item(row, column) else ""
                for column in range(selection.leftColumn(), selection.rightColumn() + 1)
            ))
        QApplication.clipboard().setText("\n".join(lines))

    def _paste_selection(self) -> None:
        start = self.currentIndex()
        if not start.isValid():
            return
        rows = [line.split("\t") for line in QApplication.clipboard().text().splitlines() if line]
        if rows:
            self.paste_requested.emit(start.row(), start.column(), rows)


class RoutePlannerPanel(QFrame):
    """Editable route workflow built around immutable RouteWaypoint values."""

    command_submitted = Signal(object)
    import_requested = Signal()
    default_requested = Signal()
    route_removed = Signal()

    NAME, LAT, LON, SPEED, ROT, RADIUS, LEG, BEARING, DTG, TTG_LEG, TTG_TOTAL = range(11)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setMinimumHeight(88)
        self.setMaximumHeight(360)
        self._route: tuple[RouteWaypoint, ...] = ()
        self._updating = False
        self._scroll_guard = False
        self._collapsed = False
        self._last_auto_scroll_row = None
        self._current_leg_row = -1
        self._total_nm = 0.0
        self._pending_seek_nm = 0.0
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.setInterval(35)
        self._seek_timer.timeout.connect(self._emit_pending_seek)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 8)
        root.setSpacing(5)
        header = QHBoxLayout()
        title = QLabel("Route Planning")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        self.status = QLabel("No route")
        self.status.setObjectName("secondaryText")
        header.addWidget(self.status)
        header.addStretch(1)
        self.collapse_button = QToolButton()
        self.collapse_button.setArrowType(Qt.DownArrow)
        self.collapse_button.setToolTip("Collapse Route Planning to its status and route-position controls.")
        self.collapse_button.clicked.connect(self._toggle_collapsed)
        header.addWidget(self.collapse_button)
        root.addLayout(header)

        seek = QHBoxLayout()
        seek.addWidget(QLabel("Route position"))
        self.position_slider = ResetSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 10000)
        self.position_slider.sliderMoved.connect(self._seek_slider)
        self.position_slider.sliderReleased.connect(self._seek_release)
        seek.addWidget(self.position_slider, 1)
        self.position_spin = QDoubleSpinBox()
        self.position_spin.setDecimals(2)
        self.position_spin.setSuffix(" NM")
        self.position_spin.setKeyboardTracking(False)
        self.position_spin.editingFinished.connect(self._seek_spin)
        seek.addWidget(self.position_spin)
        self.position_label = QLabel("0.00 / 0.00 NM")
        self.position_label.setObjectName("dataReadout")
        seek.addWidget(self.position_label)
        root.addLayout(seek)

        self.details = QWidget()
        details = QVBoxLayout(self.details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(5)
        actions = QHBoxLayout()
        for text, tooltip, callback in (
            ("Import .RTZ", "Load a route from an RTZ file.", self.import_requested.emit),
            ("Default Route", "Restore the bundled default route.", self.default_requested.emit),
            ("Add WP", "Add a waypoint after the selected row.", self._add_waypoint),
            ("Remove WP", "Remove selected waypoint rows.", self._remove_waypoints),
            ("Remove Route", "Clear the active route.", self._remove_route),
            ("Reverse Route", "Reverse waypoint order and re-synchronise progress.", self._reverse_route),
        ):
            button = QPushButton(text)
            button.setToolTip(tooltip)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch(1)
        details.addLayout(actions)

        options = QHBoxLayout()
        self.use_planned_speed = QCheckBox("Use planned speeds")
        self.use_planned_speed.setToolTip("Apply each active waypoint's planned speed instead of the manual telegraph order.")
        self.use_planned_speed.toggled.connect(
            lambda active: not self._updating and self.command_submitted.emit(SetRouteOptions(use_planned_speed=active))
        )
        options.addWidget(self.use_planned_speed)
        self.use_planned_turns = QCheckBox("Use planned Turn Rate / Radius")
        self.use_planned_turns.setToolTip(
            "Use each waypoint's planned ROT or radius. Clear this to use the Track Control values for every turn."
        )
        self.use_planned_turns.toggled.connect(
            lambda active: not self._updating
            and self.command_submitted.emit(SetRouteOptions(use_planned_turns=active))
        )
        options.addWidget(self.use_planned_turns)
        self.auto_scroll = QCheckBox("Auto-scroll to current leg")
        self.auto_scroll.setChecked(True)
        self.auto_scroll.toggled.connect(self._auto_scroll_changed)
        options.addWidget(self.auto_scroll)
        options.addStretch(1)
        options.addWidget(QLabel("Route end"))
        self.end_action = QComboBox()
        self.end_action.setMinimumWidth(112)
        self.end_action.addItems(("Stop", "Restart", "Reverse"))
        self.end_action.currentTextChanged.connect(
            lambda value: not self._updating and self.command_submitted.emit(SetRouteOptions(end_action=value))
        )
        options.addWidget(self.end_action)
        details.addLayout(options)

        self.table = RoutePlanTable(0, 11)
        self.table.setObjectName("routePlanningTable")
        self.table.setHorizontalHeaderLabels((
            "WP", "LAT", "LON", "Speed (kn)", "Turn Rate (°/min)", "Turn Radius (NM)",
            "Leg NM", "Bearing", "DTG Leg", "TTG Leg", "TTG Total",
        ))
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.cellChanged.connect(self._cell_changed)
        self.table.manual_navigation.connect(lambda: self.auto_scroll.setChecked(False))
        self.table.verticalScrollBar().sliderPressed.connect(lambda: self.auto_scroll.setChecked(False))
        self.table.paste_requested.connect(self._paste)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.Interactive)
        for column, width in {
            self.NAME: 155, self.LAT: 112, self.LON: 112, self.SPEED: 100,
            self.ROT: 145, self.RADIUS: 140, self.LEG: 78, self.BEARING: 88,
            self.DTG: 88, self.TTG_LEG: 88, self.TTG_TOTAL: 95,
        }.items():
            self.table.setColumnWidth(column, width)
        details.addWidget(self.table, 1)
        root.addWidget(self.details, 1)

    def set_route(self, route: tuple[RouteWaypoint, ...]) -> None:
        self._route = tuple(route)
        self._rebuild()

    def update_snapshot(self, snapshot: SimulationSnapshot) -> None:
        if snapshot.route_waypoints != self._route:
            self.set_route(snapshot.route_waypoints)
        geometry = RouteGeometry(snapshot.route_waypoints)
        self._total_nm = geometry.total_distance_nm
        self._updating = True
        try:
            self.use_planned_speed.setChecked(snapshot.use_planned_speed)
            self.use_planned_turns.setChecked(snapshot.use_planned_turns)
            index = self.end_action.findText(snapshot.route_end_action)
            if index >= 0:
                self.end_action.setCurrentIndex(index)
            self.position_spin.setRange(0.0, self._total_nm)
            if not self.position_spin.hasFocus():
                self.position_spin.setValue(snapshot.route_progress_nm)
            if not self.position_slider.isSliderDown():
                slider_value = 0 if self._total_nm <= 0.0 else round(snapshot.route_progress_nm / self._total_nm * 10000.0)
                self.position_slider.setValue(max(0, min(10000, slider_value)))
            self.position_label.setText(f"{snapshot.route_progress_nm:.2f} / {self._total_nm:.2f} NM")
        finally:
            self._updating = False

        next_name, leg_bearing, leg_distance = "--", "--", "--"
        index = snapshot.next_waypoint_index
        self._current_leg_row = index
        if self._route and 0 <= index < len(self._route):
            next_name = self._route[index].name
            if index > 0:
                leg_bearing = f"{bearing_deg(self._route[index - 1], self._route[index]):05.1f}°"
                leg_start = geometry.ticks_nm[index - 1]
                leg_total = geometry.ticks_nm[index] - leg_start
                leg_elapsed = max(0.0, min(leg_total, snapshot.route_progress_nm - leg_start))
                leg_distance = f"{leg_elapsed:.2f} / {leg_total:.2f} NM"
            self._update_progress_columns(snapshot, geometry)
            self._scroll_to_row(index)
        self.status.setText(
            f"Next WP: {next_name}  |  Leg Bearing: {leg_bearing}  |  "
            f"Elapsed / Total Leg Distance: {leg_distance}  |  "
            f"Controller Phase: {format_controller_phase(snapshot.track_phase)}"
        )

    def _rebuild(self) -> None:
        self._last_auto_scroll_row = None
        self._updating = True
        self.table.setUpdatesEnabled(False)
        try:
            geometry = RouteGeometry(self._route)
            self._total_nm = geometry.total_distance_nm
            self.table.setRowCount(len(self._route))
            for row, waypoint in enumerate(self._route):
                leg = 0.0 if row == 0 else geometry.ticks_nm[row] - geometry.ticks_nm[row - 1]
                bearing = "--" if row == 0 else f"{bearing_deg(self._route[row - 1], waypoint):.1f}"
                values = (
                    waypoint.name,
                    format_coordinate(waypoint.latitude_deg, latitude=True),
                    format_coordinate(waypoint.longitude_deg, latitude=False),
                    f"{10.0 if waypoint.speed_kn is None else waypoint.speed_kn:.1f}",
                    (
                        "Calculated"
                        if waypoint.turn_mode == "fixed_radius"
                        else f"{10.0 if waypoint.turn_rate_deg_min is None else waypoint.turn_rate_deg_min:.1f}"
                    ),
                    (
                        "Calculated"
                        if waypoint.turn_mode != "fixed_radius"
                        else f"{0.10 if waypoint.turn_radius_nm is None else waypoint.turn_radius_nm:.1f}"
                    ),
                    f"{leg:.3f}", bearing, "", "", "",
                )
                for column, text in enumerate(values):
                    item = QTableWidgetItem(text)
                    if column in {self.LEG, self.BEARING, self.DTG, self.TTG_LEG, self.TTG_TOTAL}:
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    self.table.setItem(row, column, item)
            self._highlight_geometry_conflicts(geometry)
        finally:
            self.table.setUpdatesEnabled(True)
            self._updating = False

    def _cell_changed(self, row: int, column: int) -> None:
        if self._updating or not 0 <= row < len(self._route):
            return
        route = list(self._route)
        text = self.table.item(row, column).text().strip()
        try:
            waypoint = self._waypoint_with_cell_value(route[row], row, column, text)
            if waypoint is None:
                return
        except ValueError as error:
            show_warning(self, "Invalid route value", str(error))
            self._rebuild()
            return
        route[row] = waypoint
        self._route = tuple(route)
        self.command_submitted.emit(UpdateRoute(self._route))
        self._rebuild()

    def _waypoint_with_cell_value(
        self,
        waypoint: RouteWaypoint,
        row: int,
        column: int,
        text: str,
    ) -> RouteWaypoint | None:
        """Validate one editable cell without publishing an intermediate route."""
        if column == self.NAME:
            return replace(waypoint, name=text or str(row + 1))
        if column == self.LAT:
            latitude = parse_coordinate(text, latitude=True)
            if not -90.0 <= latitude <= 90.0:
                raise ValueError("Latitude must be in [-90, 90]")
            return replace(waypoint, latitude_deg=latitude)
        if column == self.LON:
            longitude = parse_coordinate(text, latitude=False)
            if not -180.0 <= longitude <= 180.0:
                raise ValueError("Longitude must be in [-180, 180]")
            return replace(waypoint, longitude_deg=longitude)
        if column == self.SPEED:
            if not text or text.lower() == "manual":
                raise ValueError("Enter a numeric planned speed; use the checkbox to select manual speed control")
            speed = round(float(text), 1)
            return replace(waypoint, speed_kn=speed)
        if column == self.ROT:
            if text.lower() == "calculated":
                return waypoint
            if not text or text.lower() == "manual":
                raise ValueError("Enter a numeric turn rate; use the checkbox to select Track Control values")
            value = max(0.1, round(float(text), 1))
            return replace(waypoint, turn_mode="fixed_rot", turn_rate_deg_min=value)
        if column == self.RADIUS:
            if text.lower() == "calculated":
                return waypoint
            if not text or text.lower() == "manual":
                raise ValueError("Enter a numeric turn radius; use the checkbox to select Track Control values")
            value = max(0.1, round(float(text), 1))
            return replace(waypoint, turn_mode="fixed_radius", turn_radius_nm=value)
        return None

    def _highlight_geometry_conflicts(self, geometry: RouteGeometry) -> None:
        """Flag overlapping turn plans without modifying speed, radius, or ROT."""
        turns: list[tuple[int, float, float]] = []
        conflicted: set[int] = set()
        for index in range(1, len(self._route) - 1):
            waypoint = self._route[index]
            speed = max(0.1, abs(waypoint.speed_kn or 10.0))
            if waypoint.turn_mode == "fixed_radius":
                radius = max(0.1, waypoint.turn_radius_nm or 0.1)
            else:
                radius = radius_from_rot(speed, max(0.1, waypoint.turn_rate_deg_min or 10.0))
            inbound = bearing_deg(self._route[index - 1], waypoint)
            outbound = bearing_deg(waypoint, self._route[index + 1])
            angle = abs(signed_angle_delta(outbound, inbound))
            if angle <= 2.0:
                continue
            lead = radius * math.tan(math.radians(min(angle, 150.0)) / 2.0)
            start = geometry.ticks_nm[index] - lead
            end = geometry.ticks_nm[index] + lead
            turns.append((index, start, end))
            if start < geometry.ticks_nm[index - 1] or end > geometry.ticks_nm[index + 1]:
                conflicted.add(index)

        transition_at: dict[int, float] = {}
        for current, following in zip(turns, turns[1:]):
            if following[1] < current[2]:
                midpoint = (current[2] + following[1]) / 2.0
                conflicted.update((current[0], following[0]))
                transition_at[current[0]] = midpoint
                transition_at[following[0]] = midpoint

        for row in conflicted:
            midpoint = transition_at.get(row)
            detail = (
                " Adjacent turn transitions use their average route position "
                f"({midpoint:.2f} NM) as the visual conflict marker."
                if midpoint is not None
                else ""
            )
            tooltip = (
                "Planned turn geometry exceeds the available leg space. The requested values are retained "
                "and highlighted; speed, radius, and ROT are not forced." + detail
            )
            for column in (self.ROT, self.RADIUS):
                item = self.table.item(row, column)
                if item is not None:
                    item.setForeground(QColor("#ff737d"))
                    item.setToolTip(tooltip)

    def _add_waypoint(self) -> None:
        selected = self.table.currentRow()
        index = len(self._route) if selected < 0 else selected + 1
        if self._route:
            source = self._route[min(len(self._route) - 1, max(0, selected))]
            waypoint = replace(source, name=f"WP{len(self._route) + 1}")
        else:
            waypoint = RouteWaypoint("WP1", 52.0, 3.0, speed_kn=10.0, turn_rate_deg_min=10.0)
        route = list(self._route)
        route.insert(index, waypoint)
        self._emit_route(route)

    def _remove_waypoints(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        route = list(self._route)
        for row in rows:
            if 0 <= row < len(route):
                route.pop(row)
        self._emit_route(route)

    def _remove_route(self) -> None:
        self._emit_route(())
        self.route_removed.emit()

    def _reverse_route(self) -> None:
        self._emit_route(tuple(reversed(self._route)))

    def _emit_route(self, route) -> None:
        self._route = tuple(route)
        self.command_submitted.emit(UpdateRoute(self._route))
        self._rebuild()

    def _paste(self, start_row: int, start_column: int, rows: object) -> None:
        route = list(self._route)
        try:
            for row_offset, values in enumerate(rows):
                row = start_row + row_offset
                if not 0 <= row < len(route):
                    break
                for column_offset, value in enumerate(values):
                    column = start_column + column_offset
                    if column >= self.table.columnCount():
                        break
                    item = self.table.item(row, column)
                    if item and item.flags() & Qt.ItemIsEditable:
                        updated = self._waypoint_with_cell_value(
                            route[row], row, column, str(value).strip()
                        )
                        if updated is not None:
                            route[row] = updated
        except (TypeError, ValueError) as error:
            show_warning(self, "Invalid pasted route value", str(error))
            self._rebuild()
            return
        self._emit_route(route)

    def _context_menu(self, position) -> None:
        index = self.table.indexAt(position)
        if not index.isValid() or index.column() not in {self.SPEED, self.ROT, self.RADIUS}:
            return
        menu = QMenu(self)
        action = menu.addAction("Apply this value to all waypoints")
        selected = menu.exec(self.table.viewport().mapToGlobal(position))
        if selected is not action:
            return
        text = self.table.item(index.row(), index.column()).text()
        self._apply_value_to_all(index.column(), text)

    def _apply_value_to_all(self, column: int, text: str) -> None:
        if column not in {self.SPEED, self.ROT, self.RADIUS}:
            return
        route = list(self._route)
        try:
            for row, waypoint in enumerate(route):
                updated = self._waypoint_with_cell_value(waypoint, row, column, text.strip())
                if updated is not None:
                    route[row] = updated
        except ValueError as error:
            show_warning(self, "Invalid route value", str(error))
            self._rebuild()
            return
        self._emit_route(route)

    def _seek_slider(self, value: int) -> None:
        progress = self._total_nm * value / 10000.0
        self._pending_seek_nm = progress
        self.position_label.setText(f"{progress:.2f} / {self._total_nm:.2f} NM")
        self.position_spin.blockSignals(True)
        self.position_spin.setValue(progress)
        self.position_spin.blockSignals(False)
        self._seek_timer.start()

    def _seek_release(self) -> None:
        self._seek_timer.stop()
        self._emit_pending_seek()

    def _seek_spin(self) -> None:
        self.command_submitted.emit(SeekRoute(self.position_spin.value()))

    def _emit_pending_seek(self) -> None:
        self.command_submitted.emit(SeekRoute(self._pending_seek_nm))

    def _toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self.details.setVisible(not self._collapsed)
        self.collapse_button.setArrowType(Qt.UpArrow if self._collapsed else Qt.DownArrow)
        self.collapse_button.setToolTip(
            "Expand Route Planning controls."
            if self._collapsed
            else "Collapse Route Planning to its status and route-position controls."
        )
        self.setMaximumHeight(92 if self._collapsed else 360)
        self.updateGeometry()

    def _auto_scroll_changed(self, active: bool) -> None:
        self._last_auto_scroll_row = None
        if active:
            self._scroll_to_row(self._current_leg_row)

    def _scroll_to_row(self, row: int) -> None:
        if not self.auto_scroll.isChecked() or not 0 <= row < self.table.rowCount():
            return
        if row == self._last_auto_scroll_row:
            return
        item = self.table.item(row, 0)
        if item is None:
            return
        self._scroll_guard = True
        try:
            # selectRow() consults keyboard modifiers. Windows pinch uses Ctrl,
            # which otherwise toggles the row selection on successive ticks.
            index = self.table.model().index(row, 0)
            selection = self.table.selectionModel()
            selection.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
            selection.select(index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
            self.table.scrollToItem(item, QAbstractItemView.PositionAtCenter)
            self._last_auto_scroll_row = row
        finally:
            self._scroll_guard = False

    def _update_progress_columns(self, snapshot: SimulationSnapshot, geometry: RouteGeometry) -> None:
        speed = max(0.0, abs(snapshot.sog_kn))
        for row in range(len(self._route)):
            leg_start = geometry.ticks_nm[max(0, row - 1)] if geometry.ticks_nm else 0.0
            leg_end = geometry.ticks_nm[row] if geometry.ticks_nm else 0.0
            if row < snapshot.next_waypoint_index:
                dtg_leg = 0.0
            elif row == snapshot.next_waypoint_index:
                dtg_leg = max(0.0, leg_end - snapshot.route_progress_nm)
            else:
                dtg_leg = max(0.0, leg_end - leg_start)
            total = max(0.0, leg_end - snapshot.route_progress_nm)
            for column, text in (
                (self.DTG, f"{dtg_leg:.2f}"),
                (self.TTG_LEG, "--" if speed < 0.01 else self._format_time(dtg_leg / speed)),
                (self.TTG_TOTAL, "--" if speed < 0.01 else self._format_time(total / speed)),
            ):
                item = self.table.item(row, column)
                if item and item.text() != text:
                    item.setText(text)

    @staticmethod
    def _format_time(hours: float) -> str:
        minutes = max(0, round(hours * 60.0))
        return f"{minutes // 60:02d}:{minutes % 60:02d}"
