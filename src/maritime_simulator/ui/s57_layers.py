# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QMenu, QVBoxLayout, QWidgetAction

from maritime_simulator.visual.s57 import BASE_LAYERS
from .dialogs import OpaqueDialog, fit_dialog_to_available_screen


class S57SafetyDialog(OpaqueDialog):
    def __init__(self, contour, depth, parent=None):
        super().__init__(parent)
        self.setWindowTitle("S57 Depth & Safety")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.contour, self.depth = QDoubleSpinBox(), QDoubleSpinBox()
        for field, value in ((self.contour, contour), (self.depth, depth)):
            field.setRange(0, 1000)
            field.setDecimals(1)
            field.setSuffix(" m")
            field.setValue(value)
            field.setMinimumWidth(140)
        self.contour.setToolTip("Use this contour, or the next deeper depth boundary available in each cell. Adjacent cells may therefore have different shading. Area colour uses the minimum depth of the whole area, not individual soundings. No grounding alarm.")
        self.depth.setToolTip("Emphasize soundings and underwater hazards at or shallower than this depth. No automatic grounding alarm.")
        form.addRow("Safety contour", self.contour)
        form.addRow("Safety depth", self.depth)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.adjustSize()
        fit_dialog_to_available_screen(self)


_CATEGORIES = (
    (("Depth",), "DEPARE DRGARE DEPCNT SOUNDG"),
    (("Land / Coast", "Coast and Terrain"), "LNDARE COALNE LNDRGN BUAARE LNDELV SLCONS SLOTOP SLOGRD VEGATN"),
    (("Land / Coast", "Inland Water"), "RIVERS RIVBNK LAKARE LAKSHR CANALS CANBNK RAPIDS WATFAL SPRING"),
    (("Aids to Navigation", "Buoys"), "BOYCAR BOYINB BOYISD BOYLAT BOYSAW BOYSPP BOYWTW"),
    (("Aids to Navigation", "Beacons"), "BCNCAR BCNISD BCNLAT BCNSAW BCNSPP BCNWTW"),
    (("Aids to Navigation", "Lights and Signals"), "LIGHTS LITFLT LITVES FOGSIG DAYMAR TOPMAR RETRFL SISTAT SISTAW NOTMRK"),
    (("Hazards",), "OBSTRN WRECKS UWTROC ICEARE HULKES OILBAR"),
    (("Other Layers", "Routes and Traffic", "Routes and Fairways"), "FAIRWY FERYRT NAVLNE RECTRC RCRTCL RCTLPT TWRTPT DWRTCL DWRTPT SUBTLN"),
    (("Other Layers", "Routes and Traffic", "Traffic Separation"), "ISTZNE TSELNE TSSBND TSSCRS TSSLPT TSSRON TSEZNE ARCSLN ASLXIS"),
    (("Other Layers", "Ports and Facilities", "Berthing and Anchorage"), "ACHBRT ACHARE BERTHS MORFAC PONTON FLODOC"),
    (("Other Layers", "Ports and Facilities", "Port Services"), "HRBARE HRBFAC SMCFAC PILBOP CGUSTA RSCSTA CTSARE BUNSTA TERMNL"),
    (("Other Layers", "Ports and Facilities", "Docks and Locks"), "DOCARE DRYDOC GRIDRN LOKBSN GATCON LOGPON HRBBSN LOKARE LKBSPT PRTARE TRNBSN"),
    (("Other Layers", "Infrastructure", "Bridges and Structures"), "BRIDGE CAUSWY DAMCON DYKCON FNCLNE CONVYR CRANES PYLONS PILPNT TUNNEL OFSPLF"),
    (("Other Layers", "Infrastructure", "Cables and Pipelines"), "CBLARE CBLOHD CBLSUB PIPARE PIPOHD PIPSOL"),
    (("Other Layers", "Infrastructure", "Buildings and Transport"), "BUISGL LNDMRK FORSTC SILTNK PRDARE ROADWY RAILWY RUNWAY AIRARE SPLARE SQUARE"),
    (("Other Layers", "Areas and Regulations", "Jurisdiction"), "ADMARE CONZNE COSARE CUSZNE EXEZNE FRPARE STSLNE TESARE"),
    (("Other Layers", "Areas and Regulations", "Restrictions and Special Use"), "RESARE PRCARE CTNARE MIPARE OSPARE DMPGRD ICNARE CHKPNT"),
    (("Other Layers", "Areas and Regulations", "Fisheries"), "FSHZNE FSHFAC FSHGRD MARCUL"),
    (("Other Layers", "Natural Features", "Seabed and Water"), "SEAARE SBDARE SNDWAV WEDKLP WATTUR"),
    (("Other Layers", "Natural Features", "Currents and Tides"), "CURENT TIDEWY TS_PRH TS_PNH TS_PAD TS_TIS T_HMON T_NHMN T_TIMS TS_FEB"),
    (("Other Layers", "Chart Information", "Survey and Magnetics"), "CTRPNT DISMAR LOCMAG MAGVAR SWPARE UNSARE NEWOBJ"),
    (("Other Layers", "Chart Information", "Radar and Radio"), "RADLNE RADRNG RADRFL RADSTA RTPBCN RDOCAL RDOSTA COMARE"),
)
_PATHS = {code: path for path, codes in _CATEGORIES for code in codes.split()}
MAX_MENU_LAYERS = 10


def layer_path(code):
    return _PATHS.get(code.upper(), ("Other Layers", "Additional Objects"))


class _GroupCheckBox(QCheckBox):
    def nextCheckState(self):
        # Partial is an indication, not a third user-selectable setting.
        self.setCheckState(Qt.Unchecked if self.checkState() == Qt.Checked else Qt.Checked)


class S57LayerMenu(QMenu):
    selection_changed = Signal(object, bool)
    safety_changed = Signal(float, float)

    def __init__(self, available, descriptions, selected, base=False, parent=None, *, safety_contour=30.0, safety_depth=30.0):
        super().__init__(parent)
        self.available = dict(available)
        self.selected = set(selected) & self.available.keys()
        self.base = base
        self.safety_contour, self.safety_depth = safety_contour, safety_depth
        self.groups = {}
        self.leaves = {}
        self._all_checkbox(self, (), set(available), "All Layers")
        self.base_action = self.addAction("Base", self._select_base)
        self.base_action.setCheckable(True)
        self.base_action.setToolTip("Display Base: coast, selected safety contour (or next deeper), isolated dangers in safe water. Simplified simulator portrayal.")
        self.addAction("Depth & Safety...", self._choose_safety)
        self.addSeparator()
        tree = {}
        for code in available:
            node = tree
            for part in layer_path(code):
                node = node.setdefault(part, {})
            node.setdefault(None, []).append(code)
        self._populate(self, (), tree, descriptions)
        self._sync()

    def _choose_safety(self):
        dialog = S57SafetyDialog(self.safety_contour, self.safety_depth, self.parentWidget())
        try:
            if dialog.exec() == QDialog.Accepted:
                self.safety_contour, self.safety_depth = dialog.contour.value(), dialog.depth.value()
                self.safety_changed.emit(self.safety_contour, self.safety_depth)
        finally:
            dialog.deleteLater()

    @staticmethod
    def _codes(node):
        return set(node.get(None, ())).union(*(S57LayerMenu._codes(value) for key, value in node.items() if key is not None))

    def _populate(self, menu, path, node, descriptions):
        for title, child in node.items():
            if title is None:
                continue
            child_path = (*path, title)
            submenu = menu.addMenu(title)
            self._all_checkbox(submenu, child_path, self._codes(child), "All in this group")
            submenu.addSeparator()
            self._populate(submenu, child_path, child, descriptions)
        codes = sorted(node.get(None, ()), key=lambda code: (descriptions.get(code, code).casefold(), code))
        if len(codes) > MAX_MENU_LAYERS:
            for offset in range(0, len(codes), MAX_MENU_LAYERS):
                page = codes[offset:offset + MAX_MENU_LAYERS]
                title = f"{offset + 1}-{offset + len(page)}"
                submenu = menu.addMenu(title)
                self._all_checkbox(submenu, (*path, title), set(page), "All in this group")
                submenu.addSeparator()
                self._populate(submenu, (*path, title), {None: page}, descriptions)
            return
        for code in codes:
            count = self.available[code]
            label = f"{descriptions.get(code, code)} ({code})"
            checkbox = QCheckBox(label if count is None else f"{label}  {count:,}")
            checkbox.toggled.connect(lambda enabled, code=code: self._set_codes({code}, enabled))
            self._widget_action(menu, checkbox)
            self.leaves[code] = checkbox

    @staticmethod
    def _widget_action(menu, checkbox):
        action = QWidgetAction(menu)
        action.setDefaultWidget(checkbox)
        menu.addAction(action)

    def _all_checkbox(self, menu, path, codes, label):
        checkbox = _GroupCheckBox(label)
        checkbox.setProperty("groupLabel", label)
        checkbox.setTristate(True)
        checkbox.setToolTip((" / ".join(path) + "\n" if path else "") + f"Toggle all {len(codes)} layers in this group, including subgroups.")
        checkbox.checkStateChanged.connect(lambda state, codes=codes: self._set_codes(codes, state != Qt.Unchecked))
        self._widget_action(menu, checkbox)
        self.groups[path] = (checkbox, codes)

    def _set_codes(self, codes, enabled):
        self.selected.update(codes) if enabled else self.selected.difference_update(codes)
        self.base = False
        self._sync()
        layers = None if self.selected == self.available.keys() else tuple(sorted(self.selected))
        self.selection_changed.emit(layers, False)

    def _select_base(self):
        self.selected = self.available.keys() & BASE_LAYERS
        self.base = True
        self._sync()
        self.selection_changed.emit(None, True)

    def set_selection(self, selected, base=False):
        self.selected = set(selected) & self.available.keys()
        self.base = base
        self._sync()

    def _sync(self):
        self.base_action.setChecked(self.base)
        for code, checkbox in self.leaves.items():
            with QSignalBlocker(checkbox):
                checkbox.setChecked(code in self.selected)
        for checkbox, codes in self.groups.values():
            active = len(codes & self.selected)
            state = Qt.Checked if active == len(codes) else Qt.PartiallyChecked if active else Qt.Unchecked
            with QSignalBlocker(checkbox):
                checkbox.setCheckState(state)
                checkbox.setText(checkbox.property("groupLabel") + (" (Some)" if state == Qt.PartiallyChecked else ""))
