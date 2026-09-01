import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const source = await readFile(new URL('../web/js/pages/production-actions.js', import.meta.url), 'utf8');
assert.ok(source.includes('no_dialogue_confirmed'));
assert.ok(source.includes('audioSummary.can_assemble === false'));
assert.ok(source.includes('生成缺失台词'));
assert.ok(source.includes('当前对白音频不完整'));
assert.ok(source.includes('是否仍生成无对白版本'));
assert.ok(source.includes('audio_override'));
console.log('final audio gate UI tests passed');
