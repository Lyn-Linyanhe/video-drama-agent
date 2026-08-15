import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const studio = await readFile(new URL('../studio.html', import.meta.url), 'utf8');
assert.match(
  studio,
  /\/static\/js\/bootstrap\.js\?v=20260815-image-preview-repair/,
  'the image rendering fix must invalidate the browser module cache',
);

console.log('frontend cache version test passed');
