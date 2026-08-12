const activeProjectJobs = (...args) => globalThis.StudioActions.activeProjectJobs(...args);
const beginApiProgress = (...args) => globalThis.StudioActions.beginApiProgress(...args);
const buildShotFromScene = (...args) => globalThis.StudioActions.buildShotFromScene(...args);
const buildShotsFromStateJson = (...args) => globalThis.StudioActions.buildShotsFromStateJson(...args);
const cancelMatchingJobs = (...args) => globalThis.StudioActions.cancelMatchingJobs(...args);
const endApiProgress = (...args) => globalThis.StudioActions.endApiProgress(...args);
const escapeHtml = (...args) => globalThis.StudioActions.escapeHtml(...args);
const isCurrentProjectSession = (...args) => globalThis.StudioActions.isCurrentProjectSession(...args);
const loadProjects = (...args) => globalThis.StudioActions.loadProjects(...args);
const mergeProjectJob = (...args) => globalThis.StudioActions.mergeProjectJob(...args);
const navigateTo = (...args) => globalThis.StudioActions.navigateTo(...args);
const projectJsonRequest = (...args) => globalThis.StudioActions.projectJsonRequest(...args);
const projectSessionSnapshot = (...args) => globalThis.StudioActions.projectSessionSnapshot(...args);
const releaseSessionController = (...args) => globalThis.StudioActions.releaseSessionController(...args);
const renderProps = (...args) => globalThis.StudioActions.renderProps(...args);
const renderProjectJobs = (...args) => globalThis.StudioActions.renderProjectJobs(...args);
const renderRecoveredMedia = (...args) => globalThis.StudioActions.renderRecoveredMedia(...args);
const renderRoles = (...args) => globalThis.StudioActions.renderRoles(...args);
const renderScenes = (...args) => globalThis.StudioActions.renderScenes(...args);
const renderShots = (...args) => globalThis.StudioActions.renderShots(...args);
const resetProjectSession = (...args) => globalThis.StudioActions.resetProjectSession(...args);
const restoreProjectMedia = (...args) => globalThis.StudioActions.restoreProjectMedia(...args);
const rolesFromVisualDesign = (...args) => globalThis.StudioActions.rolesFromVisualDesign(...args);
const sessionController = (...args) => globalThis.StudioActions.sessionController(...args);
const startProjectJobPolling = (...args) => globalThis.StudioActions.startProjectJobPolling(...args);
const submitProjectJob = (...args) => globalThis.StudioActions.submitProjectJob(...args);
const updateJobDrivenControls = (...args) => globalThis.StudioActions.updateJobDrivenControls(...args);
const waitForProjectJob = (...args) => globalThis.StudioActions.waitForProjectJob(...args);

let activeAssetTab = 'characters';
let scriptConfirmInFlight = false;
let visualStylesLoadPromise = null;

// Keep the creation dialog usable while the catalog request is still loading
// or when the API is temporarily unavailable. The server remains authoritative
// for prompt layers; these entries only carry selectable IDs and previews.
const FALLBACK_VISUAL_STYLES = Object.freeze([
  {
    id: 'cinematic_cel',
    name: '现代电影级二维赛璐璐',
    description: '清晰线稿与电影级构图，适合都市悬疑和现实题材。',
    reference_image: '/static/assets/style-references/cinematic-cel.webp?v=20260810-2',
    tags: ['清晰线稿', '赛璐璐阴影', '电影光影'],
  },
  {
    id: 'semi_realistic_anime',
    name: '半写实动漫',
    description: '保留动画线稿，以细腻材质和光影强化电影感。',
    reference_image: '/static/assets/style-references/semi-realistic-anime.webp?v=20260810-2',
    tags: ['细腻线稿', '自然比例', '电影氛围'],
  },
  {
    id: 'japanese_animation',
    name: '日系商业动画',
    description: '干净线条、稳定面部设计和清晰色块，适合角色驱动叙事。',
    reference_image: '/static/assets/style-references/japanese-animation.webp?v=20260810-2',
    tags: ['干净线条', '稳定脸型', '清晰色块'],
  },
  {
    id: 'live_action_cinematic',
    name: '电影级真人写实',
    description: '真实人物、自然材质和可信镜头语言，适合现实主义短剧。',
    reference_image: '/static/assets/style-references/live-action-cinematic.webp?v=20260810-2',
    tags: ['真实人物', '自然材质', '电影镜头'],
  },
]);

function mergeVisualStyleCatalog(styles) {
  const incoming = Array.isArray(styles) ? styles.filter(style => style && style.id) : [];
  const byId = new Map(FALLBACK_VISUAL_STYLES.map(style => [style.id, style]));
  incoming.forEach(style => byId.set(style.id, {...byId.get(style.id), ...style}));
  return [...FALLBACK_VISUAL_STYLES.map(style => byId.get(style.id)),
    ...incoming.filter(style => !FALLBACK_VISUAL_STYLES.some(fallback => fallback.id === style.id))];
}

function renderVisualStyleOptions() {
  const container = document.getElementById('visual-style-options');
  if (!container) return;
  if (!container.dataset.styleClickBound) {
    container.addEventListener('click', event => {
      const card = event.target.closest('[data-visual-style-id]');
      if (!card || !container.contains(card)) return;
      event.preventDefault();
      selectVisualStyle(card.dataset.visualStyleId);
    });
    container.dataset.styleClickBound = 'true';
  }
  const styles = Array.isArray(appState.visualStyles) && appState.visualStyles.length
    ? appState.visualStyles
    : FALLBACK_VISUAL_STYLES;
  if (!styles.length) {
    container.innerHTML = '<div class="visual-style-empty">风格目录暂时不可用，已保留默认电影级真人写实。</div>';
    return;
  }
  container.innerHTML = styles.map(style => `
    <button type="button" class="visual-style-card${style.id === appState.visualStyleId ? ' selected' : ''}"
      data-visual-style-id="${escapeHtml(style.id)}">
      <span class="visual-style-image${style.reference_image ? '' : ' is-fallback'}">
        ${style.reference_image ? `<img src="${escapeHtml(style.reference_image)}" alt="${escapeHtml(style.name)}综合参考图" loading="lazy" onerror="this.style.display='none';this.parentElement.classList.add('is-fallback')">` : ''}
        <span class="visual-style-fallback-mark" aria-hidden="true">${escapeHtml((style.name || '风格').slice(0, 2))}</span>
      </span>
      <span class="visual-style-copy"><strong>${escapeHtml(style.name)}</strong><small>${escapeHtml(style.description || '')}</small>
        <span class="visual-style-tags">${(style.tags || []).map(tag => `<em>${escapeHtml(tag)}</em>`).join('')}</span>
      </span>
    </button>
  `).join('');
}

async function loadVisualStyles() {
  if (visualStylesLoadPromise) {
    return visualStylesLoadPromise.then(styles => {
      if (!Array.isArray(appState.visualStyles) || !appState.visualStyles.length) {
        appState.visualStyles = mergeVisualStyleCatalog(styles);
        renderVisualStyleOptions();
        selectVisualStyle(appState.visualStyleId || 'cinematic_cel', false);
      }
      return appState.visualStyles;
    });
  }
  visualStylesLoadPromise = fetch(`${API_BASE}/api/visual-styles`)
    .then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then(data => {
      appState.visualStyles = mergeVisualStyleCatalog(data?.styles);
      if (!appState.visualStyleId) appState.visualStyleId = 'cinematic_cel';
      renderVisualStyleOptions();
      selectVisualStyle(appState.visualStyleId, false);
      return appState.visualStyles;
    })
    .catch(error => {
      console.warn('加载视觉风格目录失败:', error);
      // Allow a later project dialog open to retry after a transient API failure.
      visualStylesLoadPromise = null;
      appState.visualStyles = mergeVisualStyleCatalog([]);
      renderVisualStyleOptions();
      selectVisualStyle(appState.visualStyleId, false);
      return [];
    });
  return visualStylesLoadPromise;
}

function selectVisualStyle(styleId, announce = true) {
  const normalized = String(styleId || '').trim();
  const style = (appState.visualStyles || []).find(item => item.id === normalized);
  if (!style) {
    if (normalized && !(appState.visualStyles || []).length) {
      void loadVisualStyles().then(() => {
        if ((appState.visualStyles || []).some(item => item.id === normalized)) {
          selectVisualStyle(normalized, announce);
        }
      });
    }
    if (announce) updateStatus('无效的视觉风格，请重新选择');
    return false;
  }
  appState.visualStyleId = style.id;
  appState.artStyle = style.name;
  document.querySelectorAll('[data-visual-style-id]').forEach(card => {
    const selected = card.dataset.visualStyleId === style.id;
    card.classList.toggle('selected', selected);
    card.setAttribute('aria-pressed', selected ? 'true' : 'false');
  });
  if (announce) updateStatus(`已选择视觉风格：${style.name}`);
  return true;
}

function toggleStyleModifier(el) {
  if (!el) return;
  el.classList.toggle('selected');
  appState.styleModifiers = [...document.querySelectorAll('#style-modifier-tags .selected')].map(tag => tag.dataset.modifier || tag.textContent.trim());
}

function showAssetTab(tab) {
  activeAssetTab = ['characters', 'scenes', 'props'].includes(tab) ? tab : 'characters';
  document.querySelectorAll('[data-asset-panel]').forEach(panel => {
    panel.style.display = panel.dataset.assetPanel === activeAssetTab ? '' : 'none';
  });
  document.querySelectorAll('[data-asset-tab]').forEach(button => {
    const active = button.dataset.assetTab === activeAssetTab;
    button.style.borderColor = active ? '#10b981' : '';
    button.style.color = active ? '#10b981' : '';
  });
}

let manualAssetType = '';

function openManualAsset(type) {
  if (!appState.projectId || !appState.currentScript?.asset_manifest) {
    updateStatus('请先完成剧本解析，再手工添加资产');
    return;
  }
  manualAssetType = type;
  const labels = {character:'角色', scene:'场景', prop:'道具'};
  document.getElementById('manual-asset-title').textContent = `添加${labels[type]}`;
  document.getElementById('manual-asset-name').value = '';
  document.getElementById('manual-asset-description').value = '';
  document.getElementById('manual-asset-time').value = '';
  document.getElementById('manual-asset-handheld').checked = false;
  document.getElementById('manual-asset-time-wrap').style.display = type === 'scene' ? '' : 'none';
  document.getElementById('manual-asset-handheld-wrap').style.display = type === 'prop' ? 'flex' : 'none';
  const scenes = appState.currentScript.scenes || [];
  document.getElementById('manual-asset-scenes').innerHTML = scenes.map(scene =>
    `<label style="display:flex;align-items:center;gap:5px;padding:5px 7px;border:1px solid #3f3f46;border-radius:5px;font-size:11px;cursor:pointer"><input type="checkbox" value="${scene.scene_id}"> 镜头 ${scene.scene_id}</label>`
  ).join('') || '<span style="font-size:12px;color:#a1a1aa">没有可绑定镜头</span>';
  document.getElementById('modal-manual-asset').style.display = 'flex';
  document.getElementById('manual-asset-name').focus();
}

async function saveManualAsset() {
  const name = document.getElementById('manual-asset-name').value.trim();
  if (!name) return alert('请填写资产名称');
  const sceneIds = [...document.querySelectorAll('#manual-asset-scenes input:checked')].map(input => Number(input.value));
  if ((manualAssetType === 'scene' || manualAssetType === 'prop') && !sceneIds.length) return alert('请至少选择一个绑定镜头');
  showLoading('btn-save-manual-asset');
  try {
    const res = await fetch(`${API_BASE}/api/projects/${appState.projectId}/assets`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({asset_type:manualAssetType, name, scene_ids:sceneIds,
        description:document.getElementById('manual-asset-description').value.trim(),
        time_condition:document.getElementById('manual-asset-time').value.trim(),
        is_handheld:document.getElementById('manual-asset-handheld').checked}),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '添加资产失败');
    appState.stateJson = data.state_json || appState.stateJson;
    syncScriptAssets(data.script, data.visual_design);
    if (data.production) productionData = data.production;
    closeModal('modal-manual-asset');
    renderRoles(); renderScenes(); renderProps(); renderShots();
    updateStatus(`已添加${{character:'角色', scene:'场景', prop:'道具'}[manualAssetType]}：${name}`);
  } catch (error) {
    alert(error.message || '添加资产失败');
  } finally {
    hideLoading('btn-save-manual-asset', '添加资产');
  }
}

// ═══════════════════════════════════════════════════════════════
//  模态弹窗
// ═══════════════════════════════════════════════════════════════
function openNewProjectModal() {
  const audioMode = document.getElementById('proj-audio-mode');
  if (audioMode) audioMode.value = 'auto';
  appState.styleModifiers = [];
  document.querySelectorAll('#style-modifier-tags .selected').forEach(tag => tag.classList.remove('selected'));
  if (!appState.visualStyles.length) appState.visualStyles = mergeVisualStyleCatalog([]);
  renderVisualStyleOptions();
  selectVisualStyle('cinematic_cel', false);
  void loadVisualStyles();
  document.getElementById('modal-new-project').style.display = 'flex';
}
function closeModal(id) { document.getElementById(id).style.display = 'none'; }

function toggleTag(el) { el.classList.toggle('selected'); }
function selectRatio(el) {
  el.parentElement.querySelectorAll('.tag').forEach(t => t.classList.remove('selected'));
  el.classList.add('selected');
  appState.videoRatio = el.dataset.ratio;
}

async function createProject() {
  const name = document.getElementById('proj-name').value || '未命名项目';
  const genres = [...document.querySelectorAll('#genre-tags .selected')].map(t => t.textContent);
  const selectedStyle = (appState.visualStyles || []).find(style => style.id === appState.visualStyleId)
    || (appState.visualStyles || [])[0];
  const visualStyleId = selectedStyle?.id || 'cinematic_cel';
  const styleModifiers = [...document.querySelectorAll('#style-modifier-tags .selected')]
    .map(tag => tag.dataset.modifier || tag.textContent.trim());
  const artStyle = [selectedStyle?.name || '现代电影级二维赛璐璐', ...styleModifiers].join('、');
  const videoRatio = document.querySelector('[data-ratio].selected')?.dataset?.ratio || '9:16';
  appState.visualStyleId = visualStyleId;
  appState.artStyle = artStyle;
  const audioMode = document.getElementById('proj-audio-mode')?.value || 'auto';
  const source = 'idea';
  resetProjectSession({ projectName: name, genreTags: genres, artStyle, visualStyleId, styleModifiers, videoRatio, audioMode });
  appState.scriptInputMode = source;
  const snapshot = projectSessionSnapshot();
  const controller = sessionController();
  let created = false;

  // 调后端创建项目
  try {
    const res = await fetch(`${API_BASE}/api/projects`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ title: name, genre_tags: genres.join('、'), art_style: artStyle, visual_style_id: visualStyleId, style_modifiers: styleModifiers, video_ratio: videoRatio, script_source: source, audio_mode: audioMode }),
      signal: controller.signal,
    });
    const data = await res.json();
    if (!isCurrentProjectSession(snapshot)) return;
    if (!res.ok) throw new Error(data.detail || '创建项目失败');
    appState.projectId = data.project_id;
    created = true;
    document.getElementById('project-info').innerHTML = `
      <b>${name}</b><br>
      类型: ${genres.join('、') || '未选择'}<br>
      风格: ${escapeHtml(selectedStyle?.name || '现代电影级二维赛璐璐')}${styleModifiers.length ? ` · ${escapeHtml(styleModifiers.join('、'))}` : ''}<br>
      比例: ${videoRatio}<br>
      <span style="font-size:10px;color:#52525b">ID: ${data.project_id.slice(0,8)}</span>
    `;
  } catch(e) {
    if (e.name === 'AbortError' || !isCurrentProjectSession(snapshot)) return;
    document.getElementById('project-info').innerHTML = `<b>${name}</b><br><span style="color:#ef4444">创建失败</span>`;
  } finally {
    releaseSessionController(controller);
  }
  if (!created) return;

  closeModal('modal-new-project');
  navigateTo('script');
  appState.scriptPhase = 'source_required';
  renderScriptWorkspace();
  updateStatus('项目已创建，请选择创作方式');
  startProjectJobPolling();
  loadProjects();  // 刷新首页项目列表
}

function openIdeaModal() {
  if (!appState.projectId) return openNewProjectModal();
  document.getElementById('modal-idea-script').style.display = 'flex';
}

function openImportModal() {
  if (!appState.projectId) return openNewProjectModal();
  document.getElementById('import-script-text').value = appState.scriptDraftText || '';
  document.getElementById('modal-import-script').style.display = 'flex';
}

function openDraftModal() {
  const editor = document.getElementById('script-draft-editor');
  if (editor && document.activeElement !== editor) editor.value = appState.scriptDraftText || '';
  document.getElementById('btn-draft-regenerate').style.display = appState.scriptInputMode === 'idea' ? '' : 'none';
  document.getElementById('modal-script-draft').style.display = 'flex';
}

function renderScriptWorkspace() {
  const workspace = document.getElementById('script-workspace');
  if (!workspace) return;
  const phase = appState.scriptPhase || (appState.currentScript ? 'confirmed' : 'source_required');
  if (phase === 'confirmed' && appState.currentScript) {
    workspace.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px"><div><b>剧本已确认</b><div style="font-size:12px;color:#a1a1aa;margin-top:6px">结构解析与视觉提示词会在后台完成。修改结构化剧本后需要重新解析下游内容。</div></div><div style="display:flex;gap:8px"><button class="btn-secondary btn-sm" onclick="StudioActions.openDraftModal()">查看原始草稿</button><button class="btn-primary btn-sm" onclick="StudioActions.reparseConfirmedScript()">重新解析</button></div></div>`;
    return;
  }
  if (phase === 'analyzing') {
    workspace.innerHTML = '<b>正在确认并解析剧本</b><div style="font-size:12px;color:#a1a1aa;margin-top:8px">正在提取结构、角色、场景、关键道具与分镜基础数据。</div>';
    return;
  }
  if (phase === 'draft_generating') {
    workspace.innerHTML = '<b>剧本草稿生成中</b><div style="font-size:12px;color:#a1a1aa;margin-top:8px">任务完成后可以编辑正文并确认解析。</div>';
    return;
  }
  if (appState.scriptDraftText) {
    workspace.innerHTML = '<b>剧本草稿待确认</b><div style="font-size:12px;color:#a1a1aa;margin-top:8px">草稿尚未进入资产和分镜流程。</div><div style="margin-top:14px"><button class="btn-primary" onclick="StudioActions.openDraftModal()">继续编辑草稿</button></div>';
    return;
  }
  const ideaClass = appState.scriptInputMode === 'idea' ? 'btn-primary' : 'btn-secondary';
  const importClass = appState.scriptInputMode === 'script' ? 'btn-primary' : 'btn-secondary';
  workspace.innerHTML = `<b>尚未开始剧本</b><div style="font-size:12px;color:#a1a1aa;margin-top:8px">选择一种方式建立可编辑草稿，确认后才会解析制作资产。</div><div style="display:flex;gap:8px;margin-top:14px"><button class="${ideaClass}" onclick="StudioActions.openIdeaModal()">AI 创意生成</button><button class="${importClass}" onclick="StudioActions.openImportModal()">输入已有剧本</button></div>`;
}

async function generateDraftFromIdea() {
  const idea = document.getElementById('idea-input').value.trim();
  if (!idea) return updateStatus('请先输入故事创意');
  const running = activeProjectJobs(job => job.job_type === 'script.draft.generate');
  if (running.length) return cancelMatchingJobs(running);
  appState.scriptPhase = 'draft_generating'; renderScriptWorkspace();
  try {
  const submitted = await submitProjectJob({
    job_type: 'script.draft.generate', target_type: 'script_draft', target_id: 'current',
    payload: { idea, input_mode: 'idea', details: {} },
    });
    await waitForProjectJob(submitted.job.job_id);
  } catch (error) { updateStatus(`草稿生成失败：${error.message}`); }
}

async function saveImportedDraft() {
  const text = document.getElementById('import-script-text').value.trim();
  if (!text) return updateStatus('请粘贴或导入剧本正文');
  try {
    const saved = await persistScriptText(text, 'script');
    if (!saved) return;
    await confirmDraftAndAnalyze({ skipSave: true });
  } catch (error) {
    updateStatus(`确认解析失败：${error.message}`);
  }
}

async function persistScriptText(text, inputMode) {
  const draftText = (text || '').trim();
  if (!draftText) return updateStatus('剧本草稿不能为空');
  const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/script/draft`, {
    method: 'PUT', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({draft_text: draftText, input_mode: inputMode, source_filename: appState.sourceFilename}),
  }, '保存草稿失败');
  if (!data) return false;
  appState.scriptInputMode = inputMode;
  appState.scriptDraftText = draftText;
  appState.scriptPhase = data.script_phase || 'draft_ready';
  appState.stateJson = data.state_json || appState.stateJson;
  renderScriptWorkspace();
  updateStatus('草稿已保存，确认后才会开始解析');
  return true;
}

async function saveDraft() {
  const editor = document.getElementById('script-draft-editor');
  try {
    return await persistScriptText(editor?.value || appState.scriptDraftText, appState.scriptInputMode);
  } catch (error) {
    updateStatus(`保存草稿失败：${error.message}`);
    return false;
  }
}

async function reviseDraft() {
  const instruction = document.getElementById('script-draft-instruction').value.trim();
  if (!instruction) return updateStatus('请输入草稿改写要求');
  try {
    if (!(await saveDraft())) return;
    const submitted = await submitProjectJob({
      job_type: 'script.draft.revise', target_type: 'script_draft', target_id: 'current', payload: {instruction},
    });
    if (!submitted?.job) return;
    appState.scriptPhase = 'draft_generating'; renderScriptWorkspace();
    await waitForProjectJob(submitted.job.job_id);
  } catch (error) {
    updateStatus(`草稿改写失败：${error.message}`);
  }
}

async function confirmDraftAndAnalyze({ skipSave = false } = {}) {
  if (scriptConfirmInFlight || appState.scriptPhase === 'analyzing') {
    updateStatus('剧本解析任务已提交，正在处理中');
    return;
  }
  scriptConfirmInFlight = true;
  const confirmButton = document.getElementById('btn-confirm-script');
  if (confirmButton) confirmButton.disabled = true;
  try {
    if (!skipSave && !(await saveDraft())) {
      scriptConfirmInFlight = false;
      if (confirmButton) confirmButton.disabled = false;
      return;
    }
    updateStatus('正在提交剧本解析任务');
    const submitted = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/script/draft/confirm`, {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({source_filename: appState.sourceFilename}),
    }, '确认解析失败');
    if (!submitted?.job) {
      scriptConfirmInFlight = false;
      if (confirmButton) confirmButton.disabled = false;
      return;
    }
    appState.scriptPhase = 'analyzing';
    scriptConfirmInFlight = false;
    closeModal('modal-script-draft');
    closeModal('modal-import-script');
    mergeProjectJob(submitted.job); renderProjectJobs(); renderScriptWorkspace();
  } catch (error) {
    scriptConfirmInFlight = false;
    if (confirmButton) confirmButton.disabled = false;
    updateStatus(`确认解析失败：${error.message}`);
  }
}

async function reparseConfirmedScript() {
  if (!confirm('重新解析将使当前资产、分镜、视频、音频和成片状态过期，但保留历史文件与版本。确认继续？')) return;
  openDraftModal();
}

// ═══════════════════════════════════════════════════════════════
//  字数统计
// ═══════════════════════════════════════════════════════════════
// ═══════════════════════════════════════════════════════════════
//  API 调用辅助
// ═══════════════════════════════════════════════════════════════
async function apiCall(endpoint, data) {
  const snapshot = projectSessionSnapshot();
  const controller = sessionController();
  updateStatus('⏳ 调用 AI 中…');
  beginApiProgress('AI 正在处理中');
  try {
    const res = await fetch(`${API_BASE}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
      signal: controller.signal,
    });
    if (!res.ok) { const err = await res.json(); throw new Error(err.detail || '请求失败'); }
    const json = await res.json();
    if (!isCurrentProjectSession(snapshot)) return null;
    updateStatus('✅ 完成');
    return json;
  } catch (e) {
    if (e.name === 'AbortError' || !isCurrentProjectSession(snapshot)) return null;
    updateStatus('❌ 错误: ' + e.message);
    console.error(e);
    return null;
  } finally {
    releaseSessionController(controller);
    if (isCurrentProjectSession(snapshot)) endApiProgress();
  }
}

function updateStatus(msg) {
  document.getElementById('gen-status').innerHTML = msg;
  void globalThis.StudioActions.checkApiHealth?.();
}

function showLoading(btnId) {
  const btn = document.getElementById(btnId);
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spin"></span> 生成中…'; }
}
function hideLoading(btnId, text) {
  const btn = document.getElementById(btnId);
  if (btn) { btn.disabled = false; btn.innerHTML = text; }
}

// ═══════════════════════════════════════════════════════════════
//  核心功能：AI 生成剧本
// ═══════════════════════════════════════════════════════════════
function setScriptInputMode(mode) {
  appState.scriptInputMode = mode;
  renderScriptWorkspace();
}

async function importScriptSource(input) {
  const file = input.files?.[0];
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch(`${API_BASE}/api/script/import`, { method: 'POST', body: form });
    if (!res.ok) throw new Error((await res.json()).detail || 'Import failed');
    const data = await res.json();
    const importEditor = document.getElementById('import-script-text');
    if (importEditor) importEditor.value = data.text;
    appState.sourceFilename = data.filename;
    document.getElementById('script-source-filename').textContent = data.filename;
  } catch (error) { alert(error.message); }
  finally { input.value = ''; }
}

function renderStructuredScript(script) {
  if (!script) return;
  appState.currentScript = script;
  document.getElementById('script-result').style.display = 'block';
  document.getElementById('script-result-title').textContent =
    appState.scriptInputMode === 'idea' ? '已生成剧本' : '剧本解析结果';
  document.getElementById('structured-script-editor').value = JSON.stringify(script, null, 2);
  document.getElementById('script-readable-analysis').innerHTML = renderReadableScript(script);
  appState.scriptPhase = 'confirmed';
  renderScriptWorkspace();
}

function renderReadableScript(script) {
  const scenes = Array.isArray(script.scenes) ? script.scenes : [];
  const allCharacters = [...new Set(scenes.flatMap(scene => scene.characters || []))];
  const dialogueCount = scenes.reduce((count, scene) => count + (scene.dialogue || []).length, 0);
  const characterText = allCharacters.length
    ? allCharacters.map(name => `<span style="display:inline-block;margin:0 6px 6px 0;padding:3px 8px;border:1px solid #3f3f46;border-radius:4px">${escapeHtml(name)}</span>`).join('')
    : '<span style="color:#71717a">未识别到明确角色</span>';
  const timeline = scenes.map((scene, index) => {
    const dialogue = (scene.dialogue || []).map(item =>
      `<div style="margin-top:5px"><b>${escapeHtml(item.character)}：</b>${escapeHtml(item.line)}${item.emotion ? ` <span style="color:#a1a1aa">(${escapeHtml(item.emotion)})</span>` : ''}</div>`
    ).join('');
    const roleNames = (scene.characters || []).map(escapeHtml).join('、') || '无明确角色';
    return `<div style="border-left:3px solid #10b981;padding:10px 12px;margin:8px 0;background:#18181b">
      <div style="display:flex;justify-content:space-between;gap:12px;align-items:baseline"><b>第 ${escapeHtml(scene.scene_id || index + 1)} 场：${escapeHtml(scene.location)}</b><span style="color:#a1a1aa;font-size:12px">${escapeHtml(scene.duration_sec)} 秒 / ${escapeHtml(scene.emotion_tone)}</span></div>
      <div style="color:#d4d4d8;margin-top:6px">${escapeHtml(scene.action_description)}</div>
      <div style="color:#a1a1aa;font-size:12px;margin-top:6px">角色：${roleNames}　镜头：${escapeHtml(scene.camera_direction)}　转场：${escapeHtml(scene.transition_from_prev || 'cut')}</div>
      ${dialogue}
    </div>`;
  }).join('') || '<div style="color:#71717a">暂无场景内容</div>';
  const turningPoints = scenes.map((scene, index) =>
    `<li>第 ${escapeHtml(scene.scene_id || index + 1)} 场：${escapeHtml(scene.action_description)} <span style="color:#a1a1aa">(${escapeHtml(scene.emotion_tone)})</span></li>`
  ).join('');
  const productionRows = scenes.map((scene, index) =>
    `<tr><td style="padding:7px">${escapeHtml(scene.scene_id || index + 1)}</td><td style="padding:7px">${escapeHtml(scene.duration_sec)} 秒</td><td style="padding:7px">${escapeHtml(scene.location)}</td><td style="padding:7px">${(scene.characters || []).map(escapeHtml).join('、')}</td><td style="padding:7px">${escapeHtml(scene.camera_direction)}</td></tr>`
  ).join('');
  return `<div style="display:grid;gap:10px">
    <details class="script-analysis-section" open><summary>剧本概览</summary><div class="script-analysis-body"><div style="font-size:18px;font-weight:700">${escapeHtml(script.title)}</div><div style="color:#a1a1aa;margin-top:4px">${escapeHtml(script.genre)} · 约 ${escapeHtml(script.duration_sec)} 秒 · ${escapeHtml(script.narration_style)} · 面向 ${escapeHtml(script.target_audience || '通用')}</div><div style="margin-top:10px;line-height:1.7">${escapeHtml(script.logline)}</div></div></details>
    <details class="script-analysis-section" open><summary>角色与对白</summary><div class="script-analysis-body"><div>${characterText}</div><div style="color:#a1a1aa;font-size:12px;margin-top:6px">共 ${dialogueCount} 句对白；角色外形与图片提示词将在下一步角色制作中生成。</div></div></details>
    <details class="script-analysis-section"><summary>剧情推进与冲突节点</summary><div class="script-analysis-body"><ol style="margin:0;padding-left:22px;line-height:1.75">${turningPoints}</ol></div></details>
    <details class="script-analysis-section"><summary>场景时间线</summary><div class="script-analysis-body">${timeline}</div></details>
    <details class="script-analysis-section"><summary>制作场景表</summary><div class="script-analysis-body"><div style="overflow:auto"><table style="width:100%;border-collapse:collapse;font-size:12px"><thead style="color:#a1a1aa"><tr><th style="text-align:left;padding:7px">场次</th><th style="text-align:left;padding:7px">时长</th><th style="text-align:left;padding:7px">地点</th><th style="text-align:left;padding:7px">角色</th><th style="text-align:left;padding:7px">镜头</th></tr></thead><tbody>${productionRows}</tbody></table></div></div></details>
  </div>`;
}

function scriptToAnalysisText(script) {
  const scenes = (script.scenes || []).map((scene, index) => {
    const dialogue = (scene.dialogue || []).map(item => `${item.character}：${item.line}`).join('\n');
    return `第${scene.scene_id || index + 1}场\n地点：${scene.location}\n角色：${(scene.characters || []).join('、')}\n剧情：${scene.action_description}\n情绪：${scene.emotion_tone}\n镜头：${scene.camera_direction}${dialogue ? `\n对白：\n${dialogue}` : ''}`;
  }).join('\n\n');
  return `标题：${script.title}\n类型：${script.genre}\n故事梗概：${script.logline}\n\n${scenes}`;
}

function syncScriptAssets(script, visualDesign) {
  if (script) appState.currentScript = script;
  if (visualDesign?.global_character_descriptions || visualDesign?.global_character_descriptions_cn || script?.asset_manifest) {
    appState.roles = rolesFromVisualDesign(visualDesign, script);
    renderRoles();
  }
  if (script?.scenes) {
    appState.scenes = sceneAssetsFromScript(script);
    appState.props = propAssetsFromScript(script);
    appState.shots = buildShotsFromStateJson() || script.scenes.map((scene, index) => buildShotFromScene(scene, index));
    renderScenes();
    renderProps();
    renderShots();
  }
}

function sceneAssetsFromScript(script) {
  const manifest = script?.asset_manifest;
  if (Array.isArray(manifest?.locations) && manifest.locations.length) {
    return manifest.locations.flatMap((location, index) => {
      const states = Array.isArray(location.states) && location.states.length
        ? location.states
        : [{
          state_id: location.default_state_id || '',
          label: location.time_condition || 'default',
          time_condition: location.time_condition || '',
          weather: '',
          light_direction: '',
          visible_state_notes: '',
          shot_ids: Array.isArray(location.shot_ids) ? location.shot_ids : [],
          representative_scene_id: Number(location.representative_scene_id),
        }];
      return states.map((state, stateIndex) => {
        const label = state.label || state.time_condition || `state ${stateIndex + 1}`;
        const stateSuffix = states.length > 1 ? ` / ${label}` : '';
        const notes = [
          location.identity_description,
          location.fixed_dressing,
          location.environment_description,
          state.visible_state_notes,
        ].filter(Boolean).join(' ');
        return {
          sceneId: Number(state.representative_scene_id || location.representative_scene_id),
          locationId: location.location_id,
          stateId: state.state_id || location.default_state_id || '',
          stateLabel: label,
          name: `${location.canonical_name || `场景 ${index + 1}`}${stateSuffix}`,
          time: state.time_condition || location.time_condition || '',
          weather: state.weather || '',
          light: state.light_direction || '',
          environment: notes || location.environment_description || '',
          camera: '',
          description: notes || location.environment_description || '',
          shotIds: Array.isArray(state.shot_ids) && state.shot_ids.length ? state.shot_ids : (Array.isArray(location.shot_ids) ? location.shot_ids : []),
        };
      });
    });
  }
  return (script?.scenes || []).map(scene => ({
    sceneId: Number(scene.scene_id),
    name: `${scene.location}-${scene.emotion_tone}`,
    time: scene.emotion_tone,
    environment: scene.action_description,
    camera: scene.camera_direction,
    description: scene.action_description,
    shotIds: [Number(scene.scene_id)],
  }));
}

function propAssetsFromScript(script) {
  return (script?.asset_manifest?.props || []).map(prop => ({
    propId: prop.prop_id,
    name: prop.name,
    description: prop.description || '',
    shotIds: Array.isArray(prop.shot_ids) ? prop.shot_ids : [],
    isHandheld: Boolean(prop.is_handheld),
  }));
}

async function saveStructuredScript() {
  const snapshot = projectSessionSnapshot();
  const controller = sessionController();
  try {
    const script = JSON.parse(document.getElementById('structured-script-editor').value);
    let payload = {script};
    let res = await fetch(`${API_BASE}/api/projects/${appState.projectId}/script`, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload), signal: controller.signal });
    if (res.status === 409) {
      const conflict = await res.json();
      if (conflict?.detail?.code === 'visual_task_running') {
        const restart = window.confirm('视觉与分镜提示词仍在生成。确定：终止旧任务并按当前剧本重新生成；取消：保留旧任务，完成后只标记为过期。');
        payload = {script, visual_conflict_action: restart ? 'restart' : 'continue'};
        res = await fetch(`${API_BASE}/api/projects/${appState.projectId}/script`, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload), signal: controller.signal });
      }
    }
    if (!res.ok) throw new Error((await res.json()).detail || 'Save failed');
    const data = await res.json();
    if (!isCurrentProjectSession(snapshot)) return;
    appState.stateJson = data.state_json;
    renderStructuredScript(data.script);
    syncScriptAssets(data.script, JSON.parse(data.state_json).visual_design);
    restoreProjectMedia(JSON.parse(data.state_json), {});
    renderRecoveredMedia();
    updateStatus(data.visual_stage === 'stale'
      ? '剧本已保存，旧视觉与分镜提示词已标记为过期'
      : '剧本已保存，正在后台生成视觉与分镜提示词');
  } catch (error) {
    if (error.name !== 'AbortError' && isCurrentProjectSession(snapshot)) alert(error.message);
  } finally { releaseSessionController(controller); }
}

async function reviseStructuredScript() {
  const instruction = document.getElementById('script-revision-instruction').value.trim();
  if (!instruction) return;
  beginApiProgress('AI 正在修改剧本');
  const snapshot = projectSessionSnapshot();
  const controller = sessionController();
  try {
    const res = await fetch(`${API_BASE}/api/projects/${appState.projectId}/script/revise`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({instruction}), signal: controller.signal });
    if (!res.ok) throw new Error((await res.json()).detail || 'AI revision failed');
    const data = await res.json();
    if (!isCurrentProjectSession(snapshot)) return;
    appState.stateJson = data.state_json;
    renderStructuredScript(data.script);
    syncScriptAssets(data.script, JSON.parse(data.state_json).visual_design);
    restoreProjectMedia(JSON.parse(data.state_json), {});
    renderRecoveredMedia();
    updateStatus('剧本已修改，正在后台生成视觉与分镜提示词');
  } catch (error) {
    if (error.name !== 'AbortError' && isCurrentProjectSession(snapshot)) alert(error.message);
  } finally {
    releaseSessionController(controller);
    if (isCurrentProjectSession(snapshot)) endApiProgress();
  }
}

async function ensureProjectForAnalysis(sourceText) {
  if (appState.projectId) return appState.projectId;
  const snapshot = projectSessionSnapshot();
  const controller = sessionController();
  const title = appState.projectName || sourceText.replace(/\s+/g, ' ').slice(0, 20) || '未命名项目';
  try {
    const res = await fetch(`${API_BASE}/api/projects`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        title,
        art_style: appState.artStyle || '',
        visual_style_id: appState.visualStyleId || 'cinematic_cel',
        style_modifiers: appState.styleModifiers || [],
        video_ratio: appState.videoRatio || '9:16',
      }), signal: controller.signal,
    });
    const data = await res.json();
    if (!isCurrentProjectSession(snapshot)) throw new DOMException('Project session changed', 'AbortError');
    if (!res.ok) throw new Error(data.detail || '创建项目失败');
    appState.projectId = data.project_id;
    appState.projectName = title;
    document.getElementById('project-info').innerHTML = `<b>${escapeHtml(title)}</b><br><span style="font-size:10px;color:#52525b">ID: ${data.project_id.slice(0,8)}</span>`;
    return data.project_id;
  } finally { releaseSessionController(controller); }
}

async function analyzeCurrentScript() {
  return confirmDraftAndAnalyze();
}

async function generateScript() {
  return openIdeaModal();
}

// ═══════════════════════════════════════════════════════════════
//  角色渲染
// ═══════════════════════════════════════════════════════════════
// 将本地绝对路径转为 /output/ 相对 URL
const __moduleActions = {
  showAssetTab,
  openManualAsset,
  saveManualAsset,
  openNewProjectModal,
  loadVisualStyles,
  selectVisualStyle,
  toggleStyleModifier,
  closeModal,
  toggleTag,
  selectRatio,
  createProject,
  openIdeaModal,
  openImportModal,
  openDraftModal,
  renderScriptWorkspace,
  generateDraftFromIdea,
  saveImportedDraft,
  saveDraft,
  reviseDraft,
  confirmDraftAndAnalyze,
  reparseConfirmedScript,
  apiCall,
  updateStatus,
  showLoading,
  hideLoading,
  setScriptInputMode,
  importScriptSource,
  renderStructuredScript,
  renderReadableScript,
  scriptToAnalysisText,
  syncScriptAssets,
  sceneAssetsFromScript,
  propAssetsFromScript,
  saveStructuredScript,
  reviseStructuredScript,
  ensureProjectForAnalysis,
  analyzeCurrentScript,
  generateScript,
};
Object.assign(globalThis.StudioActions, __moduleActions);
Object.defineProperties(globalThis, {
  activeAssetTab: { configurable: true, get: () => activeAssetTab, set: value => { activeAssetTab = value; } },
  manualAssetType: { configurable: true, get: () => manualAssetType, set: value => { manualAssetType = value; } },
});
export { __moduleActions };
