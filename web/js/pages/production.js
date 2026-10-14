const escapeHtml = (...args) => globalThis.StudioActions.escapeHtml(...args);
const mediaUrl = (...args) => globalThis.StudioActions.mediaUrl(...args);
const isMissingMediaPath = (...args) => globalThis.StudioActions.isMissingMediaPath(...args);
const productionActivateVersion = (...args) => globalThis.StudioActions.productionActivateVersion(...args);
const renderProductionWorkspace = (...args) => globalThis.StudioActions.renderProductionWorkspace(...args);

let productionData = null;
let productionSceneId = null;
let productionShotId = null;
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

function productionSelectedShot(current = productionCurrent()) {
  if (!current) return null;
  const children = Array.isArray(current.executable_shots) ? current.executable_shots.filter(item => item?.shot_id) : [];
  if (!children.length || productionShotId === `parent:${current?.scene?.scene_id}`) return current.prompt || null;
  return children.find(item => item.shot_id === productionShotId) || children[0];
}

function productionTargetJob(current, shot = productionSelectedShot(current), jobs = globalThis.projectJobs || []) {
  const targetType = shot?.shot_id ? 'shot_unit' : 'shot';
  const targetId = String(shot?.shot_id || current?.scene?.scene_id || '');
  return jobs.filter(job => job.job_type === 'video.generate' && job.target_type === targetType && String(job.target_id) === targetId)
    .sort((a, b) => String(b.created_at || b.updated_at || '').localeCompare(String(a.created_at || a.updated_at || ''))
      || String(b.updated_at || '').localeCompare(String(a.updated_at || '')))[0] || null;
}

function productionShotStatus(current, shot = productionSelectedShot(current)) {
  const job = productionTargetJob(current, shot);
  const jobStatus = {queued: 'generating', running: 'generating', validating: 'generating', succeeded: 'success', failed: 'failed', interrupted: 'failed', canceled: 'canceled'}[job?.state];
  if (jobStatus) return jobStatus;
  const shotId = shot?.shot_id;
  if (shotId) return current?.production?.generation_status_by_shot_id?.[shotId] || 'idle';
  return current?.production?.generation_status || 'idle';
}

function productionShotError(current, shot = productionSelectedShot(current)) {
  const status = productionShotStatus(current, shot);
  if (!['failed', 'canceled', 'partial_failed'].includes(status)) return '';
  const job = productionTargetJob(current, shot);
  // Child errors are persisted by exact shot scope when the terminal job has
  // already fallen out of the client job list. Never consult a parent or
  // sibling task/error for a selected child.
  return job?.error?.message
    || (shot?.shot_id ? current?.production?.generation_error_by_shot_id?.[shot.shot_id] || '' : current?.production?.error_message || '');
}

function productionFailureHtml(current) {
  const shot = productionSelectedShot(current);
  const status = productionShotStatus(current, shot);
  if (!['failed', 'canceled', 'partial_failed'].includes(status)) return '';
  const job = productionTargetJob(current, shot);
  const message = productionShotError(current, shot);
  const scope = shot?.shot_id ? `子镜头 ${shot.shot_id}` : `父镜头 ${current?.scene?.scene_id || ''}`;
  const details = job ? `<button class="btn-secondary btn-sm" onclick="StudioActions.showProjectJobDetails('${escapeHtml(job.job_id)}')">查看任务详情</button>` : '';
  const retry = job
    ? (job.can_retry ? `<button class="btn-secondary btn-sm" onclick="StudioActions.retryProjectJob('${escapeHtml(job.job_id)}')">重试当前镜头</button>` : '')
    : '<button class="btn-secondary btn-sm" onclick="StudioActions.productionGenerateCurrent()">重试当前镜头</button>';
  return `<div class="production-preview-error" role="status"><b>${escapeHtml(scope)} · ${productionStatusLabel(status)}</b><div>${escapeHtml(message || '未返回具体错误')}</div>${job?.error?.suggestion ? `<div>${escapeHtml(job.error.suggestion)}</div>` : ''}<div class="production-preview-job-actions">${details}${retry}</div></div>`;
}

function productionShotVersions(current, shot = productionSelectedShot(current)) {
  const versions = Array.isArray(current?.production?.video_versions) ? current.production.video_versions : [];
  return shot?.shot_id ? versions.filter(item => item.shot_id === shot.shot_id) : versions.filter(item => !item.shot_id);
}

function productionShotActiveVersionId(current, shot = productionSelectedShot(current)) {
  return shot?.shot_id
    ? current?.production?.active_version_by_shot_id?.[shot.shot_id] || ''
    : current?.production?.active_version_id || '';
}

function productionStatusLabel(status) {
  return {idle:'待生成', generating:'生成中', success:'已生成', failed:'失败', canceled:'已终止'}[status] || '待生成';
}

function productionImageUrl(reference) {
  if (!reference?.image_path || reference.media_missing || isMissingMediaPath(reference.image_path)) return '';
  const versionId = reference.asset_version_id || reference.version_id || '';
  return mediaUrl(reference.image_path, versionId);
}
function productionVideoUrl(version) {
  if (version?.local_path && !version.media_missing && !isMissingMediaPath(version.local_path)) {
    return mediaUrl(version.local_path);
  }
  return version?.video_url || '';
}

function productionPreviewHtml(current) {
  const shot = productionSelectedShot(current);
  const activeVersionId = productionShotActiveVersionId(current, shot);
  const active = productionShotVersions(current, shot).find(item => item.version_id === activeVersionId);
  const video = productionVideoUrl(active);
  const failure = productionFailureHtml(current);
  if (video) return failure ? `<div class="production-preview-output"><video controls src="${video}"></video>${failure}</div>` : `<video controls src="${video}"></video>`;
  const status = productionShotStatus(current, shot);
  return `<div><div style="font-size:28px;margin-bottom:8px">${status === 'generating' ? '◌' : '▷'}</div><div>${productionStatusLabel(status)}</div>${failure}</div>`;
}

function productionVersionListHtml(current) {
  const shot = productionSelectedShot(current);
  const activeVersionId = productionShotActiveVersionId(current, shot);
  const versions = productionShotVersions(current, shot);
  const scopeLabel = shot?.shot_id ? `当前子镜头 ${escapeHtml(String(shot.shot_id))}` : `父镜头 ${escapeHtml(String(current?.scene?.scene_id || ''))}`;
  return `<span class="production-version-scope">${scopeLabel}</span>${versions.map((item, index) => `<button class="version-item ${item.version_id === activeVersionId ? 'active' : ''}" aria-label="${item.version_id === activeVersionId ? '当前采用，' : ''}${scopeLabel}，版本 ${index + 1}" onclick="StudioActions.productionActivateVersion('${item.version_id}')"><video muted preload="metadata" src="${productionVideoUrl(item)}"></video><span>版本 ${index + 1}${item.version_id === activeVersionId ? ' · 当前' : ''}</span></button>`).join('') || '<span class="production-empty-inline">暂无当前镜头生成版本</span>'}`;
}

const __moduleActions = {
  ensureProductionStyle,
  setupProductionWorkspace,
  productionCurrent,
  productionSelectedShot,
  productionTargetJob,
  productionShotStatus,
  productionShotError,
  productionFailureHtml,
  productionShotVersions,
  productionShotActiveVersionId,
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
  productionShotId: { configurable: true, get: () => productionShotId, set: value => { productionShotId = value; } },
  productionDragIndex: { configurable: true, get: () => productionDragIndex, set: value => { productionDragIndex = value; } },
  productionPollTimer: { configurable: true, get: () => productionPollTimer, set: value => { productionPollTimer = value; } },
});
export { __moduleActions };
