const escapeHtml = (...args) => globalThis.StudioActions.escapeHtml(...args);
const mediaUrl = (...args) => globalThis.StudioActions.mediaUrl(...args);
const isMissingMediaPath = (...args) => globalThis.StudioActions.isMissingMediaPath(...args);
const productionActivateVersion = (...args) => globalThis.StudioActions.productionActivateVersion(...args);
const renderProductionWorkspace = (...args) => globalThis.StudioActions.renderProductionWorkspace(...args);

let productionData = null;
let productionSceneId = null;
let productionDragIndex = null;
let productionPollTimer = null;

function ensureProductionStyle() {}

function setupProductionWorkspace() {
  ensureProductionStyle();
  globalThis.StudioActions.resetProductionAudioDraft?.();
  const page = document.getElementById('page-shots');
  if (!page || document.getElementById('shot-production-workspace')) return;
  page.innerHTML = `<div id="shot-production-workspace"></div>`;
  const shotLink = document.querySelector('.sidebar-nav [data-page="shots"]');
  if (shotLink) shotLink.textContent = '🎬 分镜制作';
  const produceLink = document.querySelector('.sidebar-nav [data-page="produce"]');
  if (produceLink) produceLink.remove();
  const produceStep = document.querySelector('.step-item[data-page="produce"]');
  if (produceStep) { const divider = produceStep.nextElementSibling; produceStep.remove(); if (divider?.classList.contains('step-divider')) divider.remove(); }
  const producePage = document.getElementById('page-produce');
  if (producePage) producePage.remove();
  renderProductionWorkspace();
}

function productionCurrent() {
  return (productionData?.productions || []).find(item => item.scene.scene_id === productionSceneId) || null;
}

function productionStatusLabel(status) {
  return {idle:'待生成', generating:'生成中', success:'已生成', failed:'失败', canceled:'已终止'}[status] || '待生成';
}

function productionImageUrl(reference) {
  if (!reference?.image_path || reference.media_missing || isMissingMediaPath(reference.image_path)) return '';
  const versionId = reference.asset_version_id || reference.version_id || '';
  return mediaUrl(reference.image_path, versionId);
}
function productionVideoUrl(version) { return version?.video_url || (version?.local_path && !version.media_missing && !isMissingMediaPath(version.local_path) ? mediaUrl(version.local_path) : ''); }

function productionPreviewHtml(current) {
  const active = current?.production?.video_versions?.find(item => item.version_id === current.production.active_version_id);
  const video = productionVideoUrl(active);
  if (video) return `<video controls src="${video}"></video>`;
  const production = current?.production || {};
  return `<div><div style="font-size:28px;margin-bottom:8px">${production.generation_status === 'generating' ? '◌' : '▷'}</div><div>${productionStatusLabel(production.generation_status)}</div>${production.error_message ? `<div style="font-size:11px;color:#fca5a5;margin-top:8px">${escapeHtml(production.error_message)}</div>` : ''}</div>`;
}

function productionVersionListHtml(current) {
  const versions = current?.production?.video_versions || [];
  return versions.map((item, index) => `<button class="version-item ${item.version_id === current.production.active_version_id ? 'active' : ''}" onclick="StudioActions.productionActivateVersion('${item.version_id}')"><video muted preload="metadata" src="${productionVideoUrl(item)}"></video><span>版本 ${index + 1}</span></button>`).join('') || '<span style="font-size:11px;color:#71717a">暂无生成版本</span>';
}

const __moduleActions = {
  ensureProductionStyle,
  setupProductionWorkspace,
  productionCurrent,
  productionStatusLabel,
  productionImageUrl,
  productionVideoUrl,
  productionPreviewHtml,
  productionVersionListHtml,
};
Object.assign(globalThis.StudioActions, __moduleActions);
Object.defineProperties(globalThis, {
  productionData: { configurable: true, get: () => productionData, set: value => { productionData = value; } },
  productionSceneId: { configurable: true, get: () => productionSceneId, set: value => { productionSceneId = value; } },
  productionDragIndex: { configurable: true, get: () => productionDragIndex, set: value => { productionDragIndex = value; } },
  productionPollTimer: { configurable: true, get: () => productionPollTimer, set: value => { productionPollTimer = value; } },
});
export { __moduleActions };
