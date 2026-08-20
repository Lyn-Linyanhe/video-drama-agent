import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const studio = await readFile(new URL('../studio.html', import.meta.url), 'utf8');
assert.match(
  studio,
  /\/static\/js\/bootstrap\.js\?v=20260820-asset-review-fallback-1/,
  'the script progress UI must invalidate the browser module cache',
);

console.log('frontend cache version test passed');
