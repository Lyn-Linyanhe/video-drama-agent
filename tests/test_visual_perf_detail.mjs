import assert from 'node:assert/strict';

const detailContent = {innerHTML: ''};
const modal = {style: {display: ''}};
globalThis.document = {
  getElementById: id => id === 'project-job-detail-content' ? detailContent : id === 'modal-job-detail' ? modal : null,
};
globalThis.appState = {projectId: 'proj-x'};
globalThis.alert = msg => { throw new Error(String(msg)); };
globalThis.StudioActions = {
  escapeHtml: value => String(value ?? ''),
  jobTargetLabel: job => job.target_id || '',
  projectJsonRequest: async () => ({
    job: {
      job_id: 'viz-1',
      job_type: 'script.visualize',
      state: 'succeeded',
      stage: 'saving',
      attempt: 1,
      provider: 'deepseek',
      result: {
        visual_performance: {
          requests: [
            {stage: 'visualize_global', scene_id: 0, elapsed_ms: 1200, attempts: 1, input_chars: 3000, output_chars: 800, fallback_used: false},
            {stage: 'visualize_scene', scene_id: 1, elapsed_ms: 900, attempts: 1, input_chars: 500, output_chars: 120, fallback_used: false},
            {stage: 'visualize_scene', scene_id: 2, elapsed_ms: 600, attempts: 2, input_chars: 500, output_chars: 110, fallback_used: true},
            {stage: 'visualize', elapsed_ms: 3600, attempts: 8, fallback_used: false},
          ],
        },
      },
    },
    events: [],
  }),
};

import {readFile} from 'node:fs/promises';
const jobEventsSource = await readFile(new URL('../web/js/core/job-events.js', import.meta.url), 'utf8');
// Browser globals that plain <script> tags share in studio.html are lexical in
// Node ESM, so inject them into a data-URL copy of the module before import.
const injectedSource = `const API_BASE = '';
const JOB_TYPE_LABELS = {'script.visualize': '生成视觉与分镜提示词'};
const JOB_STAGE_LABELS = {saving: '保存结果'};
const JOB_STATE_LABELS = {succeeded: '成功'};
` + jobEventsSource;
const {__moduleActions} = await import('data:text/javascript;base64,' + Buffer.from(injectedSource).toString('base64'));
await __moduleActions.showProjectJobDetails('viz-1');

assert.ok(detailContent.innerHTML.includes('视觉生成明细'), 'visual performance block must render');
assert.ok(detailContent.innerHTML.includes('总耗时 3600ms'), 'total request row must render');
assert.ok(detailContent.innerHTML.includes('镜头 1'), 'scene row must render');
assert.ok(detailContent.innerHTML.includes('镜头 2'), 'second scene row must render');
assert.ok(detailContent.innerHTML.includes('回落'), 'fallback marker must render for scene 2');
console.log('visual perf detail UI test passed');
