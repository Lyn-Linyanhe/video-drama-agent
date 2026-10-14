import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

globalThis.StudioActions = {
  escapeHtml: value => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;'),
  mediaUrl: value => String(value ?? ''),
  isMissingMediaPath: () => false,
  productionVideoUrl: version => version?.video_url || '',
  productionImageUrl: reference => reference?.image_path || '',
  productionStatusLabel: status => status,
};

const source = await readFile(new URL('../web/js/pages/production-actions.js', import.meta.url), 'utf8');
const css = await readFile(new URL('../web/css/production.css', import.meta.url), 'utf8');
const productionSource = await readFile(new URL('../web/js/pages/production.js', import.meta.url), 'utf8');
await import('data:text/javascript;base64,' + Buffer.from(productionSource).toString('base64'));
const {__moduleActions} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

const child = (shot_id, shot_order, duration_sec) => ({
  shot_id,
  shot_order,
  duration_sec,
  prompt: `子镜头 ${shot_id}`,
  negative_prompt: '',
});
const data = {
  productions: [
    {
      scene: {scene_id: 4, location: '店门口'},
      prompt: {scene_id: 4, duration_sec: 5, prompt: '父镜头'},
      production: {
        generation_status: 'idle',
        generation_status_by_shot_id: {},
        active_version_id: '',
        active_version_by_shot_id: {},
        video_versions: [],
      },
      executable_shots: [
        child('scene-4-shot-1', 1, 4),
        child('scene-4-shot-2', 2, 4),
        child('scene-4-shot-3', 3, 4),
        child('scene-4-shot-4', 4, 4),
        child('scene-4-shot-5', 5, 7),
      ],
    },
    {
      scene: {scene_id: 5, location: '店内'},
      prompt: {scene_id: 5, duration_sec: 8, prompt: '无拆镜父镜头'},
      production: {generation_status: 'failed', video_versions: [], active_version_id: ''},
      executable_shots: [],
    },
  ],
  shot_capacity: {
    items: [{scene_id: 4, status: 'warning', estimated_duration_sec: 22.03, duration_sec: 5, warnings: ['镜头动作过多，已自动拆分']}],
  },
};

const entries = __moduleActions.productionTimelineEntries(data);
assert.equal(entries.length, 6, '拆镜场景应展开为子镜头，未拆场景保留父镜头');
assert.deepEqual(entries.slice(0, 2).map(item => item.shotId), ['scene-4-shot-1', 'scene-4-shot-2']);
assert.equal(entries[0].label, '镜头 4-1 · 4s');
assert.equal(entries[4].label, '镜头 4-5 · 7s');
assert.equal(entries[5].label, '镜头 2 · 8s');

const selected = __moduleActions.productionSelection(data, 4, 'scene-4-shot-3');
assert.equal(selected.sceneId, 4);
assert.equal(selected.shot.shot_id, 'scene-4-shot-3');
assert.equal(selected.isSubShot, true);
const defaultSelected = __moduleActions.productionSelection(data, 4);
assert.equal(defaultSelected.shot.shot_id, 'scene-4-shot-1', '选择父场景时默认选第一个子镜头');
const parentSelected = __moduleActions.productionSelection(data, 4, 'parent:4');
assert.equal(parentSelected.shotId, 'parent:4');
assert.equal(parentSelected.shot.prompt, '父镜头');
assert.equal(parentSelected.isSubShot, false);

assert.deepEqual(
  __moduleActions.productionGenerationTarget(4, 'scene-4-shot-1'),
  {target_type: 'shot_unit', target_id: 'scene-4-shot-1', payload: {scene_id: 4, shot_id: 'scene-4-shot-1'}},
);
assert.deepEqual(
  __moduleActions.productionGenerationTarget(5, null),
  {target_type: 'shot', target_id: '5', payload: {scene_id: 5}},
);

const diagnostics = __moduleActions.storyboardQualityDiagnosticsHtml(data);
assert.ok(diagnostics.includes('镜头承载偏高：已按内容拆为可执行子镜头'));
assert.ok(diagnostics.includes('已拆 5 个子镜头'));
assert.ok(diagnostics.includes('4 / 4 / 4 / 4 / 7 秒'));
assert.ok(!diagnostics.includes('建议拆镜，但当前内容未被删除'));

const notMaterialized = __moduleActions.storyboardQualityDiagnosticsHtml({
  shot_capacity: {items: [{scene_id: 9, status: 'warning', warnings: ['内容过多']}]},
  productions: [{scene: {scene_id: 9}, executable_shots: []}],
});
// A scene flagged as overloaded but not yet split reports the raw warning.
assert.ok(notMaterialized.includes('镜头承载偏高'));
assert.ok(notMaterialized.includes('内容过多'));
const timelineHtml = __moduleActions.productionTimelineHtml(data);
assert.ok(timelineHtml.includes('镜头 4-1 · 4s'));
assert.ok(timelineHtml.includes('父镜头 4 · 编辑源提示词'));
assert.ok(timelineHtml.includes('data-production-shot-id="scene-4-shot-1"'));
assert.ok(timelineHtml.includes('class="production-timeline-group"'));
assert.ok(timelineHtml.includes('class="production-timeline-shots"'));
assert.match(css, /\.production-timeline\s*\{[\s\S]*?max-height:\s*none;/, '时间轴不能用固定高度裁掉分组子镜头');
assert.match(css, /\.production-timeline\s*\{[\s\S]*?overflow-y:\s*(?:auto|visible);/, '时间轴必须允许纵向内容展示');
assert.match(css, /\.production-timeline-group\s*\{/, '拆镜分组需要正式布局样式');
assert.match(css, /\.production-timeline-shots\s*\{/, '子镜头行需要正式布局样式');

assert.equal(
  __moduleActions.productionCurrentVideoButtonLabel({job_id: 'single'}, 'generating'),
  '终止当前镜头',
);
assert.equal(
  __moduleActions.productionCurrentVideoButtonLabel({job_id: 'batch', batch_id: 'batch-1'}, 'generating'),
  '终止批量中的当前镜头',
);
assert.equal(
  __moduleActions.productionActiveVideoBatchJobs([
    {job_type: 'video.generate', target_id: 'scene-4-shot-1'},
    {job_type: 'video.generate', target_id: 'scene-4-shot-2', batch_id: 'batch-1'},
    {job_type: 'video.generate', target_id: 'scene-4-shot-3', batch_id: 'batch-1'},
  ]).length,
  2,
);
assert.ok(css.includes('.production-project-tools-note'));
assert.ok(css.includes('.production-context-actions'));
assert.ok(source.includes('不会取消或替换项目批量任务'));
assert.ok(source.includes('当前镜头任务不会被取消'));

console.log('production subshot UI tests passed');


