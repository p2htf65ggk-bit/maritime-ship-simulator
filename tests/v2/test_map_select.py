# SPDX-License-Identifier: MPL-2.0
from pathlib import Path

import build_v2


def test_map_choice_popups_stay_inside_the_page():
    html = Path('ui/web/map.html').read_text(encoding='utf-8')
    script = Path('ui/web/map_select.js').read_text(encoding='utf-8')
    assert '<script src="map_select.js"></script>' in html
    assert "MapSelect.enhance(document.getElementById('mapSource'))" in html
    assert "MapSelect.enhance(select).refresh()" in html
    assert 'select.hidden = true' in script
    assert "menu.setAttribute('role', 'listbox')" in script
    assert "select.dispatchEvent(new Event('change', {bubbles: true}))" in script
    assert 'getBoundingClientRect()' in script
    assert 'devicePixelRatio' not in script


def test_offline_detail_menu_is_disposed_and_bundled():
    html = Path('ui/web/map.html').read_text(encoding='utf-8')
    script = Path('ui/web/map_select.js').read_text(encoding='utf-8')
    assert "const detailControl = MapSelect.enhance(tools.querySelector('select'))" in html
    assert html.index('detailControl.destroy()') < html.index('tools.remove()')
    assert 'observer.disconnect()' in script
    assert "document.removeEventListener('pointerdown', outside, true)" in script
    assert "window.removeEventListener('resize', resized)" in script
    assert 'ui/web/map_select.js' in build_v2.REQUIRED_RESOURCES
