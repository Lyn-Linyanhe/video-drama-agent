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

const supported = __moduleActions.productionAudioModeHtml(
  'native_dialogue',
  {provider: 'seedance', model: 'seedance-test'},
  {supports_native_dialogue: true, native_audio_status: 'supported'},
);
assert.ok(supported.includes('视频模型原生对白'));
assert.ok(supported.includes('外部 TTS 配音'));
assert.ok(supported.includes('已验证支持'));
assert.ok(supported.includes('value="native_dialogue" checked'));

const unsupported = __moduleActions.productionAudioModeHtml(
  'native_dialogue',
  {provider: 'happyhorse', model: 'happyhorse-1.1-r2v'},
  {
    supports_native_dialogue: false,
    native_audio_status: 'unsupported',
    native_audio_reason: 'No verified contract',
  },
);
assert.ok(unsupported.includes('当前模型不支持'));
assert.ok(unsupported.includes('No verified contract'));

const legacy = __moduleActions.productionAudioModeHtml(
  'auto',
  {provider: 'happyhorse', model: 'happyhorse-1.1-r2v'},
  {supports_native_dialogue: false, native_audio_status: 'unsupported'},
);
assert.ok(legacy.includes('旧版自动策略'));
assert.ok(legacy.includes('value="external_tts" checked'));

assert.ok(source.includes("audio_mode:mode"));
assert.ok(source.includes('audioModePanel.outerHTML = productionAudioModeHtml'));
assert.ok(source.includes('/production/settings'));
console.log('audio mode UI tests passed');
