import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

globalThis.StudioActions = {
  escapeHtml: value => String(value ?? ''),
  mediaUrl: value => String(value ?? ''),
  isMissingMediaPath: () => false,
  productionActivateVersion: () => {},
  renderProductionWorkspace: () => {},
};
const source = await readFile(new URL('../web/js/pages/production.js', import.meta.url), 'utf8');
const {__moduleActions} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const item = {
  scene: {scene_id: 4},
  prompt: {scene_id: 4, prompt: '父提示词', duration_sec: 5},
  executable_shots: [
    {scene_id: 4, shot_id: 'scene-4-shot-1', shot_order: 1, prompt: '子一', duration_sec: 4},
    {scene_id: 4, shot_id: 'scene-4-shot-2', shot_order: 2, prompt: '子二', duration_sec: 6},
  ],
  production: {
    generation_status: 'idle',
    generation_status_by_shot_id: {'scene-4-shot-2': 'success'},
    video_versions: [{version_id: 'version-2', shot_id: 'scene-4-shot-2', video_url: '/two.mp4'}],
    active_version_by_shot_id: {'scene-4-shot-2': 'version-2'},
  },
};
productionData = {productions: [item]};
productionSceneId = 4;
productionShotId = null;
assert.equal(__moduleActions.productionSelectedShot().shot_id, 'scene-4-shot-1');
productionShotId = 'scene-4-shot-2';
assert.equal(__moduleActions.productionSelectedShot().prompt, '子二');
assert.equal(__moduleActions.productionShotStatus(item), 'success');
assert.equal(__moduleActions.productionShotVersions(item).length, 1);
assert.equal(__moduleActions.productionShotActiveVersionId(item), 'version-2');
productionShotId = 'parent:4';
assert.equal(__moduleActions.productionSelectedShot().prompt, '父提示词', '父镜头入口应能返回源提示词');
console.log('production selection state tests passed');
