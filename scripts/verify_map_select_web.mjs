// SPDX-License-Identifier: MPL-2.0
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const module = await import(process.env.PLAYWRIGHT_CORE_PATH || 'playwright-core');
const {chromium} = module.default || module;
const output = path.resolve(process.argv[2] || 'artifacts/map-select-web-qa');
await fs.mkdir(output, {recursive: true});
const browser = await chromium.launch({headless: true,
    ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {channel: 'chrome'})});
const results = [];
try {
    for (const scale of [1, 1.25, 1.5, 1.75, 2]) {
        for (const viewport of [{width: 960, height: 600}, {width: 640, height: 420}, {width: 320, height: 280}]) {
            const context = await browser.newContext({viewport, deviceScaleFactor: scale});
            const page = await context.newPage();
            const errors = [];
            page.on('pageerror', error => errors.push(error.message));
            await page.route('https://**/*', route => route.fulfill({contentType: 'image/png',
                body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+X2l1uQAAAABJRU5ErkJggg==', 'base64')}));
            await page.goto(pathToFileURL(path.resolve('ui/web/map.html')).href);
            await page.waitForFunction(() => window.mapReady);
            await page.evaluate(() => {
                window.testCommands = [];
                window.emitMapCommand = command => window.testCommands.push(command);
                setMapSource({id: 'online', available: [
                    {id: 'online', label: 'OpenSeaMap (Online)', enabled: true},
                    {id: 'protomaps', label: 'Protomaps (Offline)', enabled: false},
                    {id: 's57', label: 'S-57 (Experimental)', enabled: true},
                ]});
            });
            assert.equal(await page.locator('#mapSource').isVisible(), false);
            const centre = await page.evaluate(() => map.getCenter());
            await page.locator('#mapSourceTrigger').click();
            const geometry = await page.evaluate(() => {
                const trigger = document.getElementById('mapSourceTrigger').getBoundingClientRect();
                const menu = document.getElementById('mapSourceTriggerOptions');
                const box = menu.getBoundingClientRect();
                const last = menu.lastElementChild.getBoundingClientRect();
                return {width: box.width, height: box.height, left: box.left, top: box.top,
                    right: box.right, bottom: box.bottom, triggerWidth: trigger.width,
                    unusedBottom: box.bottom - last.bottom, background: getComputedStyle(menu).backgroundColor};
            });
            assert.ok(Math.abs(geometry.width - geometry.triggerWidth) < 1);
            assert.ok(geometry.height < 110 && geometry.unusedBottom >= 0 && geometry.unusedBottom <= 5,
                `Unexpected empty popup area: ${JSON.stringify(geometry)}`);
            assert.ok(geometry.left >= 0 && geometry.right <= viewport.width && geometry.top >= 0 && geometry.bottom <= viewport.height);
            assert.equal(geometry.background, 'rgb(23, 42, 53)');
            assert.equal(await page.getByRole('option', {name: 'Protomaps (Offline)', exact: true}).isDisabled(), true);
            if (viewport.width === 640) await page.screenshot({path: path.join(output, `source-menu-${scale}.png`)});
            await page.keyboard.press('ArrowDown');
            assert.equal(await page.evaluate(() => document.activeElement.dataset.value), 's57');
            await page.keyboard.press('Enter');
            assert.deepEqual(await page.evaluate(() => window.testCommands), [{action: 'map_source', source: 's57'}]);
            assert.deepEqual(await page.evaluate(() => map.getCenter()), centre, 'Menu arrows must not pan the map');
            assert.equal(await page.locator('#mapSourceTrigger').getAttribute('aria-expanded'), 'false');

            await page.locator('#mapSourceTrigger').click();
            await page.keyboard.press('Escape');
            assert.equal(await page.locator('#mapSourceTriggerOptions').isVisible(), false);
            assert.equal(await page.evaluate(() => document.activeElement.id), 'mapSourceTrigger');
            await page.locator('#mapSourceTrigger').click();
            await page.evaluate(() => {document.querySelector('#mapSource option[value="protomaps"]').disabled = false;});
            await page.getByRole('option', {name: 'Protomaps (Offline)', exact: true}).click();
            assert.deepEqual(await page.evaluate(() => window.testCommands.at(-1)), {action: 'map_source', source: 'protomaps'});
            await page.locator('#mapSourceTrigger').click();
            await page.mouse.click(viewport.width - 4, viewport.height - 4);
            assert.equal(await page.locator('#mapSourceTriggerOptions').isVisible(), false);

            await page.evaluate(() => beginOfflineAreaSelection());
            await page.locator('.offline-area-tools .map-select-trigger').click();
            await page.getByRole('option', {name: 'Regional detail', exact: true}).click();
            assert.equal(await page.locator('.offline-area-tools select').inputValue(), '14');
            await page.locator('.offline-area-tools .map-select-trigger').click();
            await page.keyboard.press('Escape');
            assert.equal(await page.locator('.offline-area-tools').count(), 1, 'Escape only closes the choice menu');
            await page.locator('[data-action="cancel"]').click();
            await page.evaluate(() => {
                for (let index = 0; index < 50; index++) {
                    beginOfflineAreaSelection();
                    offlineAreaSelection.finish({action: 'offline_area_cancel'});
                }
            });
            assert.equal(await page.locator('.map-select-menu').count(), 1, 'No orphaned popup after repeated area selection');
            assert.equal(await page.locator('.map-select-trigger').count(), 1);
            assert.deepEqual(errors, []);
            results.push({scale, viewport, geometry, errors});
            await context.close();
        }
    }
    await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(results, null, 2) + '\n');
    console.log(`Map choice menus passed ${results.length} viewport/DPI cases, including 125% and 150%.`);
} finally {
    await browser.close();
}
