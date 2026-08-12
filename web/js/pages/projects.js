const buildShotFromScene = (...args) => globalThis.StudioActions.buildShotFromScene(...args);
const buildShotsFromStateJson = (...args) => globalThis.StudioActions.buildShotsFromStateJson(...args);
const closeModal = (...args) => globalThis.StudioActions.closeModal(...args);
const isCurrentProjectSession = (...args) => globalThis.StudioActions.isCurrentProjectSession(...args);
const navigateTo = (...args) => globalThis.StudioActions.navigateTo(...args);
const projectSessionSnapshot = (...args) => globalThis.StudioActions.projectSessionSnapshot(...args);
const propAssetsFromScript = (...args) => globalThis.StudioActions.propAssetsFromScript(...args);
const releaseSessionController = (...args) => globalThis.StudioActions.releaseSessionController(...args);
const renderProps = (...args) => globalThis.StudioActions.renderProps(...args);
const markMissingMediaPaths = (...args) => globalThis.StudioActions.markMissingMediaPaths(...args);
const renderRecoveredMedia = (...args) => globalThis.StudioActions.renderRecoveredMedia(...args);
const renderRoles = (...args) => globalThis.StudioActions.renderRoles(...args);
const renderScenes = (...args) => globalThis.StudioActions.renderScenes(...args);
const renderShots = (...args) => globalThis.StudioActions.renderShots(...args);
const renderStructuredScript = (...args) => globalThis.StudioActions.renderStructuredScript(...args);
const resetProjectSession = (...args) => globalThis.StudioActions.resetProjectSession(...args);
const restoreProjectMedia = (...args) => globalThis.StudioActions.restoreProjectMedia(...args);
const rolesFromVisualDesign = (...args) => globalThis.StudioActions.rolesFromVisualDesign(...args);
const sceneAssetsFromScript = (...args) => globalThis.StudioActions.sceneAssetsFromScript(...args);
const sessionController = (...args) => globalThis.StudioActions.sessionController(...args);
const setScriptInputMode = (...args) => globalThis.StudioActions.setScriptInputMode(...args);
const startProjectJobPolling = (...args) => globalThis.StudioActions.startProjectJobPolling(...args);
const updateStatus = (...args) => globalThis.StudioActions.updateStatus(...args);
const loadVisualStyles = (...args) => globalThis.StudioActions.loadVisualStyles(...args);
const selectVisualStyle = (...args) => globalThis.StudioActions.selectVisualStyle(...args);

async function loadProjects() {
  const container = document.getElementById('project-list');
  if (container) {
    container.innerHTML = '<div style="color:#71717a;padding:40px;text-align:center;grid-column:1/-1">正在加载项目...</div>';
  }
  try {
    const res = await fetch(`${API_BASE}/api/projects`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const projects = await res.json();
    if (!container) return;
    if (!projects.length) {
      container.innerHTML = '<div style="color:#71717a;padding:40px;text-align:center;grid-column:1/-1">暂无项目，点击「新建项目」开始创作</div>';
      return;
    }
    container.innerHTML = projects.map(p => `
      <div class="card" style="cursor:pointer" onclick="StudioActions.openProject('${p.id}')">
        <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:8px">
          <h4 style="font-size:14px;margin:0">${escapeHtml(p.title.slice(0,30))}</h4>
          <span style="font-size:10px;padding:2px 8px;border-radius:4px;background:${p.status==='completed'?'#064e3b':'#1e293b'};color:${p.status==='completed'?'#10b981':'#94a3b8'}">${p.status}</span>
        </div>
        <div style="font-size:11px;color:#71717a">${escapeHtml(p.user_input?.slice(0,50)||'')}</div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px">
          <span style="font-size:10px;color:#52525b">${p.created_at}</span>
          <span class="card-actions" style="display:flex;gap:4px" onclick="event.stopPropagation()">
            <button class="btn-secondary btn-sm" onclick="StudioActions.openEditModal('${p.id}')" title="修改">🔧</button>
            <button class="btn-secondary btn-sm" onclick="StudioActions.copyProject('${p.id}')" title="复制">📋</button>
            <button class="btn-secondary btn-sm" style="color:#ef4444" onclick="StudioActions.deleteProject('${p.id}')" title="删除">🗑️</button>
          </span>
        </div>
      </div>
    `).join('');
  } catch(e) {
    console.error('加载项目失败:', e);
    if (container) {
      container.innerHTML = '<div style="color:#ef4444;padding:40px;text-align:center;grid-column:1/-1">项目列表加载失败，请检查 API 连接后重试。</div>';
    }
  }
}

// ── 防 XSS ──
function escapeHtml(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ── 恢复已有项目（加载完整数据） ──
async function openProject(projectId) {
  resetProjectSession({ projectId });
  const snapshot = projectSessionSnapshot();
  const controller = sessionController();
  document.getElementById('project-info').innerHTML = `<b>加载中...</b>`;
  navigateTo('script');
  try {
    const res = await fetch(`${API_BASE}/api/projects/${projectId}`, { signal: controller.signal });
    if (!res.ok) throw new Error('项目不存在');
    const data = await res.json();
    if (!isCurrentProjectSession(snapshot)) return;
    const meta = data.meta || {};
    const assets = meta.assets || {};
    appState.stateJson = assets.state_json || '';
    appState.projectName = meta.title || '';

    let savedState = null;
    try { savedState = appState.stateJson ? JSON.parse(appState.stateJson) : null; } catch(e) {}
    const profile = savedState?.creative_profile || {};
    appState.genreTags = profile.genre_tags || [];
    appState.artStyle = profile.art_style || '写实';
    const legacyStyle = String(appState.artStyle || '').split(/[、,，]/)[0].trim();
    const styleAliases = {
      '写实': 'live_action_cinematic', '电影级写实': 'live_action_cinematic', '真人': 'live_action_cinematic',
      '动漫': 'cinematic_cel', '动漫风': 'cinematic_cel', '二次元': 'cinematic_cel',
      '现代电影级二维赛璐璐': 'cinematic_cel', '半写实': 'semi_realistic_anime', '半写实动漫': 'semi_realistic_anime',
      '日系': 'japanese_animation', '日系动画': 'japanese_animation', '日系商业动画': 'japanese_animation',
    };
    appState.visualStyleId = profile.visual_style_id || styleAliases[legacyStyle] || 'live_action_cinematic';
    appState.styleModifiers = Array.isArray(profile.style_modifiers) ? [...profile.style_modifiers] : [];
    appState.videoRatio = profile.video_ratio || '9:16';
    appState.audioMode = profile.audio_mode || 'auto';
    markMissingMediaPaths(data.missing_media_paths || [], savedState);
    restoreProjectMedia(savedState, data.assets || {});
    markMissingMediaPaths(data.missing_media_paths || []);

    // Restore structured content from the separate project record or saved state.
    const restoredScript = assets.script || savedState?.script;
    appState.scriptInputMode = savedState?.input_mode || 'idea';
    appState.sourceFilename = savedState?.source_filename || '';
    appState.scriptPhase = savedState?.script_phase || (restoredScript ? 'confirmed' : 'source_required');
    appState.scriptDraftText = savedState?.script_draft_text || '';
    appState.scriptDraftInstruction = savedState?.script_draft_instruction || '';
    appState.draftJobId = savedState?.draft_job_id || '';
    setScriptInputMode(appState.scriptInputMode);
    void loadVisualStyles().then(() => selectVisualStyle(appState.visualStyleId, false));
    document.getElementById('script-source-filename').textContent = appState.sourceFilename;
    const restoredVisual = assets.visual_design || savedState?.visual_design;
    if (restoredScript) {
      appState.scriptText = JSON.stringify(restoredScript, null, 2);
      renderStructuredScript(restoredScript);
      // 提取角色
      if (restoredVisual?.global_character_descriptions || restoredVisual?.global_character_descriptions_cn || restoredScript?.asset_manifest) {
        appState.roles = rolesFromVisualDesign(restoredVisual, restoredScript);
      }
      // 提取场景
      if (restoredScript.scenes) {
        appState.scenes = sceneAssetsFromScript(restoredScript);
        appState.props = propAssetsFromScript(restoredScript);
        appState.shots = buildShotsFromStateJson() || restoredScript.scenes.map((s, i) => buildShotFromScene(s, i));
      }
    }
    document.getElementById('project-info').innerHTML =
      `<b>${escapeHtml(meta.title?.slice(0,20)||'项目')}</b><br>状态: ${meta.status}`;
    renderRoles();
    renderScenes();
    renderProps();
    renderShots();
    renderRecoveredMedia();
    renderScriptWorkspace();
    updateStatus(`已恢复项目 ${projectId.slice(0,8)}`);
    startProjectJobPolling();
  } catch(e) {
    if (e.name === 'AbortError' || !isCurrentProjectSession(snapshot)) return;
    document.getElementById('project-info').innerHTML = `<b>项目 ${projectId.slice(0,8)}</b><br>加载失败`;
    updateStatus('⚠️ 项目数据加载失败');
  } finally {
    releaseSessionController(controller);
  }
}

// ── 删除项目 ──
async function deleteProject(projectId) {
  if (!confirm('确定删除此项目？所有资产将被永久删除，不可恢复。')) return;
  try {
    await fetch(`${API_BASE}/api/projects/${projectId}`, { method: 'DELETE' });
    updateStatus('✅ 项目已删除');
    loadProjects();
  } catch(e) { updateStatus('❌ 删除失败'); }
}

// ── 重命名项目 ──
// ── 修改项目：弹出编辑窗口 ──
async function openEditModal(projectId) {
  try {
    const res = await fetch(`${API_BASE}/api/projects/${projectId}`);
    if (!res.ok) throw new Error('项目不存在');
    const data = await res.json();
    const meta = data.meta || {};
    document.getElementById('edit-project-id').value = projectId;
    document.getElementById('edit-proj-name').value = meta.title || '';
    document.getElementById('edit-user-input').value = meta.user_input || '';
    document.getElementById('edit-status').value = meta.status || 'created';
    document.getElementById('modal-edit-project').style.display = 'flex';
  } catch(e) { updateStatus('❌ 加载项目失败'); }
}

async function saveEditProject() {
  const projectId = document.getElementById('edit-project-id').value;
  const title = document.getElementById('edit-proj-name').value || '未命名';
  const userInput = document.getElementById('edit-user-input').value;
  const status = document.getElementById('edit-status').value;

  // 更新名称
  try {
    await fetch(`${API_BASE}/api/projects/${projectId}/rename`, {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({title}),
    });
    // 同步更新剧本内容（如果填了）
    if (userInput) {
      await fetch(`${API_BASE}/api/script`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          user_input: userInput, project_id: projectId,
          genre_tags: appState.genreTags.join('、') || '都市',
          art_style: appState.artStyle || '写实',
          visual_style_id: appState.visualStyleId || '',
          style_modifiers: appState.styleModifiers || [],
          video_ratio: appState.videoRatio || '9:16',
        }),
      });
    }
  } catch(e) { updateStatus('❌ 保存失败'); }

  closeModal('modal-edit-project');
  updateStatus('✅ 项目已更新');
  loadProjects();
}

// ── 复制项目 ──
async function copyProject(projectId) {
  if (!confirm('确定复制此项目？将创建包含所有元数据的副本。')) return;
  try {
    const res = await fetch(`${API_BASE}/api/projects/${projectId}/copy`, { method: 'POST' });
    const data = await res.json();
    updateStatus(`✅ 已复制 → 新项目 ID: ${data.new_project_id?.slice(0,8)}`);
    loadProjects();
  } catch(e) { updateStatus('❌ 复制失败'); }
}

// ═══════════════════════════════════════════════════════════════
//  API 配置功能
// ═══════════════════════════════════════════════════════════════

// ── 服务商联动配置 ──
const __moduleActions = {
  loadProjects,
  escapeHtml,
  openProject,
  deleteProject,
  openEditModal,
  saveEditProject,
  copyProject,
};
Object.assign(globalThis.StudioActions, __moduleActions);
export { __moduleActions };
