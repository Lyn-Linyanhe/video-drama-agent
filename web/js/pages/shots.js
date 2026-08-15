const activeProjectJobs = (...args) => globalThis.StudioActions.activeProjectJobs(...args);
const apiCall = (...args) => globalThis.StudioActions.apiCall(...args);
const assetPreviewMarkup = (...args) => globalThis.StudioActions.assetPreviewMarkup(...args);
const beginApiProgress = (...args) => globalThis.StudioActions.beginApiProgress(...args);
const endApiProgress = (...args) => globalThis.StudioActions.endApiProgress(...args);
const escapeHtml = (...args) => globalThis.StudioActions.escapeHtml(...args);
const handleProjectJobEvent = (...args) => globalThis.StudioActions.handleProjectJobEvent(...args);
const hideLoading = (...args) => globalThis.StudioActions.hideLoading(...args);
const loadProfileList = (...args) => globalThis.StudioActions.loadProfileList(...args);
const loadProjects = (...args) => globalThis.StudioActions.loadProjects(...args);
const mediaUrl = (...args) => globalThis.StudioActions.mediaUrl(...args);
const navigateTo = (...args) => globalThis.StudioActions.navigateTo(...args);
const openPropImageEditor = (...args) => globalThis.StudioActions.openPropImageEditor(...args);
const projectJsonRequest = (...args) => globalThis.StudioActions.projectJsonRequest(...args);
const projectImagePolicyPayload = (...args) => globalThis.StudioActions.projectImagePolicyPayload(...args);
const renderProductionWorkspace = (...args) => globalThis.StudioActions.renderProductionWorkspace(...args);
const renderRecoveredMedia = (...args) => globalThis.StudioActions.renderRecoveredMedia(...args);
const renderRoles = (...args) => globalThis.StudioActions.renderRoles(...args);
const renderScenes = (...args) => globalThis.StudioActions.renderScenes(...args);
const rolesFromVisualDesign = (...args) => globalThis.StudioActions.rolesFromVisualDesign(...args);
const sceneAssetsFromScript = (...args) => globalThis.StudioActions.sceneAssetsFromScript(...args);
const setElementHtml = (...args) => globalThis.StudioActions.setElementHtml(...args);
const showLoading = (...args) => globalThis.StudioActions.showLoading(...args);
const submitProjectJob = (...args) => globalThis.StudioActions.submitProjectJob(...args);
const updateStatus = (...args) => globalThis.StudioActions.updateStatus(...args);

function getStateObject() {
  if (!appState.stateJson) return null;
  try { return JSON.parse(appState.stateJson); } catch(e) { return null; }
}

function setStateObject(state) {
  if (!state) return;
  appState.stateJson = JSON.stringify(state);
}

function buildShotFromScene(scene, index = 0) {
  return {
    scene_id: scene.scene_id || index + 1,
    desc: scene.action_description || '',
    role: (scene.characters || [])[0] || '',
    scene: scene.location || '',
    duration: scene.duration_sec || 5,
    camera: scene.camera_direction || '',
    tone: scene.emotion_tone || '',
    prompt: scene.action_description || '',
    negative_prompt: 'blurry, distorted, low quality, watermark, text, logo, deformed face, bad anatomy',
    seed: null,
    motion_strength: 0.85,
    batch_size: 1,
  };
}

function buildShotFromPrompt(promptShot, index = 0, state = null) {
  const sid = promptShot.scene_id || index + 1;
  const scene = (state?.script?.scenes || []).find(s => s.scene_id === sid) || {};
  return {
    scene_id: sid,
    desc: scene.action_description || promptShot.prompt || '',
    role: (scene.characters || [])[0] || '',
    scene: scene.location || '',
    duration: promptShot.duration_sec || scene.duration_sec || 5,
    camera: scene.camera_direction || '',
    tone: scene.emotion_tone || '',
    prompt: promptShot.prompt || scene.action_description || '',
    negative_prompt: promptShot.negative_prompt || 'blurry, distorted, low quality, watermark, text, logo, deformed face, bad anatomy',
    seed: promptShot.seed ?? null,
    motion_strength: promptShot.motion_strength ?? 0.85,
    batch_size: promptShot.batch_size || 1,
    sub_shots: promptShot.sub_shots || [],
  };
}

function buildShotsFromStateJson() {
  const state = getStateObject();
  if (!state) return null;
  if (state.generation_prompts?.shots?.length) {
    return state.generation_prompts.shots.map((s, i) => buildShotFromPrompt(s, i, state));
  }
  if (state.script?.scenes?.length) {
    return state.script.scenes.map((s, i) => buildShotFromScene(s, i));
  }
  return null;
}

function buildBlankShot(insertIndex = appState.shots.length) {
  const maxSceneId = appState.shots.reduce((max, s) => Math.max(max, Number(s.scene_id) || 0), 0);
  const prev = appState.shots[Math.max(0, insertIndex - 1)] || {};
  return {
    scene_id: maxSceneId + 1,
    desc: '新分镜：描述人物动作、镜头运动和情绪变化',
    role: prev.role || '',
    scene: prev.scene || '',
    duration: 5,
    camera: prev.camera || '中景跟拍',
    tone: prev.tone || '自然',
    prompt: '新分镜：描述人物动作、镜头运动和情绪变化',
    negative_prompt: 'blurry, distorted, low quality, watermark, text, logo, deformed face, bad anatomy',
    seed: null,
    motion_strength: 0.85,
    batch_size: 1,
    sub_shots: [],
  };
}

function syncShotsToStateJson() {
  const state = getStateObject();
  if (!state) return;

  state.script = state.script || {
    title: appState.projectName || '手动分镜项目',
    genre: '短剧',
    logline: '',
    duration_sec: 0,
    narration_style: 'none',
    target_audience: '通用',
    scenes: [],
  };

  const oldScenes = new Map((state.script.scenes || []).map(s => [s.scene_id, s]));
  state.script.scenes = appState.shots.map((shot, i) => {
    const sid = Number(shot.scene_id) || i + 1;
    const old = oldScenes.get(sid) || {};
    return {
      scene_id: sid,
      duration_sec: Math.max(3, Math.min(30, Number(shot.duration) || 5)),
      location: shot.scene || old.location || '未设定场景',
      characters: shot.role ? [shot.role] : (old.characters || []),
      dialogue: old.dialogue || [],
      action_description: shot.desc || old.action_description || '',
      camera_direction: shot.camera || old.camera_direction || '中景',
      emotion_tone: shot.tone || old.emotion_tone || '自然',
      transition_from_prev: old.transition_from_prev || 'cut',
    };
  });
  state.script.duration_sec = state.script.scenes.reduce((sum, s) => sum + (Number(s.duration_sec) || 0), 0);

  state.generation_prompts = state.generation_prompts || {
    api_provider: 'happyhorse',
    global_negative_prompt: 'blurry, distorted, low quality, watermark, text, logo, bad anatomy',
    global_seed_range: [1, 999999],
    shots: [],
  };

  const oldPrompts = new Map((state.generation_prompts.shots || []).map(s => [s.scene_id, s]));
  state.generation_prompts.shots = appState.shots.map((shot, i) => {
    const sid = Number(shot.scene_id) || i + 1;
    const old = oldPrompts.get(sid) || {};
    return {
      scene_id: sid,
      prompt: shot.prompt || shot.desc || old.prompt || '',
      negative_prompt: shot.negative_prompt || old.negative_prompt || 'blurry, distorted, low quality, watermark, text, logo, deformed face, bad anatomy',
      seed: shot.seed ?? old.seed ?? null,
      motion_strength: Number(shot.motion_strength ?? old.motion_strength ?? 0.85),
      duration_sec: Math.max(3, Math.min(30, Number(shot.duration) || old.duration_sec || 5)),
      batch_size: Number(shot.batch_size || old.batch_size || 1),
      sub_shots: shot.sub_shots || old.sub_shots || [],
    };
  });

  state.prompt_overrides = {};
  setStateObject(state);
  queueStateSave();
}

function queueStateSave() {
  if (!appState.projectId || !appState.stateJson) return;
  if (saveStateTimer) clearTimeout(saveStateTimer);
  saveStateTimer = setTimeout(() => {
    fetch(`${API_BASE}/api/projects/${appState.projectId}/state`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ state_json: appState.stateJson }),
    }).catch(() => {});
  }, 500);
}

function updateShotField(index, field, value) {
  const shot = appState.shots[index];
  if (!shot) return;
  if (field === 'duration') {
    shot.duration = Math.max(3, Math.min(30, Number(value) || 5));
  } else {
    shot[field] = value;
  }
  if (field === 'desc') shot.prompt = value;
  syncShotsToStateJson();
  updateShotDurationList();
}

function updateShotDurationList() {
  const el = document.getElementById('shot-duration-list');
  if (!el) return;
  if (!appState.shots.length) {
    el.innerHTML = '暂无分镜数据';
    return;
  }
  el.innerHTML = appState.shots.map((s, i) =>
    `<div style="padding:2px 0">镜头${i + 1}: ${s.duration || 5}s</div>`
  ).join('');
}

function renderShots() {
  if (document.getElementById('shot-production-workspace')) {
    renderProductionWorkspace();
    return;
  }
  const container = document.getElementById('shot-list');
  if (!appState.shots.length) {
    container.innerHTML = '<div style="color:#71717a;padding:40px;text-align:center">暂无分镜</div>';
    updateShotDurationList();
    return;
  }
  container.innerHTML = appState.shots.map((s, i) => `
    <div class="card" style="display:flex;gap:16px;margin-bottom:12px;align-items:flex-start">
      <div style="width:80px;height:120px;background:#12121a;border-radius:8px;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:20px;margin-top:2px">
        🎬
      </div>
      <div style="flex:1">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px">
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <span class="tag">镜头 ${i+1}</span>
            <span class="tag">Scene ${s.scene_id || i + 1}</span>
            <span class="tag">${s.duration || 5}s</span>
          </div>
          <div style="display:flex;gap:6px">
            <button class="btn-secondary btn-sm" onclick="StudioActions.insertShot(${i + 1})">+ 后插</button>
            <button class="btn-secondary btn-sm" style="color:#ef4444" onclick="StudioActions.deleteShot(${i})">删除</button>
          </div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr 90px;gap:8px;margin-bottom:8px">
          <input type="text" value="${escapeHtml(s.role || '')}" placeholder="角色" oninput="StudioActions.updateShotField(${i}, 'role', this.value)">
          <input type="text" value="${escapeHtml(s.scene || '')}" placeholder="场景/地点" oninput="StudioActions.updateShotField(${i}, 'scene', this.value)">
          <input type="number" min="2" max="30" value="${s.duration || 5}" onchange="StudioActions.updateShotField(${i}, 'duration', this.value)">
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">
          <input type="text" value="${escapeHtml(s.camera || '')}" placeholder="镜头运动" oninput="StudioActions.updateShotField(${i}, 'camera', this.value)">
          <input type="text" value="${escapeHtml(s.tone || '')}" placeholder="情绪/氛围" oninput="StudioActions.updateShotField(${i}, 'tone', this.value)">
        </div>
        <textarea rows="3" placeholder="分镜动作描述" oninput="StudioActions.updateShotField(${i}, 'desc', this.value)">${escapeHtml(s.desc || '')}</textarea>
      </div>
    </div>
  `).join('');
  updateShotDurationList();
}

// ═══════════════════════════════════════════════════════════════
//  功能占位（连接后端 / Mock 演示）
// ═══════════════════════════════════════════════════════════════
function extractNext(target) {
  if (!appState.currentScript || appState.scriptPhase !== 'confirmed') {
    updateStatus('⚠️ 请先确认并解析剧本');
    return;
  }
  if (!appState.roles.length) updateStatus('⚠️ 未提取到角色，请在角色页面手动添加');
  navigateTo(target);
}

async function requestImageGeneration(endpoint, statusElementId, label) {
  const statusElement = document.getElementById(statusElementId);
  const startedAt = Date.now();
  beginApiProgress(`正在生成${label}`);
  const controller = new AbortController();
  const progressTimer = setInterval(() => {
    const elapsed = Math.floor((Date.now() - startedAt) / 1000);
    statusElement.innerHTML = `<span class="spin"></span> 正在生成${label}，已等待 ${elapsed} 秒。图片服务通常需要几十秒，请勿关闭页面。`;
  }, 10000);
  const timeoutTimer = setTimeout(() => controller.abort(), 185000);
  try {
    const response = await fetch(`${API_BASE}${endpoint}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({state_json: appState.stateJson}), signal: controller.signal,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `生成${label}失败`);
    return payload;
  } catch (error) {
    const message = error.name === 'AbortError'
      ? `${label}请求超过 185 秒未完成，已停止等待。请检查图片服务配置、余额或网络后重试。`
      : error.message;
    return {error: message};
  } finally {
    clearInterval(progressTimer);
    clearTimeout(timeoutTimer);
    endApiProgress();
  }
}

function renderProps() {
  const container = document.getElementById('prop-cards');
  if (!container) return;
  if (!appState.props.length) {
    container.innerHTML = '<div style="color:#71717a;padding:40px;text-align:center;grid-column:1/-1">暂无关键道具。AI 仅提取需要保持外观连续、被操作或推动剧情的物件。</div>';
    return;
  }
  const imagesByProp = new Map();
  (appState.propImages || []).forEach(image => {
    const key = String(image.prop_id || '');
    if (!imagesByProp.has(key)) imagesByProp.set(key, []);
    imagesByProp.get(key).push(image);
  });
  container.innerHTML = appState.props.map((prop, index) => {
    const images = imagesByProp.get(String(prop.propId)) || [];
    const availableImages = images.filter(image =>
      image.image_path && !(image.media_missing || StudioActions.isMissingMediaPath(image.image_path))
    );
    const image = availableImages.find(item => item.view_type === 'front')
      || availableImages.find(item => item.view_type !== 'standard_views')
      || availableImages[0];
    const pendingMaster = availableImages.find(item => item.view_type === 'front' && item.is_master && item.needs_confirmation);
    const confirmedMaster = availableImages.find(item => item.view_type === 'front' && item.is_master && !item.needs_confirmation);
    const independentReady = availableImages.some(item => item.view_type && item.view_type !== 'standard_views');
    const legacyOnly = Boolean(image?.view_type === 'standard_views' && !independentReady);
    const encodedId = encodeURIComponent(prop.propId);
    const error = appState.assetErrors[`prop:${prop.propId}`];
    const busy = appState.assetBusy[`prop:${prop.propId}`];
    const missing = images.some(item => item.image_path && (item.media_missing || StudioActions.isMissingMediaPath(item.image_path)));
    const ready = Boolean(confirmedMaster);
    const preview = assetPreviewMarkup(image, prop.name, missing);
    const statusText = busy ? '生成中' : error ? '生成失败' : missing ? '文件缺失，可重新生成' : pendingMaster ? '待确认母版' : ready ? '母版已确认' : legacyOnly ? '旧六视图，待生成母版' : '待生成母版';
    const action = pendingMaster
      ? `<button class="btn-secondary btn-sm" onclick="event.stopPropagation();StudioActions.confirmPropMaster('${encodedId}','${escapeHtml(pendingMaster.version_id || '')}')">确认母版</button>`
      : !confirmedMaster
        ? `<button class="btn-secondary btn-sm" onclick="event.stopPropagation();StudioActions.generatePropMaster('${encodedId}')">生成母版</button>`
        : `<button class="btn-secondary btn-sm" onclick="event.stopPropagation();StudioActions.generateAssetCard('prop','${encodedId}',false)">${busy ? '终止' : '生成所需视图'}</button>`;
    return `<div class="card" data-asset-card style="padding:12px;cursor:pointer" onclick="StudioActions.openPropImageEditor(${index})"><div class="asset-card-preview asset-card-preview-prop">${preview}</div><div style="display:flex;justify-content:space-between;gap:8px;margin-top:10px"><b>${escapeHtml(prop.name)}</b><span style="font-size:10px;color:#a1a1aa">${prop.isHandheld ? '手持/操作' : '剧情关键'}</span></div><div style="font-size:11px;color:#a1a1aa;line-height:1.5;margin-top:6px">${escapeHtml(prop.description || '以剧本设定为准')}</div><div style="font-size:10px;color:#71717a;margin-top:6px">镜头 ${escapeHtml((prop.shotIds || []).join('、') || '未绑定')}</div><div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:8px"><span data-asset-status style="font-size:10px;color:${error ? '#ef4444' : ready ? '#10b981' : '#f59e0b'}">${statusText}</span>${action}</div></div>`;
  }).join('');
}

const ASSET_UI = {
  character: { statusId: 'char-status', buttonId: 'btn-gen-roles', label: '角色图片' },
  scene: { statusId: 'scene-status', buttonId: 'btn-gen-scenes', label: '场景图片' },
  prop: { statusId: 'prop-status', buttonId: 'btn-gen-props', label: '道具图片' },
};

function applyAssetGenerationResult(result) {
  if (!result?.state_json) throw new Error('生成接口未返回项目状态');
  appState.stateJson = result.state_json;
  const state = JSON.parse(result.state_json);
  appState.characterImages = state.character_images?.characters || [];
  appState.sceneImages = state.scene_images?.scenes || [];
  appState.propImages = state.prop_images?.props || [];
  (result.generated || []).forEach(item => { delete appState.assetErrors[`${item.asset_type}:${item.asset_id}`]; });
  (result.errors || []).forEach(item => { appState.assetErrors[`${item.asset_type}:${item.asset_id}`] = item.message; });
  renderRoles(); renderScenes(); renderProps();
}

async function generateAssetCard(assetType, encodedAssetId, regenerate = false, stateId = '') {
  if (!appState.projectId || !appState.stateJson || appState.scriptPhase !== 'confirmed') return updateStatus('请先确认并解析剧本');
  const assetId = decodeURIComponent(encodedAssetId);
  const key = assetType === 'scene' && stateId ? `${assetType}:${assetId}:${stateId}` : `${assetType}:${assetId}`;
  const active = activeProjectJobs(job => (
    job.job_type === 'asset.generate'
    || job.job_type === 'scene.reference.group'
    || job.job_type === 'character.reference.group'
    || job.job_type === 'prop.reference.group'
  ) && job.target_type === assetType && String(job.target_id).split(':')[0] === assetId && (!stateId || String(job.payload?.state_id || '') === stateId));
  if (active.length) return cancelMatchingJobs(active);
  try {
    delete appState.assetErrors[key];
    await submitProjectJob({
      job_type: 'asset.generate', target_type: assetType, target_id: assetId,
      payload: {asset_type: assetType, asset_id: assetId, state_id: stateId || '', regenerate, ...(await projectImagePolicyPayload())},
    });
    setElementHtml(ASSET_UI[assetType].statusId, '<span style="color:#93c5fd">任务已提交，可再次点击生成按钮终止</span>');
  } catch (error) {
    appState.assetErrors[key] = error.message || '图片生成失败';
    renderRoles(); renderScenes(); renderProps();
  }
}

async function confirmSceneMaster(encodedAssetId, versionId, stateId = '') {
  if (!appState.projectId || !appState.stateJson || appState.scriptPhase !== 'confirmed') return updateStatus('请先确认并解析剧本');
  const assetId = decodeURIComponent(encodedAssetId);
  if (!versionId) return updateStatus('场景中心母版版本缺失');
  try {
    const data = await projectJsonRequest(
      `${API_BASE}/api/projects/${appState.projectId}/scenes/${encodeURIComponent(assetId)}/master/confirm`,
      {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({version_id: versionId, state_id: stateId || ''}),
      },
      '确认场景母版失败',
    );
    if (data?.state_json) {
      appState.stateJson = data.state_json;
      const state = JSON.parse(data.state_json);
      appState.sceneImages = state.scene_images?.scenes || [];
      renderScenes();
      setElementHtml('scene-status', '<span style="color:#10b981">场景中心母版已确认，可以生成所需视图</span>');
    }
  } catch (error) {
    appState.assetErrors[`scene:${assetId}`] = error.message || '确认母版失败';
    renderScenes();
  }
}

async function generatePropMaster(encodedAssetId) {
  if (!appState.projectId || !appState.stateJson || appState.scriptPhase !== 'confirmed') return updateStatus('请先确认并解析剧本');
  const assetId = decodeURIComponent(encodedAssetId);
  const active = activeProjectJobs(job => job.job_type === 'asset.generate'
    && job.target_type === 'prop'
    && String(job.target_id).split(':')[0] === assetId);
  if (active.length) return cancelMatchingJobs(active);
  try {
    delete appState.assetErrors[`prop:${assetId}`];
    await submitProjectJob({
      job_type: 'asset.generate',
      target_type: 'prop',
      target_id: assetId,
      payload: {
        asset_type: 'prop',
        asset_id: assetId,
        view_type: 'front',
        ...(await projectImagePolicyPayload()),
      },
    });
    setElementHtml('prop-status', '<span style="color:#93c5fd">道具母版任务已提交</span>');
  } catch (error) {
    appState.assetErrors[`prop:${assetId}`] = error.message || '生成道具母版失败';
    renderProps();
  }
}

async function confirmPropMaster(encodedAssetId, versionId) {
  if (!appState.projectId || !appState.stateJson || appState.scriptPhase !== 'confirmed') return updateStatus('请先确认并解析剧本');
  const assetId = decodeURIComponent(encodedAssetId);
  if (!versionId) return updateStatus('道具母版版本缺失');
  try {
    const data = await projectJsonRequest(
      `${API_BASE}/api/projects/${appState.projectId}/props/${encodeURIComponent(assetId)}/master/confirm`,
      {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({version_id: versionId}),
      },
      '确认道具母版失败',
    );
    applyAssetGenerationResult(data);
    setElementHtml('prop-status', '<span style="color:#10b981">道具母版已确认，可生成所需视图</span>');
  } catch (error) {
    appState.assetErrors[`prop:${assetId}`] = error.message || '确认道具母版失败';
    renderProps();
  }
}

async function cancelMatchingJobs(jobs) {
  if (!jobs.length) return;
  if (!confirm('终止后将停止本地等待，但供应商任务可能继续执行并产生费用。确认终止？')) return;
  const batches = [...new Set(jobs.map(job => job.batch_id).filter(Boolean))];
  const singles = jobs.filter(job => !job.batch_id);
  try {
    for (const batchId of batches) {
      const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/job-batches/${batchId}/cancel`, {method:'POST'}, '终止批次失败');
      (data?.batch?.jobs || []).forEach(job => handleProjectJobEvent({job}));
    }
    for (const job of singles) {
      const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/jobs/${job.job_id}/cancel`, {method:'POST'}, '终止任务失败');
      if (data?.job) handleProjectJobEvent({job: data.job});
    }
  } catch (error) { alert(error.message); }
}

async function generatePendingAssets(assetType) {
  const ui = ASSET_UI[assetType];
  if (!appState.projectId || !appState.stateJson || appState.scriptPhase !== 'confirmed') {
    setElementHtml(ui.statusId, '<span style="color:#ef4444">请先确认并解析剧本</span>');
    return;
  }
  const active = activeProjectJobs(job => (
    job.job_type === 'asset.generate'
    || job.job_type === 'scene.reference.group'
    || job.job_type === 'character.reference.group'
    || job.job_type === 'prop.reference.group'
  ) && job.target_type === assetType);
  if (active.length) return cancelMatchingJobs(active);
  try {
    const result = await submitProjectJob({
      job_type: 'asset.generate', target_type: assetType,
      payload: {asset_type: assetType, ...(await projectImagePolicyPayload())},
    });
    const total = result?.batch?.total || (result?.job ? 1 : 0);
    setElementHtml(ui.statusId, total ? `<span style="color:#93c5fd">已提交 ${total} 个缺失图片任务</span>` : '<span style="color:#10b981">图片已经齐全</span>');
  } catch (error) {
    setElementHtml(ui.statusId, `<span style="color:#ef4444">${escapeHtml(error.message || '生成失败')}</span>`);
  }
}

async function generatePropImages() {
  return generatePendingAssets('prop');
}

async function generateRoleImages() {
  return generatePendingAssets('character');
}

async function generateSceneImages() {
  return generatePendingAssets('scene');
}

async function extractShots() {
  // 使用剧本中的 scenes 数据作为分镜
  if (!appState.shots.length) {
    appState.shots = buildShotsFromStateJson() || [];
  }
  if (appState.shots.length) { renderShots(); return; }
  updateStatus('⚠️ 请先 AI 生成剧本');
}

function produceStatus(msg) {
  const productionStatus = document.getElementById('production-global-status');
  if (productionStatus) {
    productionStatus.innerHTML = msg;
    return;
  }
  const legacyStatus = document.getElementById('produce-status');
  if (legacyStatus) legacyStatus.innerHTML = msg;
}

async function generateVideos() {
  if (!appState.stateJson) { produceStatus('<span style="color:#ef4444">⚠️ 请先完成前序步骤</span>'); return; }
  syncShotsToStateJson();
  showLoading('btn-gen-videos');
  produceStatus('<span class="spin"></span> 正在调用可灵生成视频片段 (约1-3分钟)...');
  const result = await apiCall('/api/videos', { state_json: appState.stateJson });
  if (result && result.state_json) {
    appState.stateJson = result.state_json;
    const state = JSON.parse(result.state_json);
    appState.videoAssets = state.generated_assets?.shots || [];
    renderRecoveredMedia();
    produceStatus('<span style="color:#10b981">✅ 视频片段生成完成</span>');
  } else {
    produceStatus('<span style="color:#ef4444">❌ 视频生成失败</span>');
  }
  hideLoading('btn-gen-videos', '🎬 批量生成视频');
}

async function generateAudio() {
  if (!appState.stateJson) { produceStatus('<span style="color:#ef4444">⚠️ 请先完成前序步骤</span>'); return; }
  showLoading('btn-gen-audio');
  produceStatus('<span class="spin"></span> 正在生成 TTS 配音和 BGM...');
  const result = await apiCall('/api/audio', { state_json: appState.stateJson });
  if (result && result.state_json) {
    appState.stateJson = result.state_json;
    const state = JSON.parse(result.state_json);
    appState.finalVideoPath = state.output_video_path || '';
    renderRecoveredMedia();
    produceStatus('<span style="color:#10b981">✅ 配音和 BGM 生成完成</span>');
  } else if (result && result.error) {
    produceStatus('<span style="color:#ef4444">❌ ' + result.error + '</span>');
  } else {
    produceStatus('<span style="color:#ef4444">❌ 音频生成失败</span>');
  }
  hideLoading('btn-gen-audio', '🎵 生成配音BGM');
}

async function assembleVideo() {
  if (!appState.stateJson) { produceStatus('<span style="color:#ef4444">⚠️ 请先完成前序步骤</span>'); return; }
  showLoading('btn-assemble');
  produceStatus('<span class="spin"></span> 正在合成最终视频...');
  const result = await apiCall('/api/assemble', { state_json: appState.stateJson });
  if (result && result.state_json) {
    appState.stateJson = result.state_json;
    produceStatus('<span style="color:#10b981">🎉 视频合成完成!</span>');
    // 尝试显示最终视频
    try {
      const state = JSON.parse(result.state_json);
      if (state.output_video_path) {
        const videoUrl = mediaUrl(state.output_video_path);
        document.getElementById('final-video-container').style.display = 'block';
        document.getElementById('final-video-container').innerHTML =
          `<video controls style="width:100%;max-width:300px;border-radius:8px" src="${videoUrl}"></video>
           <div style="font-size:11px;color:#10b981;margin-top:4px">✅ 视频已合成，可在作品管理页查看</div>`;
        document.getElementById('preview-player').style.display = 'none';
        // 刷新作品列表
        loadProjects();
      }
    } catch(e) {}
  } else if (result && result.error) {
    produceStatus('<span style="color:#ef4444">❌ ' + result.error + '</span>');
  } else {
    produceStatus('<span style="color:#ef4444">❌ 视频合成失败</span>');
  }
  hideLoading('btn-assemble', '🎞️ 合成最终视频');
}

async function reExtractRoles() {
  const status = document.getElementById('char-status');
  if (!appState.projectId) {
    status.innerHTML = '<span style="color:#f59e0b">请先创建并解析项目</span>';
    return;
  }
  status.innerHTML = '<span class="spin"></span> 正在重新提取实际出镜角色...';
  try {
    const res = await fetch(`${API_BASE}/api/projects/${appState.projectId}/characters/reextract`, {method: 'POST'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '角色提取失败');
    appState.stateJson = data.state_json;
    const state = JSON.parse(data.state_json);
    appState.roles = rolesFromVisualDesign(data.visual_design || state.visual_design, state.script);
    renderRoles();
    status.innerHTML = '<span style="color:#10b981">✅ 已重新提取 ' + appState.roles.length + ' 个实际出镜角色</span>';
  } catch (error) {
    status.innerHTML = `<span style="color:#f59e0b">⚠️ ${escapeHtml(error.message || '请先生成并解析剧本')}</span>`;
  }
}
function reExtractScenes() {
  if (appState.stateJson) {
    document.getElementById('scene-status').innerHTML = '<span class="spin"></span> 正在从剧本重新提取场景...';
    try {
      const state = JSON.parse(appState.stateJson);
      if (state.script?.scenes) {
        appState.scenes = sceneAssetsFromScript(state.script);
        renderScenes();
        document.getElementById('scene-status').innerHTML = '<span style="color:#10b981">✅ 已重新提取 ' + appState.scenes.length + ' 个场景</span>';
        return;
      }
    } catch(e) {}
  }
  document.getElementById('scene-status').innerHTML = '<span style="color:#f59e0b">⚠️ 请先生成剧本后再提取场景</span>';
}
function addManualRole() { document.getElementById('char-status').innerHTML = '<span style="color:#f59e0b">📝 手动添加角色（待实现，当前请通过剧本编辑页自动提取）</span>'; }
function addManualScene() { document.getElementById('scene-status').innerHTML = '<span style="color:#f59e0b">📝 手动添加场景（待实现，当前请通过剧本编辑页自动提取）</span>'; }

function insertShot(index = appState.shots.length) {
  const insertIndex = Math.max(0, Math.min(Number(index) || 0, appState.shots.length));
  if (appState.shots.length >= 20) {
    updateStatus('⚠️ 分镜最多只能有 20 条');
    return;
  }
  appState.shots.splice(insertIndex, 0, buildBlankShot(insertIndex));
  syncShotsToStateJson();
  renderShots();
  updateStatus(`✅ 已插入镜头 ${insertIndex + 1}`);
}

function deleteShot(index) {
  if (index < 0 || index >= appState.shots.length) return;
  if (!confirm(`确定删除镜头 ${index + 1}？`)) return;
  appState.shots.splice(index, 1);
  syncShotsToStateJson();
  renderShots();
  updateStatus('✅ 分镜已删除');
}

// ── 从服务器加载已保存的项目 ──
const __moduleActions = {
  getStateObject,
  setStateObject,
  buildShotFromScene,
  buildShotFromPrompt,
  buildShotsFromStateJson,
  buildBlankShot,
  syncShotsToStateJson,
  queueStateSave,
  updateShotField,
  updateShotDurationList,
  renderShots,
  extractNext,
  requestImageGeneration,
  renderProps,
  applyAssetGenerationResult,
  generateAssetCard,
  confirmSceneMaster,
  generatePropMaster,
  confirmPropMaster,
  cancelMatchingJobs,
  generatePendingAssets,
  generatePropImages,
  generateRoleImages,
  generateSceneImages,
  extractShots,
  produceStatus,
  generateVideos,
  generateAudio,
  assembleVideo,
  reExtractRoles,
  reExtractScenes,
  addManualRole,
  addManualScene,
  insertShot,
  deleteShot,
};
Object.assign(globalThis.StudioActions, __moduleActions);
Object.defineProperties(globalThis, {
  ASSET_UI: { configurable: true, get: () => ASSET_UI },
});
export { __moduleActions };
