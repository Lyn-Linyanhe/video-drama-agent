import assert from 'node:assert/strict';

const cardContainer = {innerHTML: ''};
globalThis.appState = {
  roles: [{name: '苏眠', gender: '女', identity: '记者', personality: '', description: ''}],
  characterImages: [{
    character_name: '苏眠',
    view_type: 'front_half',
    image_path: 'E:/workspace/output/project/assets/characters/苏眠/front_half.png',
    media_missing: true,
  }],
  assetErrors: {},
  assetBusy: {},
};
globalThis.document = {getElementById: id => id === 'role-cards' ? cardContainer : null};
globalThis.StudioActions = {
  escapeHtml: value => String(value ?? ''),
  mediaUrl: value => String(value ?? ''),
  isMissingMediaPath: () => true,
};

const {__moduleActions} = await import('../web/js/components/asset-cards.js');
__moduleActions.renderRoles();

assert.match(cardContainer.innerHTML, /<img\s/, 'stale missing status must not suppress an image path from the asset card');
assert.doesNotMatch(cardContainer.innerHTML, /文件缺失/, 'stale missing status must not be shown when a path is available');

console.log('asset card media tests passed');
