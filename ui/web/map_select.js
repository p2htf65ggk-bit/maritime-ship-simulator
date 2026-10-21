// SPDX-License-Identifier: MPL-2.0
// Keep map choices in the page, avoiding native WebEngine popup DPI geometry.
window.MapSelect = (() => {
    const controls = new WeakMap();
    let serial = 0;

    function enhance(select) {
        if (controls.has(select)) return controls.get(select);
        const originalTabIndex = select.getAttribute('tabindex');
        const wrapper = document.createElement('div');
        wrapper.className = 'map-select-control';
        const trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.className = 'map-select-trigger';
        trigger.id = `${select.id || 'mapSelect' + ++serial}Trigger`;
        trigger.setAttribute('aria-haspopup', 'listbox');
        trigger.setAttribute('aria-expanded', 'false');
        const menu = document.createElement('div');
        menu.className = 'map-select-menu';
        menu.id = trigger.id + 'Options';
        menu.setAttribute('role', 'listbox');
        menu.setAttribute('aria-label', select.getAttribute('aria-label') || 'Map options');
        menu.hidden = true;
        trigger.setAttribute('aria-controls', menu.id);
        select.before(wrapper);
        wrapper.append(select, trigger);
        select.hidden = true;
        select.tabIndex = -1;
        document.body.appendChild(menu);

        function close(restoreFocus = false) {
            const wasOpen = !menu.hidden;
            menu.hidden = true;
            trigger.setAttribute('aria-expanded', 'false');
            if (restoreFocus && wasOpen) trigger.focus({preventScroll: true});
        }
        function focusOption(last = false) {
            const options = [...menu.querySelectorAll('[role="option"]:not(:disabled)')];
            const selected = options.find(item => item.dataset.value === select.value);
            (last ? options.at(-1) : selected || options[0])?.focus({preventScroll: true});
        }
        function position() {
            const rect = trigger.getBoundingClientRect();
            const width = Math.min(rect.width, Math.max(0, innerWidth - 16));
            menu.style.width = `${width}px`;
            menu.style.maxHeight = `${Math.max(0, innerHeight - 16)}px`;
            const naturalHeight = menu.scrollHeight + 2;
            const below = Math.max(0, innerHeight - rect.bottom - 12);
            const above = Math.max(0, rect.top - 12);
            const down = below >= Math.min(naturalHeight, 100) || below >= above;
            const available = down ? below : above;
            menu.style.maxHeight = `${available}px`;
            menu.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - width - 8))}px`;
            menu.style.top = `${down ? rect.bottom + 4 : Math.max(8, rect.top - Math.min(naturalHeight, available) - 4)}px`;
        }
        function refresh() {
            const selected = select.selectedOptions[0];
            const label = select.getAttribute('aria-label') || 'Map options';
            trigger.textContent = selected?.label || label;
            trigger.setAttribute('aria-label', `${label}: ${selected?.label || ''}`);
            trigger.disabled = select.disabled;
            if (menu.hidden) return;
            const focusedValue = menu.contains(document.activeElement) ? document.activeElement.dataset.value : null;
            menu.replaceChildren();
            for (const option of select.options) {
                const item = document.createElement('button');
                item.type = 'button';
                item.tabIndex = -1;
                item.setAttribute('role', 'option');
                item.setAttribute('aria-selected', String(option.selected));
                item.dataset.value = option.value;
                item.disabled = option.disabled || select.disabled;
                item.textContent = option.label;
                item.addEventListener('click', event => {
                    event.stopPropagation();
                    if (item.disabled) return;
                    select.value = option.value;
                    close(true);
                    select.dispatchEvent(new Event('change', {bubbles: true}));
                    refresh();
                });
                menu.appendChild(item);
            }
            position();
            if (focusedValue !== null) {
                const item = [...menu.children].find(item => item.dataset.value === focusedValue && !item.disabled);
                (item || menu.querySelector('button:not(:disabled)'))?.focus({preventScroll: true});
            }
        }
        function open(last = false) {
            if (trigger.disabled) return;
            menu.hidden = false;
            trigger.setAttribute('aria-expanded', 'true');
            refresh();
            focusOption(last);
        }
        function onKey(event) {
            if (event.key === 'Tab') { close(true); return; }
            if (event.key === 'Escape') {
                if (menu.hidden) return;
                close(true);
            } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
                if (menu.hidden) open(event.key === 'End' || event.key === 'ArrowUp');
                else {
                    const options = [...menu.querySelectorAll('button:not(:disabled)')];
                    const current = options.indexOf(document.activeElement);
                    const index = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
                        : (current + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
                    options[index]?.focus({preventScroll: true});
                }
            } else if (event.key === 'Enter' || event.key === ' ') {
                if (menu.hidden) open();
                else if (document.activeElement !== trigger) document.activeElement.click();
                else close(true);
            } else return;
            event.preventDefault();
            event.stopPropagation();
        }
        function outside(event) {
            if (!wrapper.contains(event.target) && !menu.contains(event.target)) close();
        }
        function resized() { close(); }
        trigger.addEventListener('click', event => {
            event.stopPropagation();
            if (menu.hidden) open(); else close(true);
        });
        trigger.addEventListener('keydown', onKey);
        menu.addEventListener('keydown', onKey);
        select.addEventListener('change', refresh);
        document.addEventListener('pointerdown', outside, true);
        window.addEventListener('resize', resized);
        const observer = new MutationObserver(refresh);
        observer.observe(select, {childList: true, subtree: true, attributes: true, characterData: true});
        const control = {refresh, close, destroy() {
            observer.disconnect();
            document.removeEventListener('pointerdown', outside, true);
            window.removeEventListener('resize', resized);
            select.removeEventListener('change', refresh);
            menu.remove();
            wrapper.replaceWith(select);
            select.hidden = false;
            if (originalTabIndex === null) select.removeAttribute('tabindex');
            else select.setAttribute('tabindex', originalTabIndex);
            controls.delete(select);
        }};
        controls.set(select, control);
        refresh();
        return control;
    }
    return {enhance};
})();
