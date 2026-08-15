import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {formatApiHealth} from '../web/js/core/api-health.js';

assert.deepEqual(
  formatApiHealth({status: 'ok', provider: 'seedance'}),
  {statusClass: 'green', statusLabel: '在线', providerLabel: 'Seedance'}
);

assert.deepEqual(
  formatApiHealth({status: 'ok', provider: 'mock'}),
  {statusClass: 'green', statusLabel: '在线', providerLabel: 'Mock 模式'}
);

assert.deepEqual(
  formatApiHealth({status: 'error', provider: 'seedance'}),
  {statusClass: 'red', statusLabel: '离线', providerLabel: '服务不可用'}
);

const bootstrap = await readFile(new URL('../web/js/bootstrap.js', import.meta.url), 'utf8');
const healthCall = bootstrap.indexOf('void checkApiHealth();');
const firstPageImport = bootstrap.indexOf("await import(`./core/state-api.js${moduleVersion}`);");
assert.ok(healthCall >= 0 && healthCall < firstPageImport, 'health check must run before page module imports');
const initializeCall = bootstrap.indexOf('globalThis.StudioActions.initializeStudio();');
const projectLoadCall = bootstrap.indexOf('globalThis.StudioActions.loadProjects?.();');
assert.ok(projectLoadCall > initializeCall && projectLoadCall > firstPageImport, 'project list must load after page modules are ready');

const productionActions = await readFile(new URL('../web/js/pages/production-actions.js', import.meta.url), 'utf8');
const initializeStart = productionActions.indexOf('function initializeStudio()');
const registryStart = productionActions.indexOf('const __moduleActions');
assert.ok(initializeStart >= 0 && registryStart > initializeStart, 'production actions must expose its action registry');
assert.equal(productionActions.slice(initializeStart, registryStart).trimEnd().endsWith('}'), true, 'initializeStudio must be closed before the module registry');

console.log('frontend API health status tests passed');
