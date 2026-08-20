const activeProjectJobs = (...args) => globalThis.StudioActions.activeProjectJobs(...args);
const cancelMatchingJobs = (...args) => globalThis.StudioActions.cancelMatchingJobs(...args);
const escapeHtml = (...args) => globalThis.StudioActions.escapeHtml(...args);
const isCurrentProjectSession = (...args) => globalThis.StudioActions.isCurrentProjectSession(...args);
const mediaUrl = (...args) => globalThis.StudioActions.mediaUrl(...args);
const isMissingMediaPath = (...args) => globalThis.StudioActions.isMissingMediaPath(...args);
const projectSessionSnapshot = (...args) => globalThis.StudioActions.projectSessionSnapshot(...args);
const releaseSessionController = (...args) => globalThis.StudioActions.releaseSessionController(...args);
const renderProps = (...args) => globalThis.StudioActions.renderProps(...args);
const renderRoles = (...args) => globalThis.StudioActions.renderRoles(...args);
const renderScenes = (...args) => globalThis.StudioActions.renderScenes(...args);
const sessionController = (...args) => globalThis.StudioActions.sessionController(...args);
const submitProjectJob = (...args) => globalThis.StudioActions.submitProjectJob(...args);
const updateJobDrivenControls = (...args) => globalThis.StudioActions.updateJobDrivenControls(...args);

let imageCapabilities = null;
let imagePolicy = {provider: 'mock', model: 'mock', resolution: '1K'};
let savedImagePolicy = null;
let imagePolicyProjectId = '';

function imagePolicyPayload() {
  return {provider: imagePolicy.provider, model: imagePolicy.model, resolution: imagePolicy.resolution};
}

function hasCharacterReferenceMaster() {
  if (activeImageEditor?.assetType !== 'character') return false;
  return (appState.characterImages || []).some(item => (
    item.character_name === activeImageEditor.assetId
    && item.view_type === 'front_half'
    && String(item.model || '').startsWith('wan2.7-image')
    && item.version_id
    && item.image_path
    && !isMissingMediaPath(item.image_path)
  ));
}

function hasSceneReferenceMaster() {
  if (activeImageEditor?.assetType !== 'scene') return false;
  return (appState.sceneImages || []).some(item => {
    const itemId = String(item.location_id || item.scene_id || '');
    return itemId === activeImageEditor.assetId
      && String(item.state_id || '') === (activeImageEditor.stateId || '')
      && item.view_type === 'scene_center'
      && item.is_master
      && !item.needs_confirmation
      && item.version_id
      && item.image_path
      && !isMissingMediaPath(item.image_path);
  });
}

function imageEditorHasCurrentImage() {
  const entry = imageEditorEntry();
  return Boolean(imageEditorImageSource(entry));
}

function imageEditorGenerationError() {
  if (!activeImageEditor) return '';
  const baseKey = `${activeImageEditor.assetType}:${activeImageEditor.assetId}`;
  if (activeImageEditor.assetType !== 'scene' || !activeImageEditor.stateId) {
    return appState.assetErrors?.[baseKey] || '';
  }
  return appState.assetErrors?.[`${baseKey}:${activeImageEditor.stateId}`]
    || appState.assetErrors?.[baseKey]
    || '';
}

async function projectImagePolicyPayload() {
  const projectId = appState.projectId;
  if (!projectId) return {};
  if (imagePolicyProjectId === projectId && savedImagePolicy) return {...savedImagePolicy};
  const response = await fetch(`${API_BASE}/api/projects/${projectId}/image-generation-policy`);
  if (!response.ok) throw new Error(await imageEditorResponseError(response, '读取项目图片模型设置失败'));
  const payload = await response.json();
  if (appState.projectId !== projectId) return {};
  imagePolicyProjectId = projectId;
  savedImagePolicy = payload.policy;
  return {...savedImagePolicy};
}

function renderImagePolicyControls() {
  const provider = document.getElementById('image-editor-provider');
  const model = document.getElementById('image-editor-model');
  const resolution = document.getElementById('image-editor-resolution');
  const note = document.getElementById('image-generation-policy-note');
  if (!provider || !imageCapabilities) return;
  provider.innerHTML = Object.entries(imageCapabilities).map(([key, value]) =>
    `<option value="${key}" ${value.available === false ? 'disabled' : ''}>${key === 'wan' ? '万相 2.7' : key === 'kling' ? '可灵 Kling' : 'Mock'}${value.available === false ? '（不可用）' : ''}</option>`
  ).join('');
  if (!imageCapabilities[imagePolicy.provider] || imageCapabilities[imagePolicy.provider].available === false) imagePolicy.provider = 'mock';
  provider.value = imagePolicy.provider;
  const models = imageCapabilities[imagePolicy.provider].models || {};
  model.innerHTML = Object.keys(models).map(key => `<option value="${key}">${key}</option>`).join('');
  if (!models[imagePolicy.model]) imagePolicy.model = Object.keys(models)[0];
  model.value = imagePolicy.model;
  const capabilities = models[imagePolicy.model];
  const canReferenceEdit = capabilities?.reference_edit && (
    activeImageEditor?.assetType === 'character'
    || (activeImageEditor?.assetType === 'scene' && activeImageEditor?.viewType !== 'scene_center')
  );
  const mode = canReferenceEdit ? 'reference_edit' : 'single';
  const allowed = mode === 'reference_edit' ? capabilities.group_resolutions : capabilities.resolutions;
  if (!allowed.includes(imagePolicy.resolution)) imagePolicy.resolution = allowed[0];
  resolution.innerHTML = allowed.map(value => `<option value="${value}">${value}</option>`).join('');
  resolution.value = imagePolicy.resolution;
  const canGroup = activeImageEditor?.assetType === 'character' && capabilities?.group;
  const hasMaster = hasCharacterReferenceMaster();
  const masterButton = document.getElementById('btn-image-generate-master');
  const allButton = document.getElementById('btn-image-generate-all');
  if (masterButton) masterButton.style.display = canGroup && !hasMaster ? '' : 'none';
  if (allButton) {
    allButton.style.display = canGroup && hasMaster ? '' : 'none';
    allButton.disabled = false;
    allButton.textContent = '生成四视图组图';
    allButton.title = '';
  }
  if (note) note.textContent = canGroup && !hasMaster
    ? '请先生成并确认正面半身母版，组图会锁定它作为唯一参考图。'
    : capabilities?.reference_edit && activeImageEditor?.assetType === 'character'
      ? '单图修订将自动带入角色母版作为参考图。'
      : capabilities?.group ? '万相支持角色参考组图；其他资产使用单图生成。' : (imageCapabilities[imagePolicy.provider]?.unavailable_reason || '当前模型仅支持单张文生图。');
}

async function loadImageGenerationPolicy() {
  const projectId = appState.projectId;
  if (!projectId) return;
  const [capRes, policyRes] = await Promise.all([
    fetch(`${API_BASE}/api/image-capabilities`),
    fetch(`${API_BASE}/api/projects/${projectId}/image-generation-policy`),
  ]);
  if (!capRes.ok || !policyRes.ok || appState.projectId !== projectId) return;
  imageCapabilities = (await capRes.json()).providers;
  imagePolicy = (await policyRes.json()).policy;
  savedImagePolicy = {...imagePolicy};
  imagePolicyProjectId = projectId;
  renderImagePolicyControls();
  if (activeImageEditor && appState.projectId === projectId) renderImageEditor();
}

async function changeImagePolicy() {
  if (!imageCapabilities || !appState.projectId) return;
  const provider = document.getElementById('image-editor-provider').value;
  const model = document.getElementById('image-editor-model').value;
  const resolution = document.getElementById('image-editor-resolution').value;
  imagePolicy = {provider, model, resolution};
  renderImagePolicyControls();
  updateJobDrivenControls();
  if (!document.getElementById('image-editor-save-policy')?.checked) return;
  const res = await fetch(`${API_BASE}/api/projects/${appState.projectId}/image-generation-policy`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(imagePolicy),
  });
  if (!res.ok) throw new Error(await imageEditorResponseError(res, '保存图片模型设置失败'));
  imagePolicy = (await res.json()).policy;
  savedImagePolicy = {...imagePolicy};
  imagePolicyProjectId = appState.projectId;
  renderImagePolicyControls();
  updateJobDrivenControls();
}

async function imageEditorResponseError(response, fallbackMessage) {
  const body = await response.text();
  if (!body) return `${fallbackMessage}（HTTP ${response.status}）`;
  try {
    const payload = JSON.parse(body);
    return payload.detail || fallbackMessage;
  } catch {
    return `${fallbackMessage}：${body.slice(0, 300)}`;
  }
}

function openCharacterImageEditor(index) {
  const role = appState.roles[index];
  if (!role) return;
  openImageEditor('character', role.name, 'front_half');
}

function openSceneImageEditor(index) {
  const scene = appState.scenes[index];
  if (!scene) return;
  const assetId = String(scene.locationId || scene.sceneId || index + 1);
  const stateId = scene.stateId || '';
  const images = (appState.sceneImages || []).filter(item =>
    String(item.location_id || item.scene_id) === assetId
    && (!stateId || String(item.state_id || '') === stateId)
  );
  const viewType = images.find(item => item.view_type === 'scene_center')?.view_type || images[0]?.view_type || 'scene_center';
  openImageEditor('scene', assetId, viewType, stateId);
}

function openPropImageEditor(index) {
  const prop = appState.props[index];
  if (!prop) return;
  const images = (appState.propImages || []).filter(item => String(item.prop_id) === String(prop.propId));
  const viewType = images.find(item => item.view_type === 'front')?.view_type
    || images.find(item => item.view_type && item.view_type !== 'standard_views')?.view_type
    || 'front';
  openImageEditor('prop', prop.propId, viewType);
}

async function openImageEditor(assetType, assetId, viewType, stateId = '') {
  if (!appState.projectId || !['character', 'scene', 'prop'].includes(assetType)) return;
  activeImageEditor = { assetType, assetId: String(assetId), stateId: stateId || '', viewType, history: [], previewRecord: null };
  document.getElementById('modal-image-editor').style.display = 'flex';
  renderImageEditor();
  loadImageGenerationPolicy().catch(console.error);
  await loadImageHistory();
}

function closeImageEditor() {
  const modal = document.getElementById('modal-image-editor');
  if (modal) modal.style.display = 'none';
  activeImageEditor = null;
}

function imageEditorSessionSnapshot() {
  return {
    project: projectSessionSnapshot(),
    assetType: activeImageEditor?.assetType || '',
    assetId: activeImageEditor?.assetId || '',
    stateId: activeImageEditor?.stateId || '',
    viewType: activeImageEditor?.viewType || '',
  };
}

function isCurrentImageEditor(snapshot) {
  return isCurrentProjectSession(snapshot.project)
    && activeImageEditor?.assetType === snapshot.assetType
    && activeImageEditor?.assetId === snapshot.assetId
    && (activeImageEditor?.stateId || '') === (snapshot.stateId || '')
    && activeImageEditor?.viewType === snapshot.viewType;
}

function imageEditorEntries() {
  if (activeImageEditor?.assetType === 'character') return appState.characterImages || [];
  if (activeImageEditor?.assetType === 'prop') return appState.propImages || [];
  return appState.sceneImages || [];
}

function imageEditorImageSource(entry) {
  return typeof entry?.image_path === 'string' ? entry.image_path.trim() : '';
}

function shouldShowMissingMediaNotice(imageSource) {
  return Boolean(imageSource && isMissingMediaPath(imageSource));
}

function isUsableHistoryRecord(record) {
  return Boolean(record?.path)
    && record.file_available !== false
    && !shouldShowMissingMediaNotice(record.path);
}

function imageEditorEntry() {
  if (!activeImageEditor) return null;
  const direct = imageEditorEntries().find(item => {
    const id = activeImageEditor.assetType === 'character' ? item.character_name : activeImageEditor.assetType === 'prop' ? item.prop_id : String(item.location_id || item.scene_id);
    const sameState = activeImageEditor.assetType !== 'scene' || String(item.state_id || '') === (activeImageEditor.stateId || '');
    return String(id) === activeImageEditor.assetId && item.view_type === activeImageEditor.viewType && sameState;
  });
  if (direct || activeImageEditor.assetType !== 'character') return direct || null;
  const legacyFallback = {
    front_half: 'portrait', front_full: 'full_body', left_profile: 'profile',
  }[activeImageEditor.viewType];
  if (!legacyFallback) return null;
  return imageEditorEntries().find(item =>
    String(item.character_name) === activeImageEditor.assetId && item.view_type === legacyFallback
  ) || null;
}

function imageEditorViews() {
  if (!activeImageEditor) return [];
  if (activeImageEditor.assetType === 'character') {
    return ['front_half', 'front_full', 'left_profile', 'back_full'];
  }
  if (activeImageEditor.assetType === 'prop') {
    const base = ['front', 'back', 'left', 'right', 'top', 'bottom'];
    const hasLegacySheet = imageEditorEntries().some(item =>
      String(item.prop_id) === activeImageEditor.assetId && item.view_type === 'standard_views'
    );
    return hasLegacySheet ? [...base, 'standard_views'] : base;
  }
  const preferred = ['scene_center', 'scene_left_45', 'scene_right_45', 'scene_reverse', 'wide', 'medium'];
  const entryId = String(activeImageEditor.assetId);
  const available = new Set(imageEditorEntries()
    .filter(item => String(activeImageEditor.assetType === 'character' ? item.character_name : activeImageEditor.assetType === 'prop' ? item.prop_id : (item.location_id || item.scene_id)) === entryId)
    .filter(item => activeImageEditor.assetType !== 'scene' || String(item.state_id || '') === (activeImageEditor.stateId || ''))
    .map(item => item.view_type));
  const views = preferred.filter(view => available.has(view));
  return views.length ? views : [activeImageEditor.viewType];
}

function hasChinesePrompt(prompt) {
  return (String(prompt || '').match(/[\u3400-\u9fff]/g) || []).length >= 12;
}

function characterIdentity(name) {
  const chinese = (String(name || '').match(/[\u3400-\u9fff]+/g) || []).join('');
  return chinese || String(name || '').replace(/\s*\([^)]*\)\s*/g, '').trim().toLowerCase();
}

function characterDescription(visualDesign, name) {
  const registries = [
    visualDesign?.global_character_descriptions_cn || {},
    visualDesign?.global_character_descriptions || {},
  ];
  const identity = characterIdentity(name);
  for (const registry of registries) {
    if (registry[name]) return registry[name];
    const matchedName = Object.keys(registry).find(key => characterIdentity(key) === identity);
    if (matchedName) return registry[matchedName];
  }
  return '角色外貌以剧本设定为准。';
}

function rolesFromVisualDesign(visualDesign, script = null) {
  const manifestCharacters = script?.asset_manifest?.characters || [];
  const visibleManifestNames = manifestCharacters.filter(item => item.appearance_mode === 'on_screen').map(item => item.name);
  const names = new Set(visibleManifestNames.length
    ? visibleManifestNames
    : Object.keys(visualDesign?.global_character_descriptions || visualDesign?.global_character_descriptions_cn || {}));
  const manifestByName = new Map(manifestCharacters.map(item => [item.name, item]));
  return [...names].map(name => {
    const resolved = characterDescription(visualDesign, name);
    const description = resolved === '角色外貌以剧本设定为准。'
      ? (manifestByName.get(name)?.description || resolved)
      : resolved;
    return { name, gender: '未知', age: '成年', identity: '', personality: description, clothes: '', description };
  });
}

function imageViewLabel(viewType) {
  return {
    front_half: '正面半身', front_full: '正面全身', left_profile: '左侧面', back_full: '背面全身',
    portrait: '正面肖像', full_body: '全身照', profile: '侧面照', wide: '全景', medium: '中景', standard_views: '2x3 道具六视图',
    front: '正面', back: '背面', left: '左侧', right: '右侧', top: '俯视', bottom: '仰视',
    scene_center: '正面中轴', scene_left_45: '左前45度', scene_right_45: '右前45度', scene_reverse: '空间向外',
  }[viewType] || viewType;
}

function draftImageEditorPrompt() {
  if (!activeImageEditor) return '';
  if (activeImageEditor.assetType === 'character') {
    return '';
  }
  if (activeImageEditor.assetType === 'prop') return '';
  const scene = appState.scenes.find(item =>
    String(item.locationId || item.sceneId) === activeImageEditor.assetId
    && String(item.stateId || '') === (activeImageEditor.stateId || '')
  );
  return `正在加载${scene?.name || `场景 ${activeImageEditor.assetId}`}的${imageViewLabel(activeImageEditor.viewType)}横向16:9场景参考提示词...`;
}

function imageEditorPayload() {
  return {
    asset_type: activeImageEditor.assetType,
    asset_id: activeImageEditor.assetId,
    view_type: activeImageEditor.viewType,
    state_id: activeImageEditor.assetType === 'scene' ? (activeImageEditor.stateId || '') : '',
    ...imagePolicyPayload(),
  };
}

function setImageEditorView(viewType) {
  activeImageEditor.viewType = viewType;
  activeImageEditor.previewRecord = null;
  activeImageEditor.history = [];
  renderImageEditor();
  loadImageHistory();
}

function qualityReportForEntry(entry) {
  const report = entry?.quality_report;
  return report && typeof report === 'object' ? report : {};
}

function qualityVerdict(report) {
  const visual = report?.visual_review && typeof report.visual_review === 'object' ? report.visual_review : report;
  if (report?.hard_fail === true || visual?.hard_fail === true) return 'hard_fail';
  return String(report?.review_verdict || visual?.review_verdict || (report?.status === 'passed' ? 'passed' : report?.status === 'unreviewed' ? 'unreviewed' : '')).toLowerCase();
}

function renderQualityReport(report) {
  const box = document.getElementById('image-editor-quality-report');
  if (!box) return;
  const payload = report && typeof report === 'object' ? report : {};
  const visual = payload.visual_review && typeof payload.visual_review === 'object' ? payload.visual_review : payload;
  const verdict = qualityVerdict(payload);
  const labels = {passed: '通过', warning: '有待确认', hard_fail: '明确不通过', unreviewed: '未执行'};
  const label = labels[verdict] || '未审核';
  const evidence = Array.isArray(payload.review_evidence) ? payload.review_evidence : (Array.isArray(visual.contract_checks) ? visual.contract_checks : []);
  const issues = Array.isArray(visual.issues) ? visual.issues : (Array.isArray(payload.issues) ? payload.issues : []);
  if (!verdict && !evidence.length && !issues.length) {
    box.textContent = '尚未执行视觉审核';
    return;
  }
  const score = Number.isFinite(Number(visual.score ?? payload.score)) ? ` · 评分 ${Number(visual.score ?? payload.score).toFixed(2)}` : '';
  const reason = String(payload.review_reason || visual.summary || '').trim();
  const evidenceHtml = evidence.slice(0, 8).map(item => {
    const passed = item.passed === true || String(item.passed).toLowerCase() === 'true';
    return `<li class="${passed ? 'is-pass' : 'is-fail'}"><strong>${escapeHtml(item.item || '检查项')}</strong>：${escapeHtml(item.evidence || item.message || '无证据')}</li>`;
  }).join('');
  const issueHtml = issues.slice(0, 5).map(item => `<li>${escapeHtml(item)}</li>`).join('');
  const autoSelected = payload.auto_selected_after_review === true;
  const selectedAttempt = Number(payload.selected_attempt_no || 0);
  const autoSelectionHtml = autoSelected
    ? `<div class="image-editor-quality-reason">已在审核未通过后自动选用${selectedAttempt ? `第 ${selectedAttempt} 次尝试` : '当前结果'}；其他候选仍保留在历史版本中，可随时切换或重新生成。</div>`
    : '';
  box.innerHTML = `<div class="image-editor-quality-verdict verdict-${escapeHtml(verdict || 'unknown')}">${escapeHtml(label)}${escapeHtml(score)}</div>${autoSelectionHtml}${reason ? `<div class="image-editor-quality-reason">${escapeHtml(reason)}</div>` : ''}${evidenceHtml ? `<ul class="image-editor-quality-evidence">${evidenceHtml}</ul>` : ''}${issueHtml ? `<ul class="image-editor-quality-issues">${issueHtml}</ul>` : ''}`;
}

function previewImageHistory(versionId) {
  if (!activeImageEditor) return;
  const record = (activeImageEditor.history || []).find(item => item.id === versionId);
  if (!record) return;
  activeImageEditor.previewRecord = record;
  renderImageEditor();
}

async function acceptImageCandidate(versionId) {
  if (!activeImageEditor || !versionId) return;
  const operation = imageEditorSessionSnapshot();
  try {
    const res = await fetch(`${API_BASE}/api/projects/${appState.projectId}/image-history/candidate/accept`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...imageEditorPayload(), version_id: versionId}),
    });
    if (!res.ok) throw new Error(await imageEditorResponseError(res, '接受候选版本失败'));
    const result = await res.json();
    if (!isCurrentImageEditor(operation)) return;
    activeImageEditor.previewRecord = null;
    applyImageMutation(result);
  } catch (error) {
    if (isCurrentImageEditor(operation)) alert(error.message || '接受候选版本失败');
  }
}

async function rejectImageCandidate(versionId) {
  if (!activeImageEditor || !versionId) return;
  const operation = imageEditorSessionSnapshot();
  try {
    const res = await fetch(`${API_BASE}/api/projects/${appState.projectId}/image-history/candidate/reject`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...imageEditorPayload(), version_id: versionId}),
    });
    if (!res.ok) throw new Error(await imageEditorResponseError(res, '放弃候选版本失败'));
    const result = await res.json();
    if (!isCurrentImageEditor(operation)) return;
    activeImageEditor.previewRecord = null;
    activeImageEditor.history = result.history || [];
    renderImageEditor();
  } catch (error) {
    if (isCurrentImageEditor(operation)) alert(error.message || '放弃候选版本失败');
  }
}

async function reviewCurrentImage() {
  if (!activeImageEditor) return;
  const operation = imageEditorSessionSnapshot();
  const versionId = activeImageEditor.previewRecord?.id || imageEditorEntry()?.version_id || '';
  try {
    const res = await fetch(`${API_BASE}/api/projects/${appState.projectId}/image-history/review`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...imageEditorPayload(), version_id: versionId}),
    });
    if (!res.ok) throw new Error(await imageEditorResponseError(res, '视觉检查失败'));
    const result = await res.json();
    if (!isCurrentImageEditor(operation)) return;
    activeImageEditor.previewRecord = null;
    activeImageEditor.history = result.history || [];
    applyImageMutation(result);
  } catch (error) {
    if (isCurrentImageEditor(operation)) alert(error.message || '视觉检查失败');
  }
}

async function loadCharacterPromptPreview() {
  if (!activeImageEditor || activeImageEditor.assetType !== 'character' || !appState.projectId) return;
  const assetId = activeImageEditor.assetId;
  const viewType = activeImageEditor.viewType;
  const systemPromptElement = document.getElementById('image-editor-system-prompt');
  try {
    const params = new URLSearchParams({ asset_id: assetId, view_type: viewType });
    const res = await fetch(`${API_BASE}/api/projects/${appState.projectId}/character-image-prompt?${params}`);
    if (!res.ok) throw new Error('无法加载系统提示词');
    const preview = await res.json();
    if (!activeImageEditor || activeImageEditor.assetId !== assetId || activeImageEditor.viewType !== viewType) return;
    systemPromptElement.value = preview.prompt;
    const entry = imageEditorEntry();
    const legacyPrompt = (entry?.prompt_template_version || 1) < 2;
    document.getElementById('image-editor-asset-meta').textContent =
      `视图：${imageViewLabel(viewType)} · ${preview.width} × ${preview.height}${legacyPrompt ? ' · 旧版提示词' : ''}`;
  } catch (error) {
    console.error(error);
  }
}

async function loadAssetPromptPreview() {
  if (!activeImageEditor || !appState.projectId) return;
  const snapshot = { ...activeImageEditor };
  try {
    const params = new URLSearchParams({view_type: snapshot.viewType});
    if (snapshot.assetType === 'scene' && snapshot.stateId) params.set('state_id', snapshot.stateId);
    const res = await fetch(`${API_BASE}/api/projects/${appState.projectId}/assets/${snapshot.assetType}/${encodeURIComponent(snapshot.assetId)}/prompt?${params}`);
    const preview = await res.json();
    if (!res.ok) throw new Error(preview.detail || '无法加载系统提示词');
    if (!activeImageEditor || activeImageEditor.assetType !== snapshot.assetType || activeImageEditor.assetId !== snapshot.assetId || (activeImageEditor.stateId || '') !== (snapshot.stateId || '') || activeImageEditor.viewType !== snapshot.viewType) return;
    document.getElementById('image-editor-system-prompt').value = preview.display_prompt || preview.effective_prompt || preview.prompt || '';
    document.getElementById('image-editor-negative-prompt').value = preview.negative_prompt || '';
    document.getElementById('image-editor-project-constraints').textContent = preview.project_constraints || '';
    const characterDescription = document.getElementById('image-editor-character-description');
    const characterDescriptionText = document.getElementById('image-editor-character-description-text');
    const description = snapshot.assetType === 'character' ? String(preview.character_description || '').trim() : '';
    if (characterDescription && characterDescriptionText) {
      characterDescription.hidden = !description;
      characterDescriptionText.textContent = description;
    }
    document.getElementById('image-editor-asset-meta').textContent = `视图：${imageViewLabel(snapshot.viewType)} · ${preview.width} × ${preview.height}`;
  } catch (error) { console.error(error); }
}

function renderImageEditor() {
  if (!activeImageEditor) return;
  const entry = imageEditorEntry();
  const previewRecord = activeImageEditor.previewRecord;
  const displayEntry = previewRecord || entry;
  const assetLabel = activeImageEditor.assetType === 'character' ? '角色形象' : activeImageEditor.assetType === 'prop' ? '道具视图' : '场景图片';
  const assetDisplayName = activeImageEditor.assetType === 'prop'
    ? (appState.props.find(item => item.propId === activeImageEditor.assetId)?.name || activeImageEditor.assetId)
    : activeImageEditor.assetType === 'scene'
      ? (appState.scenes.find(item => String(item.locationId || item.sceneId) === activeImageEditor.assetId && String(item.stateId || '') === (activeImageEditor.stateId || ''))?.name || activeImageEditor.assetId)
      : activeImageEditor.assetId;
  document.getElementById('image-editor-title').textContent = `编辑${assetLabel}`;
  document.getElementById('image-editor-asset-name').textContent = `${assetLabel}：${assetDisplayName}`;
  const legacyPrompt = activeImageEditor.assetType === 'character' && (entry?.prompt_template_version || 1) < 2;
  document.getElementById('image-editor-asset-meta').textContent = `视图：${imageViewLabel(activeImageEditor.viewType)}${entry?.width && entry?.height ? ` · ${entry.width} × ${entry.height}` : ''}${legacyPrompt ? ' · 旧版提示词' : ''}`;
  const previewImage = document.getElementById('image-editor-preview');
  const previewEmpty = document.getElementById('image-editor-empty-state');
  const previewStatus = document.getElementById('image-editor-preview-status');
  const imageSource = previewRecord?.path || imageEditorImageSource(entry);
  const imageMissing = shouldShowMissingMediaNotice(imageSource);
  const imageExists = Boolean(imageSource);
  previewImage.onerror = () => {
    if (!activeImageEditor || !previewEmpty) return;
    previewImage.style.display = 'none';
    previewImage.hidden = true;
    previewEmpty.hidden = false;
    previewEmpty.querySelector('strong').textContent = '图片加载失败';
    previewEmpty.querySelector('span').textContent = '可重新生成当前视图或选择历史版本';
    if (previewStatus) previewStatus.hidden = true;
  };
  if (imageMissing) {
    previewImage.style.display = 'none';
    previewImage.hidden = true;
    previewImage.removeAttribute('src');
    if (previewEmpty) previewEmpty.hidden = true;
    previewStatus.hidden = false;
    previewStatus.textContent = '图片文件缺失，历史记录已保留。可重新生成此视图或选择历史版本。';
  } else if (!imageExists) {
    previewImage.style.display = 'none';
    previewImage.hidden = true;
    previewImage.removeAttribute('src');
    if (previewEmpty) previewEmpty.hidden = false;
    previewStatus.hidden = true;
  } else {
    previewImage.style.display = '';
    previewImage.hidden = false;
    if (previewEmpty) previewEmpty.hidden = true;
    previewStatus.hidden = true;
    previewImage.src = imageSource ? mediaUrl(imageSource, displayEntry?.id || displayEntry?.version_id || '') : '';
  }
  const generationError = imageEditorGenerationError();
  if (generationError && previewStatus) {
    previewStatus.hidden = false;
    previewStatus.classList.add('is-error');
    previewStatus.textContent = `生成失败：${generationError}`;
  } else if (previewStatus) {
    previewStatus.classList.remove('is-error');
  }
  const systemPrompt = activeImageEditor.assetType === 'character'
    ? (displayEntry?.image_prompt || displayEntry?.prompt || '将由系统根据角色设定和当前视图生成。')
    : activeImageEditor.assetType === 'prop'
      ? (displayEntry?.image_prompt || displayEntry?.prompt || '正在加载道具当前视图系统提示词...')
      : (displayEntry?.image_prompt || displayEntry?.prompt || draftImageEditorPrompt());
  const systemPromptElement = document.getElementById('image-editor-system-prompt');
  if (systemPromptElement) systemPromptElement.value = systemPrompt;
  document.getElementById('image-editor-negative-prompt').value = '';
  if (systemPromptElement) systemPromptElement.value = '正在加载当前视图的最终提示词...';
  const characterDescription = document.getElementById('image-editor-character-description');
  if (characterDescription) characterDescription.hidden = activeImageEditor.assetType !== 'character';
  loadAssetPromptPreview();
  const promptInput = document.getElementById('image-editor-prompt');
  const promptLabel = document.getElementById('image-editor-prompt-label');
  const currentImageExists = imageEditorHasCurrentImage();
  const hasWanMaster = hasCharacterReferenceMaster();
  const hasSceneMaster = hasSceneReferenceMaster();
  const activeCapabilities = imageCapabilities?.[imagePolicy.provider]?.models?.[imagePolicy.model];
  const wanCharacter = Boolean(activeCapabilities?.reference_edit) && (
    (activeImageEditor.assetType === 'character' && hasWanMaster)
    || (activeImageEditor.assetType === 'scene' && activeImageEditor.viewType !== 'scene_center' && hasSceneMaster)
  );
  promptInput.value = entry?.custom_instructions || '';
  promptInput.placeholder = wanCharacter && currentImageExists
    ? '填写本次要改变的外观或细节（参考修订时必填）'
    : '可选：补充外观、服装或材质要求。系统会保留视图构图与约束。';
  if (promptLabel) promptLabel.textContent = wanCharacter && currentImageExists ? '参考修订要求（必填）' : '补充生成要求（可选）';
  const primaryButton = document.getElementById('btn-image-regenerate');
  if (primaryButton) primaryButton.style.display = activeImageEditor.assetType === 'character' && activeCapabilities?.group && !hasWanMaster ? 'none' : '';
  if (primaryButton) primaryButton.textContent = wanCharacter
    ? (currentImageExists ? '参考修订当前图' : '生成当前视图')
    : (activeImageEditor.assetType === 'character' && activeCapabilities?.group && !hasWanMaster
      ? '生成角色母版'
      : (currentImageExists ? '重新生成图片' : '生成当前视图'));
  const referenceRow = document.getElementById('image-editor-reference-row');
  const referenceSelect = document.getElementById('image-editor-reference-versions');
  if (referenceRow && referenceSelect) {
    const canUseReferences = Boolean(imageCapabilities?.[imagePolicy.provider]?.models?.[imagePolicy.model]?.reference_edit) && (
      (activeImageEditor.assetType === 'character' && hasWanMaster)
      || (activeImageEditor.assetType === 'scene' && activeImageEditor.viewType !== 'scene_center' && hasSceneMaster)
    );
    referenceRow.style.display = canUseReferences ? '' : 'none';
    if (canUseReferences) {
      const candidates = imageEditorEntries().filter(item => {
        if (!item.version_id || item.view_type === activeImageEditor.viewType) return false;
        if (activeImageEditor.assetType === 'character') return item.character_name === activeImageEditor.assetId;
        const itemId = String(item.location_id || item.scene_id || '');
        return itemId === activeImageEditor.assetId && String(item.state_id || '') === (activeImageEditor.stateId || '');
      });
      referenceSelect.innerHTML = candidates.map(item => `<option value="${escapeHtml(item.version_id)}">${escapeHtml(imageViewLabel(item.view_type))}</option>`).join('');
    }
  }
  document.getElementById('image-editor-views').innerHTML = imageEditorViews().map(view => {
    const active = view === activeImageEditor.viewType;
    return `<button class="btn-secondary btn-sm image-editor-view-tab${active ? ' is-active' : ''}" aria-pressed="${active}" onclick="StudioActions.setImageEditorView('${view}')">${imageViewLabel(view)}</button>`;
  }).join('');
  const history = activeImageEditor.history || [];
  const historyError = String(activeImageEditor.historyError || '');
  const historySection = document.getElementById('image-editor-history-section');
  if (historySection) historySection.hidden = !history.length && !historyError;
  document.getElementById('image-editor-history').innerHTML = history.length
    ? history.map(record => {
      const usable = isUsableHistoryRecord(record);
      const preview = usable
        ? `<img src="${mediaUrl(record.path)}" style="display:block;width:100%;height:74px;object-fit:cover;border-radius:3px" alt="">`
        : '<span class="media-missing-notice">文件不可用</span>';
      const decision = String(record.review_verdict || record.review_decision || record.quality_report?.review_verdict || record.quality_report?.review_decision || '').trim();
      const decisionLabel = decision === 'hard_fail'
        ? '硬失败候选'
        : decision === 'failed_after_retry'
          ? '重试失败候选'
          : decision === 'uncertain'
            ? '审核不确定'
            : record.candidate
              ? '候选版本'
              : (record.source || '历史版本');
      const attempt = record.attempt_no ? ` · 第${record.attempt_no}次` : '';
      const label = record.active ? `当前版本${attempt}` : `${decisionLabel}${attempt}`;
      const previewButton = `<button class="btn-secondary image-history-entry${record.candidate ? ' is-candidate' : ''}" style="padding:4px;border-color:${record.active ? '#10b981' : record.candidate ? '#f59e0b' : '#27272a'}" onclick="StudioActions.${record.candidate ? 'previewImageHistory' : 'activateImageHistory'}('${record.id}')" title="${escapeHtml([record.source || 'history', decision].filter(Boolean).join(' · '))}">${preview}<span style="display:block;font-size:10px;margin-top:3px">${escapeHtml(label)}</span></button>`;
      return record.candidate
        ? `<div class="image-history-candidate">${previewButton}<div class="image-history-candidate-actions"><button class="btn-secondary btn-sm" type="button" onclick="StudioActions.acceptImageCandidate('${record.id}')">接受</button><button class="btn-secondary btn-sm" type="button" onclick="StudioActions.rejectImageCandidate('${record.id}')">放弃</button></div></div>`
        : previewButton;
    }).join('')
    : historyError
      ? `<div class="image-editor-history-error"><span>${escapeHtml(historyError)}</span><button class="btn-secondary btn-sm" type="button" onclick="StudioActions.loadImageHistory()">重试</button></div>`
      : '<div style="font-size:12px;color:#71717a">暂无历史版本</div>';
  renderQualityReport(qualityReportForEntry(displayEntry));
  renderImagePolicyControls();
  updateJobDrivenControls();
}

async function loadImageHistory() {
  if (!activeImageEditor || !appState.projectId) return;
  const snapshot = imageEditorSessionSnapshot();
  const params = new URLSearchParams({
    asset_type: activeImageEditor.assetType,
    asset_id: activeImageEditor.assetId,
    view_type: activeImageEditor.viewType,
  });
  if (activeImageEditor.assetType === 'scene' && activeImageEditor.stateId) params.set('state_id', activeImageEditor.stateId);
  try {
    const res = await fetch(`${API_BASE}/api/projects/${appState.projectId}/image-history?${params}`);
    if (!isCurrentImageEditor(snapshot)) return;
    if (res.status === 404) { activeImageEditor.history = []; activeImageEditor.historyError = ''; renderImageEditor(); return; }
    if (!res.ok) throw new Error('无法加载图片历史');
    activeImageEditor.history = (await res.json()).versions || [];
    activeImageEditor.historyError = '';
    renderImageEditor();
  } catch (error) {
    console.error(error);
    if (isCurrentImageEditor(snapshot)) {
      activeImageEditor.historyError = error.message || '无法加载图片历史';
      renderImageEditor();
    }
  }
}

function applyImageMutation(result) {
  if (!result?.state_json || !activeImageEditor) throw new Error('图片更新失败');
  appState.stateJson = result.state_json;
  const state = JSON.parse(result.state_json);
  if (activeImageEditor.assetType === 'character') {
    appState.characterImages = state.character_images?.characters || [];
    renderRoles();
  } else if (activeImageEditor.assetType === 'prop') {
    appState.propImages = state.prop_images?.props || [];
    renderProps();
  } else {
    appState.sceneImages = state.scene_images?.scenes || [];
    renderScenes();
  }
  activeImageEditor.history = result.history || [];
  renderImageEditor();
}

async function requestImageRegeneration(prompt, useStandardViewPrompt = false) {
  if (!activeImageEditor) throw new Error('图片编辑器未打开');
  const res = await fetch(`${API_BASE}/api/projects/${appState.projectId}/image-regenerate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...imageEditorPayload(), prompt, use_standard_view_prompt: useStandardViewPrompt }),
  });
  if (!res.ok) throw new Error(await imageEditorResponseError(res, '图片重新生成失败'));
  const result = await res.json();
  applyImageMutation(result);
  return result;
}

async function generateAllCharacterViews() {
  if (!activeImageEditor || activeImageEditor.assetType !== 'character') return;
  const operation = imageEditorSessionSnapshot();
  const customInstructions = document.getElementById('image-editor-prompt').value.trim();
  const capabilities = imageCapabilities?.[imagePolicy.provider]?.models?.[imagePolicy.model];
  if (!capabilities?.group) { alert('角色参考组图仅支持万相 2.7，请先修改项目默认图片模型。'); return; }
  if (!hasCharacterReferenceMaster()) {
    return generateCharacterMaster();
  }
  const active = activeProjectJobs(job => (job.job_type === 'asset.generate' || job.job_type === 'character.reference.group') && job.target_type === 'character' && String(job.target_id).split(':')[0] === activeImageEditor.assetId);
  if (active.length) return cancelMatchingJobs(active);
  try {
    await submitProjectJob({
      job_type: 'character.reference.group', target_type: 'character', target_id: activeImageEditor.assetId,
      payload: {asset_id:activeImageEditor.assetId, custom_instructions:customInstructions, ...imagePolicyPayload()},
    });
    document.getElementById('char-status').innerHTML = '<span style="color:#93c5fd">四视图任务已提交</span>';
  } catch (error) {
    if (!isCurrentImageEditor(operation)) return;
    document.getElementById('char-status').innerHTML = `<span style="color:#ef4444">四视图生成失败：${escapeHtml(error.message)}</span>`;
  } finally {
    if (isCurrentImageEditor(operation)) renderImageEditor();
  }
}

async function generateCharacterMaster() {
  if (!activeImageEditor || activeImageEditor.assetType !== 'character') return;
  const capabilities = imageCapabilities?.[imagePolicy.provider]?.models?.[imagePolicy.model];
  if (!capabilities?.group) { alert('角色参考组图仅支持万相 2.7，请先修改项目默认图片模型。'); return; }
  const active = activeProjectJobs(job => job.job_type === 'asset.generate'
    && job.target_type === 'character'
    && String(job.target_id).split(':')[0] === activeImageEditor.assetId);
  if (active.length) return cancelMatchingJobs(active);
  try {
    activeImageEditor.viewType = 'front_half';
    activeImageEditor.history = [];
    renderImageEditor();
    const operation = imageEditorSessionSnapshot();
    await submitProjectJob({
      job_type: 'asset.generate', target_type: 'character', target_id: activeImageEditor.assetId,
      payload: {
        ...imageEditorPayload(), view_type: 'front_half',
        custom_instructions: document.getElementById('image-editor-prompt').value.trim(),
        // An older Kling/legacy front image must be replaced by a Wan master;
        // otherwise asset.generate would correctly skip the existing file.
        regenerate: imageEditorHasCurrentImage(),
      },
    });
    document.getElementById('char-status').innerHTML = '<span style="color:#93c5fd">角色母版任务已提交</span>';
  } catch (error) {
    if (activeImageEditor?.assetType === 'character') alert(error.message);
  } finally {
    if (activeImageEditor?.assetType === 'character') renderImageEditor();
  }
}

async function regenerateEditedImage() {
  if (!activeImageEditor) return;
  const currentCapabilities = imageCapabilities?.[imagePolicy.provider]?.models?.[imagePolicy.model];
  if (activeImageEditor.assetType === 'character' && currentCapabilities?.group && !hasCharacterReferenceMaster()) {
    return generateCharacterMaster();
  }
  const operation = imageEditorSessionSnapshot();
  const prompt = document.getElementById('image-editor-prompt').value.trim();
  const targetId = `${activeImageEditor.assetId}:${activeImageEditor.viewType}`;
  const capabilities = imageCapabilities?.[imagePolicy.provider]?.models?.[imagePolicy.model];
  const referenceEdit = Boolean(capabilities?.reference_edit) && (
    (activeImageEditor.assetType === 'character' && hasCharacterReferenceMaster() && imageEditorHasCurrentImage())
    || (activeImageEditor.assetType === 'scene' && activeImageEditor.viewType !== 'scene_center' && hasSceneReferenceMaster())
  );
  const active = activeProjectJobs(job => {
    const jobAssetId = String(job.target_id || '').split(':')[0];
    const sameAsset = job.target_type === activeImageEditor.assetType && jobAssetId === activeImageEditor.assetId;
    if (!sameAsset) return false;
    if (activeImageEditor.assetType === 'scene' && String(job.payload?.state_id || '') !== (activeImageEditor.stateId || '')) return false;
    if (job.job_type === 'prop.reference.group') return true;
    if (job.job_type === 'character.reference.edit') return job.target_id === targetId;
    if (job.job_type === 'scene.reference.edit') return job.target_id === targetId;
    if (job.job_type !== 'asset.generate') return false;
    return job.target_id === targetId || !String(job.target_id || '').includes(':');
  });
  if (active.length) return cancelMatchingJobs(active);
  try {
    if (referenceEdit) {
      if (!prompt) throw new Error('请填写更新后的提示词，再进行角色参考修订。');
      const editJobType = activeImageEditor.assetType === 'scene' ? 'scene.reference.edit' : 'character.reference.edit';
      await submitProjectJob({
        job_type: editJobType, target_type: activeImageEditor.assetType, target_id: targetId,
        payload: {...imageEditorPayload(), prompt, reference_version_ids: [...document.getElementById('image-editor-reference-versions').selectedOptions].map(option => option.value)},
      });
    } else {
      await submitProjectJob({
        job_type: 'asset.generate', target_type: activeImageEditor.assetType, target_id: activeImageEditor.assetId,
        payload: {
          ...imageEditorPayload(),
          view_type: activeImageEditor.viewType,
          custom_instructions: prompt,
          regenerate: imageEditorHasCurrentImage(),
        },
      });
    }
  } catch (error) {
    if (!isCurrentImageEditor(operation)) return;
    alert(error.message);
  } finally { if (isCurrentImageEditor(operation)) renderImageEditor(); }
}

async function uploadEditedImage(input) {
  const file = input.files?.[0];
  if (!file || !activeImageEditor) return;
  const operation = imageEditorSessionSnapshot();
  const controller = sessionController();
  const form = new FormData();
  form.append('asset_type', activeImageEditor.assetType);
  form.append('asset_id', activeImageEditor.assetId);
  form.append('view_type', activeImageEditor.viewType);
  if (activeImageEditor.assetType === 'scene') form.append('state_id', activeImageEditor.stateId || '');
  form.append('file', file);
  try {
    const res = await fetch(`${API_BASE}/api/projects/${appState.projectId}/image-upload`, { method: 'POST', body: form, signal: controller.signal });
    if (!res.ok) throw new Error(await imageEditorResponseError(res, '图片上传失败'));
    const result = await res.json();
    if (!isCurrentImageEditor(operation)) return;
    applyImageMutation(result);
  } catch (error) {
    if (error.name === 'AbortError' || !isCurrentImageEditor(operation)) return;
    alert(error.message);
  } finally {
    releaseSessionController(controller);
    input.value = '';
  }
}

async function activateImageHistory(versionId) {
  if (!activeImageEditor) return;
  const operation = imageEditorSessionSnapshot();
  const controller = sessionController();
  try {
    const res = await fetch(`${API_BASE}/api/projects/${appState.projectId}/image-history/activate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...imageEditorPayload(), version_id: versionId }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(await imageEditorResponseError(res, '切换历史图片失败'));
    const result = await res.json();
    if (!isCurrentImageEditor(operation)) return;
    applyImageMutation(result);
  } catch (error) {
    if (error.name === 'AbortError' || !isCurrentImageEditor(operation)) return;
    alert(error.message);
  } finally {
    releaseSessionController(controller);
  }
}
const __moduleActions = {
  openCharacterImageEditor,
  openSceneImageEditor,
  openPropImageEditor,
  openImageEditor,
  closeImageEditor,
  imageEditorSessionSnapshot,
  isCurrentImageEditor,
  imageEditorEntries,
  imageEditorEntry,
  imageEditorViews,
  imageEditorImageSource,
  shouldShowMissingMediaNotice,
  isUsableHistoryRecord,
  hasChinesePrompt,
  characterIdentity,
  characterDescription,
  rolesFromVisualDesign,
  imageViewLabel,
  draftImageEditorPrompt,
  imageEditorPayload,
  imageEditorResponseError,
  imagePolicyPayload,
  projectImagePolicyPayload,
  loadImageGenerationPolicy,
  changeImagePolicy,
  setImageEditorView,
  loadCharacterPromptPreview,
  loadAssetPromptPreview,
  renderImageEditor,
  loadImageHistory,
  renderQualityReport,
  previewImageHistory,
  acceptImageCandidate,
  rejectImageCandidate,
  reviewCurrentImage,
  applyImageMutation,
  requestImageRegeneration,
  generateCharacterMaster,
  generateAllCharacterViews,
  regenerateEditedImage,
  uploadEditedImage,
  activateImageHistory,
};
Object.assign(globalThis.StudioActions, __moduleActions);
export { __moduleActions };
