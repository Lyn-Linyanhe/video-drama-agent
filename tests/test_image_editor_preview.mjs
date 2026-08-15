import assert from 'node:assert/strict';

globalThis.StudioActions = {};
const {__moduleActions} = await import('../web/js/components/image-editor.js');

const staleEntry = {
  image_path: 'E:/workspace/output/project/assets/characters/苏眠/front_half.png',
  media_missing: true,
};

assert.equal(
  __moduleActions.imageEditorImageSource(staleEntry),
  staleEntry.image_path,
  'an existing image path must still be attempted when a stale missing flag is present',
);

assert.equal(__moduleActions.imageEditorImageSource({}), '', 'entries without an image path stay empty');
assert.match(
  await (await import('node:fs/promises')).readFile(new URL('../web/js/components/image-editor.js', import.meta.url), 'utf8'),
  /const usable = Boolean\(record\.path\);/,
  'history thumbnails must also try persisted paths before showing a missing notice',
);

console.log('image editor preview tests passed');
