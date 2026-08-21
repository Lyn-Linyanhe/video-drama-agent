import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

globalThis.StudioActions = {
  escapeHtml: value => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;'),
};

const source = await readFile(new URL('../web/js/pages/production-actions.js', import.meta.url), 'utf8');
const {__moduleActions} = await import(
  'data:text/javascript;base64,' + Buffer.from(source).toString('base64')
);

const issue = {
  scene_id: 0,
  severity: 'major',
  category: 'coverage',
  layer: 'source_parse',
  scope: 'source_alignment',
  issue_code: 'parse.missing_action',
  confidence: 'confirmed',
  description: '<script>alert(1)</script>',
  suggestion: '修正结构化剧本',
  repair_action: 'edit_prompt',
  evidence_items: [{source: 'source_script', line_start: 2, line_end: 2, excerpt: '<b>原文</b>'}],
};
const crossShotIssue = {
  scene_id: 2,
  related_scene_ids: [1, 2],
  severity: 'minor',
  category: 'continuity',
  layer: 'prompt',
  scope: 'cross_shot',
  issue_code: 'prompt.continuity',
  confidence: 'suspected',
  description: '承接待确认',
  suggestion: '人工复核',
  evidence_items: [{source: 'prompt', time_start_sec: 1, time_end_sec: 2, excerpt: '实际片段'}],
};

const report = {
  report_version: 2,
  verdict: 'blocked',
  overall_score: 60,
  passed: false,
  summary: '存在结构错误',
  issues: [issue, crossShotIssue],
  shots: [{scene_id: 2, score: 95, passed: true, issues: [crossShotIssue]}],
  shots_issue_index: {'2': [crossShotIssue]},
};
const review = {status: 'ready', report};

assert.equal(__moduleActions.promptReviewIssues(report).length, 2);
const html = __moduleActions.promptReviewHtml(review);
assert.ok(html.includes('已阻断'));
assert.ok(html.includes('解析问题'));
assert.ok(html.includes('跨镜问题'));
assert.ok(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
assert.ok(!html.includes('定位提示词'));

const staleHtml = __moduleActions.promptReviewHtml({
  status: 'stale',
  report: {overall_score: 95, passed: true, issues: []},
});
assert.ok(staleHtml.includes('报告已过期'));
assert.ok(!staleHtml.includes('>通过<'));
assert.equal(__moduleActions.promptReviewShotStatusLabel({status: 'stale'}, report, {score: 100, passed: true}), '报告已过期');
const legacyReport = {...report, report_version: 1, verdict: 'pass', passed: true};
assert.equal(__moduleActions.promptReviewShotStatusLabel({status: 'ready'}, legacyReport, {score: 100, passed: true}), '旧版报告');
assert.ok(__moduleActions.promptReviewHtml({status: 'ready', report: legacyReport}).includes('旧版报告'));
console.log('prompt review UI tests passed');
