import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const drawer = await readFile(new URL('../web/js/components/task-drawer.js', import.meta.url), 'utf8');
const studio = await readFile(new URL('../studio.html', import.meta.url), 'utf8');

assert.match(drawer, /function renderScriptProgress\(\)/);
assert.match(drawer, /function latestScriptJob\(predicate\)/);
assert.match(drawer, /renderScriptProgress\(\);\s*return;/);
assert.match(studio, /id="script-progress"/);
assert.match(studio, /id="script-progress-fill"/);

console.log('script progress UI contract passed');
