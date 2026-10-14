import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

globalThis.StudioActions = {
  escapeHtml: value => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('\"', '&quot;')
    .replaceAll("'", '&#39;'),
};

const source = await readFile(new URL('../web/js/pages/production-actions.js', import.meta.url), 'utf8');
const {__moduleActions} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

const data = {
  prompt_fidelity: {
    status: 'failed',
    issues: [{scene_id: 2, issue_code: 'forbidden_exit_action', severity: 'critical', description: '提示词加入了出门动作', blocks_generation: true, evidence: {expected_excerpt: '门始终关闭', prompt_excerpt: '角色推门走出', prompt_section: '动作时间轴'}}],
  },
  shot_capacity: {items: [{scene_id: 3, status: 'warning', suggested_shot_count: 2, source_duration_sec: 5, recommended_total_duration_sec: 9, recommended_durations_sec: [4, 5], estimated_dialogue_sec: 3, action_required_sec: 2, warnings: ['预计完整执行需要约9s，但场景只有5s']}]},
  character_profile_repair: {status: 'partial', errors: ['缺少发型信息']},
};
const html = __moduleActions.storyboardQualityDiagnosticsHtml(data);
assert.ok(html.includes('提示词存在确定性保真问题：禁止生成'));
assert.ok(html.includes('镜头 2'));
assert.ok(html.includes('门始终关闭'));
assert.ok(html.includes('角色推门走出'));
assert.ok(html.includes('镜头承载偏高：已按内容合理扩时'));
assert.ok(html.includes('原场景 5s'));
assert.ok(html.includes('建议 9s'));
assert.ok(html.includes('4 / 5'));
assert.ok(html.includes('对白约 3s'));
assert.ok(html.includes('动作约 2s'));
assert.ok(html.includes('镜头 3'));
assert.ok(html.includes('角色状态信息不完整：可继续查看，但建议补充'));
assert.ok(html.includes('缺少发型信息'));
assert.ok(!html.includes('审核过期，禁止生成'));
console.log('storyboard quality UI tests passed');
