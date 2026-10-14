const activeProjectJobs = (...args) => globalThis.StudioActions.activeProjectJobs(...args);
const cancelMatchingJobs = (...args) => globalThis.StudioActions.cancelMatchingJobs(...args);
const escapeHtml = (...args) => globalThis.StudioActions.escapeHtml(...args);
const isCurrentProjectSession = (...args) => globalThis.StudioActions.isCurrentProjectSession(...args);
const isTerminalJob = (...args) => globalThis.StudioActions.isTerminalJob(...args);
const loadProfileList = (...args) => globalThis.StudioActions.loadProfileList(...args);
const mediaUrl = (...args) => globalThis.StudioActions.mediaUrl(...args);
const markMissingMediaPaths = (...args) => globalThis.StudioActions.markMissingMediaPaths(...args);
const isMissingMediaPath = (...args) => globalThis.StudioActions.isMissingMediaPath(...args);
const onProviderChange = (...args) => globalThis.StudioActions.onProviderChange(...args);
const produceStatus = (...args) => globalThis.StudioActions.produceStatus(...args);
const productionCurrent = (...args) => globalThis.StudioActions.productionCurrent(...args);
const productionSelectedShot = (...args) => globalThis.StudioActions.productionSelectedShot(...args);
const productionShotStatus = (...args) => globalThis.StudioActions.productionShotStatus(...args);
const productionShotVersions = (...args) => globalThis.StudioActions.productionShotVersions(...args);
const productionShotActiveVersionId = (...args) => globalThis.StudioActions.productionShotActiveVersionId(...args);
const productionImageUrl = (...args) => globalThis.StudioActions.productionImageUrl(...args);
const productionPreviewHtml = (...args) => globalThis.StudioActions.productionPreviewHtml(...args);
const productionStatusLabel = (...args) => globalThis.StudioActions.productionStatusLabel(...args);
const productionVersionListHtml = (...args) => globalThis.StudioActions.productionVersionListHtml(...args);
const productionVideoUrl = (...args) => globalThis.StudioActions.productionVideoUrl(...args);
const projectJsonRequest = (...args) => globalThis.StudioActions.projectJsonRequest(...args);
const projectSessionSnapshot = (...args) => globalThis.StudioActions.projectSessionSnapshot(...args);
const releaseSessionController = (...args) => globalThis.StudioActions.releaseSessionController(...args);
const renderProductionAudioTimeline = (...args) => globalThis.StudioActions.renderProductionAudioTimeline(...args);
const sessionController = (...args) => globalThis.StudioActions.sessionController(...args);
const setupProductionWorkspace = (...args) => globalThis.StudioActions.setupProductionWorkspace(...args);
const submitProjectJob = (...args) => globalThis.StudioActions.submitProjectJob(...args);
const syncSecretInput = (...args) => globalThis.StudioActions.syncSecretInput(...args);

// The visible prompt is a contenteditable surface so asset mentions can be
// rendered as image chips.  The serialized value remains plain text and keeps
// the existing backend contract (`@asset:type:id:view`).
let productionMentionSelection = null;
let productionMentionOutsideHandlerInstalled = false;
let productionVideoPolicySaveChain = Promise.resolve();
let productionVideoPolicyRevision = 0;
let productionShotSaveChain = Promise.resolve();
let productionShotSaveRevision = 0;
let productionInspectorTab = 'words';
const productionDrafts = new Map();
let productionDraftProjectId = null;

function productionPromptEditor() {
  return document.getElementById('production-prompt');
}

function productionEditorShotKey(shotId = '') {
  const projectId = String(appState?.projectId || '');
  const id = String(shotId || '').trim();
  if (!projectId || !id) return '';
  return `${projectId}:${id}`;
}

function productionRenderedEditorKey(root) {
  if (!root) return '';
  const shotId = String(root.dataset.productionEditorShotId || '').trim();
  if (!shotId) return '';
  const sceneId = String(root.dataset.productionEditorSceneId || '').trim();
  // Parent editors used to be keyed by scene id, so a later child with the
  // same digits could restore the parent's unsaved text. Keep parent drafts
  // on the parent: selection id.
  const keyId = sceneId && shotId === sceneId ? `parent:${sceneId}` : shotId;
  return productionEditorShotKey(keyId);
}

function productionRememberDraft(key, values) {
  if (!key || !values) return;
  productionDrafts.set(key, {
    prompt: String(values.prompt ?? ''),
    negative: String(values.negative ?? ''),
    feedback: String(values.feedback ?? ''),
    duration: String(values.duration ?? ''),
  });
}

function productionDraftFromEditor() {
  const prompt = document.getElementById('production-prompt');
  if (!prompt) return null;
  return {
    prompt: productionPromptText(),
    negative: document.getElementById('production-negative')?.value || '',
    feedback: document.getElementById('production-feedback')?.value || '',
    duration: document.getElementById('production-duration')?.value || '',
  };
}

function productionRememberRenderedDraft(root) {
  if (!root || !document.getElementById('production-prompt')) return;
  const key = productionRenderedEditorKey(root);
  const values = productionDraftFromEditor();
  if (key && values) productionRememberDraft(key, values);
}

function productionPromptSerializeNode(node) {
  if (!node) return '';
  if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || '';
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  if (node.dataset?.assetToken) return node.dataset.assetToken;
  if (node.tagName === 'BR') return '\n';
  const content = Array.from(node.childNodes || []).map(productionPromptSerializeNode).join('');
  return ['DIV', 'P', 'LI'].includes(node.tagName) ? `${content}\n` : content;
}

function productionPromptText() {
  const editor = productionPromptEditor();
  if (!editor) return '';
  const text = Array.from(editor.childNodes || []).map(productionPromptSerializeNode).join('');
  return text.replace(/\n$/, '');
}

function productionPromptReferenceCatalog() {
  const catalog = productionData?.asset_catalog || {characters: [], scenes: [], props: []};
  const values = [...(catalog.characters || []), ...(catalog.scenes || []), ...(catalog.props || [])];
  const current = productionCurrent()?.production?.references || [];
  const merged = new Map();
  const merge = (base, override) => {
    const result = {...(base || {})};
    Object.entries(override || {}).forEach(([key, value]) => {
      // Empty fields from a partial shot reference must not erase the richer
      // label/image metadata supplied by the project asset catalog.
      if (value !== undefined && value !== null && value !== '') result[key] = value;
      else if (!(key in result)) result[key] = value;
    });
    return result;
  };
  values.forEach(item => {
    const id = String(item?.reference_id || '');
    if (id) merged.set(id, merge(null, item));
  });
  // A saved shot reference is authoritative for source, version and token.
  // Overlay it after the catalog so explicit @ mentions survive reloads.
  current.forEach(item => {
    const id = String(item?.reference_id || '');
    if (id) merged.set(id, merge(merged.get(id), item));
  });
  return [...merged.values()];
}

function productionEscapeRegExp(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function productionPromptChipHtml(reference, token) {
  const label = productionMentionLabel(reference, token);
  const image = productionImageUrl(reference);
  const referenceId = reference?.reference_id || '';
  return `<span class="production-mention-chip" contenteditable="false" spellcheck="false" data-entity-mention data-asset-token="${escapeHtml(token)}" data-reference-id="${escapeHtml(referenceId)}" data-id="${escapeHtml(productionMentionReferenceId(reference))}" data-label="${escapeHtml(label)}" data-entity-type="${escapeHtml(productionMentionEntityType(reference))}" data-action="open-production-mention-picker" data-action-area="production-prompt-editor" data-action-object-id="${escapeHtml(referenceId)}" role="button" tabindex="0" aria-label="引用${escapeHtml(label)}" title="${escapeHtml(token)}" onclick="StudioActions.productionOpenMentionPickerForChip(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();StudioActions.productionOpenMentionPickerForChip(this)}"><img src="${escapeHtml(image || '')}" alt=""><span>${escapeHtml(label)}</span></span>`;
}

function productionPromptEditorHtml(value = '') {
  const raw = String(value || '');
  const entries = productionPromptReferenceCatalog().map(reference => ({
    reference,
    token: productionMentionToken(reference),
  })).filter(item => item.token).sort((a, b) => b.token.length - a.token.length);
  if (!entries.length) return escapeHtml(raw).replace(/\r?\n/g, '<br>');
  const pattern = new RegExp(entries.map(item => productionEscapeRegExp(item.token)).join('|'), 'g');
  let cursor = 0;
  let html = '';
  raw.replace(pattern, (match, offset) => {
    html += escapeHtml(raw.slice(cursor, offset)).replace(/\r?\n/g, '<br>');
    const entry = entries.find(item => item.token === match);
    html += entry ? productionPromptChipHtml(entry.reference, match) : escapeHtml(match);
    cursor = offset + match.length;
    return match;
  });
  html += escapeHtml(raw.slice(cursor)).replace(/\r?\n/g, '<br>');
  return html;
}

function productionSetPromptText(value, {focus = false} = {}) {
  const editor = productionPromptEditor();
  if (!editor) return;
  editor.innerHTML = productionPromptEditorHtml(value);
  editor.dataset.serializedValue = String(value || '');
  const serialized = document.getElementById('production-prompt-value');
  if (serialized) serialized.value = String(value || '');
  if (focus) editor.focus({preventScroll: true});
}

function productionSyncPromptValue() {
  const value = productionPromptText();
  const editor = productionPromptEditor();
  if (editor) editor.dataset.serializedValue = value;
  const serialized = document.getElementById('production-prompt-value');
  if (serialized) serialized.value = value;
  return value;
}

function productionRememberPromptSelection(event) {
  const editor = event?.currentTarget || productionPromptEditor();
  const selection = window.getSelection?.();
  if (!editor || !selection?.rangeCount) return;
  const range = selection.getRangeAt(0);
  if (editor.contains(range.startContainer)) productionMentionSelection = range.cloneRange();
}

function captureProductionEditorState(root) {
  const fields = ['production-prompt', 'production-negative', 'production-feedback', 'production-style-prefix', 'production-global-negative', 'production-duration'];
  const values = {};
  fields.forEach(id => {
    const element = root.querySelector(`#${id}`);
    if (!element) return;
    const promptEditor = id === 'production-prompt' ? element : null;
    values[id] = {
      value: promptEditor ? productionPromptText() : element.value,
      scrollTop: element.scrollTop,
      selectionStart: promptEditor ? null : (typeof element.selectionStart === 'number' ? element.selectionStart : null),
      selectionEnd: promptEditor ? null : (typeof element.selectionEnd === 'number' ? element.selectionEnd : null),
      focused: document.activeElement === element,
    };
  });
  const renderedSceneId = root.dataset.productionEditorShotId || root.dataset.productionEditorSceneId || String(productionShotId || productionSceneId || '');
  return {
    sceneId: renderedSceneId, values,
    policyOpen: root.querySelector('details.production-panel')?.open || false,
    projectToolsOpen: root.querySelector('details.production-project-tools')?.open || false,
    secondaryOpen: [...root.querySelectorAll('details.production-secondary-section')]
      .filter(item => item.open)
      .map(item => item.classList.contains('production-assets-section') ? 'assets' : item.classList.contains('production-audio-section') ? 'audio' : item.classList.contains('production-timeline-section') ? 'timeline' : ''),
    pageScrollY: window.scrollY,
  };
}

function restoreProductionEditorState(root, snapshot) {
  const renderedSceneId = root.dataset.productionEditorShotId || root.dataset.productionEditorSceneId || String(productionShotId || productionSceneId || '');
  if (!snapshot || snapshot.sceneId !== renderedSceneId) return;
  const details = root.querySelector('details.production-panel');
  if (details) details.open = snapshot.policyOpen;
  const projectTools = root.querySelector('details.production-project-tools');
  if (projectTools) projectTools.open = Boolean(snapshot.projectToolsOpen);
  const secondaryOpen = new Set(snapshot.secondaryOpen || []);
  root.querySelectorAll('details.production-secondary-section').forEach(item => {
    const key = item.classList.contains('production-assets-section') ? 'assets' : item.classList.contains('production-audio-section') ? 'audio' : item.classList.contains('production-timeline-section') ? 'timeline' : '';
    item.open = secondaryOpen.has(key);
  });
  Object.entries(snapshot.values).forEach(([id, state]) => {
    const element = root.querySelector(`#${id}`);
    if (!element) return;
    if (id === 'production-prompt') productionSetPromptText(state.value);
    else element.value = state.value;
    element.scrollTop = state.scrollTop;
    if (state.focused) {
      element.focus({preventScroll: true});
      if (state.selectionStart !== null && typeof element.setSelectionRange === 'function') element.setSelectionRange(state.selectionStart, state.selectionEnd);
    }
  });
  window.scrollTo(0, snapshot.pageScrollY);
}

function productionActiveVideoBatchJobs(jobs = activeProjectJobs(item => item.job_type === 'video.generate')) {
  const batchId = jobs.find(item => String(item.batch_id || '').trim())?.batch_id || '';
  return batchId ? jobs.filter(item => item.batch_id === batchId) : [];
}

function productionCurrentVideoButtonLabel(job, shotStatus) {
  if (job) return job.batch_id ? '终止批量中的当前镜头' : '终止当前镜头';
  return shotStatus === 'failed' || shotStatus === 'canceled' ? '重试当前镜头' : '生成当前镜头';
}

function patchProductionCurrentPreview(current) {
  if (!current) return;
  const shot = productionSelectedShot(current);
  const status = productionShotStatus(current, shot);
  ['production-current-status', 'production-context-status'].forEach(id => {
    const element = document.getElementById(id);
    if (element) element.textContent = productionStatusLabel(status);
  });
  const stage = document.getElementById('production-preview-stage');
  const html = productionPreviewHtml(current);
  // Keep a playing video intact when an unrelated job emits progress.
  if (stage && stage.dataset.productionPreviewHtml !== html) {
    stage.innerHTML = html;
    stage.dataset.productionPreviewHtml = html;
  }
}

function productionTimelineTargetSelector(sceneId, shotId = '') {
  return `[data-production-scene-id="${CSS.escape(String(sceneId))}"][data-production-shot-id="${CSS.escape(String(shotId))}"]`;
}

function patchProductionJobState(job) {
  const root = document.getElementById('shot-production-workspace');
  if (!root || !productionData) return;
  const current = productionCurrent();
  const activeVideos = activeProjectJobs(item => item.job_type === 'video.generate');
  const activeBatchVideos = productionActiveVideoBatchJobs(activeVideos);
  const currentShot = productionSelectedShot(current);
  const currentTargetId = String(currentShot?.shot_id || productionSceneId || '');
  const currentVideoJob = activeVideos.find(item => item.target_type === (currentShot?.shot_id ? 'shot_unit' : 'shot') && String(item.target_id) === currentTargetId);
  const pendingButton = document.getElementById('production-generate-pending');
  const currentButton = document.getElementById('production-generate-current');
  const currentFooterButton = document.getElementById('production-generate-current-footer');
  const audioButton = document.getElementById('btn-gen-audio');
  const finalButton = document.getElementById('btn-assemble');
  const currentStatus = current ? productionShotStatus(current, currentShot) : 'idle';
  if (pendingButton) pendingButton.textContent = activeBatchVideos.length ? '终止项目批量视频' : '项目级批量生成未生成镜头';
  const currentLabel = productionCurrentVideoButtonLabel(currentVideoJob, currentStatus);
  if (currentButton && current) currentButton.textContent = currentLabel;
  if (currentFooterButton && current) currentFooterButton.textContent = currentLabel;
  const audioSummary = productionData?.audio_summary || {};
  if (audioButton) audioButton.textContent = activeProjectJobs(item => item.job_type === 'audio.generate').length
    ? '终止台词生成'
    : (audioSummary.failed_line_count ? '重试失败台词' : '生成缺失台词');
  if (finalButton) finalButton.textContent = activeProjectJobs(item => item.job_type === 'final.assemble').length ? '终止合成' : '合成作品';
  patchProductionCurrentPreview(current);
  if (job?.job_type === 'video.generate' && ['shot', 'shot_unit'].includes(job.target_type)) {
    const targetId = String(job.target_id || '');
    const targetItem = (productionData.productions || []).find(item => job.target_type === 'shot'
      ? String(item.scene?.scene_id) === targetId
      : executableShotsForItem(item).some(shot => String(shot.shot_id) === targetId));
    if (targetItem) {
      const targetShot = job.target_type === 'shot_unit'
        ? executableShotsForItem(targetItem).find(shot => String(shot.shot_id) === targetId)
        : targetItem.prompt;
      const timeline = root.querySelector(`${productionTimelineTargetSelector(targetItem.scene.scene_id, targetShot?.shot_id || '')} .timeline-status`);
      if (timeline) timeline.textContent = productionStatusLabel(productionShotStatus(targetItem, targetShot));
    }
  }
}

function patchProductionWorkspace(previousData) {
  const current = productionCurrent();
  if (!current) { renderProductionWorkspace(); return; }
  const selectedShot = productionSelectedShot(current);
  const activeVersion = productionShotActiveVersionId(current, selectedShot);
  patchProductionCurrentPreview(current);
  const versions = document.getElementById('production-version-list');
  const download = document.getElementById('production-preview-download');
  if (versions) versions.innerHTML = productionVersionListHtml(current);
  if (download) {
    const active = productionShotVersions(current, selectedShot).find(item => item.version_id === activeVersion);
    const url = productionVideoUrl(active);
    download.innerHTML = url ? `<a class="btn-secondary btn-sm" href="${url}" download>下载</a>` : '';
  }
  if (JSON.stringify(previousData?.audio || {}) !== JSON.stringify(productionData?.audio || {})) {
    renderProductionAudioTimeline();
  }
  const timelineEntries = (productionData.productions || []).flatMap(item => [
    {item, sceneId: item.scene.scene_id, shotId: '', shot: item.prompt},
    ...executableShotsForItem(item).map(shot => ({item, sceneId: item.scene.scene_id, shotId: shot.shot_id, shot})),
  ]);
  timelineEntries.forEach(entry => {
    const selector = productionTimelineTargetSelector(entry.sceneId, entry.shotId);
    const button = document.querySelector(`#shot-production-workspace ${selector}`);
    if (!button) return;
    const item = entry.item;
    const status = productionShotStatus(item, entry.shot);
    const activeVersionId = entry.shotId
      ? item.production.active_version_by_shot_id?.[entry.shotId] || ''
      : item.production.active_version_id || '';
    const version = item.production.video_versions.find(candidate => candidate.version_id === activeVersionId);
    const url = productionVideoUrl(version);
    if (entry.shotId || !executableShotsForItem(item).length) {
      const oldVideo = button.querySelector('video');
      if (url && (!oldVideo || oldVideo.src !== new URL(url, window.location.href).href)) {
        const replacement = document.createElement('video');
        replacement.muted = true;
        replacement.preload = 'metadata';
        replacement.src = url;
        (oldVideo || button.querySelector('img, div'))?.replaceWith(replacement);
      }
    }
    const label = button.querySelector('.timeline-status');
    if (label) label.textContent = productionStatusLabel(status);
  });
  patchProductionJobState();
}

async function loadProductionWorkspace({patch = false} = {}) {
  if (!appState.projectId) { renderProductionWorkspace(); return; }
  const snapshot = projectSessionSnapshot();
  const controller = sessionController();
  try {
    const res = await fetch(`${API_BASE}/api/projects/${appState.projectId}/production`, {signal: controller.signal});
    if (!res.ok) throw new Error((await res.json()).detail || '加载分镜制作数据失败');
    const data = await res.json();
    if (!isCurrentProjectSession(snapshot)) return;
    markMissingMediaPaths(data.missing_media_paths || [], data);
    const previousData = productionData;
    productionData = data;
    if (!productionSceneId || !productionData.productions.some(item => item.scene.scene_id === productionSceneId)) {
      productionSceneId = productionData.productions[0]?.scene.scene_id || null;
      productionShotId = null;
    }
    const selection = productionSelection(productionData, productionSceneId, productionShotId);
    productionShotId = selection.shotId;
    if (patch && previousData && document.getElementById('shot-production-workspace')) patchProductionWorkspace(previousData);
    else renderProductionWorkspace();
  } catch (error) {
    if (error.name === 'AbortError' || !isCurrentProjectSession(snapshot)) return;
    const root = document.getElementById('shot-production-workspace');
    if (root) root.innerHTML = `<div class="production-empty">${escapeHtml(error.message)}</div>`;
  } finally {
    releaseSessionController(controller);
  }
}

function productionReferenceHtml(reference, index) {
  const image = productionImageUrl(reference);
  const sourceLabels = {asset: '自动', manual: '手动', explicit: '显式 @'};
  const sourceLabel = sourceLabels[reference.source] || '参考';
  const versionLabel = reference.asset_version_id ? `版本 ${escapeHtml(reference.asset_version_id)}` : '当前版本';
  const stateLabel = reference.stale
    ? '<span style="color:#fca5a5">文件失效</span>'
    : `<span style="color:#a1a1aa">${versionLabel}</span>`;
  const historyButton = reference.asset_type && reference.asset_id && reference.view_type
    ? `<button class="btn-secondary btn-sm" type="button" title="加载历史图片版本" onclick="StudioActions.productionLoadReferenceHistory(${index})">历史</button>`
    : '';
  return `<div class="reference-chip" draggable="true" ondragstart="StudioActions.productionDragStart(${index})" ondragover="event.preventDefault()" ondrop="StudioActions.productionDropReference(${index})"><img src="${image}" alt=""><span style="min-width:0;display:grid;gap:2px"><strong>${escapeHtml(reference.reference_token || reference.label || reference.reference_id)}</strong><small style="color:#a1a1aa">${escapeHtml(sourceLabel)} · ${stateLabel}</small>${Array.isArray(reference.history) && reference.history.length ? `<select title="选择历史版本" onchange="StudioActions.productionApplyReferenceVersion(${index}, this.value)"><option value="">历史版本</option>${reference.history.map(item => `<option value="${escapeHtml(item.id || '')}" ${item.id === reference.asset_version_id ? 'selected' : ''}>${escapeHtml(item.id || '未命名')} ${item.active ? '(当前)' : ''}</option>`).join('')}</select>` : ''}</span>${historyButton}<button title="移除参考" onclick="StudioActions.productionRemoveReference(${index})">×</button></div>`;
}

function productionMentionToken(reference) {
  if (!reference) return '';
  return reference.reference_token || `@${reference.asset_type || 'asset'}:${reference.asset_id || reference.reference_id}:${reference.view_type || 'default'}`;
}

function productionMentionEntityType(reference) {
  const type = String(reference?.asset_type || reference?.type || '').toLowerCase();
  if (type === 'character' || type === 'role' || type === 'person') return 'role';
  if (type === 'scene' || type === 'location' || type === 'place') return 'location';
  if (type === 'prop' || type === 'object') return 'prop';
  return 'asset';
}

function productionMentionReferenceId(reference) {
  return String(reference?.asset_id || reference?.reference_id || '');
}

function productionMentionLabel(reference, token = '') {
  return String(reference?.label || reference?.asset_name || reference?.asset_id || token || '');
}

function productionMentionOptions() {
  return productionPromptReferenceCatalog()
    .map(reference => `<option value="${escapeHtml(reference.reference_id)}">${escapeHtml(productionMentionToken(reference))} · ${escapeHtml(reference.label || reference.reference_id)}</option>`)
    .join('');
}

function productionMentionSuggestions(query = '') {
  const catalog = productionData?.asset_catalog || {characters: [], scenes: [], props: []};
  const references = productionPromptReferenceCatalog();
  const normalized = String(query || '').replace(/^@/, '').trim().toLowerCase();
  const groups = [
    ['characters', '角色', references.filter(reference => (catalog.characters || []).some(item => item.reference_id === reference.reference_id) || productionMentionEntityType(reference) === 'role')],
    ['scenes', '场景', references.filter(reference => (catalog.scenes || []).some(item => item.reference_id === reference.reference_id) || productionMentionEntityType(reference) === 'location')],
    ['props', '道具', references.filter(reference => (catalog.props || []).some(item => item.reference_id === reference.reference_id) || productionMentionEntityType(reference) === 'prop')],
  ];
  return groups.map(([key, label, values]) => {
    const items = values.filter(reference => {
      if (!normalized) return true;
      return [productionMentionToken(reference), reference.label, reference.reference_id]
        .some(value => String(value || '').toLowerCase().includes(normalized));
    }).slice(0, 12);
    if (!items.length) return '';
    return `<section class="production-mention-group" data-mention-group="${key}"><h5>${label}</h5><div class="production-mention-grid">${items.map(reference => {
      const encodedId = encodeURIComponent(reference.reference_id || '');
      return `<button type="button" class="production-mention-card" data-entity-mention data-id="${escapeHtml(productionMentionReferenceId(reference))}" data-label="${escapeHtml(productionMentionLabel(reference))}" data-entity-type="${escapeHtml(productionMentionEntityType(reference))}" data-action="select-production-mention" data-action-area="production-prompt-editor" data-action-object-id="${escapeHtml(reference.reference_id || '')}" onclick="StudioActions.productionApplyMentionSuggestion(decodeURIComponent('${encodedId}'))"><img src="${escapeHtml(productionImageUrl(reference) || '')}" alt=""><span><strong>${escapeHtml(productionMentionLabel(reference))}</strong><small>${escapeHtml(productionMentionToken(reference))}</small></span></button>`;
    }).join('')}</div></section>`;
  }).join('');
}

function productionOpenMentionPicker(query = '') {
  const panel = document.getElementById('production-mention-suggestions');
  if (!panel) return;
  panel.innerHTML = `<div class="production-mention-panel-head"><strong>插入参考资产</strong><button type="button" class="production-mention-close" aria-label="关闭" onclick="StudioActions.productionCloseMentionPicker()">×</button></div><input id="production-mention-search" class="production-mention-search" type="search" placeholder="搜索角色、场景或道具" value="${escapeHtml(query || '')}" oninput="StudioActions.productionMentionSearch(this.value)"><div class="production-mention-list">${productionMentionSuggestions(query)}</div>`;
  panel.hidden = false;
}

function productionMentionSearch(query = '') {
  const panel = document.getElementById('production-mention-suggestions');
  const list = panel?.querySelector('.production-mention-list');
  if (list) list.innerHTML = productionMentionSuggestions(query);
}

function productionCloseMentionPicker() {
  const panel = document.getElementById('production-mention-suggestions');
  if (panel) { panel.hidden = true; panel.innerHTML = ''; }
}

function productionOpenMentionPickerForChip(chip) {
  const editor = productionPromptEditor();
  if (!chip || !editor || !editor.contains(chip)) return;
  productionSetCaretAfter(chip);
  productionOpenMentionPicker('');
  document.getElementById('production-mention-search')?.focus({preventScroll: true});
}

function productionInstallMentionPicker() {
  if (productionMentionOutsideHandlerInstalled) return;
  productionMentionOutsideHandlerInstalled = true;
  document.addEventListener('mousedown', event => {
    const panel = document.getElementById('production-mention-suggestions');
    const editor = productionPromptEditor();
    if (!panel || panel.hidden) return;
    if (panel.contains(event.target) || editor?.contains(event.target) || event.target.closest?.('.production-mention-picker-trigger')) return;
    productionCloseMentionPicker();
  });
}

function productionPromptCaretContext(editor) {
  if (!editor) return null;
  const selection = window.getSelection?.();
  let range = selection?.rangeCount ? selection.getRangeAt(0) : null;
  if (!range || !editor.contains(range.startContainer)) range = productionMentionSelection;
  if (!range || !editor.contains(range.startContainer)) {
    range = document.createRange();
    range.selectNodeContents(editor);
    range.collapse(false);
  }
  if (range.collapsed && selection?.rangeCount && editor.contains(selection.anchorNode)) productionMentionSelection = range.cloneRange();
  let node = range.startContainer;
  let offset = range.startOffset;
  if (node.nodeType !== Node.TEXT_NODE) {
    const child = node.childNodes?.[Math.max(0, offset - 1)];
    if (child?.nodeType === Node.TEXT_NODE && offset > 0) {
      node = child;
      offset = child.nodeValue.length;
    }
  }
  if (node.nodeType !== Node.TEXT_NODE) return {range, node: null, offset: 0, query: '', start: 0};
  const before = (node.nodeValue || '').slice(0, offset);
  const match = before.match(/@([^\s@]*)$/);
  return {range, node, offset, query: match ? match[0] : '', start: match ? offset - match[0].length : offset};
}

function productionSetCaretAfter(node) {
  if (!node?.parentNode) return;
  const range = document.createRange();
  range.setStartAfter(node);
  range.collapse(true);
  const selection = window.getSelection?.();
  selection?.removeAllRanges();
  selection?.addRange(range);
  productionMentionSelection = range.cloneRange();
}

function productionMakeMentionChip(reference, token) {
  const chip = document.createElement('span');
  chip.className = 'production-mention-chip';
  chip.contentEditable = 'false';
  chip.spellcheck = false;
  chip.dataset.assetToken = token;
  chip.dataset.referenceId = reference.reference_id || '';
  chip.dataset.entityMention = '';
  chip.dataset.id = productionMentionReferenceId(reference);
  chip.dataset.label = productionMentionLabel(reference, token);
  chip.dataset.entityType = productionMentionEntityType(reference);
  chip.dataset.action = 'open-production-mention-picker';
  chip.dataset.actionArea = 'production-prompt-editor';
  chip.dataset.actionObjectId = reference.reference_id || '';
  chip.setAttribute('role', 'button');
  chip.setAttribute('tabindex', '0');
  chip.setAttribute('aria-label', `引用${productionMentionLabel(reference, token)}`);
  chip.addEventListener('click', () => productionOpenMentionPickerForChip(chip));
  chip.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      productionOpenMentionPickerForChip(chip);
    }
  });
  chip.title = token;
  const image = document.createElement('img');
  image.src = productionImageUrl(reference) || '';
  image.alt = '';
  const label = document.createElement('span');
  label.textContent = reference.label || reference.asset_id || reference.reference_id;
  chip.append(image, label);
  return chip;
}

function productionInsertReferenceToken(reference) {
  const editor = productionPromptEditor();
  if (!editor || !reference) return false;
  const token = productionMentionToken(reference);
  const context = productionPromptCaretContext(editor);
  const chip = productionMakeMentionChip(reference, token);
  if (context.node) {
    const node = context.node;
    const before = (node.nodeValue || '').slice(0, context.start);
    const after = (node.nodeValue || '').slice(context.offset);
    const fragment = document.createDocumentFragment();
    if (before) fragment.append(document.createTextNode(before));
    fragment.append(chip);
    const spacer = document.createTextNode(' ');
    fragment.append(spacer);
    if (after) fragment.append(document.createTextNode(after));
    node.parentNode.replaceChild(fragment, node);
    productionSetCaretAfter(spacer);
  } else {
    const range = context.range;
    range.deleteContents();
    range.insertNode(chip);
    const spacer = document.createTextNode(' ');
    chip.parentNode.insertBefore(spacer, chip.nextSibling);
    productionSetCaretAfter(spacer);
  }
  productionSyncPromptValue();
  return true;
}

function productionMarkExplicitReference(reference, token) {
  const current = productionCurrent();
  if (!current?.production?.references || !reference) return;
  const existing = current.production.references.find(item => item.reference_id === reference.reference_id);
  if (existing) {
    if (existing.source !== 'explicit') existing.explicit_base_source = existing.source || 'asset';
    existing.source = 'explicit';
    existing.reference_token = token;
    return;
  }
  current.production.references.push({...reference, source: 'explicit', reference_token: token, explicit_base_source: ''});
}

function productionAdjacentMention(range, direction) {
  if (!range?.collapsed) return null;
  const container = range.startContainer;
  let parent = container;
  let index = range.startOffset;
  if (container.nodeType === Node.TEXT_NODE) {
    if ((direction === 'backward' && range.startOffset !== 0) || (direction === 'forward' && range.startOffset !== container.nodeValue.length)) return null;
    parent = container.parentNode;
    index = Array.prototype.indexOf.call(parent?.childNodes || [], container) + (direction === 'backward' ? 0 : 1);
  } else if (container.nodeType === Node.ELEMENT_NODE && direction === 'backward') {
    index = range.startOffset;
  }
  if (!parent?.childNodes) return null;
  const step = direction === 'backward' ? -1 : 1;
  let cursor = direction === 'backward' ? index - 1 : index;
  while (cursor >= 0 && cursor < parent.childNodes.length) {
    const candidate = parent.childNodes[cursor];
    if (candidate.nodeType === Node.TEXT_NODE && /^\s*$/.test(candidate.nodeValue || '')) {
      cursor += step;
      continue;
    }
    return candidate?.dataset?.assetToken ? candidate : null;
  }
  return null;
}

function productionMentionKeydown(event) {
  const target = event?.target;
  if (!target) return;
  if (target.id === 'productionMentionInput' && event.type === 'keydown' && event.key === 'Enter') {
    event.preventDefault();
    productionInsertMention();
    return;
  }
  const editor = productionPromptEditor();
  if (!editor || target !== editor) return;
  productionRememberPromptSelection(event);
  if (event.type === 'keydown' && event.key === 'Escape') {
    productionCloseMentionPicker();
    return;
  }
  if (event.type === 'keydown' && (event.key === 'Backspace' || event.key === 'Delete')) {
    const context = productionPromptCaretContext(editor);
    if (context?.range?.collapsed) {
      const candidate = productionAdjacentMention(context.range, event.key === 'Backspace' ? 'backward' : 'forward');
      if (candidate?.dataset?.assetToken) {
        event.preventDefault();
        const parent = candidate.parentNode;
        const index = Array.prototype.indexOf.call(parent?.childNodes || [], candidate);
        candidate.remove();
        if (parent) {
          const caret = document.createRange();
          caret.setStart(parent, Math.min(index, parent.childNodes.length));
          caret.collapse(true);
          const selection = window.getSelection?.();
          selection?.removeAllRanges();
          selection?.addRange(caret);
          productionMentionSelection = caret.cloneRange();
        }
        productionSyncPromptValue();
        syncProductionMentionBindings(productionCurrent());
        productionCloseMentionPicker();
        void productionSaveCurrent();
        return;
      }
    }
  }
  const context = productionPromptCaretContext(editor);
  const query = context?.query || '';
  if (event.type === 'input' || event.type === 'keyup') {
    if (query || (event.type === 'input' && productionPromptText().endsWith('@'))) productionOpenMentionPicker(query);
    else productionCloseMentionPicker();
  }
}

function productionApplyMentionSuggestion(referenceId) {
  const current = productionCurrent();
  const reference = productionPromptReferenceCatalog().find(item => item.reference_id === referenceId);
  if (!current || !reference) return;
  const token = productionMentionToken(reference);
  if (!productionInsertReferenceToken(reference)) return;
  productionMarkExplicitReference(reference, token);
  productionCloseMentionPicker();
  void productionSaveCurrent();
}

async function productionInsertMention() {
  const current = productionCurrent();
  const selector = document.getElementById('productionMentionInput');
  if (!current || !selector || !selector.value) return;
  const reference = productionPromptReferenceCatalog().find(item => item.reference_id === selector.value);
  if (!reference || !productionInsertReferenceToken(reference)) return;
  productionMarkExplicitReference(reference, productionMentionToken(reference));
  productionCloseMentionPicker();
  await productionSaveCurrent();
}

function syncProductionMentionBindings(current) {
  const prompt = productionPromptText();
  if (!current?.production?.references) return;
  current.production.references = current.production.references.flatMap(item => {
    if (item.source !== 'explicit' || !item.reference_token || prompt.includes(item.reference_token)) return [item];
    if (item.explicit_base_source === 'manual') {
      return [{...item, source: 'manual', reference_token: '', explicit_base_source: ''}];
    }
    if (item.explicit_base_source === 'asset') {
      return [{...item, source: 'asset', reference_token: '', explicit_base_source: ''}];
    }
    return [];
  });
}

async function productionLoadReferenceHistory(index) {
  const current = productionCurrent();
  const reference = current?.production?.references?.[index];
  if (!reference || !appState.projectId) return;
  try {
    const params = new URLSearchParams({
      asset_type: reference.asset_type || '',
      asset_id: reference.asset_id || '',
      view_type: reference.view_type || '',
      state_id: reference.state_id || '',
    });
    const response = await fetch(`${API_BASE}/api/projects/${appState.projectId}/image-history?${params}`);
    if (!response.ok) throw new Error('加载历史版本失败');
    reference.history = (await response.json()).versions || [];
    renderProductionWorkspace();
  } catch (error) {
    alert(error.message);
  }
}

async function productionApplyReferenceVersion(index, versionId) {
  if (!versionId) return;
  const current = productionCurrent();
  const reference = current?.production?.references?.[index];
  const record = reference?.history?.find(item => item.id === versionId);
  if (!reference || !record) return;
  reference.source = reference.source === 'explicit' ? 'explicit' : 'manual';
  reference.asset_version_id = versionId;
  reference.image_path = record.path || reference.image_path;
  reference.stale = !record.path;
  await productionSaveCurrent();
}

function promptReviewReport(review = {}) {
  const report = review?.report || {};
  return {
    ...report,
    report_version: Number(report.report_version || 1),
    verdict: report.verdict || (report.passed ? 'pass' : 'needs_review'),
  };
}

function promptReviewIssues(report = {}) {
  const values = [];
  const add = issue => {
    if (!issue || typeof issue !== 'object') return;
    const key = [
      issue.layer || '', issue.scope || '', issue.issue_code || '', issue.scene_id || 0,
      (issue.related_scene_ids || []).join(','), (issue.fact_ids || []).join(','),
      (issue.evidence_items || []).map(item => item.fact_id || item.excerpt || '').join(','),
      issue.severity || '', issue.description || '', issue.confidence || '',
    ].join('|');
    if (!values.some(item => item.__key === key)) values.push({...issue, __key: key});
  };
  (report.issues || []).forEach(add);
  (report.shots || []).forEach(shot => (shot.issues || []).forEach(add));
  Object.values(report.shots_issue_index || {}).forEach(items => (items || []).forEach(add));
  return values.map(({__key, ...issue}) => issue);
}

function promptReviewIssueGroup(issue) {
  if (issue?.layer === 'source_parse' || issue?.scope === 'source_alignment') return '解析问题';
  if (issue?.scope === 'cross_shot') return '跨镜问题';
  return '分镜问题';
}

function promptReviewShotStatusLabel(review, report, shot) {
  const status = review?.status || 'pending';
  if (status === 'stale') return '报告已过期';
  if (status === 'generating') return '审核中';
  if (status !== 'ready') return '未完成';
  if (Number(report?.report_version || 1) < 2) return '旧版报告';
  const labels = {pass: '通过', needs_review: '需复核', blocked: '已阻断', unreviewed: '未完成'};
  return labels[report?.verdict] || (shot?.passed ? '通过' : '需复核');
}

function promptReviewEvidenceHtml(issue) {
  const items = Array.isArray(issue?.evidence_items) ? issue.evidence_items : [];
  if (!items.length && issue?.evidence) return `<div style="margin-top:5px;color:#a1a1aa">证据：${escapeHtml(issue.evidence)}</div>`;
  if (!items.length) return '';
  return `<div style="margin-top:5px;color:#a1a1aa">${items.slice(0, 4).map(item => {
    const location = [item.source || '证据', item.section || '', item.line_start ? `第${item.line_start}-${item.line_end || item.line_start}行` : '', item.time_start_sec != null ? `${item.time_start_sec}-${item.time_end_sec}s` : ''].filter(Boolean).join(' · ');
    return `<div style="margin-top:2px"><span style="color:#71717a">${escapeHtml(location)}：</span>${escapeHtml(item.excerpt || '')}</div>`;
  }).join('')}</div>`;
}

function promptReviewRepairAction(issue) {
  const action = issue?.repair_action;
  if (!['edit_prompt', 'edit_script'].includes(action)) return '';
  const sceneId = Number(issue.scene_id || 0);
  if (action === 'edit_prompt' && sceneId <= 0) return '';
  const encoded = action === 'edit_prompt' ? `StudioActions.productionFocusReviewRepair('edit_prompt', ${sceneId})` : `StudioActions.productionFocusReviewRepair('edit_script', ${sceneId})`;
  return `<button type="button" class="btn-secondary btn-sm" style="margin-top:6px" onclick="${encoded}">${action === 'edit_script' ? '打开剧本编辑' : '定位提示词'}</button>`;
}

function promptReviewIssueHtml(issue, compact = false) {
  const color = issue.severity === 'critical' ? '#ef4444' : issue.severity === 'major' ? '#f59e0b' : '#60a5fa';
  const related = Array.isArray(issue.related_scene_ids) && issue.related_scene_ids.length ? ` · 关联 ${issue.related_scene_ids.join('、')}` : '';
  return `<div style="padding:8px;border-left:3px solid ${color};background:#202024;font-size:11px"><b>${escapeHtml(promptReviewIssueGroup(issue))} · 镜头 ${escapeHtml(String(issue.scene_id || '全片'))}${escapeHtml(related)} · ${escapeHtml(issue.severity || '')}${issue.confidence && issue.confidence !== 'confirmed' ? ` · ${escapeHtml(issue.confidence)}` : ''}</b><div style="margin-top:3px">${escapeHtml(issue.description || '')}</div><div style="margin-top:3px;color:#a1a1aa">${escapeHtml(issue.suggestion || '')}</div>${promptReviewEvidenceHtml(issue)}${promptReviewRepairAction(issue)}</div>`;
}

function promptReviewHtml(review) {
  const status = review?.status || 'pending';
  const report = promptReviewReport(review);
  const legacyReport = report.report_version < 2;
  const verdictLabels = {pass: '通过', needs_review: '需人工复核', blocked: '已阻断', unreviewed: '未完成'};
  const labels = {
    pending: '待审核',
    generating: 'Kimi 审核中',
    ready: legacyReport ? '旧版报告' : (verdictLabels[report.verdict] || '审核完成'),
    stale: '报告已过期',
    failed: '审核失败',
    canceled: '已取消',
    unavailable: '未配置 Kimi',
  };
  const score = Number.isFinite(Number(report.overall_score)) ? Number(report.overall_score) : null;
  const color = status === 'ready' && !legacyReport && report.verdict === 'pass'
    ? '#86efac'
    : status === 'ready' || status === 'stale'
      ? '#fbbf24'
      : status === 'failed'
        ? '#fca5a5'
        : '#a1a1aa';
  const summary = review?.error || report.summary || (
    status === 'stale'
      ? '分镜提示词已被修改，请重新检验以获得当前报告。'
      : status === 'unavailable'
        ? '请在 API 设置的“分镜提示词审核（Kimi）”中保存 Kimi API Key 后再检验。'
        : '审核将逐镜核对剧本覆盖度、剧情忠实度、连续性和可执行性。'
  );
  const issues = promptReviewIssues(report);
  const grouped = ['解析问题', '分镜问题', '跨镜问题'].map(group => ({group, items: issues.filter(issue => promptReviewIssueGroup(issue) === group)})).filter(item => item.items.length);
  const groupHtml = grouped.length ? grouped.map(item => `<div style="margin-top:8px"><b style="font-size:11px;color:#d4d4d8">${item.group} · ${item.items.length}</b><div style="display:grid;gap:6px;margin-top:5px">${item.items.map(issue => promptReviewIssueHtml(issue)).join('')}</div></div>`).join('') : '';
  return `<section class="production-panel production-review-fold">
    <div class="production-review-bar"><b>Kimi 分镜提示词检验</b><span style="color:${color}">${escapeHtml(labels[status] || status)}${score !== null ? ` · ${score} 分` : ''}</span><button class="btn-secondary btn-sm" onclick="StudioActions.productionReviewPrompts()" ${status === 'generating' ? 'disabled' : ''}>${status === 'generating' ? '审核中' : '重新检验'}</button><span class="production-review-summary">${escapeHtml(summary)}${review?.model ? ` · ${escapeHtml(review.model)}` : ''}</span></div>
    ${groupHtml}
  </section>`;
}

function executableShotsForItem(item) {
  return Array.isArray(item?.executable_shots)
    ? item.executable_shots.filter(shot => shot && shot.shot_id)
    : [];
}

function productionTimelineEntries(data = {}) {
  const productions = Array.isArray(data?.productions) ? data.productions : [];
  return productions.flatMap((item, sceneIndex) => {
    const sceneId = Number(item?.scene?.scene_id);
    const children = executableShotsForItem(item);
    if (children.length) {
      return children.map((shot, childIndex) => ({
        item,
        sceneId,
        shotId: String(shot.shot_id),
        shot,
        isSubShot: true,
        label: `镜头 ${sceneId}-${Number(shot.shot_order || childIndex + 1)} · ${Number(shot.duration_sec || 0)}s`,
      }));
    }
    const prompt = item?.prompt || {};
    return [{
      item,
      sceneId,
      shotId: null,
      shot: prompt,
      isSubShot: false,
      label: `镜头 ${sceneIndex + 1} · ${Number(prompt.duration_sec || item?.scene?.duration_sec || 0)}s`,
    }];
  });
}

function productionSelection(data = {}, sceneId, shotId = null) {
  const item = (data?.productions || []).find(entry => Number(entry?.scene?.scene_id) === Number(sceneId)) || null;
  if (!item) return {item: null, sceneId: Number(sceneId), shotId: null, shot: null, isSubShot: false};
  const children = executableShotsForItem(item);
  const parentSelected = String(shotId || '') === `parent:${Number(sceneId)}`;
  const shot = children.length && !parentSelected
    ? (children.find(entry => String(entry.shot_id) === String(shotId || '')) || children[0])
    : (item.prompt || null);
  return {
    item,
    sceneId: Number(sceneId),
    shotId: parentSelected ? `parent:${Number(sceneId)}` : (shot?.shot_id ? String(shot.shot_id) : null),
    shot,
    isSubShot: Boolean(shot?.shot_id),
  };
}

function productionGenerationTarget(sceneId, shotId = null) {
  const normalizedShotId = String(shotId || '').trim();
  return normalizedShotId
    ? {target_type: 'shot_unit', target_id: normalizedShotId, payload: {scene_id: Number(sceneId), shot_id: normalizedShotId}}
    : {target_type: 'shot', target_id: String(sceneId), payload: {scene_id: Number(sceneId)}};
}

function productionTimelineHtml(data = {}) {
  const productions = Array.isArray(data?.productions) ? data.productions : [];
  return productions.map((item, sceneIndex) => {
    const sceneId = Number(item?.scene?.scene_id);
    const production = item?.production || {};
    const children = executableShotsForItem(item);
    const entries = children.length ? children : [item?.prompt || {}];
    const buttons = entries.map((shot, childIndex) => {
      const shotId = String(shot?.shot_id || '');
      const status = productionShotStatus(item, shot);
      const activeVersionId = shotId
        ? production.active_version_by_shot_id?.[shotId] || ''
        : production.active_version_id || '';
      const version = (production.video_versions || []).find(entry => entry.version_id === activeVersionId && (!shotId || entry.shot_id === shotId));
      const video = productionVideoUrl(version);
      const fallback = (production.references || []).find(reference => reference.asset_type === 'scene') || production.references?.[0];
      const activeSceneId = Number(globalThis.productionSceneId || 0);
      const activeShotId = globalThis.productionShotId || (sceneId === activeSceneId ? String(children[0]?.shot_id || '') : '');
      const active = sceneId === activeSceneId && activeShotId === shotId;
      const label = shotId
        ? `镜头 ${sceneId}-${Number(shot.shot_order || childIndex + 1)} · ${Number(shot.duration_sec || 0)}s`
        : `镜头 ${sceneId || sceneIndex + 1} · ${Number(shot.duration_sec || item?.scene?.duration_sec || 0)}s`;
      const shotArg = shotId ? `'${escapeHtml(shotId)}'` : 'null';
      return `<button class="timeline-shot ${active ? 'active' : ''}" data-production-scene-id="${sceneId}" data-production-shot-id="${escapeHtml(shotId)}" onclick="StudioActions.productionSelectScene(${sceneId}, ${shotArg})"><span class="timeline-status">${productionStatusLabel(status)}</span>${video ? `<video muted preload="metadata" src="${video}"></video>` : fallback ? `<img src="${productionImageUrl(fallback)}" alt="">` : '<div style="height:64px;display:grid;place-items:center">◌</div>'}<span>${escapeHtml(label)}</span></button>`;
    }).join('');
    if (!children.length) return buttons;
    const parentActive = sceneId === Number(globalThis.productionSceneId || 0) && String(globalThis.productionShotId || '') === `parent:${sceneId}`;
    return `<div class="production-timeline-group"><div class="production-timeline-shots">${buttons}</div><button type="button" class="btn-secondary btn-sm production-timeline-parent ${parentActive ? 'active' : ''}" data-production-scene-id="${sceneId}" data-production-shot-id="" aria-current="${parentActive ? 'true' : 'false'}" title="编辑父镜头 ${sceneId} 的源提示词" onclick="StudioActions.productionSelectScene(${sceneId}, 'parent:${sceneId}')">父镜头 ${sceneId} · 编辑源提示词 <span class="timeline-status">${productionStatusLabel(productionShotStatus(item, item.prompt))}</span></button><div class="production-timeline-group-label">已拆 ${children.length} 个可执行子镜头</div></div>`;
  }).join('');
}

function storyboardQualityDiagnosticsHtml(data = {}) {
  const fidelity = data?.prompt_fidelity || {};
  const capacity = data?.shot_capacity || {};
  const characterRepair = data?.character_profile_repair || {};
  const fidelityIssues = Array.isArray(fidelity.issues) ? fidelity.issues : [];
  const capacityItems = Array.isArray(capacity.items) ? capacity.items : [];
  const characterErrors = Array.isArray(characterRepair.errors) ? characterRepair.errors : [];
  const hasBlockingFidelity = Boolean(
    fidelity.status === 'blocked'
    || fidelity.status === 'failed'
    || fidelity.blocked
    || fidelityIssues.some(issue => issue?.blocks_generation || issue?.severity === 'critical'),
  );
  const sections = [];
  if (hasBlockingFidelity) {
    const issueHtml = fidelityIssues.map(issue => {
      const sceneLabel = Number(issue?.scene_id) > 0 ? `镜头 ${issue.scene_id}` : '全片';
      const evidence = issue?.evidence || {};
      return `<div style="margin-top:6px;padding:8px;border-left:3px solid #ef4444;background:#202024;font-size:11px"><b>${escapeHtml(sceneLabel)} · ${escapeHtml(issue?.issue_code || '保真问题')}</b><div style="margin-top:3px">期望：${escapeHtml(evidence.expected_excerpt || issue?.expected_excerpt || '')}</div><div style="margin-top:3px">实际：${escapeHtml(evidence.prompt_excerpt || issue?.prompt_excerpt || '')}</div><div style="margin-top:3px;color:#a1a1aa">章节：${escapeHtml(evidence.prompt_section || issue?.prompt_section || '未标注')}</div></div>`;
    }).join('');
    sections.push(`<div style="color:#fca5a5;font-weight:600">提示词存在确定性保真问题：禁止生成</div>${issueHtml}`);
  }
  const warningItems = capacityItems.filter(item => item?.status === 'warning' || item?.status === 'overloaded' || item?.warnings?.length);
  if (warningItems.length) {
    const productionByScene = new Map((data?.productions || []).map(item => [Number(item?.scene?.scene_id), item]));
    const formatDuration = value => Number(value || 0).toFixed(0).replace(/\.0$/, '');
    const warningHtml = warningItems.map(item => {
      const sceneId = Number(item?.scene_id);
      const production = productionByScene.get(sceneId);
      const materialized = executableShotsForItem(production);
      const savedDurations = materialized.map(shot => Number(shot.duration_sec || 0)).filter(value => value > 0);
      const recommendedDurations = Array.isArray(item?.recommended_durations_sec)
        ? item.recommended_durations_sec.map(value => Number(value || 0)).filter(value => value > 0)
        : [];
      const durationList = recommendedDurations.length ? recommendedDurations : savedDurations;
      const sourceDuration = Number(item?.source_duration_sec || production?.scene?.duration_sec || 0);
      const recommendedTotal = Number(
        item?.recommended_total_duration_sec
        || durationList.reduce((sum, value) => sum + value, 0)
        || sourceDuration,
      );
      const dialogueSec = Number(item?.estimated_dialogue_sec || 0);
      const actionSec = Number(item?.action_required_sec || 0);
      const detail = durationList.length > 1
        ? `原场景 ${formatDuration(sourceDuration)}s；建议 ${formatDuration(recommendedTotal)}s；已拆 ${durationList.length} 个子镜头（${durationList.map(formatDuration).join(' / ')} 秒）${dialogueSec || actionSec ? `；对白约 ${formatDuration(dialogueSec)}s，动作约 ${formatDuration(actionSec)}s` : ''}`
        : (item.warnings || []).join('；') || '当前镜头承载偏高';
      return `<div style="margin-top:5px;color:#d4d4d8;font-size:11px">镜头 ${escapeHtml(String(item.scene_id || '未知'))}：${escapeHtml(detail)}</div>`;
    }).join('');
    const hasExpandedPlan = warningItems.some(item => {
      const sourceDuration = Number(item?.source_duration_sec || 0);
      const recommendedTotal = Number(item?.recommended_total_duration_sec || 0);
      return recommendedTotal > sourceDuration;
    });
    sections.push(`<div style="color:#fbbf24;font-weight:600">${hasExpandedPlan ? '镜头承载偏高：已按内容合理扩时' : '镜头承载偏高：已按内容拆为可执行子镜头'}</div>${warningHtml}`);
  }
  if (characterRepair.status === 'partial' || characterRepair.status === 'warning' || characterErrors.length) {
    sections.push(`<div style="color:#fbbf24;font-weight:600">角色状态信息不完整：可继续查看，但建议补充</div>${characterErrors.map(error => `<div style="margin-top:5px;color:#d4d4d8;font-size:11px">${escapeHtml(String(error))}</div>`).join('')}`);
  }
  if (!sections.length) return '';
  return `<section class="production-panel storyboard-quality-diagnostics"><div class="production-diagnostics-head"><span>分镜质量诊断</span><span>${sections.length} 项提示</span></div><div class="production-diagnostics-body">${sections.map(section => `<div>${section}</div>`).join('')}</div></section>`;
}

function promptPolicyFields(policy = {}) {
  const stylePrefix = String(policy.style_prefix || '').trim();
  const globalNegative = String(policy.global_negative_prompt || '').trim();
  return {
    stylePrefix,
    globalNegative,
    configuredCount: [stylePrefix, globalNegative].filter(Boolean).length,
  };
}

function promptPolicyEditorHtml(policy = {}, open = false) {
  const {stylePrefix, globalNegative, configuredCount} = promptPolicyFields(policy);
  const status = configuredCount ? `已设置 ${configuredCount}/2` : '未设置';
  return `<details class="production-panel production-policy-panel" ${open ? 'open' : ''}>
    <summary class="production-policy-summary"><span>全局规则</span><span class="production-policy-status ${configuredCount ? 'is-active' : ''}">${status}</span></summary>
    <div class="production-policy-editor">
      <input id="production-style-prefix" class="production-script" style="min-height:38px" placeholder="全项目风格前缀" value="${escapeHtml(stylePrefix)}">
      <textarea id="production-global-negative" class="production-script" style="min-height:58px" placeholder="全项目负面约束">${escapeHtml(globalNegative)}</textarea>
      <button class="btn-secondary btn-sm" onclick="StudioActions.productionSavePromptPolicy()">保存全局规则</button>
    </div>
  </details>`;
}

function promptPolicySummaryHtml(policy = {}) {
  const {configuredCount} = promptPolicyFields(policy);
  if (!configuredCount) {
    return `<div id="production-prompt-policy-slot" class="production-policy-slot"><button type="button" class="production-policy-trigger" onclick="StudioActions.productionOpenPromptPolicy()"><span>全局规则</span><span class="production-policy-status">未设置 · 添加</span></button></div>`;
  }
  return `<div id="production-prompt-policy-slot" class="production-policy-slot">${promptPolicyEditorHtml(policy)}</div>`;
}

function productionSetInspector(name) {
  const root = document.getElementById('shot-production-workspace');
  const inspector = root?.querySelector('.production-inspector');
  const next = ['words', 'generate', 'assets'].includes(name) ? name : 'words';
  productionInspectorTab = next;
  if (!inspector) return;
  inspector.dataset.inspector = next;
  inspector.querySelectorAll('[data-inspector-tab]').forEach(button => {
    const active = button.dataset.inspectorTab === next;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  if (next === 'words') inspector.querySelector('#production-prompt')?.focus({preventScroll: true});
}

function productionFocusReviewRepair(action, sceneId) {
  const id = Number(sceneId || 0);
  if (action === 'edit_prompt' && id > 0) {
    productionSelectScene(id);
    setTimeout(() => {
      const editor = document.getElementById('production-prompt');
      editor?.focus({preventScroll: false});
    }, 0);
    return;
  }
  if (action === 'edit_script') {
    globalThis.StudioActions.navigateTo?.('script');
    setTimeout(() => document.getElementById('structured-script-editor')?.focus(), 0);
  }
}

function productionOpenPromptPolicy() {
  const slot = document.getElementById('production-prompt-policy-slot');
  if (!slot) return;
  slot.innerHTML = promptPolicyEditorHtml(productionData?.prompt_policy || {}, true);
  slot.querySelector('#production-style-prefix')?.focus();
}

function productionAudioModeHtml(audioMode = 'native_dialogue', videoPolicy = {}, capability = {}) {
  const supportsNative = capability.supports_native_dialogue === true;
  const capabilityStatus = String(capability.native_audio_status || 'unsupported');
  const legacyAuto = audioMode === 'auto';
  const effectiveMode = legacyAuto
    ? (supportsNative ? 'native_dialogue' : 'external_tts')
    : audioMode;
  const provider = String(videoPolicy.provider || 'unknown');
  const model = String(videoPolicy.model || 'unknown');
  const capabilityLabel = capabilityStatus === 'best_effort'
    ? '尽力保留（生成后检测实际音轨）'
    : (supportsNative ? '已验证支持' : '当前模型不支持');
  const capabilityClass = supportsNative ? 'audio-ok' : 'audio-warning';
  const reason = String(capability.native_audio_reason || '').trim();
  const warning = capabilityStatus === 'best_effort' && effectiveMode === 'native_dialogue'
    ? '<div class="production-audio-mode-warning">当前模型将尝试生成并保留视频原生音频；生成完成后检测实际音轨。若没有音轨，不会静默改用外部 TTS。</div>'
    : (!supportsNative && effectiveMode === 'native_dialogue'
      ? '<div class="production-audio-mode-warning">当前模型不支持或尚未接通原生对白，请切换为外部 TTS 配音后再生成。</div>'
      : '');
  const legacy = legacyAuto
    ? '<div class="production-audio-mode-legacy">旧版自动策略：切换一次后将保存为明确的音频来源。</div>'
    : '';
  return `<div class="production-audio-mode" data-audio-mode="${escapeHtml(audioMode)}">
    <div class="production-audio-mode-heading"><strong>对白音频来源</strong><span>${escapeHtml(provider)} · ${escapeHtml(model)}</span></div>
    <div class="production-audio-mode-options">
      <label><input type="radio" name="production-audio-mode" value="native_dialogue" ${effectiveMode === 'native_dialogue' ? 'checked' : ''} onchange="StudioActions.productionSetAudioMode(this.value)"><span>视频模型原生对白</span></label>
      <label><input type="radio" name="production-audio-mode" value="external_tts" ${effectiveMode === 'external_tts' ? 'checked' : ''} onchange="StudioActions.productionSetAudioMode(this.value)"><span>外部 TTS 配音</span></label>
    </div>
    <div class="production-audio-mode-capability ${capabilityClass}">原生对白能力：${capabilityLabel}${reason ? ` · ${escapeHtml(reason)}` : ''}</div>
    ${legacy}${warning}
  </div>`;
}

function renderProductionWorkspace() {
  const root = document.getElementById('shot-production-workspace');
  if (!root) return;
  const nextProjectId = String(appState.projectId || '');
  if (productionDraftProjectId !== nextProjectId) {
    productionDrafts.clear();
    productionDraftProjectId = nextProjectId;
  } else {
    productionRememberRenderedDraft(root);
  }
  if (!appState.projectId) { root.innerHTML = '<div class="production-empty">请先完成剧本解析，再进入分镜制作。</div>'; return; }
  if (!productionData) { root.innerHTML = '<div class="production-empty"><span class="spin"></span> 正在加载分镜制作工作台</div>'; loadProductionWorkspace(); return; }
  const current = productionCurrent();
  if (!current) { root.innerHTML = '<div class="production-empty">暂无分镜数据</div>'; return; }
  const selection = productionSelection(productionData, productionSceneId, productionShotId);
  productionShotId = selection.shotId;
  const {scene, production} = current;
  const selectedShot = productionSelectedShot(current) || current.prompt || {};
  const prompt = selectedShot;
  const selectedShotId = selectedShot?.shot_id ? String(selectedShot.shot_id) : null;
  const parentEditorSelected = !selectedShotId;
  const selectedStatus = productionShotStatus(current, selectedShot);
  const selectedVersions = productionShotVersions(current, selectedShot);
  const selectedActiveVersionId = productionShotActiveVersionId(current, selectedShot);
  const effective = selectedShot?.effective_prompt || (selectedShotId ? {} : current.effective_prompt || {});
  const promptQuality = productionData.prompt_quality || {};
  const promptReview = productionData.prompt_review || {};
  const reviewReport = promptReviewReport(promptReview);
  const currentPromptReview = (reviewReport.shots || []).find(item => Number(item.scene_id) === Number(scene.scene_id));
  const currentPromptIssues = promptReviewIssues(reviewReport).filter(item => Number(item.scene_id) === Number(scene.scene_id) || (item.related_scene_ids || []).map(Number).includes(Number(scene.scene_id)));
  const currentPromptReviewHtml = (currentPromptReview || currentPromptIssues.length)
    ? `<div style="margin-top:8px;padding:8px;border:1px solid ${promptReview.status === 'ready' && reviewReport.verdict === 'pass' && currentPromptReview?.passed ? '#166534' : '#854d0e'};background:#18181b;font-size:11px;line-height:1.55"><b style="color:${promptReview.status === 'ready' && reviewReport.verdict === 'pass' && currentPromptReview?.passed ? '#86efac' : '#fbbf24'}">Kimi 检验：${escapeHtml(String(currentPromptReview?.score ?? ''))} 分 · ${escapeHtml(promptReviewShotStatusLabel(promptReview, reviewReport, currentPromptReview))}</b>${currentPromptReview?.summary ? `<div style="margin-top:3px;color:#d4d4d8">${escapeHtml(currentPromptReview.summary)}</div>` : ''}${currentPromptIssues.length ? `<div style="display:grid;gap:4px;margin-top:6px">${currentPromptIssues.map(item => promptReviewIssueHtml(item, true)).join('')}</div>` : ''}</div>`
    : '';
  const providerPrompt = effective.provider_prompt || effective.effective_prompt || prompt.prompt || '';
  const providerNegative = effective.provider_negative_prompt || '';
  const negativeMode = effective.negative_prompt_mode || 'merged';
  const finalPromptPreview = negativeMode === 'separate'
    ? `<div style="font-size:11px;color:#a1a1aa;margin-bottom:4px">主提示词</div>${escapeHtml(providerPrompt)}<div style="font-size:11px;color:#a1a1aa;margin:10px 0 4px">独立负面提示词参数</div>${escapeHtml(providerNegative || '无')}`
    : `<div style="font-size:11px;color:#a1a1aa;margin-bottom:4px">主提示词（已合并负面约束）</div>${escapeHtml(providerPrompt)}`;
  const promptPolicy = productionData.prompt_policy || {};
  const activeVideoJobs = activeProjectJobs(job => job.job_type === 'video.generate');
  const currentShot = selectedShot;
  const currentTargetId = String(currentShot?.shot_id || scene.scene_id || '');
  const currentVideoJob = activeVideoJobs.find(job => job.target_type === (selectedShotId ? 'shot_unit' : 'shot') && String(job.target_id) === currentTargetId);
  const batchJobs = productionActiveVideoBatchJobs(activeVideoJobs);
  const batchScopeNote = batchJobs.length
    ? `项目级批量进行中 · ${batchJobs.length} 项活动任务`
    : '项目级 · 仅处理未生成镜头';
  const currentVideoButtonLabel = productionCurrentVideoButtonLabel(currentVideoJob, selectedStatus);
  const activeAudioJob = activeProjectJobs(job => job.job_type === 'audio.generate')[0];
  const audioSummary = productionData.audio_summary || {};
  const activeFinalJob = activeProjectJobs(job => job.job_type === 'final.assemble')[0];
  const active = selectedVersions.find(item => item.version_id === selectedActiveVersionId);
  const activeVideo = productionVideoUrl(active);
  const videoPolicy = current.video_policy || productionData.video_generation_policy || {provider:'mock',model:'mock',resolution:'1080p'};
  const videoCapabilities = productionData.video_capabilities || {};
  const currentCapability = current.video_capability || videoCapabilities[videoPolicy.provider]?.models?.[videoPolicy.model] || {};
  const audioModeHtml = productionAudioModeHtml(productionData.audio_mode || 'auto', videoPolicy, currentCapability);
  const durationMin = Number(currentCapability.duration_min || 3);
  const durationMax = Number(currentCapability.duration_max || 15);
  const maxReferenceImages = Number(currentCapability.max_reference_images || 0);
  const hasReferenceLimit = Object.prototype.hasOwnProperty.call(currentCapability, 'max_reference_images');
  const legacyReferenceWarning = videoPolicy.provider === 'seedance' && production.continuity_enabled && production.references.length > 1
    ? '<div class="production-reference-warning" style="margin-top:6px;color:#fbbf24;font-size:11px">连续镜头使用首帧模式，只会提交上一镜尾帧；其他普通参考图不会随请求发送。</div>'
    : videoPolicy.provider === 'seedance' && production.references.length > maxReferenceImages && maxReferenceImages > 0
      ? `<div class="production-reference-warning" style="margin-top:6px;color:#fbbf24;font-size:11px">当前模型最多接受 ${maxReferenceImages} 张参考图，超出的图片提交时会被忽略，任务详情会列出具体原因。</div>`
      : '';
  const providerLabels = {mock: 'Mock', wan: '万相 2.6', happyhorse: '快乐小马', seedance: 'Seedance 火山方舟'};
  const providerOptions = Object.entries(videoCapabilities).map(([key, value]) => `<option value="${key}" ${value.available === false ? 'disabled' : ''} ${key === videoPolicy.provider ? 'selected' : ''}>${escapeHtml(providerLabels[key] || key)}</option>`).join('');
  const referenceModes = Array.isArray(currentCapability.reference_modes) ? currentCapability.reference_modes : [];
  const modeLabels = {text_to_video:'纯文生视频', first_frame:'首帧', first_last_frame:'首尾帧', multi_reference:'多参考图'};
  const providerAcceptsReferences = currentCapability.image_to_video !== false;
  const referenceModeHint = referenceModes.length
    ? referenceModes.map(mode => modeLabels[mode] || mode).join(' / ')
    : (!providerAcceptsReferences ? '不支持参考图' : (hasReferenceLimit ? (maxReferenceImages > 0 ? '单参考图' : '不支持参考图') : '支持参考图，上限由服务决定'));
  const continuityConsumesReferences = production.continuity_enabled
    && production.references.length > 0
    && (videoPolicy.provider === 'seedance' || !currentCapability.multi_reference);
  const continuityWarning = continuityConsumesReferences
    ? '<div id="production-continuity-conflict" class="production-reference-warning" style="margin-top:6px;color:#fbbf24;font-size:11px">连续镜头会优先使用上一镜尾帧；当前模型不会同时提交普通参考图。显式 @ 引用可能被模型限制拒绝，请关闭连续性或减少普通参考图。</div>'
    : '';
  const referenceCount = production.references.length + (
    production.continuity_enabled && currentCapability.multi_reference && currentCapability.continuity ? 1 : 0
  );
  const automaticReferenceCount = production.references.filter(item => item.source === 'asset').length;
  const manualReferenceCount = production.references.filter(item => item.source === 'manual').length;
  const explicitReferenceCount = production.references.filter(item => item.source === 'explicit').length;
  const referenceLimit = maxReferenceImages > 0
    ? maxReferenceImages
    : (providerAcceptsReferences ? referenceCount : 0);
  const submittedReferenceCount = continuityConsumesReferences
    ? (production.continuity_enabled && providerAcceptsReferences ? Math.min(1, referenceLimit) : 0)
    : (providerAcceptsReferences ? Math.min(referenceCount, referenceLimit || referenceCount) : 0);
  const referenceSummaryParts = [
    automaticReferenceCount ? `自动 ${automaticReferenceCount}` : '',
    manualReferenceCount ? `手动 ${manualReferenceCount}` : '',
    explicitReferenceCount ? `显式 ${explicitReferenceCount}` : '',
    referenceCount ? `提交 ${submittedReferenceCount}/${referenceCount}` : '未绑定参考图',
  ].filter(Boolean);
  const referenceWarning = continuityWarning || legacyReferenceWarning || (hasReferenceLimit && maxReferenceImages > 0 && referenceCount > maxReferenceImages
    ? `<div class="production-reference-warning" style="margin-top:6px;color:#fbbf24;font-size:11px">当前模型最多接受 ${maxReferenceImages} 张参考图，超出的图片不会发送；任务详情会列出被忽略的图片。</div>`
    : '');
  const referenceManagerOpen = Boolean(referenceWarning || production.references.some(item => item.stale));
  const modelOptions = Object.keys(videoCapabilities[videoPolicy.provider]?.models || {}).map(model => `<option value="${model}" ${model === videoPolicy.model ? 'selected' : ''}>${model}</option>`).join('');
  const resolutionOptions = (current.video_capability?.resolutions || ['720p','1080p']).map(value => `<option value="${value}" ${value === videoPolicy.resolution ? 'selected' : ''}>${value}</option>`).join('');
  const continuityHint = production.continuity_enabled ? (production.continuity_stale ? '上一镜已变化，需重新生成' : (production.continuity_tail_frame_path ? `引用镜头 ${production.continuity_source_scene_id} 尾帧` : '生成时引用上一镜尾帧')) : '独立镜头';
  const catalog = productionData.asset_catalog || {characters:[], scenes:[], props:[]};
  const assetGrid = (assets, empty) => assets.length ? `<div class="asset-grid">${assets.map(item => `<button class="asset-item" onclick="StudioActions.productionAddReference('${StudioActions.escapeHtml(item.reference_id)}')"><img src="${productionImageUrl(item)}" alt=""><span>${escapeHtml(item.label)}</span></button>`).join('')}</div>` : `<div style="font-size:11px;color:#71717a">${empty}</div>`;
  const preview = productionPreviewHtml(current);
  const editorSnapshot = captureProductionEditorState(root);
  const timelineHtml = productionTimelineHtml(productionData);
  const inspectorOpen = ['words', 'generate', 'assets'].includes(productionInspectorTab) ? productionInspectorTab : 'words';
  const shotLabel = selectedShotId ? `${scene.scene_id}-${Number(selectedShot.shot_order || 1)}` : String(scene.scene_id);
  const scopeLabel = parentEditorSelected ? `父镜头 ${scene.scene_id} · 可编辑源提示词` : `子镜头 ${shotLabel} · 画面只读，生成设置可改`;
  root.innerHTML = `
    <div class="production-toolbar production-workspace-toolbar">
      <div class="production-toolbar-title"><h3>镜头 ${escapeHtml(shotLabel)}</h3><span id="production-global-status" class="production-global-status"></span><span class="production-context-scope">${scopeLabel}</span><span id="production-context-status" class="production-context-status">${productionStatusLabel(selectedStatus)}</span></div>
      <div class="production-context-actions"><button type="button" class="btn-secondary btn-sm" onclick="StudioActions.productionNavigateAdjacent(-1)" title="上一个镜头">上一镜</button><button type="button" class="btn-secondary btn-sm" onclick="StudioActions.productionNavigateAdjacent(1)" title="下一个镜头">下一镜</button><button class="btn-primary btn-sm" id="production-generate-current" onclick="StudioActions.productionGenerateCurrent()">${currentVideoButtonLabel}</button></div>
      <div class="production-project-tools" title="项目级 · 不会改变当前镜头选择"><div class="production-toolbar-actions"><button class="btn-secondary btn-sm" id="production-generate-pending" onclick="StudioActions.productionGeneratePending()">${batchJobs.length ? `终止项目批量视频（${batchJobs.length} 项活动）` : '批量未生成'}</button><button class="btn-secondary btn-sm" id="btn-gen-audio" onclick="StudioActions.productionGenerateAudio()">${activeAudioJob ? '终止项目台词' : (audioSummary.failed_line_count ? '重试失败台词' : '生成缺失台词')}</button><button class="btn-secondary btn-sm" id="btn-assemble" onclick="StudioActions.productionAssembleVideo()">${activeFinalJob ? '终止正式合成' : '正式合成'}</button></div><span class="production-project-tools-note">作用于整个项目，不会改变当前镜头选择</span></div>
    </div>
    <div class="production-shell production-stage-shell">
      <aside class="production-panel production-preview">
        <div class="production-preview-header"><span id="production-preview-download">${activeVideo ? `<a class="btn-secondary btn-sm" href="${activeVideo}" download>下载</a>` : ''}</span></div>
        <div id="production-preview-stage" class="preview-stage">${preview}</div>
        <div class="production-editor-section production-generation-section production-stage-quickset">
          <label class="production-quickset-field"><span>服务</span><select id="production-video-provider" onchange="StudioActions.productionVideoProviderChanged()">${providerOptions}</select></label>
          <label class="production-quickset-field"><span>模型</span><select id="production-video-model" onchange="StudioActions.productionVideoModelChanged()">${modelOptions}</select></label>
          <label class="production-quickset-field"><span>清晰度</span><select id="production-video-resolution" onchange="StudioActions.productionVideoResolutionChanged()">${resolutionOptions}</select></label>
          <label class="production-toggle"><input id="production-continuity" type="checkbox" ${production.continuity_enabled ? 'checked' : ''} onchange="StudioActions.productionContinuityChanged(this)"><span>承接上一镜尾帧</span></label>
        </div>
        <section class="production-version-section"><div class="production-section-heading"><h5>这一镜的版本</h5><span class="production-section-note">${selectedShotId ? `子镜头 ${escapeHtml(selectedShotId)}` : `父镜头 ${scene.scene_id}`}</span></div><div id="production-version-list" class="version-list">${productionVersionListHtml(current)}</div></section>
      </aside>
      <main class="production-panel production-editor production-inspector" data-inspector="${escapeHtml(inspectorOpen)}">
        <div class="production-inspector-tabs" role="tablist" aria-label="镜头检查">
          <button type="button" role="tab" data-inspector-tab="words" aria-selected="${inspectorOpen === 'words' ? 'true' : 'false'}" class="${inspectorOpen === 'words' ? 'is-active' : ''}" onclick="StudioActions.productionSetInspector('words')">提示词</button>
          <button type="button" role="tab" data-inspector-tab="generate" aria-selected="${inspectorOpen === 'generate' ? 'true' : 'false'}" class="${inspectorOpen === 'generate' ? 'is-active' : ''}" onclick="StudioActions.productionSetInspector('generate')">对白与成本</button>
          <button type="button" role="tab" data-inspector-tab="assets" aria-selected="${inspectorOpen === 'assets' ? 'true' : 'false'}" class="${inspectorOpen === 'assets' ? 'is-active' : ''}" onclick="StudioActions.productionSetInspector('assets')">素材</button>
        </div>
        <section class="production-notices" aria-label="项目提示">
          ${promptQuality.needs_refresh ? '<div class="production-panel production-stale-note">当前项目含旧版非中文分镜提示词。已有视频与手动编辑将保留；确认需要更新时，请重新解析剧本以生成新版中文分镜。</div>' : ''}
          ${promptReviewHtml(promptReview)}
          ${storyboardQualityDiagnosticsHtml(productionData)}
          ${promptPolicySummaryHtml(promptPolicy)}
        </section>
        <header class="production-editor-header">
          <div class="production-shot-title"><span class="production-eyebrow">当前镜头</span><h3>镜头 ${selectedShotId ? `${scene.scene_id}-${Number(selectedShot.shot_order || 1)}` : scene.scene_id}</h3><p>${parentEditorSelected ? '父镜头源提示词与生成目标' : `来源镜头 ${scene.scene_id} · 自动拆镜执行版本`} <span>·</span> ${escapeHtml(scene.location)} <span>·</span> 可见角色：${escapeHtml((scene.characters || []).join('、') || '无')}</p></div>
          <div class="production-meta"><label class="production-chip production-duration-chip"><span>时长</span><input id="production-duration" type="number" min="3" max="30" value="${prompt.duration_sec}" ${selectedShotId ? 'disabled' : ''}><span class="production-unit">秒</span></label><span id="production-current-status" class="production-chip production-status-chip">${productionStatusLabel(selectedStatus)}</span></div>
        </header>
        <section class="production-editor-section production-reference-section">
          <div class="production-section-heading"><div><span class="production-eyebrow">01 · 参考输入</span><h4>参考素材</h4></div><span class="production-section-note">${escapeHtml(referenceSummaryParts.join(' · '))}</span></div>
          <div id="production-reference-capability" class="production-reference-summary"><span>${escapeHtml(referenceSummaryParts.join(' · '))}</span><span>${hasReferenceLimit ? `最多 ${maxReferenceImages} 张` : '未声明固定上限'} <b>·</b> ${escapeHtml(referenceModeHint)}</span></div>
          ${referenceWarning}
          <details class="production-reference-manager" ${referenceManagerOpen ? 'open' : ''}>
            <summary><span>管理参考图</span><span>排序、版本和手动添加</span></summary>
            <div class="production-reference-manager-body">
              <div class="reference-row">${production.references.map(productionReferenceHtml).join('') || '<span class="production-empty-inline">从左侧资产库选择角色图或场景图</span>'}</div>
              <div class="production-reference-toolbar"><button type="button" class="production-field-label production-mention-label" onclick="StudioActions.productionOpenMentionPicker('')">@ 资产引用</button><select id="productionMentionInput" onkeydown="StudioActions.productionMentionKeydown(event)"><option value="">选择角色、场景或道具</option>${productionMentionOptions()}</select><button class="btn-secondary btn-sm" type="button" onclick="StudioActions.productionInsertMention()">插入引用</button><button class="btn-secondary btn-sm production-mention-picker-trigger" type="button" onclick="StudioActions.productionOpenMentionPicker('')">浏览资产</button></div>
            </div>
          </details>
        </section>
        <section class="production-editor-section production-prompt-section">
          <div class="production-section-heading"><div><span class="production-eyebrow">02 · 提示词</span><h4>生成提示词</h4></div><span class="production-section-note">${parentEditorSelected ? '可直接编辑父镜头' : '自动拆镜执行版本 · 只读'}</span></div>
          <div class="production-prompt-field"><label class="production-field-label" for="production-prompt">${selectedShotId ? `自动拆镜 · 来源镜头 ${scene.scene_id} · 子镜头 ${Number(selectedShot.shot_order || 1)}/${executableShotsForItem(current).length}` : '主提示词'}</label><div class="production-textarea-wrap"><div id="production-prompt" class="production-script production-prompt-editor" contenteditable="${selectedShotId ? 'false' : 'true'}" role="textbox" aria-multiline="true" data-placeholder="视频生成提示词" spellcheck="false" oninput="StudioActions.productionMentionKeydown(event)" onkeydown="StudioActions.productionMentionKeydown(event)" onkeyup="StudioActions.productionMentionKeydown(event)" onmouseup="StudioActions.productionRememberPromptSelection(event)" onblur="StudioActions.productionRememberPromptSelection(event)">${productionPromptEditorHtml(prompt.prompt)}</div><textarea id="production-prompt-value" hidden>${escapeHtml(prompt.prompt)}</textarea><div id="production-mention-suggestions" class="production-mention-suggestions" hidden></div></div></div>
          <div class="production-prompt-field production-negative-field"><label class="production-field-label" for="production-negative">负面约束 <span>可选</span></label><textarea id="production-negative" class="production-script" placeholder="填写需要避免的画面问题" ${selectedShotId ? 'readonly' : ''}>${escapeHtml(prompt.negative_prompt || '')}</textarea></div>
          <details class="production-provider-preview"><summary>查看最终发送内容 <span>${escapeHtml(effective.provider || '')} · ${negativeMode === 'merged' ? '负面约束已合并' : '独立负面参数'}</span></summary><div class="production-provider-preview-body">${finalPromptPreview}</div></details>
          ${currentPromptReviewHtml}
        </section>
        <section class="production-editor-section production-ai-section">
          <div class="production-section-heading"><div><span class="production-eyebrow">04 · 辅助修改</span><h4>AI 修改提示词</h4></div><span class="production-section-note">${parentEditorSelected ? '仅修改父镜头源提示词' : '子镜头只读，请切回父镜头修改'}</span></div>
          ${parentEditorSelected ? '<textarea id="production-feedback" class="production-feedback" placeholder="例如：让镜头更近一些，保留人物动作和场景不变"></textarea><button class="btn-secondary production-ai-button" onclick="StudioActions.productionRevisePrompt()">AI 修改提示词</button>' : '<div class="production-readonly-note">当前是自动拆镜执行版本。AI 修改会作用于父镜头源提示词，请先点击时间线中的“父镜头”入口。</div>'}
        </section>
        <section class="production-editor-section production-generation-detail">
          <div class="production-section-heading"><div><span class="production-eyebrow">对白音频来源</span><h4>${escapeHtml(String(videoPolicy.provider || ''))} · ${escapeHtml(String(videoPolicy.model || ''))}</h4></div><span class="production-section-note">提交前可调整</span></div>
          ${audioModeHtml}
          <div class="production-capability-note">成本档位：${escapeHtml(current.video_capability?.cost_tier || '未标注')} · 未单独设置的后续分镜会继承当前选择</div>
          <div class="production-continuity-row"><span class="production-chip production-hard-cut-chip">新任务采用硬切</span><span class="production-continuity-state ${production.continuity_stale ? 'is-warning' : ''}">${escapeHtml(continuityHint)}</span></div>
        </section>
        <section class="production-editor-section production-assets-section">
          <div class="production-section-heading"><div><span class="production-eyebrow">素材库</span><h4>参考资产</h4></div><span class="production-section-note">点击加入当前镜头</span></div>
          <section class="production-asset-group"><h5>角色形象</h5>${assetGrid(catalog.characters, '请先生成角色形象图')}</section>
          <section class="production-asset-group"><h5>场景参考</h5>${assetGrid(catalog.scenes, '请先生成场景参考图')}</section>
          <section class="production-asset-group"><h5>道具参考</h5>${assetGrid(catalog.props, '当前镜头没有可用关键道具图')}</section>
        </section>
        <footer class="production-editor-footer-actions production-actions"><div class="production-save-hint" id="production-save-state">${selectedShotId ? '当前子镜头为自动拆镜执行版本，生成设置仍会保存。' : '当前父镜头可编辑；未保存修改会在生成前保存。'}</div><div class="production-footer-buttons">${selectedShotId ? '<button class="btn-secondary" disabled>当前子镜头只读</button>' : '<button class="btn-secondary" onclick="StudioActions.productionSaveCurrent()">保存父镜头编辑</button>'}<button class="btn-primary" id="production-generate-current-footer" onclick="StudioActions.productionGenerateCurrent()">${currentVideoButtonLabel}</button></div></footer>
      </main>
    </div>
    <section class="production-filmstrip" aria-label="全片镜头胶片">
      <div class="production-filmstrip-head"><span class="production-eyebrow">胶片</span><strong>${productionData.productions.length} 场</strong><span>点画面切换镜头</span></div>
      <div class="production-panel production-timeline">${timelineHtml}</div>
    </section>
    <section class="production-secondary-section production-audio-section">
      <div class="production-panel-heading"><div><span class="production-eyebrow">项目音频</span><h4>对白、BGM 与字幕</h4></div><span class="production-secondary-meta">${audioSummary.voice_count ? `音色 ${audioSummary.confirmed_voice_count || 0}/${audioSummary.voice_count}` : '全项目共用'}</span></div>
      <section id="production-audio-workspace" class="production-panel production-audio-workspace"></section>
    </section>`;
  root.dataset.productionEditorSceneId = String(scene.scene_id);
  root.dataset.productionEditorShotId = String(selection.shotId || selectedShotId || `parent:${scene.scene_id}`);
  root.querySelector('#production-video-provider option[value="seedance"]')?.replaceChildren(document.createTextNode('Seedance 火山方舟'));
  root.querySelector('.production-meta .production-chip:last-child')?.setAttribute('id', 'production-current-status');
  const previewStage = root.querySelector('.preview-stage');
  if (previewStage) {
    previewStage.setAttribute('id', 'production-preview-stage');
    previewStage.dataset.productionPreviewHtml = preview;
  }
  root.querySelector('.version-list')?.setAttribute('id', 'production-version-list');
  const durationInput = root.querySelector('#production-duration');
  if (durationInput) {
    durationInput.min = String(durationMin);
    durationInput.max = String(durationMax);
    const currentDuration = Number(durationInput.value);
    if (Number.isFinite(currentDuration) && currentDuration < durationMin) durationInput.value = String(durationMin);
    if (Number.isFinite(currentDuration) && currentDuration > durationMax) durationInput.value = String(durationMax);
  }
  updateProductionVideoSelection();
  renderProductionAudioTimeline();
  restoreProductionEditorState(root, editorSnapshot);
  // The same-shot snapshot is captured before paint and can still hold the
  // saved prompt. Per-shot drafts are the unsaved text, so they win after it.
  const draft = productionDrafts.get(productionRenderedEditorKey(root));
  if (draft) {
    const negative = root.querySelector('#production-negative');
    const feedback = root.querySelector('#production-feedback');
    const duration = root.querySelector('#production-duration');
    if (negative) negative.value = draft.negative;
    if (feedback) feedback.value = draft.feedback;
    if (duration && draft.duration !== '') duration.value = draft.duration;
    productionSetPromptText(draft.prompt);
  }
  root.querySelector('.production-filmstrip .timeline-shot.active, .production-filmstrip .production-timeline-parent.active')
    ?.scrollIntoView({block: 'nearest', inline: 'center'});
  const stageShell = root.querySelector('.production-stage-shell');
  const scheduleFrame = typeof requestAnimationFrame === 'function'
    ? requestAnimationFrame
    : (callback => setTimeout(callback, 16));
  const fitStageShell = (attempt = 0) => {
    if (!stageShell) return;
    if (window.innerWidth <= 1100) { stageShell.style.height = ''; return; }
    if (attempt > 60) return;
    if (stageShell.getClientRects().length === 0) { scheduleFrame(() => fitStageShell(attempt + 1)); return; }
    const top = Math.ceil(stageShell.getBoundingClientRect().top);
    if (top <= 1) { scheduleFrame(() => fitStageShell(attempt + 1)); return; }
    const strip = root.querySelector('.production-filmstrip');
    const stripHeight = strip ? Math.ceil(strip.getBoundingClientRect().height) + 12 : 0;
    stageShell.style.height = `${Math.max(300, window.innerHeight - top - stripHeight - 20)}px`;
  };
  fitStageShell();
  scheduleFrame(() => fitStageShell());
  [120, 400, 800, 1500].forEach(delay => setTimeout(() => fitStageShell(), delay));
  if (!root.dataset.productionStageFitBound && typeof window.addEventListener === 'function') {
    root.dataset.productionStageFitBound = '1';
    window.addEventListener('resize', () => fitStageShell());
  }
  root.querySelectorAll('#production-preview-stage video').forEach(video => {
    const fit = () => {
      if (!video.videoWidth || !video.videoHeight) return;
      video.parentElement.style.aspectRatio = `${video.videoWidth} / ${video.videoHeight}`;
    };
    video.addEventListener('loadedmetadata', fit);
    fit();
  });
  root.querySelectorAll('.production-filmstrip video').forEach(video => {
    const showFrame = () => {
      if (!video.duration) return;
      video.currentTime = Math.min(0.8, video.duration / 2);
    };
    video.addEventListener('loadedmetadata', showFrame);
    showFrame();
  });
}

function productionHasVideoOverride(item) {
  const override = item?.production?.video_override || {};
  return ['provider', 'model', 'resolution'].some(key => Boolean(String(override[key] || '').trim()));
}

function applyProjectVideoPolicyLocally(policy) {
  if (!productionData || !policy) return;
  productionData.video_generation_policy = {
    ...(productionData.video_generation_policy || {}),
    ...policy,
  };
  const capability = productionData.video_capabilities?.[policy.provider]?.models?.[policy.model] || {};
  (productionData.productions || []).forEach(item => {
    if (productionHasVideoOverride(item)) return;
    item.video_policy = {...(item.video_policy || {}), ...policy};
    item.video_capability = capability;
  });
}

function persistProductionVideoPolicy(policy) {
  const projectId = appState.projectId;
  if (!projectId || !productionData) return Promise.resolve(null);
  const nextPolicy = {
    provider: String(policy?.provider || '').trim(),
    model: String(policy?.model || '').trim(),
    resolution: String(policy?.resolution || '').trim(),
  };
  if (Object.values(nextPolicy).some(value => !value)) return Promise.resolve(null);
  applyProjectVideoPolicyLocally(nextPolicy);
  const revision = ++productionVideoPolicyRevision;
  productionVideoPolicySaveChain = productionVideoPolicySaveChain
    .catch(() => null)
    .then(async () => {
      if (projectId !== appState.projectId) return null;
      const data = await projectJsonRequest(
        `${API_BASE}/api/projects/${projectId}/video-generation-policy`,
        {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(nextPolicy)},
        '保存视频默认设置失败',
      );
      if (data && projectId === appState.projectId && revision === productionVideoPolicyRevision) {
        if (data.providers) productionData.video_capabilities = data.providers;
        applyProjectVideoPolicyLocally(data.policy || nextPolicy);
      }
      return data;
    })
    .catch(error => {
      if (projectId === appState.projectId && revision === productionVideoPolicyRevision) {
        produceStatus(`<span style="color:#fbbf24">视频默认设置保存失败：${escapeHtml(error.message || '请稍后重试')}</span>`);
      }
      return null;
    });
  return productionVideoPolicySaveChain;
}

function productionSaveCurrent() {
  const current = productionCurrent();
  if (!current || !appState.projectId) return Promise.resolve(false);
  const selectedShot = productionSelectedShot(current);
  const projectId = appState.projectId;
  const sceneId = productionSceneId;
  const shotId = productionShotId;
  const policySave = productionVideoPolicySaveChain;
  if (!selectedShot?.shot_id) syncProductionMentionBindings(current);
  const payload = {
    references: current.production.references.map(item => ({reference_id:item.reference_id,source:item.source || 'asset',reference_token:item.reference_token || '',token:item.reference_token || '',version_id:item.asset_version_id || '',explicit_base_source:item.source === 'explicit' ? (item.explicit_base_source || '') : ''})),
    excluded_automatic_reference_ids: current.production.excluded_automatic_reference_ids || [],
    video_override: {
      provider: document.getElementById('production-video-provider').value,
      model: document.getElementById('production-video-model').value,
      resolution: document.getElementById('production-video-resolution').value,
    },
    continuity_enabled: document.getElementById('production-continuity').checked,
  };
  // Child prompts are derived and read-only; saving their settings must not
  // revalidate the parent duration or reconcile its mentions against child text.
  if (!selectedShot?.shot_id) {
    payload.prompt = productionSyncPromptValue();
    payload.negative_prompt = document.getElementById('production-negative').value;
    payload.duration_sec = Number(document.getElementById('production-duration').value);
  }
  const revision = ++productionShotSaveRevision;
  productionShotSaveChain = productionShotSaveChain.catch(() => false).then(async () => {
    await policySave;
    if (projectId !== appState.projectId) return false;
    try {
      const data = await projectJsonRequest(`${API_BASE}/api/projects/${projectId}/production/shots/${sceneId}`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}, '保存失败');
      if (!data || projectId !== appState.projectId) return false;
      if (revision === productionShotSaveRevision) {
        productionData = data;
        if (sceneId === productionSceneId && shotId === productionShotId) renderProductionWorkspace();
      }
      return true;
    } catch (error) { alert(error.message); return false; }
  });
  return productionShotSaveChain;
}

async function productionAddReference(referenceId) {
  const current=productionCurrent();
  if (!current || current.production.references.some(item=>item.reference_id===referenceId)) return;
  const reference=(productionData.asset_catalog.characters || []).concat(productionData.asset_catalog.scenes || [], productionData.asset_catalog.props || []).find(item=>item.reference_id===referenceId);
  if (!reference) return;
  current.production.excluded_automatic_reference_ids = (current.production.excluded_automatic_reference_ids || []).filter(id => id !== referenceId);
  current.production.references.push({...reference,source:'manual'});
  await productionSaveCurrent();
}
async function productionRemoveReference(index) {
  const current=productionCurrent();
  if (!current) return;
  const reference = current.production.references[index];
  if (!reference) return;
  if (reference.source === 'asset' && reference.reference_id) {
    const excluded = current.production.excluded_automatic_reference_ids || [];
    if (!excluded.includes(reference.reference_id)) excluded.push(reference.reference_id);
    current.production.excluded_automatic_reference_ids = excluded;
  }
  current.production.references.splice(index,1);
  await productionSaveCurrent();
}
function productionDragStart(index) { productionDragIndex=index; }
async function productionDropReference(index) { const current=productionCurrent(); if (!current || productionDragIndex===null || productionDragIndex===index) return; const [item]=current.production.references.splice(productionDragIndex,1); current.production.references.splice(index,0,item); productionDragIndex=null; await productionSaveCurrent(); }
function productionEditorHasUnsavedChanges() {
  const current = productionCurrent();
  const selectedShot = productionSelectedShot(current);
  if (!current || selectedShot?.shot_id) return false;
  const prompt = productionPromptText();
  const negative = document.getElementById('production-negative')?.value || '';
  const duration = Number(document.getElementById('production-duration')?.value || 0);
  return prompt !== String(current.prompt?.prompt || '')
    || negative !== String(current.prompt?.negative_prompt || '')
    || duration !== Number(current.prompt?.duration_sec || 0);
}

async function productionNavigateAdjacent(delta) {
  const entries = productionTimelineEntries(productionData);
  const currentSelectionShotId = String(productionShotId || '');
  let currentIndex = entries.findIndex(entry => Number(entry.sceneId) === Number(productionSceneId)
    && String(entry.shotId || '') === String(currentSelectionShotId.startsWith('parent:') ? '' : currentSelectionShotId));
  if (currentIndex < 0 && currentSelectionShotId.startsWith('parent:')) {
    currentIndex = entries.findIndex(entry => Number(entry.sceneId) === Number(productionSceneId));
  }
  const nextIndex = currentIndex + Number(delta || 0);
  const next = entries[nextIndex];
  if (!next) return;
  if (productionEditorHasUnsavedChanges()) {
    const save = confirm('当前父镜头有未保存修改。点击“确定”保存后切换，点击“取消”可继续选择是否放弃。');
    if (save) {
      if (!await productionSaveCurrent()) return;
    } else if (!confirm('放弃未保存修改并切换到下一个镜头？')) {
      return;
    }
  }
  productionSelectScene(next.sceneId, next.shotId || `parent:${next.sceneId}`);
}

function productionSelectScene(sceneId, shotId = null) {
  productionRememberRenderedDraft(document.getElementById('shot-production-workspace'));
  productionSceneId = Number(sceneId);
  const selection = productionSelection(productionData, productionSceneId, shotId);
  productionShotId = selection.shotId;
  renderProductionWorkspace();
}
function updateProductionVideoSelection({resetModel = false} = {}) {
  const current = productionCurrent();
  const providerElement = document.getElementById('production-video-provider');
  const model = document.getElementById('production-video-model');
  const resolution = document.getElementById('production-video-resolution');
  if (!providerElement || !model || !resolution) return;
  const provider = providerElement.value;
  const models = productionData.video_capabilities?.[provider]?.models || {};
  const keys = Object.keys(models);
  const previousModel = model.value;
  if (resetModel || !models[previousModel]) {
    model.innerHTML = keys.map(key => `<option value="${key}">${key}</option>`).join('');
    const preferred = provider === 'seedance'
      ? 'doubao-seedance-2-0-fast-260128'
      : provider === 'happyhorse'
        ? 'happyhorse-1.1-r2v'
        : keys[0];
    model.value = preferred && models[preferred] ? preferred : (keys[0] || '');
  }
  const selectedModel = model.value;
  const selectedCapability = models[selectedModel] || {};
  const continuityControl = document.getElementById('production-continuity');
  if (continuityControl) {
    const supportsContinuity = Boolean(selectedCapability.continuity);
    continuityControl.disabled = !supportsContinuity;
    continuityControl.title = supportsContinuity ? '' : '当前模型不支持连续镜头';
    if (!supportsContinuity) continuityControl.checked = false;
    // The generation-state harness uses a plain control object, so the HTML
    // onchange attribute never becomes a function. Bind it here after every
    // model update so production-continuity.onchange() can save immediately.
    if (typeof continuityControl.onchange !== 'function') {
      continuityControl.onchange = () => productionContinuityChanged(continuityControl);
    }
  }
  const previousResolution = resolution.value;
  const supportedResolutions = selectedCapability.resolutions || ['720p', '1080p'];
  resolution.innerHTML = supportedResolutions.map(value => `<option value="${value}">${value}</option>`).join('');
  resolution.value = supportedResolutions.includes(previousResolution)
    ? previousResolution
    : (supportedResolutions[supportedResolutions.length - 1] || '');
  if (current) {
    current.video_capability = selectedCapability;
    current.video_policy = {
      ...(current.video_policy || {}),
      provider,
      model: selectedModel,
      resolution: resolution.value,
    };
  }
  const audioModePanel = document.querySelector('#shot-production-workspace .production-audio-mode');
  if (audioModePanel) {
    audioModePanel.outerHTML = productionAudioModeHtml(
      productionData.audio_mode || 'auto',
      current?.video_policy || {provider, model: selectedModel},
      selectedCapability,
    );
  }
  const hint = document.querySelector('#shot-production-workspace #production-reference-capability');
  if (hint) {
    const limit = Number(selectedCapability.max_reference_images || 0);
    const hasLimit = Object.prototype.hasOwnProperty.call(selectedCapability, 'max_reference_images');
    const labels = {text_to_video:'纯文生视频', first_frame:'首帧参考', first_last_frame:'首尾帧', multi_reference:'多参考图'};
    const modes = Array.isArray(selectedCapability.reference_modes) ? selectedCapability.reference_modes : [];
    const acceptsReferences = selectedCapability.image_to_video !== false;
    const mode = modes.map(value => labels[value] || value).join(' / ')
      || (!acceptsReferences ? '不支持参考图' : (hasLimit ? (limit > 0 ? '单参考图' : '不支持参考图') : '支持参考图，上限由服务决定'));
    hint.innerHTML = `<span>${escapeHtml(acceptsReferences ? '参考图能力已更新' : '当前模型不支持参考图')}</span><span>${hasLimit ? `最多 ${limit} 张` : '未声明固定上限'} <b>·</b> ${escapeHtml(mode)}</span>`;
  }
  const duration = document.getElementById('production-duration');
  if (duration) {
    const minimum = Number(selectedCapability.duration_min || 3);
    const maximum = Number(selectedCapability.duration_max || 15);
    duration.min = String(minimum);
    duration.max = String(maximum);
    const currentDuration = Number(duration.value);
    if (Number.isFinite(currentDuration) && currentDuration < minimum) duration.value = String(minimum);
    if (Number.isFinite(currentDuration) && currentDuration > maximum) duration.value = String(maximum);
  }
}
function persistSelectedProductionVideoPolicy() {
  return persistProductionVideoPolicy({
    provider: document.getElementById('production-video-provider')?.value,
    model: document.getElementById('production-video-model')?.value,
    resolution: document.getElementById('production-video-resolution')?.value,
  });
}
function productionVideoProviderChanged() { updateProductionVideoSelection({resetModel: true}); void persistSelectedProductionVideoPolicy(); }
function productionContinuityChanged(control) {
  const current = productionCurrent();
  if (current) current.production.continuity_enabled = Boolean(control?.checked);
  updateProductionVideoSelection();
  void productionSaveCurrent();
}
function productionVideoModelChanged() { updateProductionVideoSelection(); void persistSelectedProductionVideoPolicy(); }
function productionVideoResolutionChanged() { updateProductionVideoSelection(); void persistSelectedProductionVideoPolicy(); }
async function productionGenerateCurrent() {
  const current = productionCurrent();
  if (!current) return;
  const projectId = appState.projectId;
  const sceneId = productionSceneId;
  const shotId = productionShotId;
  const target = productionGenerationTarget(sceneId, productionSelectedShot(current)?.shot_id || null);
  const active = activeProjectJobs(job => job.job_type === 'video.generate' && String(job.target_id) === String(target.target_id));
  if (active.length) return cancelMatchingJobs(active);
  const saved = await productionSaveCurrent();
  if (!saved) return;
  if (projectId !== appState.projectId || sceneId !== productionSceneId || shotId !== productionShotId) return;
  const batchJobs = productionActiveVideoBatchJobs(activeProjectJobs(job => job.job_type === 'video.generate'));
  if (batchJobs.length && !confirm(`项目级批量生成正在进行（${batchJobs.length} 个任务）。\n\n确定只生成当前${target.target_type === 'shot_unit' ? '子镜头' : '父镜头'}吗？这不会取消或替换项目批量任务。`)) return;
  produceStatus('<span style="color:#93c5fd">正在准备当前镜头的参考图并提交视频...</span>');
  try { await submitProjectJob({job_type:'video.generate', ...target}); }
  catch(error){alert(error.message);}
}
async function productionGeneratePending() {
  const active = activeProjectJobs(job => job.job_type === 'video.generate');
  const activeBatch = productionActiveVideoBatchJobs(active);
  if (activeBatch.length) return cancelMatchingJobs(activeBatch);
  if (active.length && !confirm('当前镜头视频任务正在进行。\n\n确定提交项目级批量任务吗？当前镜头任务不会被取消，但批量任务会跳过已有活动任务。')) return;
   produceStatus('<span style="color:#93c5fd">正在准备项目级批量参考图并生成视频...</span>');
  try { await submitProjectJob({job_type:'video.generate',target_type:'shot',payload:{}}); }
  catch(error){alert(error.message);}
}
async function productionSetAudioMode(mode) {
  if (!['native_dialogue', 'external_tts'].includes(mode)) return;
  try {
    const data = await projectJsonRequest(
      `${API_BASE}/api/projects/${appState.projectId}/production/settings`,
      {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({audio_mode:mode})},
      '保存对白音频来源失败',
    );
    if (!data) return;
    productionData = data;
    appState.audioMode = data.audio_mode || mode;
    renderProductionWorkspace();
    renderProductionAudioTimeline();
    produceStatus(`<span style="color:#93c5fd">对白音频来源已切换为${mode === 'native_dialogue' ? '视频模型原生对白' : '外部 TTS 配音'}，应用于后续生成。</span>`);
  } catch (error) {
    alert(error.message);
    renderProductionWorkspace();
  }
}

async function productionSetSubtitle(enabled) {
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/production/settings`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({subtitle_enabled:Boolean(enabled)})}, '保存字幕设置失败');
    if (!data) return;
    productionData = data;
    renderProductionWorkspace();
  } catch (error) { alert(error.message); }
}
async function productionSetSubtitleMode(mode) {
  if (!['off', 'dialogue', 'speaker', 'narration', 'all'].includes(mode)) return;
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/production/settings`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({subtitle_enabled:mode !== 'off',subtitle_mode:mode})}, '保存字幕模式失败');
    if (!data) return;
    productionData = data;
    renderProductionAudioTimeline();
  } catch (error) { alert(error.message); }
}
async function productionSetAsr(enabled) {
  try {
    const data = await projectJsonRequest(
      `${API_BASE}/api/projects/${appState.projectId}/production/settings`,
      {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({asr_enabled:Boolean(enabled)})},
      '保存 ASR 设置失败',
    );
    if (!data) return;
    productionData = data;
    renderProductionAudioTimeline();
  } catch (error) { alert(error.message); }
}
async function productionSavePromptPolicy() {
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/prompt-policy`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({style_prefix:document.getElementById('production-style-prefix').value,global_negative_prompt:document.getElementById('production-global-negative').value})}, '保存提示词策略失败');
    if (!data) return;
    productionData = data;
    renderProductionWorkspace();
  } catch (error) { alert(error.message); }
}
async function productionReviewPrompts() {
  const active = activeProjectJobs(job => job.job_type === 'prompt.review');
  if (active.length) return cancelMatchingJobs(active);
  try {
    await submitProjectJob({job_type:'prompt.review',target_type:'prompt_set',target_id:'current'});
    if (productionData) {
      productionData.prompt_review = {...(productionData.prompt_review || {}), status:'generating'};
      renderProductionWorkspace();
    }
  } catch (error) {
    alert(error.message);
  }
}
async function productionGenerateAudio() {
  const active = activeProjectJobs(job => job.job_type === 'audio.generate');
  if (active.length) return cancelMatchingJobs(active);
  try { await submitProjectJob({job_type:'audio.generate',target_type:'project'}); produceStatus('<span style="color:#93c5fd">正在生成台词、BGM 和音效...</span>'); }
  catch(error){produceStatus(`<span style="color:#ef4444">${escapeHtml(error.message)}</span>`);}
}
async function productionAssembleVideo() {
  const active = activeProjectJobs(job => job.job_type === 'final.assemble');
  if (active.length) return cancelMatchingJobs(active);
  const audioSummary = productionData?.audio_summary || {};
  const hasDialogue = Number(audioSummary.dialogue_count || 0) > 0;
  const audioIncomplete = hasDialogue && audioSummary.can_assemble === false;
  let payload = {};
  if (audioIncomplete) {
    const unresolved = Number(audioSummary.unresolved_line_count || 0);
    const detail = unresolved ? `\n未解决台词：${unresolved} 条。` : '';
    const confirmed = confirm(`当前对白音频不完整。${detail}\n请先点击“生成缺失台词”修复；如果确认继续，将生成无对白版本。\n\n是否仍生成无对白版本？`);
    if (!confirmed) {
      produceStatus('<span style="color:#fbbf24">已取消合成。请先生成或修复对白音频。</span>');
      return;
    }
    payload = {audio_override: 'no_dialogue_confirmed'};
  }
  try {
    await submitProjectJob({job_type:'final.assemble',target_type:'project',payload});
    produceStatus(payload.audio_override
      ? '<span style="color:#fbbf24">已确认生成无对白版本，正在合成作品...</span>'
      : '<span style="color:#93c5fd">正在合成作品...</span>');
  } catch(error){produceStatus(`<span style="color:#ef4444">${escapeHtml(error.message)}</span>`);}
}
async function productionActivateVersion(versionId) {
  const current = productionCurrent();
  const selectedShot = productionSelectedShot(current);
  if (!current || !productionShotVersions(current, selectedShot).some(item => item.version_id === versionId)) return;
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/production/shots/${productionSceneId}/activate-version`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version_id:versionId, ...(selectedShot?.shot_id ? {shot_id: selectedShot.shot_id} : {})})}, '切换版本失败');
    if (!data) return;
    productionData = data;
    renderProductionWorkspace();
  } catch (error) { alert(error.message); }
}
async function productionRevisePrompt() { const current=productionCurrent(); const selected=productionSelectedShot(current); if (selected?.shot_id) return alert('自动拆镜执行版本只读，请切回父镜头修改源提示词。'); const feedback=document.getElementById('production-feedback')?.value.trim(); if(!feedback)return; try { const data=await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/production/shots/${productionSceneId}/revise-prompt`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({feedback})},'AI 修改失败'); if(!data)return; productionData=data; renderProductionWorkspace(); } catch(error){alert(error.message);} }

function initializeStudio() {
setupProductionWorkspace();
productionInstallMentionPicker();

// 初始化全局设置与工作台
onProviderChange('llm');
onProviderChange('video');
onProviderChange('image');
document.querySelectorAll('[data-secret-field]').forEach(input => {
  input.addEventListener('input', () => {
    if (input.value) clearedSecretFields.delete(input.dataset.secretField);
    syncSecretInput(input);
  });
});
loadProfileList();

console.log('AI 短剧工作台已初始化');
console.log('API 地址:', API_BASE);
console.log('提示词模板数量:', Object.keys(PROMPT_TEMPLATES).length);
}
const __moduleActions = {
  initializeStudio,
  captureProductionEditorState,
  restoreProductionEditorState,
  patchProductionJobState,
  patchProductionWorkspace,
  loadProductionWorkspace,
  productionReferenceHtml,
  productionPromptText,
  productionSetPromptText,
  productionPromptEditorHtml,
  productionRememberPromptSelection,
  productionInsertReferenceToken,
  productionMentionEntityType,
  productionMentionReferenceId,
  productionMentionLabel,
  promptReviewHtml,
  promptReviewReport,
  promptReviewIssues,
  promptReviewIssueGroup,
  promptReviewShotStatusLabel,
  productionTimelineEntries,
  productionSelection,
  productionGenerationTarget,
  productionActiveVideoBatchJobs,
  productionCurrentVideoButtonLabel,
  productionTimelineHtml,
  storyboardQualityDiagnosticsHtml,
  productionFocusReviewRepair,
  productionAudioModeHtml,
  productionSetAudioMode,
  renderProductionWorkspace,
  productionSaveCurrent,
  productionMentionToken,
  productionMentionOptions,
  productionMentionSuggestions,
  productionOpenMentionPicker,
  productionOpenMentionPickerForChip,
  productionMentionSearch,
  productionCloseMentionPicker,
  productionInstallMentionPicker,
  productionMentionKeydown,
  productionApplyMentionSuggestion,
  productionInsertMention,
  syncProductionMentionBindings,
  productionLoadReferenceHistory,
  productionApplyReferenceVersion,
  productionAddReference,
  productionRemoveReference,
  productionDragStart,
  productionDropReference,
  productionSelectScene,
  productionNavigateAdjacent,
  productionSetInspector,
  productionVideoProviderChanged,
  productionContinuityChanged,
  productionVideoModelChanged,
  productionVideoResolutionChanged,
  productionGenerateCurrent,
  productionGeneratePending,
  productionSetSubtitle,
  productionSetSubtitleMode,
  productionSetAsr,
  productionSavePromptPolicy,
  productionOpenPromptPolicy,
  productionReviewPrompts,
  productionGenerateAudio,
  productionAssembleVideo,
  productionActivateVersion,
  productionRevisePrompt,
};
Object.assign(globalThis.StudioActions, __moduleActions);
export { __moduleActions };
