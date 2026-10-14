import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const controls = new Map();
const root = {
  dataset: {}, innerHTML: '',
  querySelector: () => null,
  querySelectorAll: () => [],
};
globalThis.document = {
  getElementById: id => id === 'shot-production-workspace' ? root : controls.get(id) || null,
  querySelector: () => null,
};
globalThis.window = {scrollY: 0, scrollTo: () => {}};
globalThis.Node = {TEXT_NODE: 3, ELEMENT_NODE: 1};
globalThis.appState = {projectId: 'test-project'};
globalThis.API_BASE = '';
globalThis.projectJobs = [];
const alerts = [];
globalThis.alert = message => alerts.push(message);
globalThis.confirm = () => true;
globalThis.StudioActions = {
  escapeHtml: value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll("'", '&#39;'),
  mediaUrl: value => String(value ?? ''),
  isMissingMediaPath: () => false,
  activeProjectJobs: () => [],
  isTerminalJob: () => false,
  produceStatus: () => {},
  renderProductionAudioTimeline: () => {},
};
for (const filename of ['production.js', 'production-actions.js']) {
  const source = await readFile(new URL(`../web/js/pages/${filename}`, import.meta.url), 'utf8');
  await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
}
const actions = StudioActions;
const oldPolicy = {provider: 'mock', model: 'old', resolution: '720p'};
const item = {
  scene: {scene_id: 4, location: 'room', characters: []},
  prompt: {scene_id: 4, prompt: 'parent @Hero', negative_prompt: 'parent negative', duration_sec: 30},
  executable_shots: [
    {shot_id: 'scene-4-shot-1', shot_order: 1, prompt: 'child one', duration_sec: 4},
    {shot_id: 'scene-4-shot-2', shot_order: 2, prompt: 'child two', duration_sec: 4},
  ],
  video_policy: {...oldPolicy},
  production: {
    references: [], continuity_enabled: true, video_override: {...oldPolicy},
    active_version_id: 'parent-version',
    active_version_by_shot_id: {'scene-4-shot-1': 'one-active', 'scene-4-shot-2': 'two-active'},
    video_versions: [
      {version_id: 'parent-version', video_url: '/parent.mp4'},
      {version_id: 'one-old', shot_id: 'scene-4-shot-1', video_url: '/one-old.mp4'},
      {version_id: 'one-active', shot_id: 'scene-4-shot-1', video_url: '/one.mp4'},
      {version_id: 'two-active', shot_id: 'scene-4-shot-2', video_url: '/two.mp4'},
    ],
  },
};
const capability = continuity => ({continuity, resolutions: ['720p', '1080p'], duration_min: 3, duration_max: 15});
productionData = {
  productions: [item], video_generation_policy: {...oldPolicy},
  video_capabilities: {
    mock: {models: {old: capability(true), text: capability(false)}},
    other: {models: {new: capability(false)}},
  },
};
productionSceneId = 4;
function resetControls(policy = oldPolicy, continuity = true) {
  controls.clear();
  controls.set('production-video-provider', {value: policy.provider});
  controls.set('production-video-model', {value: policy.model});
  controls.set('production-video-resolution', {value: policy.resolution});
  controls.set('production-continuity', {checked: continuity});
  controls.set('production-duration', {value: '4'});
  controls.set('production-negative', {value: 'child negative'});
  controls.set('production-prompt', {childNodes: [{nodeType: 3, nodeValue: 'child one'}], dataset: {}});
}
function versionBand() {
  return root.innerHTML.match(/id="production-version-list"[^>]*>([\s\S]*?)<\/section>/)?.[1] || '';
}
for (const [shotId, included, excluded] of [
  ['scene-4-shot-1', 'one-active', ['two-active', 'parent-version']],
  ['scene-4-shot-2', 'two-active', ['one-active', 'parent-version']],
  ['parent:4', 'parent-version', ['one-active', 'two-active']],
]) {
  productionShotId = shotId;
  resetControls();
  root.dataset = {};
  actions.renderProductionWorkspace();
  const html = versionBand();
  assert.ok(html.includes(`productionActivateVersion('${included}')`), `first render contains ${included}`);
  const activeButton = html.match(/<button class="version-item active"[^>]*>/)?.[0] || '';
  assert.ok(activeButton.includes(`productionActivateVersion('${included}')`), 'active marker belongs to the selected shot active version');
  for (const versionId of excluded) assert.ok(!html.includes(versionId), `first render excludes ${versionId}`);
}

let activationRequests = 0;
actions.projectJsonRequest = async (_url, options) => {
  activationRequests++;
  assert.equal(JSON.parse(options.body).version_id, 'one-old');
  item.production.active_version_by_shot_id['scene-4-shot-1'] = 'one-old';
  return productionData;
};
productionShotId = 'scene-4-shot-1';
await actions.productionActivateVersion('two-active');
await actions.productionActivateVersion('parent-version');
await actions.productionActivateVersion('missing-version');
assert.equal(activationRequests, 0, 'foreign and missing versions never reach the server');
await actions.productionActivateVersion('one-old');
assert.equal(activationRequests, 1);
assert.equal(item.production.active_version_by_shot_id['scene-4-shot-2'], 'two-active');
productionShotId = 'parent:4';
await actions.productionActivateVersion('one-active');
assert.equal(activationRequests, 1, 'parent selection rejects child versions');

const deferred = () => {
  let resolve;
  const promise = new Promise(done => {resolve = done;});
  return {promise, resolve};
};
const flush = async () => {for (let i = 0; i < 15; i++) await Promise.resolve();};
for (const change of ['provider', 'model', 'resolution', 'continuity']) {
  productionShotId = 'scene-4-shot-1';
  resetControls();
  item.production.continuity_enabled = true;
  item.production.references = [{reference_id: 'character|Hero|front', source: 'explicit', reference_token: '@Hero', asset_version_id: 'hero-v1'}];
  const gate = deferred();
  const events = [];
  const saves = [];
  actions.projectJsonRequest = async (url, options) => {
    const payload = JSON.parse(options.body);
    if (url.endsWith('/video-generation-policy')) {
      events.push('policy-start');
      await gate.promise;
      events.push('policy-done');
      return {policy: payload};
    }
    events.push('shot-start');
    saves.push(payload);
    // Match the existing API: settings-only saves must not validate the 30s parent duration.
    assert.ok(!('duration_sec' in payload), 'child save leaves parent duration untouched');
    assert.ok(!('prompt' in payload), 'child save leaves parent prompt untouched');
    assert.ok(!('negative_prompt' in payload), 'child save leaves parent negative prompt untouched');
    assert.equal(payload.references[0].token, '@Hero', 'child prompt must not strip parent explicit mentions');
    item.production.video_override = payload.video_override;
    item.production.continuity_enabled = payload.continuity_enabled;
    events.push('shot-done');
    return productionData;
  };
  actions.submitProjectJob = async spec => {
    assert.equal(spec.target_id, 'scene-4-shot-1');
    assert.equal(spec.payload.shot_id, 'scene-4-shot-1');
    assert.equal(item.production.video_override.provider, controls.get('production-video-provider').value);
    assert.equal(item.production.video_override.model, controls.get('production-video-model').value);
    assert.equal(item.production.video_override.resolution, controls.get('production-video-resolution').value);
    assert.equal(item.production.continuity_enabled, controls.get('production-continuity').checked);
    events.push('generate');
  };
  if (change === 'provider') {
    controls.get('production-video-provider').value = 'other';
    actions.productionVideoProviderChanged();
  } else if (change === 'model') {
    controls.get('production-video-model').value = 'text';
    actions.productionVideoModelChanged();
  } else if (change === 'resolution') {
    controls.get('production-video-resolution').value = '1080p';
    actions.productionVideoResolutionChanged();
  } else {
    actions.productionVideoModelChanged();
    controls.get('production-continuity').checked = false;
    controls.get('production-continuity').onchange();
  }
  const generation = actions.productionGenerateCurrent();
  await flush();
  assert.deepEqual(events, ['policy-start'], `${change}: generation waits for the pending policy save`);
  gate.resolve();
  await generation;
  assert.equal(events.at(-1), 'generate');
  assert.ok(events.indexOf('policy-done') < events.indexOf('shot-start'));
  assert.ok(saves.length > 0);
  if (change !== 'resolution') assert.equal(saves.at(-1).continuity_enabled, false);
}

// Continuity autosave and immediate generation must not issue overlapping shot writes.
resetControls();
controls.get('production-continuity').checked = false;
const shotGate = deferred();
let shotWrites = 0;
let generated = false;
actions.projectJsonRequest = async () => {
  shotWrites++;
  if (shotWrites === 1) await shotGate.promise;
  return productionData;
};
actions.submitProjectJob = async () => {generated = true;};
const autosave = actions.productionSaveCurrent();
const immediate = actions.productionGenerateCurrent();
await flush();
assert.equal(shotWrites, 1, 'shot saves are serialized');
assert.equal(generated, false);
shotGate.resolve();
await Promise.all([autosave, immediate]);
assert.equal(shotWrites, 2);
assert.equal(generated, true);

// A failed settings write blocks generation rather than submitting an old policy.
generated = false;
actions.projectJsonRequest = async () => {throw new Error('save failed');};
await actions.productionGenerateCurrent();
assert.equal(generated, false);
assert.ok(alerts.includes('save failed'));

// Editable parent prompts still use the established save contract.
productionShotId = 'parent:4';
resetControls();
let parentPayload;
actions.projectJsonRequest = async (_url, options) => {parentPayload = JSON.parse(options.body); return productionData;};
assert.equal(await actions.productionSaveCurrent(), true);
assert.equal(parentPayload.prompt, 'child one');
assert.equal(parentPayload.negative_prompt, 'child negative');
assert.equal(parentPayload.duration_sec, 4);
console.log('production version and generation state tests passed');
