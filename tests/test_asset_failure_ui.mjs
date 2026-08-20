import assert from 'node:assert/strict';

const sceneContainer = {innerHTML: ''};
globalThis.appState = {
  scenes: [{
    sceneId: 4,
    locationId: 'loc_test_alley',
    stateId: 'state_rain',
    name: '雨夜小巷',
    shotIds: [4],
  }],
  sceneImages: [],
  assetErrors: {},
  assetBusy: {},
};
globalThis.projectJobs = [{
  job_id: 'failed-master',
  job_type: 'asset.generate',
  target_type: 'scene',
  target_id: 'loc_test_alley:scene_center',
  state: 'failed',
  updated_at: '2026-08-20T12:00:00Z',
  payload: {asset_id: 'loc_test_alley', view_type: 'scene_center'},
  error: {message: 'Image visual review requires regeneration'},
}];
globalThis.document = {
  getElementById: id => id === 'scene-cards' ? sceneContainer : null,
};
globalThis.StudioActions = {
  escapeHtml: value => String(value ?? ''),
  isMissingMediaPath: path => path === 'missing/history.png',
  mediaUrl: value => String(value ?? ''),
};

const {__moduleActions: taskActions} = await import('../web/js/components/task-drawer.js');
const {__moduleActions: cardActions} = await import('../web/js/components/asset-cards.js');
const {__moduleActions: imageEditorActions} = await import('../web/js/components/image-editor.js');

assert.equal(
  imageEditorActions.shouldShowMissingMediaNotice('missing/history.png'),
  true,
  'a known missing history file must use the dedicated missing-media notice',
);
assert.equal(
  imageEditorActions.shouldShowMissingMediaNotice('available/history.png'),
  false,
  'an available history file must still be loaded normally',
);
assert.equal(
  imageEditorActions.isUsableHistoryRecord({path: 'missing/history.png'}),
  false,
  'a history record whose file is known missing must render as unavailable',
);
assert.equal(
  imageEditorActions.isUsableHistoryRecord({path: 'available/history.png'}),
  true,
  'an available history record must keep its thumbnail preview',
);
assert.equal(
  imageEditorActions.isUsableHistoryRecord({path: 'unknown/history.png', file_available: false}),
  false,
  'the backend missing-file marker must suppress a broken history thumbnail',
);

taskActions.syncJobsToAssetState();
cardActions.renderScenes();

assert.equal(
  globalThis.appState.assetErrors['scene:loc_test_alley'],
  '视觉审核未通过，失败图片已保留在历史版本中',
  'a failed scene master job must be associated with its scene card',
);
assert.match(sceneContainer.innerHTML, /生成失败/, 'the scene card must show a failed state');
assert.match(
  sceneContainer.innerHTML,
  /视觉审核未通过，失败图片已保留在历史版本中/,
  'the scene card must expose the concrete failure reason instead of only a generic label',
);

let projectRefreshRequests = 0;
Object.assign(globalThis.StudioActions, {
  buildShotFromScene: () => ({}),
  buildShotsFromStateJson: () => [],
  defaultAppState: () => ({}),
  endApiProgress: () => {},
  isCurrentProjectSession: () => true,
  isTerminalJob: job => ['succeeded', 'failed', 'canceled', 'interrupted'].includes(job?.state),
  loadImageHistory: () => {},
  loadProductionWorkspace: () => {},
  mergeProjectJob: () => ({state: 'running'}),
  patchProductionJobState: () => {},
  projectJsonRequest: async () => {
    projectRefreshRequests += 1;
    return null;
  },
  projectSessionSnapshot: () => ({token: 1, projectId: 'project-test'}),
  renderImageEditor: () => {},
  renderProjectJobs: () => {},
  renderProps: () => {},
  renderRecoveredMedia: () => {},
  renderRoles: () => {},
  renderScenes: () => {},
  renderShots: () => {},
  renderStructuredScript: () => {},
  restoreProjectMedia: () => {},
  setScriptInputMode: () => {},
  showAssetTab: () => {},
  syncJobsToAssetState: () => {},
  syncScriptAssets: () => {},
  updateJobDrivenControls: () => {},
});
globalThis.appState = {projectId: 'project-test'};
globalThis.API_BASE = 'http://example.test';

const {__moduleActions: jobEventActions} = await import('../web/js/core/job-events.js');
jobEventActions.handleProjectJobEvent({
  job: {
    job_id: 'failed-master',
    job_type: 'asset.generate',
    target_type: 'scene',
    target_id: 'loc_test_alley:scene_center',
    state: 'failed',
  },
});
await new Promise(resolve => setTimeout(resolve, 0));

assert.equal(
  projectRefreshRequests,
  1,
  'a terminal failed image job must refresh persisted candidates and image history',
);

console.log('asset failure UI tests passed');
