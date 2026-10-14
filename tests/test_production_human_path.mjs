import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const controls = new Map();
const root = {
  dataset: {},
  _html: '',
  querySelector: selector => {
    if (selector === '.production-inspector') return controls.get('production-inspector') || null;
    if (selector.startsWith('#')) return controls.get(selector.slice(1)) || null;
    return null;
  },
  querySelectorAll: () => [],
};
function applyRenderedPrompt() {
  const match = root._html.match(/id="production-prompt"[^>]*>([\s\S]*?)<\/div>/);
  const prompt = controls.get('production-prompt');
  if (!match || !prompt) return;
  const text = match[1]
    .replace(/<br\s*\/?>/g, '\n')
    .replace(/<[^>]+>/g, '')
    .replaceAll('&amp;', '&')
    .replaceAll('&quot;', '"')
    .replaceAll('&#39;', "'");
  prompt.childNodes = [{nodeType: 3, nodeValue: text}];
  prompt.innerHTML = text;
}
Object.defineProperty(root, 'innerHTML', {
  get() { return this._html; },
  set(value) {
    this._html = String(value);
    applyRenderedPrompt();
  },
});
globalThis.document = {
  getElementById: id => id === 'shot-production-workspace' ? root : controls.get(id) || null,
  querySelector: selector => root.querySelector(selector),
  activeElement: null,
};
globalThis.window = {scrollY: 0, scrollTo: () => {}, getSelection: () => null};
globalThis.Node = {TEXT_NODE: 3, ELEMENT_NODE: 1};
globalThis.appState = {projectId: 'human-project'};
globalThis.API_BASE = '';
globalThis.projectJobs = [];
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
const saveRequests = [];
actions.projectJsonRequest = async (_url, options) => {
  saveRequests.push(JSON.parse(options.body));
  return productionData;
};

const parent = {
  scene: {scene_id: 4, location: 'room', characters: []},
  prompt: {scene_id: 4, prompt: 'parent saved', negative_prompt: 'parent negative', duration_sec: 8},
  executable_shots: [
    {shot_id: 'scene-4-shot-1', shot_order: 1, prompt: 'child one', negative_prompt: '', duration_sec: 4},
    {shot_id: 'scene-4-shot-2', shot_order: 2, prompt: 'child two', negative_prompt: '', duration_sec: 4},
  ],
  production: {references: [], continuity_enabled: false, video_versions: [], active_version_id: '', active_version_by_shot_id: {}},
};
const other = {
  scene: {scene_id: 5, location: 'hall', characters: []},
  prompt: {scene_id: 5, prompt: 'other parent', negative_prompt: '', duration_sec: 6},
  executable_shots: [],
  production: {references: [], continuity_enabled: false, video_versions: [], active_version_id: ''},
};
productionData = {productions: [parent, other], video_generation_policy: {provider: 'mock', model: 'old', resolution: '720p'}, video_capabilities: {}};

function promptNode(text) {
  const node = {childNodes: [{nodeType: 3, nodeValue: text}], dataset: {}, value: text, _html: text, focus() {}};
  Object.defineProperty(node, 'innerHTML', {
    get() { return this._html; },
    set(value) {
      this._html = String(value);
      const text = this._html
        .replace(/<br\s*\/?>/g, '\n')
        .replace(/<[^>]+>/g, '')
        .replaceAll('&amp;', '&')
        .replaceAll('&quot;', '"')
        .replaceAll('&#39;', "'");
      this.childNodes = [{nodeType: 3, nodeValue: text}];
    },
  });
  return node;
}
function wireControls(promptText) {
  controls.clear();
  const prompt = promptNode(promptText);
  controls.set('production-prompt', prompt);
  controls.set('production-negative', {value: ''});
  controls.set('production-feedback', {value: ''});
  controls.set('production-duration', {value: '8', min: '', max: ''});
  controls.set('production-video-provider', {value: 'mock', innerHTML: ''});
  controls.set('production-video-model', {value: 'old', innerHTML: ''});
  controls.set('production-video-resolution', {value: '720p', innerHTML: ''});
  controls.set('production-continuity', {checked: false, disabled: false, title: ''});
  controls.set('production-inspector', {
    dataset: {},
    querySelector: selector => selector === '#production-prompt' ? prompt : null,
    querySelectorAll: () => [],
  });
  root.querySelector = selector => {
    if (selector === '.production-inspector') return controls.get('production-inspector');
    if (selector.startsWith('#')) return controls.get(selector.slice(1)) || null;
    return null;
  };
}

productionSceneId = 4;
productionShotId = 'parent:4';
wireControls('parent saved');
root.dataset = {};
actions.renderProductionWorkspace();
assert.equal(root.dataset.productionEditorShotId, 'parent:4');
const parentPrompt = controls.get('production-prompt');
parentPrompt.childNodes[0].nodeValue = 'unsaved parent line';
actions.productionSelectScene(5, 'parent:5');
assert.equal(saveRequests.length, 0, 'leaving a shot must not save the unsaved draft');
assert.equal(controls.get('production-prompt').childNodes[0].nodeValue, 'other parent');
actions.productionSelectScene(4, 'parent:4');
assert.equal(controls.get('production-prompt').childNodes[0].nodeValue, 'unsaved parent line', 'parent text survives leaving and returning');
assert.equal(saveRequests.length, 0, 'returning must not send a save');

actions.productionSetInspector('words');
actions.renderProductionWorkspace();
assert.equal(root.innerHTML.includes('data-inspector="words"'), true, 'same shot redraw keeps the prompt inspector');
assert.equal(root.innerHTML.includes('aria-selected="true"'), true);
assert.ok(root.innerHTML.includes('data-inspector-tab="words" aria-selected="true"'));

productionShotId = 'scene-4-shot-1';
actions.renderProductionWorkspace();
controls.get('production-prompt').childNodes[0].nodeValue = 'unsaved child line';
actions.productionSelectScene(4, 'scene-4-shot-2');
assert.equal(controls.get('production-prompt').childNodes[0].nodeValue, 'child two');
actions.productionSelectScene(4, 'scene-4-shot-1');
assert.equal(controls.get('production-prompt').childNodes[0].nodeValue, 'unsaved child line', 'child text is restored too');
assert.equal(saveRequests.length, 0);

appState.projectId = 'other-project';
productionShotId = 'parent:4';
actions.renderProductionWorkspace();
assert.equal(controls.get('production-prompt').childNodes[0].nodeValue, 'parent saved', 'a project change drops remembered drafts');

const css = await readFile(new URL('../web/css/production.css', import.meta.url), 'utf8');
assert.equal(css.includes('width: min(100%, 240px)'), false);
assert.equal((root.innerHTML.match(/productionTimelineHtml/g) || []).length, 0);
assert.equal((actions.renderProductionWorkspace.toString().match(/productionTimelineHtml\(/g) || []).length, 1);

console.log('production human path tests passed');
