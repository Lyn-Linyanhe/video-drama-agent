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

function productionPromptEditor() {
  return document.getElementById('production-prompt');
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
  const renderedSceneId = root.dataset.productionEditorSceneId || String(productionSceneId || '');
  return {
    sceneId: renderedSceneId, values,
    policyOpen: root.querySelector('details.production-panel')?.open || false,
    pageScrollY: window.scrollY,
  };
}

function restoreProductionEditorState(root, snapshot) {
  const renderedSceneId = root.dataset.productionEditorSceneId || String(productionSceneId || '');
  if (!snapshot || snapshot.sceneId !== renderedSceneId) return;
  const details = root.querySelector('details.production-panel');
  if (details) details.open = snapshot.policyOpen;
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

function patchProductionJobState(job) {
  const root = document.getElementById('shot-production-workspace');
  if (!root || !productionData) return;
  const current = productionCurrent();
  const activeVideos = activeProjectJobs(item => item.job_type === 'video.generate');
  const currentVideoJob = activeVideos.find(item => item.target_id === String(productionSceneId));
  const pendingButton = document.getElementById('production-generate-pending');
  const currentButton = document.getElementById('production-generate-current');
  const audioButton = document.getElementById('btn-gen-audio');
  const finalButton = document.getElementById('btn-assemble');
  if (pendingButton) pendingButton.textContent = activeVideos.length ? '终止本批次' : '一键准备参考图并生成视频';
  if (currentButton && current) currentButton.textContent = currentVideoJob ? '终止' : (current.production.generation_status === 'failed' || current.production.generation_status === 'canceled' ? '重试生成' : '生成视频');
  const audioSummary = productionData?.audio_summary || {};
  if (audioButton) audioButton.textContent = activeProjectJobs(item => item.job_type === 'audio.generate').length
    ? '终止台词生成'
    : (audioSummary.failed_line_count ? '重试失败台词' : '生成缺失台词');
  if (finalButton) finalButton.textContent = activeProjectJobs(item => item.job_type === 'final.assemble').length ? '终止合成' : '合成作品';
  if (job?.target_type === 'shot') {
    const timeline = root.querySelector(`[data-production-scene-id="${CSS.escape(String(job.target_id))}"] .timeline-status`);
    if (timeline) timeline.textContent = isTerminalJob(job) ? productionStatusLabel(productionData.productions.find(item => String(item.scene.scene_id) === String(job.target_id))?.production?.generation_status) : (JOB_STATE_LABELS[job.state] || job.state);
  }
}

function patchProductionWorkspace(previousData) {
  const current = productionCurrent();
  const previous = (previousData?.productions || []).find(item => item.scene.scene_id === productionSceneId);
  if (!current) { renderProductionWorkspace(); return; }
  const previousVersion = previous?.production?.active_version_id || '';
  const activeVersion = current.production.active_version_id || '';
  const status = document.getElementById('production-current-status');
  if (status) status.textContent = productionStatusLabel(current.production.generation_status);
  const refreshPreview = previousVersion !== activeVersion || (
    !activeVersion && previous?.production?.generation_status !== current.production.generation_status
  );
  if (refreshPreview) {
    const stage = document.getElementById('production-preview-stage');
    const versions = document.getElementById('production-version-list');
    const download = document.getElementById('production-preview-download');
    if (stage) stage.innerHTML = productionPreviewHtml(current);
    if (versions) versions.innerHTML = productionVersionListHtml(current);
    if (download) {
      const active = current.production.video_versions.find(item => item.version_id === activeVersion);
      const url = productionVideoUrl(active);
      download.innerHTML = url ? `<a class="btn-secondary btn-sm" href="${url}" download>下载</a>` : '';
    }
  }
  if (JSON.stringify(previousData?.audio || {}) !== JSON.stringify(productionData?.audio || {})) {
    renderProductionAudioTimeline();
  }
  (productionData.productions || []).forEach(item => {
    const button = document.querySelector(`#shot-production-workspace [data-production-scene-id="${CSS.escape(String(item.scene.scene_id))}"]`);
    if (!button) return;
    const version = item.production.video_versions.find(entry => entry.version_id === item.production.active_version_id);
    const url = productionVideoUrl(version);
    const oldVideo = button.querySelector('video');
    if (url && (!oldVideo || oldVideo.src !== new URL(url, window.location.href).href)) {
      const replacement = document.createElement('video');
      replacement.muted = true;
      replacement.preload = 'metadata';
      replacement.src = url;
      (oldVideo || button.querySelector('img, div')).replaceWith(replacement);
    }
    const label = button.querySelector('.timeline-status');
    if (label) label.textContent = productionStatusLabel(item.production.generation_status);
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
    }
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

function promptReviewHtml(review) {
  const status = review?.status || 'pending';
  const labels = {
    pending: '待审核',
    generating: 'Kimi 审核中',
    ready: '检验通过',
    stale: '报告已过期',
    failed: '审核失败',
    unavailable: '未配置 Kimi',
  };
  const report = review?.report || {};
  const score = Number.isFinite(Number(report.overall_score)) ? Number(report.overall_score) : null;
  const color = status === 'ready' && report.passed
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
  const issues = report.issues || [];
  return `<section class="production-panel" style="margin:0 0 10px;padding:12px">
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <div><b style="font-size:13px">Kimi 分镜提示词检验</b><span style="margin-left:8px;color:${color};font-size:12px">${escapeHtml(labels[status] || status)}${score !== null ? ` · ${score} 分` : ''}</span></div>
      <button class="btn-secondary btn-sm" onclick="StudioActions.productionReviewPrompts()" ${status === 'generating' ? 'disabled' : ''}>${status === 'generating' ? '审核中' : '重新检验'}</button>
      ${review?.model ? `<span style="color:#71717a;font-size:11px">${escapeHtml(review.model)}</span>` : ''}
    </div>
    <div style="margin-top:7px;color:#d4d4d8;font-size:12px;line-height:1.55">${escapeHtml(summary)}</div>
    ${issues.length ? `<div style="display:grid;gap:6px;margin-top:10px">${issues.slice(0, 3).map(item => `<div style="padding:8px;border-left:3px solid ${item.severity === 'critical' ? '#ef4444' : item.severity === 'major' ? '#f59e0b' : '#60a5fa'};background:#202024;font-size:11px"><b>镜头 ${escapeHtml(String(item.scene_id || '全片'))} · ${escapeHtml(item.severity)}</b><div style="margin-top:3px">${escapeHtml(item.description)}</div><div style="margin-top:3px;color:#a1a1aa">${escapeHtml(item.suggestion)}</div></div>`).join('')}</div>` : ''}
  </section>`;
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

function productionOpenPromptPolicy() {
  const slot = document.getElementById('production-prompt-policy-slot');
  if (!slot) return;
  slot.innerHTML = promptPolicyEditorHtml(productionData?.prompt_policy || {}, true);
  slot.querySelector('#production-style-prefix')?.focus();
}

function renderProductionWorkspace() {
  const root = document.getElementById('shot-production-workspace');
  if (!root) return;
  if (!appState.projectId) { root.innerHTML = '<div class="production-empty">请先完成剧本解析，再进入分镜制作。</div>'; return; }
  if (!productionData) { root.innerHTML = '<div class="production-empty"><span class="spin"></span> 正在加载分镜制作工作台</div>'; loadProductionWorkspace(); return; }
  const current = productionCurrent();
  if (!current) { root.innerHTML = '<div class="production-empty">暂无分镜数据</div>'; return; }
  const {scene, prompt, production} = current;
  const effective = current.effective_prompt || {};
  const promptQuality = productionData.prompt_quality || {};
  const promptReview = productionData.prompt_review || {};
  const currentPromptReview = (promptReview.report?.shots || []).find(item => Number(item.scene_id) === Number(scene.scene_id));
  const currentPromptIssues = [
    ...(currentPromptReview?.issues || []),
    ...(promptReview.report?.issues || []).filter(item => Number(item.scene_id) === Number(scene.scene_id)),
  ];
  const currentPromptReviewHtml = currentPromptReview
    ? `<div style="margin-top:8px;padding:8px;border:1px solid ${currentPromptReview.passed ? '#166534' : '#854d0e'};background:#18181b;font-size:11px;line-height:1.55"><b style="color:${currentPromptReview.passed ? '#86efac' : '#fbbf24'}">Kimi 检验：${escapeHtml(String(currentPromptReview.score))} 分 · ${currentPromptReview.passed ? '通过' : '需修改'}</b>${currentPromptReview.summary ? `<div style="margin-top:3px;color:#d4d4d8">${escapeHtml(currentPromptReview.summary)}</div>` : ''}${currentPromptIssues.length ? `<div style="display:grid;gap:4px;margin-top:6px">${currentPromptIssues.map(item => `<div><span style="color:${item.severity === 'critical' ? '#fca5a5' : '#fbbf24'}">${escapeHtml(item.severity)}</span> · ${escapeHtml(item.description)}<br><span style="color:#a1a1aa">${escapeHtml(item.suggestion)}</span></div>`).join('')}</div>` : ''}</div>`
    : '';
  const providerPrompt = effective.provider_prompt || effective.effective_prompt || prompt.prompt || '';
  const providerNegative = effective.provider_negative_prompt || '';
  const negativeMode = effective.negative_prompt_mode || 'merged';
  const finalPromptPreview = negativeMode === 'separate'
    ? `<div style="font-size:11px;color:#a1a1aa;margin-bottom:4px">主提示词</div>${escapeHtml(providerPrompt)}<div style="font-size:11px;color:#a1a1aa;margin:10px 0 4px">独立负面提示词参数</div>${escapeHtml(providerNegative || '无')}`
    : `<div style="font-size:11px;color:#a1a1aa;margin-bottom:4px">主提示词（已合并负面约束）</div>${escapeHtml(providerPrompt)}`;
  const promptPolicy = productionData.prompt_policy || {};
  const activeVideoJobs = activeProjectJobs(job => job.job_type === 'video.generate');
  const currentVideoJob = activeVideoJobs.find(job => job.target_id === String(scene.scene_id));
  const activeVideoBatch = activeVideoJobs.find(job => job.batch_id)?.batch_id || '';
  const batchJobs = activeVideoBatch ? projectJobs.filter(job => job.batch_id === activeVideoBatch) : [];
  const batchCompleted = batchJobs.filter(job => ['succeeded','failed','canceled','interrupted'].includes(job.state)).length;
  const activeAudioJob = activeProjectJobs(job => job.job_type === 'audio.generate')[0];
  const audioSummary = productionData.audio_summary || {};
  const activeFinalJob = activeProjectJobs(job => job.job_type === 'final.assemble')[0];
  const active = production.video_versions.find(item => item.version_id === production.active_version_id);
  const activeVideo = productionVideoUrl(active);
  const videoPolicy = current.video_policy || productionData.video_generation_policy || {provider:'mock',model:'mock',resolution:'1080p'};
  const videoCapabilities = productionData.video_capabilities || {};
  const currentCapability = current.video_capability || videoCapabilities[videoPolicy.provider]?.models?.[videoPolicy.model] || {};
  const durationMin = Number(currentCapability.duration_min || 3);
  const durationMax = Number(currentCapability.duration_max || 15);
  const maxReferenceImages = Number(currentCapability.max_reference_images || 0);
  const legacyReferenceWarning = videoPolicy.provider === 'seedance' && production.continuity_enabled && production.references.length > 1
    ? '<div class="production-reference-warning" style="margin-top:6px;color:#fbbf24;font-size:11px">连续镜头使用首帧模式，只会提交上一镜尾帧；其他普通参考图不会随请求发送。</div>'
    : videoPolicy.provider === 'seedance' && production.references.length > maxReferenceImages && maxReferenceImages > 0
      ? `<div class="production-reference-warning" style="margin-top:6px;color:#fbbf24;font-size:11px">当前模型最多接受 ${maxReferenceImages} 张参考图，超出的图片提交时会被忽略，任务详情会列出具体原因。</div>`
      : '';
  const providerOptions = Object.entries(videoCapabilities).map(([key, value]) => `<option value="${key}" ${value.available === false ? 'disabled' : ''} ${key === videoPolicy.provider ? 'selected' : ''}>${key === 'wan' ? '万相 2.6' : key === 'happyhorse' ? '快乐小马' : 'Mock'}</option>`).join('');
  const referenceModes = Array.isArray(currentCapability.reference_modes) ? currentCapability.reference_modes : [];
  const modeLabels = {text_to_video:'纯文生视频', first_frame:'首帧', first_last_frame:'首尾帧', multi_reference:'多参考图'};
  const referenceModeHint = referenceModes.length
    ? referenceModes.map(mode => modeLabels[mode] || mode).join(' / ')
    : (maxReferenceImages > 0 ? '单参考图' : '不支持参考图');
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
  const providerAcceptsReferences = currentCapability.image_to_video !== false;
  const referenceLimit = maxReferenceImages > 0
    ? maxReferenceImages
    : (providerAcceptsReferences ? referenceCount : 0);
  const submittedReferenceCount = continuityConsumesReferences
    ? (production.continuity_enabled && providerAcceptsReferences ? Math.min(1, referenceLimit) : 0)
    : Math.min(referenceCount, referenceLimit);
  const referenceSummaryParts = [
    automaticReferenceCount ? `自动 ${automaticReferenceCount}` : '',
    manualReferenceCount ? `手动 ${manualReferenceCount}` : '',
    explicitReferenceCount ? `显式 ${explicitReferenceCount}` : '',
    referenceCount ? `提交 ${submittedReferenceCount}/${referenceCount}` : '未绑定参考图',
  ].filter(Boolean);
  const referenceWarning = continuityWarning || legacyReferenceWarning || (referenceCount > maxReferenceImages
    ? `<div class="production-reference-warning" style="margin-top:6px;color:#fbbf24;font-size:11px">当前模型最多接受 ${maxReferenceImages} 张参考图，超出的图片不会发送；任务详情会列出被忽略的图片。</div>`
    : '');
  const referenceManagerOpen = Boolean(referenceWarning || production.references.some(item => item.stale));
  const modelOptions = Object.keys(videoCapabilities[videoPolicy.provider]?.models || {}).map(model => `<option value="${model}" ${model === videoPolicy.model ? 'selected' : ''}>${model}</option>`).join('');
  const resolutionOptions = (current.video_capability?.resolutions || ['720p','1080p']).map(value => `<option value="${value}" ${value === videoPolicy.resolution ? 'selected' : ''}>${value}</option>`).join('');
  const continuityHint = production.continuity_enabled ? (production.continuity_stale ? '上一镜已变化，需重新生成' : (production.continuity_tail_frame_path ? `引用镜头 ${production.continuity_source_scene_id} 尾帧` : '生成时引用上一镜尾帧')) : '独立镜头';
  const catalog = productionData.asset_catalog || {characters:[], scenes:[], props:[]};
  const assetGrid = (assets, empty) => assets.length ? `<div class="asset-grid">${assets.map(item => `<button class="asset-item" onclick="StudioActions.productionAddReference('${StudioActions.escapeHtml(item.reference_id)}')"><img src="${productionImageUrl(item)}" alt=""><span>${escapeHtml(item.label)}</span></button>`).join('')}</div>` : `<div style="font-size:11px;color:#71717a">${empty}</div>`;
  const preview = activeVideo ? `<video controls src="${activeVideo}"></video>` : `<div><div style="font-size:28px;margin-bottom:8px">${production.generation_status === 'generating' ? '◌' : '▶'}</div><div>${productionStatusLabel(production.generation_status)}</div>${production.error_message ? `<div style="font-size:11px;color:#fca5a5;margin-top:8px">${escapeHtml(production.error_message)}</div>` : ''}</div>`;
  const editorSnapshot = captureProductionEditorState(root);
  root.innerHTML = `
    <div class="production-toolbar production-workspace-toolbar">
      <div class="production-toolbar-title"><span class="production-eyebrow">分镜工作台</span><h3>分镜制作</h3><span id="production-global-status" class="production-global-status"></span></div>
      <div class="production-toolbar-actions"><button class="btn-primary btn-sm" id="production-generate-pending" onclick="StudioActions.productionGeneratePending()">${activeVideoJobs.length ? `终止本批次 ${batchCompleted}/${batchJobs.length || activeVideoJobs.length}` : '一键准备参考图并生成视频'}</button><button class="btn-secondary btn-sm" id="btn-gen-audio" onclick="StudioActions.productionGenerateAudio()">${activeAudioJob ? '终止台词生成' : (audioSummary.failed_line_count ? '重试失败台词' : '生成缺失台词')}</button><button class="btn-secondary btn-sm" id="btn-assemble" onclick="StudioActions.productionAssembleVideo()">${activeFinalJob ? '终止合成' : '合成作品'}</button></div>
    </div>
    ${promptQuality.needs_refresh ? '<div class="production-panel" style="margin:0 0 10px;padding:10px;color:#fbbf24;font-size:12px">当前项目含旧版非中文分镜提示词。已有视频与手动编辑将保留；确认需要更新时，请重新解析剧本以生成新版中文分镜。</div>' : ''}
    ${promptReviewHtml(promptReview)}
    ${promptPolicySummaryHtml(promptPolicy)}
    <div class="production-shell">
      <aside class="production-panel production-assets">
        <div class="production-panel-heading"><div><span class="production-eyebrow">参考素材</span><h4>资产库</h4></div><span class="production-count">${catalog.characters.length + catalog.scenes.length + catalog.props.length}</span></div>
        <section class="production-asset-group"><h5>角色形象</h5>${assetGrid(catalog.characters, '请先生成角色形象图')}</section>
        <section class="production-asset-group"><h5>场景参考</h5>${assetGrid(catalog.scenes, '请先生成场景参考图')}</section>
        <section class="production-asset-group"><h5>道具参考</h5>${assetGrid(catalog.props, '当前镜头没有可用关键道具图')}</section>
        <p class="production-help-text">点击资产即可加入当前镜头；已加入的参考图可在中间区域排序或移除。</p>
      </aside>
      <main class="production-panel production-editor">
        <header class="production-editor-header">
          <div class="production-shot-title"><span class="production-eyebrow">当前镜头</span><h3>镜头 ${scene.scene_id}</h3><p>${escapeHtml(scene.location)} <span>·</span> 可见角色：${escapeHtml((scene.characters || []).join('、') || '无')}</p></div>
          <div class="production-meta"><label class="production-chip production-duration-chip"><span>时长</span><input id="production-duration" type="number" min="3" max="30" value="${prompt.duration_sec}"><span class="production-unit">秒</span></label><span class="production-chip production-status-chip">${productionStatusLabel(production.generation_status)}</span></div>
        </header>
        <section class="production-editor-section production-reference-section">
          <div class="production-section-heading"><div><span class="production-eyebrow">01 · 参考输入</span><h4>参考素材</h4></div><span class="production-section-note">${escapeHtml(referenceSummaryParts.join(' · '))}</span></div>
          <div id="production-reference-capability" class="production-reference-summary"><span>${escapeHtml(referenceSummaryParts.join(' · '))}</span><span>最多 ${maxReferenceImages} 张 <b>·</b> ${escapeHtml(referenceModeHint)}</span></div>
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
          <div class="production-section-heading"><div><span class="production-eyebrow">02 · 提示词</span><h4>生成提示词</h4></div><span class="production-section-note">可直接编辑</span></div>
          <div class="production-prompt-field"><label class="production-field-label" for="production-prompt">主提示词</label><div class="production-textarea-wrap"><div id="production-prompt" class="production-script production-prompt-editor" contenteditable="true" role="textbox" aria-multiline="true" data-placeholder="视频生成提示词" spellcheck="false" oninput="StudioActions.productionMentionKeydown(event)" onkeydown="StudioActions.productionMentionKeydown(event)" onkeyup="StudioActions.productionMentionKeydown(event)" onmouseup="StudioActions.productionRememberPromptSelection(event)" onblur="StudioActions.productionRememberPromptSelection(event)">${productionPromptEditorHtml(prompt.prompt)}</div><textarea id="production-prompt-value" hidden>${escapeHtml(prompt.prompt)}</textarea><div id="production-mention-suggestions" class="production-mention-suggestions" hidden></div></div></div>
          <div class="production-prompt-field production-negative-field"><label class="production-field-label" for="production-negative">负面约束 <span>可选</span></label><textarea id="production-negative" class="production-script" placeholder="填写需要避免的画面问题">${escapeHtml(prompt.negative_prompt || '')}</textarea></div>
          <details class="production-provider-preview"><summary>查看最终发送内容 <span>${escapeHtml(effective.provider || '')} · ${negativeMode === 'merged' ? '负面约束已合并' : '独立负面参数'}</span></summary><div class="production-provider-preview-body">${finalPromptPreview}</div></details>
          ${currentPromptReviewHtml}
        </section>
        <section class="production-editor-section production-generation-section">
          <div class="production-section-heading"><div><span class="production-eyebrow">03 · 生成设置</span><h4>生成设置</h4></div><span class="production-section-note">提交前可调整</span></div>
          <div class="production-settings-grid"><label class="production-field-label">视频服务<select id="production-video-provider" onchange="StudioActions.productionVideoProviderChanged()">${providerOptions}</select></label><label class="production-field-label">模型<select id="production-video-model" onchange="StudioActions.productionVideoModelChanged()">${modelOptions}</select></label><label class="production-field-label">清晰度<select id="production-video-resolution">${resolutionOptions}</select></label></div>
          <div class="production-capability-note">成本档位：${escapeHtml(current.video_capability?.cost_tier || '未标注')}</div>
          <div class="production-continuity-row"><label class="production-toggle"><input id="production-continuity" type="checkbox" ${production.continuity_enabled ? 'checked' : ''}><span>承接上一镜尾帧</span></label><span class="production-continuity-state ${production.continuity_stale ? 'is-warning' : ''}">${escapeHtml(continuityHint)}</span><span class="production-chip production-hard-cut-chip">新任务采用硬切</span></div>
        </section>
        <section class="production-editor-section production-ai-section">
          <div class="production-section-heading"><div><span class="production-eyebrow">04 · 辅助修改</span><h4>AI 修改提示词</h4></div><span class="production-section-note">用自然语言描述修改方向</span></div>
          <textarea id="production-feedback" class="production-feedback" placeholder="例如：让镜头更近一些，保留人物动作和场景不变"></textarea>
          <button class="btn-secondary production-ai-button" onclick="StudioActions.productionRevisePrompt()">AI 修改提示词</button>
        </section>
        <footer class="production-editor-footer-actions production-actions"><div class="production-save-hint">保存后再提交生成，当前镜头的修改会单独记录。</div><div class="production-footer-buttons"><button class="btn-secondary" onclick="StudioActions.productionSaveCurrent()">保存编辑</button><button class="btn-primary" id="production-generate-current" onclick="StudioActions.productionGenerateCurrent()">${currentVideoJob ? '终止' : production.generation_status === 'failed' || production.generation_status === 'canceled' ? '重试生成' : '生成视频'}</button></div></footer>
      </main>
      <aside class="production-panel production-preview"><div class="production-preview-header"><div><span class="production-eyebrow">输出预览</span><h4>视频预览</h4></div><span id="production-preview-download">${activeVideo ? `<a class="btn-secondary btn-sm" href="${activeVideo}" download>下载</a>` : ''}</span></div><div class="preview-stage">${preview}</div><section class="production-version-section"><div class="production-section-heading"><h5>历史版本</h5><span class="production-section-note">点击切换</span></div><div id="production-version-list" class="version-list">${production.video_versions.map((item,index) => `<button class="version-item ${item.version_id === production.active_version_id ? 'active' : ''}" onclick="StudioActions.productionActivateVersion('${item.version_id}')"><video muted preload="metadata" src="${productionVideoUrl(item)}"></video><span>版本 ${index + 1}</span></button>`).join('') || '<span class="production-empty-inline">暂无生成版本</span>'}</div></section></aside>
    </div>
    <section id="production-audio-workspace" class="production-panel production-audio-workspace"></section>
    <div class="production-panel production-timeline">${productionData.productions.map((item,index) => { const version=item.production.video_versions.find(v=>v.version_id===item.production.active_version_id); const video=productionVideoUrl(version); const fallback=item.production.references.find(r=>r.asset_type==='scene') || item.production.references[0]; return `<button class="timeline-shot ${item.scene.scene_id===productionSceneId?'active':''}" onclick="StudioActions.productionSelectScene(${item.scene.scene_id})"><span class="timeline-status">${productionStatusLabel(item.production.generation_status)}</span>${video ? `<video muted preload="metadata" src="${video}"></video>` : fallback ? `<img src="${productionImageUrl(fallback)}" alt="">` : '<div style="height:72px;display:grid;place-items:center">◌</div>'}<span>镜头 ${index+1} · ${item.prompt.duration_sec}s</span></button>`; }).join('')}</div>`;
  root.dataset.productionEditorSceneId = String(scene.scene_id);
  root.querySelector('.production-toolbar .btn-primary')?.setAttribute('id', 'production-generate-pending');
  root.querySelector('#production-video-provider option[value="seedance"]')?.replaceChildren(document.createTextNode('Seedance 火山方舟'));
  root.querySelector('.production-meta .production-chip:last-child')?.setAttribute('id', 'production-current-status');
  root.querySelector('.production-actions .btn-primary')?.setAttribute('id', 'production-generate-current');
  root.querySelector('.preview-stage')?.setAttribute('id', 'production-preview-stage');
  root.querySelector('.version-list')?.setAttribute('id', 'production-version-list');
  root.querySelectorAll('.timeline-shot').forEach((button, index) => {
    const item = productionData.productions[index];
    if (item) button.dataset.productionSceneId = String(item.scene.scene_id);
  });
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
}

async function productionSaveCurrent() {
  const current = productionCurrent(); if (!current) return false;
  syncProductionMentionBindings(current);
  const payload = {prompt: productionSyncPromptValue(), negative_prompt: document.getElementById('production-negative').value, duration_sec: Number(document.getElementById('production-duration').value), references: current.production.references.map(item => ({reference_id:item.reference_id,source:item.source || 'asset',reference_token:item.reference_token || '',token:item.reference_token || '',version_id:item.asset_version_id || '',explicit_base_source:item.source === 'explicit' ? (item.explicit_base_source || '') : ''})), video_override:{provider:document.getElementById('production-video-provider').value,model:document.getElementById('production-video-model').value,resolution:document.getElementById('production-video-resolution').value}, continuity_enabled:document.getElementById('production-continuity').checked};
  try {
     const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/production/shots/${productionSceneId}`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}, '保存失败');
    if (!data) return false;
    productionData = data; renderProductionWorkspace();
    return true;
  } catch (error) { alert(error.message); return false; }
}

async function productionAddReference(referenceId) { const current=productionCurrent(); if (!current || current.production.references.some(item=>item.reference_id===referenceId)) return; const reference=(productionData.asset_catalog.characters || []).concat(productionData.asset_catalog.scenes || [], productionData.asset_catalog.props || []).find(item=>item.reference_id===referenceId); if (!reference) return; current.production.references.push({...reference,source:'manual'}); await productionSaveCurrent(); }
async function productionRemoveReference(index) { const current=productionCurrent(); if (!current) return; current.production.references.splice(index,1); await productionSaveCurrent(); }
function productionDragStart(index) { productionDragIndex=index; }
async function productionDropReference(index) { const current=productionCurrent(); if (!current || productionDragIndex===null || productionDragIndex===index) return; const [item]=current.production.references.splice(productionDragIndex,1); current.production.references.splice(index,0,item); productionDragIndex=null; await productionSaveCurrent(); }
function productionSelectScene(sceneId) { productionSceneId=sceneId; renderProductionWorkspace(); }
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
  const hint = document.querySelector('#shot-production-workspace .production-reference-capability');
  if (hint) {
    const limit = Number(selectedCapability.max_reference_images || 0);
     const labels = {text_to_video:'纯文生视频', first_frame:'首帧参考', first_last_frame:'首尾帧', multi_reference:'多参考图'};
    const modes = Array.isArray(selectedCapability.reference_modes) ? selectedCapability.reference_modes : [];
     hint.textContent = `最多 ${limit} 张参考图 · ${modes.map(mode => labels[mode] || mode).join(' / ') || (limit > 0 ? '支持参考图' : '不支持参考图')}`;
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
function productionVideoProviderChanged() { updateProductionVideoSelection({resetModel: true}); }
function productionVideoModelChanged() { updateProductionVideoSelection(); }
async function productionGenerateCurrent() {
  const active = activeProjectJobs(job => job.job_type === 'video.generate' && job.target_id === String(productionSceneId));
  if (active.length) return cancelMatchingJobs(active);
  const saved = await productionSaveCurrent();
  if (!saved) return;
   produceStatus('<span style="color:#93c5fd">正在准备参考图并提交视频...</span>');
  try { await submitProjectJob({job_type:'video.generate',target_type:'shot',target_id:String(productionSceneId),payload:{scene_id:productionSceneId}}); }
  catch(error){alert(error.message);}
}
async function productionGeneratePending() {
  const active = activeProjectJobs(job => job.job_type === 'video.generate');
  if (active.length) return cancelMatchingJobs(active);
   produceStatus('<span style="color:#93c5fd">正在批量准备参考图并生成视频...</span>');
  try { await submitProjectJob({job_type:'video.generate',target_type:'shot',payload:{}}); }
  catch(error){alert(error.message);}
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
  if (!['off', 'dialogue', 'speaker'].includes(mode)) return;
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
  try { await submitProjectJob({job_type:'final.assemble',target_type:'project'}); produceStatus('<span style="color:#93c5fd">正在合成作品...</span>'); }
  catch(error){produceStatus(`<span style="color:#ef4444">${escapeHtml(error.message)}</span>`);}
}
async function productionActivateVersion(versionId) { try { const data=await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/production/shots/${productionSceneId}/activate-version`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version_id:versionId})},'切换版本失败'); if(!data)return; productionData=data; renderProductionWorkspace(); } catch(error){alert(error.message);} }
async function productionRevisePrompt() { const feedback=document.getElementById('production-feedback').value.trim(); if(!feedback)return; try { const data=await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/production/shots/${productionSceneId}/revise-prompt`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({feedback})},'AI 修改失败'); if(!data)return; productionData=data; renderProductionWorkspace(); } catch(error){alert(error.message);} }

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
  productionVideoProviderChanged,
  productionVideoModelChanged,
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
