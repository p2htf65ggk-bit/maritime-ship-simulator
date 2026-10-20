# SPDX-License-Identifier: MPL-2.0
from collections import OrderedDict
from threading import Event, Thread, get_ident
import json
import time
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from maritime_simulator.application.memory_policy import AdaptiveMemoryPolicy, MIB
from maritime_simulator.application.system_resources import SystemMemoryStatus, _tree_rss
from maritime_simulator.ui.memory_monitor import MemoryMonitor
from maritime_simulator.visual.offline import OfflineTileServer
from maritime_simulator.visual.s57 import S57Store
from maritime_simulator.visual.water_mask import OSMNavigableWaterMask
from ui.web_memory import InactiveWebPage


def status(available, **values):
    return SystemMemoryStatus(8192, available, "test", **values)


@pytest.mark.parametrize("available,expected", [(8000,192),(4000,128),(2000,96),(800,64)])
def test_shared_cache_pool_partitions_sum_to_available_ram_tier(available, expected):
    budget = AdaptiveMemoryPolicy().update(status(available), now_s=0)
    assert budget.cache_mb == expected
    assert sum(budget.allocations().values()) == expected * MIB
    assert budget.allocations()["water"] >= 40 * MIB


def test_ram_recovery_requires_sixty_seconds_of_stable_target():
    policy = AdaptiveMemoryPolicy()
    assert policy.update(status(8000), now_s=0).cache_mb == 192
    assert policy.update(status(500), now_s=5).cache_mb == 64
    assert policy.update(status(8000), now_s=10).cache_mb == 64
    assert policy.update(status(4000), now_s=65).cache_mb == 64
    assert policy.update(status(4000), now_s=124).cache_mb == 64
    assert policy.update(status(4000), now_s=125).cache_mb == 128
    assert policy.update(status(None), now_s=130).cache_mb == 128


def test_old_swap_is_not_pressure_but_new_swap_out_is():
    policy = AdaptiveMemoryPolicy()
    assert policy.update(status(8000, paging_out_mb=1000, swap_used_mb=500), now_s=0).cache_mb == 192
    assert policy.update(status(8000, paging_out_mb=1000), now_s=5).cache_mb == 192
    budget = policy.update(status(8000, paging_out_mb=1002), now_s=10)
    assert budget.cache_mb == 96 and budget.pressure and budget.paging_active
    assert not policy.update(status(8000, paging_out_mb=1002), now_s=15).paging_active


def test_current_rss_includes_children_and_grandchildren_not_other_apps():
    assert _tree_rss([(10,1,100),(12,10,50),(13,12,25),(99,1,999)], 10) == (100,75)
    assert _tree_rss([], 10) == (None,None)
    assert status(1,process_rss_mb=100,children_rss_mb=75).tree_rss_mb == 175


def test_linux_sample_preserves_swap_telemetry_and_missing_process_rss_is_unavailable(tmp_path, monkeypatch):
    from maritime_simulator.application import system_resources as resources
    monkeypatch.setattr(resources, '_last_memory_status', None)
    path = tmp_path/'meminfo'
    path.write_text('MemTotal: 8388608 kB\nMemAvailable: 2097152 kB\nSwapTotal: 1048576 kB\nSwapFree: 524288 kB\n')
    monkeypatch.setattr(resources, '_linux_paging_out', lambda:8)
    sample = resources._linux_memory_status(path)
    assert sample.total_mb == 8192 and sample.available_mb == 2048
    assert sample.swap_used_mb == 512 and sample.paging_out_mb == 8
    monkeypatch.setattr(resources, 'system_memory_status', lambda:sample)
    monkeypatch.setattr(resources, 'process_tree_rss', lambda:(None,None))
    assert resources.sample_memory_status().tree_rss_mb is None
    assert resources.cached_memory_status().available_mb == 2048


def test_water_budget_counts_shared_images_once_and_keeps_active_region(tmp_path):
    water = OSMNavigableWaterMask(tmp_path/'unused.zip', tmp_path, allow_network=False)
    try:
        current = QImage(256,256,QImage.Format_Indexed8)
        older = QImage(256,256,QImage.Format_Indexed8)
        key, old = (15,1,1), (15,2,2)
        water._enc_region_key_set = frozenset([key])
        water._enc_region_images = OrderedDict([(old,older),(key,current)])
        water._enc_images = OrderedDict([(key,current)])
        usage = water.apply_memory_budget(current.sizeInBytes()+1024, 1024)
        assert list(water._enc_region_images) == [key]
        assert water._enc_images[key] is current
        assert usage["water_enc"] == current.sizeInBytes()+1024
        water.apply_memory_budget(1, 1)
        assert water._enc_region_images[key] is current
    finally:
        water.close()


def test_water_cache_stays_bounded_between_samples_and_resets_on_source_change(tmp_path):
    water = OSMNavigableWaterMask(tmp_path/'unused.zip', tmp_path, allow_network=False)
    try:
        water.apply_memory_budget(70000, 0)
        for index in range(10):
            water._remember_tile_image((9,index,1), QImage(256,256,QImage.Format_Indexed8))
        assert len(water._tile_images) == 1 and water._tile_memory_bytes <= 70000
        water.set_offline(False)
        water.set_offline(True)
        assert water._tile_memory_bytes == 0
        water._remember_tile_image((9,12,1), QImage(256,256,QImage.Format_Indexed8))
        assert len(water._tile_images) == 1
    finally:
        water.close()


def test_concurrent_region_and_point_tiles_share_completed_image_and_account_for_both_before_sharing(tmp_path):
    water = OSMNavigableWaterMask(tmp_path/'unused.zip', tmp_path, allow_network=False)
    try:
        key = (15,1,1)
        first, second = [QImage(256,256,QImage.Format_Indexed8) for _ in range(2)]
        water._enc_region_key_set = frozenset([key])
        water._enc_region_images[key], water._enc_images[key] = first, second
        usage = water.apply_memory_budget(MIB,0)
        assert usage['water_enc'] == 2*(first.sizeInBytes()+1024)
        water._remember_enc_region_image(key,first)
        assert water._enc_region_images[key] is second
        assert water._enc_memory_bytes == second.sizeInBytes()+1024
    finally:
        water.close()


def test_s57_coverage_and_serialized_map_cache_trim_lru_without_waiting(tmp_path):
    store = S57Store(tmp_path/'s57')
    store._coverage_cache.update([(1,b'x'*10),(2,b'y'*10)])
    assert store.apply_memory_budget(10) == {"coverage":10}
    assert list(store._coverage_cache) == [2]
    # Cache methods do not require a listening HTTP server.
    server = OfflineTileServer.__new__(OfflineTileServer)
    from threading import RLock
    server._lock, server._enc_lock = RLock(), RLock()
    server._tiles = OrderedDict([(1,b'x'*10),(2,b'y'*10)])
    server._cache_bytes = 20
    server._enc_cache = OrderedDict([(1,b'a'*10),(2,b'b'*10)])
    assert server.apply_memory_budget(10,10) == {"raster":10,"enc":10}
    locked, release = Event(), Event()
    def hold_query():
        with server._enc_lock:
            locked.set()
            release.wait(2)
    thread = Thread(target=hold_query)
    thread.start()
    try:
        assert locked.wait(1)
        started = time.monotonic()
        assert server.apply_memory_budget(0,0) == {"raster":0}
        assert time.monotonic()-started < .1
    finally:
        release.set()
        thread.join()


class FakePage:
    def __init__(self):
        from PySide6.QtWebEngineCore import QWebEnginePage
        self.life = QWebEnginePage.LifecycleState.Active
        self.scripts = []
        self.navigation = {"viewBearingDeg":35,"namesVisible":False}

    def runJavaScript(self, script, callback=None):
        self.scripts.append(script)
        if callback:
            callback(json.dumps(self.navigation))

    def isLoading(self):
        return False

    def setVisible(self, active):
        self.visible = active

    def setLifecycleState(self, state):
        self.life = state

    def lifecycleState(self):
        return self.life


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_hidden_page_discards_only_under_pressure_and_restores_view_on_return(app):
    page = FakePage()
    changes = []
    memory = InactiveWebPage(SimpleNamespace(page=lambda:page), lambda:changes.append('reload'), lambda:changes.append('discard'))
    memory.hide(lambda:changes.append('freeze'))
    assert not memory.discard_if_idle(False, now_s=memory.hidden_since+100)
    assert not memory.discard_if_idle(True, now_s=memory.hidden_since+59)
    assert memory.discard_if_idle(True, now_s=memory.hidden_since+61)
    assert changes == ['freeze','discard']
    memory.show()
    memory.loaded()
    assert changes[-1] == 'reload'
    assert 'restoreNavigationState' in page.scripts[-1]
    assert '35' in page.scripts[-1]
    memory.close()
    count = len(page.scripts)
    memory.hide(lambda:None)
    assert len(page.scripts) == count


def test_hidden_page_with_unsaved_offline_selection_is_never_discarded(app):
    page = FakePage()
    page.navigation = {"busy":True}
    memory = InactiveWebPage(SimpleNamespace(page=lambda:page), lambda:None, lambda:None)
    memory.hide(lambda:None)
    assert not memory.discard_if_idle(True, now_s=memory.hidden_since+100)
    memory.close()


def test_explicit_provider_change_drops_old_document_snapshot(app):
    page = FakePage()
    memory = InactiveWebPage(SimpleNamespace(page=lambda:page), lambda:None, lambda:None)
    memory.hide(lambda:None)
    assert memory.discard_if_idle(True, now_s=memory.hidden_since+100)
    memory.reset()
    assert memory.state is None and not memory.discarded
    memory.show()
    count = len(page.scripts)
    memory.loaded()
    assert len(page.scripts) == count
    memory.close()


def test_ram_sampling_and_cache_work_run_off_ui_thread_without_job_backlog(app, tmp_path):
    main_thread = get_ident()
    calls = []
    ready = Event()
    def sample():
        calls.append(get_ident())
        ready.set()
        return status(800)
    cache = SimpleNamespace(apply_memory_budget=lambda *args:{})
    monitor = MemoryMonitor(cache,cache,sampler=sample,interval_s=5)
    monitor.start()
    try:
        assert ready.wait(2)
    finally:
        monitor.stop()
    assert calls == [calls[0]] and calls[0] != main_thread
    assert monitor.policy.budget.cache_mb == 64 or monitor.status is None


def test_forward_view_memory_poll_is_cached_and_does_not_switch_renderer(app, tmp_path, monkeypatch):
    from maritime_simulator.application.service import SimulatorService
    from maritime_simulator.ui import forward_view as module
    monkeypatch.setattr(module, "cached_memory_status", lambda:status(800))
    service = SimulatorService()
    dialog = module.ForwardViewDialog(service.snapshot,lambda snapshot:[],tmp_path)
    try:
        renderer = dialog.active_renderer
        dialog._refresh_visual_quality()
        assert dialog.active_renderer == renderer
        assert dialog._visual_quality_payload["name"] == "critical"
    finally:
        dialog.shutdown()
        service.close()
