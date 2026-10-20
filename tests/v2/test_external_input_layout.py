# SPDX-License-Identifier: MPL-2.0
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.ui.control_sources import ControlSourceSelector
from maritime_simulator.ui.input_runtime import RudderXdrSettings
from maritime_simulator.ui.mode_panels import ModeControlStack
from maritime_simulator.ui.content_tabs import ContentSizedTabs
from maritime_simulator.ui.rudder_endpoint import RudderEndpointEditor
from maritime_simulator.ui.rudder_xdr import RudderXdrEditor
from ui.style import apply_global_ui_style


def test_sources_collapse_without_changing_choices():
    app = QApplication.instance() or QApplication([])
    selector = ControlSourceSelector(("TRC", "XDR"))
    selector.load("PRIO_TRC_XDR", "ZERO")
    selector.show()
    app.processEvents()
    try:
        assert not selector.options.isVisible()
        assert selector.summary.text() == "Main: TRC  |  Fallback: XDR  |  Loss: Zero"
        small = selector.sizeHint().height()
        selector.toggle.click()
        app.processEvents()
        assert selector.options.isVisible() and selector.sizeHint().height() > small
        selector.toggle.click()
        assert selector.values() == ("PRIO_TRC_XDR", "ZERO")
    finally:
        selector.close()
        selector.deleteLater()
        app.processEvents()


def test_ship_sections_and_editors_are_aligned():
    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    service = SimulatorService()
    stack = ModeControlStack(service)
    stack.set_mode(ControlMode.SHIP)
    page = stack._pages[ControlMode.SHIP]
    stack.resize(340, 1000)
    stack.show()
    app.processEvents()
    try:
        bow = stack._bow_fields[ControlMode.SHIP]
        assert stack._ship_rudder_label.objectName() == bow.label.objectName() == "sectionTitle"
        assert stack._ship_rudder_spin.width() == bow.spin.width()
        assert stack._ship_rudder_feedback.objectName() == bow.readout.objectName() == "dataReadout"
        assert stack._engine_fields[ControlMode.SHIP].y() < stack._ship_rudder_label.y() < bow.y() < stack.keyboard_control.y()
        assert abs(stack._ship_rudder.width() - bow.slider.width()) <= 2
        bow.spin.setValue(-45)
        bow.spin.editingFinished.emit()
        assert bow.slider.value() == -45
        assert stack.keyboard_control.parentWidget() is page
    finally:
        stack.close()
        stack.deleteLater()
        service.close()
        app.processEvents()


def test_endpoint_tabs_fit_visible_page_without_empty_space():
    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    page = QWidget()
    layout = QVBoxLayout(page)
    tabs = ContentSizedTabs()
    native = RudderEndpointEditor()
    xdr = RudderXdrEditor()
    xdr.load(RudderXdrSettings())
    tabs.addTab(native, "ROR")
    tabs.addTab(xdr, "XDR")
    indicator = QLabel("Input indications")
    layout.addWidget(tabs)
    layout.addWidget(indicator)
    layout.addStretch(1)
    page.resize(700, 1000)
    page.show()
    app.processEvents()
    try:
        native_height = tabs.height()
        assert indicator.y() - (tabs.y() + tabs.height()) < 20
        tabs.setCurrentIndex(1)
        app.processEvents()
        assert tabs.height() > native_height
        assert indicator.y() - (tabs.y() + tabs.height()) < 20
        tabs.setCurrentIndex(0)
        page.resize(480, 760)
        app.processEvents()
        assert tabs.height() < 400
        assert indicator.y() - (tabs.y() + tabs.height()) < 20
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()
