import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

globalThis.StudioActions = {
  escapeHtml: value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;'),
  mediaUrl: value => String(value ?? ''),
  isMissingMediaPath: () => false,
};
globalThis.projectJobs = [];
for (const name of ['production.js', 'production-actions.js']) {
  const source = await readFile(new URL(`../web/js/pages/${name}`, import.meta.url), 'utf8');
  await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
}
const actions = StudioActions;
const item = {
  scene: {scene_id: 4, location: 'room', characters: []},
  prompt: {scene_id: 4, prompt: 'parent', duration_sec: 8},
  executable_shots: [
    {shot_id: 'scene-4-shot-1', shot_order: 1, prompt: 'one', duration_sec: 4},
    {shot_id: 'scene-4-shot-2', shot_order: 2, prompt: 'two', duration_sec: 4},
  ],
  production: {
    references: [], generation_status: 'generating', error_message: 'parent-only error',
    generation_status_by_shot_id: {'scene-4-shot-1': 'failed', 'scene-4-shot-2': 'generating'},
    generation_error_by_shot_id: {'scene-4-shot-1': 'persisted child error', 'scene-4-shot-2': 'sibling persisted error'},
    video_versions: [], active_version_by_shot_id: {},
  },
};
productionData = {productions: [item]};
productionSceneId = 4;
productionShotId = 'scene-4-shot-1';
const failed = {
  job_id: 'failed-one', job_type: 'video.generate', target_type: 'shot_unit', target_id: 'scene-4-shot-1',
  state: 'failed', created_at: '2026-10-02T10:00:00', updated_at: '2026-10-02T10:00:02',
  error: {message: '<provider rejected one>', suggestion: 'change input'}, can_retry: true,
};
const sibling = {...failed, job_id: 'failed-two', target_id: 'scene-4-shot-2', error: {message: 'sibling error'}};
const parent = {...failed, job_id: 'failed-parent', target_type: 'shot', target_id: '4', error: {message: 'parent task error'}};
projectJobs = [sibling, parent, failed];
const preview = actions.productionPreviewHtml(item);
assert.ok(preview.includes('&lt;provider rejected one&gt;'), 'preview uses the selected child task error');
assert.ok(preview.includes("showProjectJobDetails('failed-one')"), 'details opens the selected child task');
assert.ok(!preview.includes('sibling error'));
assert.ok(!preview.includes('parent-only error'));
assert.ok(!preview.includes('parent task error'));
projectJobs = [];
assert.ok(actions.productionPreviewHtml(item).includes('persisted child error'), 'missing child task uses the persisted exact-target error');
assert.ok(!actions.productionPreviewHtml(item).includes('sibling persisted error'), 'missing child task does not borrow a sibling persisted error');
assert.ok(!actions.productionPreviewHtml(item).includes('parent-only error'), 'missing child task does not borrow the parent error');

const controls = new Map();
const nodes = new Map();
const root = {
  dataset: {}, innerHTML: '',
  querySelector: selector => {
    for (const [target, node] of nodes) {
      if (selector.includes(`data-production-scene-id="${target.sceneId}"`) && selector.includes(`data-production-shot-id="${target.shotId}"`)) {
        return selector.endsWith(' .timeline-status') ? node.label : node;
      }
    }
    return null;
  },
  querySelectorAll: () => [],
};
globalThis.document = {
  getElementById: id => id === 'shot-production-workspace' ? root : controls.get(id) || null,
  querySelector: selector => root.querySelector(selector),
};
globalThis.window = {scrollY: 0, scrollTo: () => {}, location: {href: 'http://localhost/'}};
globalThis.CSS = {escape: String};
globalThis.appState = {projectId: 'test-project'};
globalThis.API_BASE = '';
actions.activeProjectJobs = predicate => projectJobs.filter(job => ['queued', 'running', 'validating'].includes(job.state) && predicate(job));
actions.isTerminalJob = job => ['succeeded', 'failed', 'canceled', 'interrupted'].includes(job?.state);
actions.renderProductionAudioTimeline = () => {};
globalThis.JOB_STATE_LABELS = {running: '生成中', failed: '失败'};
for (const id of ['production-current-status', 'production-context-status', 'production-preview-stage', 'production-version-list', 'production-preview-download', 'production-generate-current']) {
  controls.set(id, {textContent: '', innerHTML: '', dataset: {}});
}
item.production.generation_status_by_shot_id['scene-4-shot-1'] = 'generating';
const running = {...failed, state: 'running', error: null};
projectJobs = [running];
controls.get('production-preview-stage').innerHTML = actions.productionPreviewHtml(item);
projectJobs = [failed];
actions.patchProductionJobState(failed);
assert.equal(controls.get('production-current-status').textContent, '失败', 'SSE immediately patches selected status');
assert.equal(controls.get('production-context-status').textContent, '失败', 'toolbar status stays synchronized');
assert.ok(controls.get('production-preview-stage').innerHTML.includes('&lt;provider rejected one&gt;'), 'SSE replaces generating preview without waiting for hydration');
// Hydration changes only the child status while the parent remains generating.
projectJobs = [];
item.production.generation_status_by_shot_id['scene-4-shot-1'] = 'generating';
const before = structuredClone(productionData);
controls.get('production-preview-stage').dataset = {};
controls.get('production-preview-stage').innerHTML = actions.productionPreviewHtml(item);
item.production.generation_status_by_shot_id['scene-4-shot-1'] = 'failed';
actions.patchProductionWorkspace(before);
assert.ok(controls.get('production-preview-stage').innerHTML.includes('失败'));
assert.ok(!controls.get('production-preview-stage').innerHTML.includes('生成中'), 'hydrate does not leave a stale generating preview');

// Parent and child targets remain independently locatable and independently updated.
const parentNode = {label: {textContent: 'parent'}, querySelector(selector) {return selector === '.timeline-status' ? this.label : null;}};
const childNode = {label: {textContent: 'child'}, querySelector(selector) {return selector === '.timeline-status' ? this.label : null;}};
const siblingNode = {label: {textContent: 'sibling'}, querySelector(selector) {return selector === '.timeline-status' ? this.label : null;}};
nodes.set({sceneId: '4', shotId: ''}, parentNode);
nodes.set({sceneId: '4', shotId: 'scene-4-shot-1'}, childNode);
nodes.set({sceneId: '4', shotId: 'scene-4-shot-2'}, siblingNode);
projectJobs = [failed];
actions.patchProductionJobState(failed);
const selectedPreview = controls.get('production-preview-stage').innerHTML;
const doneSibling = {...sibling, state: 'succeeded', error: null};
projectJobs = [doneSibling, failed];
actions.patchProductionJobState(doneSibling);
assert.equal(childNode.label.textContent, '失败');
assert.equal(siblingNode.label.textContent, '已生成');
assert.equal(parentNode.label.textContent, 'parent');
assert.equal(controls.get('production-preview-stage').innerHTML, selectedPreview);
const doneParent = {...parent, state: 'succeeded', error: null};
projectJobs = [doneParent, failed];
actions.patchProductionJobState(doneParent);
assert.equal(parentNode.label.textContent, '已生成');
assert.equal(childNode.label.textContent, '失败', 'parent event never updates the first child node');
const timeline = actions.productionTimelineHtml(productionData);
assert.match(timeline, /production-timeline-parent[^>]*data-production-scene-id="4"[^>]*data-production-shot-id=""/);
assert.ok(timeline.includes('data-production-shot-id="scene-4-shot-1"'));

// Retry is authoritative even when a late event reorders an older failure to the front.
const retry = {...running, job_id: 'retry-one', created_at: '2026-10-02T10:00:03', updated_at: '2026-10-02T10:00:03'};
projectJobs = [{...failed, updated_at: '2026-10-02T10:00:04'}, retry];
assert.equal(actions.productionShotStatus(item), 'generating');
assert.ok(!actions.productionPreviewHtml(item).includes('provider rejected one'));
projectJobs = [failed];
item.production.active_version_by_shot_id['scene-4-shot-1'] = 'old-good';
item.production.video_versions = [{version_id: 'old-good', shot_id: 'scene-4-shot-1', video_url: '/old-good.mp4'}];
const retained = actions.productionPreviewHtml(item);
assert.ok(retained.includes('/old-good.mp4'));
assert.ok(retained.includes('provider rejected one'), 'a retained video must not hide the failed retry');
assert.ok(retained.includes("retryProjectJob('failed-one')"));
const previewStage = controls.get('production-preview-stage');
let previewWrites = 0;
let previewMarkup = retained;
Object.defineProperty(previewStage, 'innerHTML', {configurable: true, get: () => previewMarkup, set: value => {previewWrites++; previewMarkup = value;}});
previewStage.dataset.productionPreviewHtml = retained;
actions.patchProductionJobState(failed);
assert.equal(previewWrites, 0, 'unchanged preview does not recreate the playing video');
const canceled = {...failed, state: 'canceled', error: null};
projectJobs = [canceled];
actions.patchProductionJobState(canceled);
assert.equal(controls.get('production-current-status').textContent, '已终止');
assert.ok(!previewStage.innerHTML.includes('provider rejected one'));
projectJobs = [{...failed, can_retry: false}];
assert.ok(!actions.productionPreviewHtml(item).includes("retryProjectJob('failed-one')"), 'retry capability is taken from the task API');
projectJobs = [failed];
productionShotId = 'parent:4';
projectJobs = [parent, failed];
assert.ok(actions.productionPreviewHtml(item).includes('parent task error'));
assert.ok(!actions.productionPreviewHtml(item).includes('provider rejected one'));
productionShotId = 'scene-4-shot-1';
item.production.video_versions = [];
item.production.active_version_by_shot_id = {};

// Exercise the real SSE entry point and job-list hydration boundary.
const eventSource = await readFile(new URL('../web/js/core/job-events.js', import.meta.url), 'utf8');
await import('data:text/javascript;base64,' + Buffer.from(eventSource).toString('base64'));
actions.mergeProjectJob = job => {
  const previous = projectJobs.find(item => item.job_id === job.job_id) || null;
  projectJobs = [job, ...projectJobs.filter(item => item.job_id !== job.job_id)];
  return previous;
};
for (const name of ['renderProjectJobs', 'updateJobDrivenControls', 'syncJobsToAssetState', 'renderRoles', 'renderScenes', 'renderProps']) actions[name] = () => {};
actions.projectSessionSnapshot = () => ({projectId: 'test-project'});
actions.isCurrentProjectSession = () => true;
actions.loadProductionWorkspace = async () => {};
projectJobs = [running];
actions.handleProjectJobEvent({job: failed});
assert.equal(controls.get('production-current-status').textContent, '失败');
assert.ok(controls.get('production-preview-stage').innerHTML.includes("showProjectJobDetails('failed-one')"));
globalThis.projectJobStateCache = new Map();
projectJobs = [running];
actions.patchProductionJobState(running);
actions.projectJsonRequest = async () => ({jobs: [failed]});
await actions.loadProjectJobs();
assert.equal(controls.get('production-current-status').textContent, '失败');
assert.ok(controls.get('production-preview-stage').innerHTML.includes('provider rejected one'));
console.log('production failure recovery tests passed');
