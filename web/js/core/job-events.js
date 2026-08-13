const buildShotFromScene = (...args) => globalThis.StudioActions.buildShotFromScene(...args);
const buildShotsFromStateJson = (...args) => globalThis.StudioActions.buildShotsFromStateJson(...args);
const defaultAppState = (...args) => globalThis.StudioActions.defaultAppState(...args);
const endApiProgress = (...args) => globalThis.StudioActions.endApiProgress(...args);
const escapeHtml = (...args) => globalThis.StudioActions.escapeHtml(...args);
const isCurrentProjectSession = (...args) => globalThis.StudioActions.isCurrentProjectSession(...args);
const isTerminalJob = (...args) => globalThis.StudioActions.isTerminalJob(...args);
const jobTargetLabel = (...args) => globalThis.StudioActions.jobTargetLabel(...args);
const loadImageHistory = (...args) => globalThis.StudioActions.loadImageHistory(...args);
const loadProductionWorkspace = (...args) => globalThis.StudioActions.loadProductionWorkspace(...args);
const mergeProjectJob = (...args) => globalThis.StudioActions.mergeProjectJob(...args);
const patchProductionJobState = (...args) => globalThis.StudioActions.patchProductionJobState(...args);
const projectJsonRequest = (...args) => globalThis.StudioActions.projectJsonRequest(...args);
const projectSessionSnapshot = (...args) => globalThis.StudioActions.projectSessionSnapshot(...args);
const renderImageEditor = (...args) => globalThis.StudioActions.renderImageEditor(...args);
const renderProjectJobs = (...args) => globalThis.StudioActions.renderProjectJobs(...args);
const renderProps = (...args) => globalThis.StudioActions.renderProps(...args);
const renderRecoveredMedia = (...args) => globalThis.StudioActions.renderRecoveredMedia(...args);
const renderRoles = (...args) => globalThis.StudioActions.renderRoles(...args);
const renderScenes = (...args) => globalThis.StudioActions.renderScenes(...args);
const renderShots = (...args) => globalThis.StudioActions.renderShots(...args);
const renderStructuredScript = (...args) => globalThis.StudioActions.renderStructuredScript(...args);
const restoreProjectMedia = (...args) => globalThis.StudioActions.restoreProjectMedia(...args);
const setScriptInputMode = (...args) => globalThis.StudioActions.setScriptInputMode(...args);
const showAssetTab = (...args) => globalThis.StudioActions.showAssetTab(...args);
const syncJobsToAssetState = (...args) => globalThis.StudioActions.syncJobsToAssetState(...args);
const syncScriptAssets = (...args) => globalThis.StudioActions.syncScriptAssets(...args);
const updateJobDrivenControls = (...args) => globalThis.StudioActions.updateJobDrivenControls(...args);

async function refreshCurrentProjectData({renderShots = true, refreshProduction = true, preserveScriptEditor = false} = {}) {
  if (!appState.projectId) return;
  const snapshot = projectSessionSnapshot();
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}`, {}, '刷新项目失败');
    if (!data || !isCurrentProjectSession(snapshot)) return;
    const meta = data.meta || {};
    const assets = meta.assets || {};
    appState.projectName = meta.title || appState.projectName;
    appState.stateJson = assets.state_json || appState.stateJson;
    let state = null;
    try { state = appState.stateJson ? JSON.parse(appState.stateJson) : null; } catch (_) {}
    if (state) {
      appState.scriptPhase = state.script_phase || (state.script ? 'confirmed' : 'source_required');
      if (!preserveScriptEditor) appState.scriptDraftText = state.script_draft_text || '';
      appState.scriptDraftInstruction = state.script_draft_instruction || '';
      appState.draftJobId = state.draft_job_id || '';
    }
    restoreProjectMedia(state, data.assets || {});
    const script = assets.script || state?.script;
    const visual = assets.visual_design || state?.visual_design;
    if (script) {
      appState.scriptText = JSON.stringify(script, null, 2);
      if (!preserveScriptEditor) renderStructuredScript(script);
      syncScriptAssets(script, visual);
      appState.shots = buildShotsFromStateJson() || script.scenes?.map((scene,index) => buildShotFromScene(scene,index)) || [];
    }
    globalThis.StudioActions.renderScriptWorkspace?.();
    const status = document.getElementById('gen-status');
    if (status && state?.visual_stage) {
      const messages = {
        pending: '剧本结构已完成，等待生成视觉与分镜提示词',
        generating: '剧本结构已完成，正在后台生成视觉与分镜提示词',
        ready: '视觉与分镜提示词已完成，可以继续制作资产和视频',
        stale: '剧本已更新，现有视觉与分镜提示词已过期，请重新生成',
        failed: `视觉与分镜提示词生成失败：${state.visual_stage_error || '请在任务列表重试'}`,
      };
      const color = state.visual_stage === 'ready' ? '#10b981' : state.visual_stage === 'failed' ? '#ef4444' : '#f59e0b';
      status.innerHTML = `<span style="color:${color}">${escapeHtml(messages[state.visual_stage] || messages.generating)}</span>`;
    }
    renderRoles(); renderScenes(); renderProps();
    if (renderShots) renderShots();
    renderRecoveredMedia();
    if (activeImageEditor) { renderImageEditor(); loadImageHistory(); }
    if (refreshProduction && typeof loadProductionWorkspace === 'function') loadProductionWorkspace();
  } catch (error) { console.error('刷新项目任务结果失败:', error); }
}

async function loadProjectJobs() {
  if (!appState.projectId) { projectJobs = []; renderProjectJobs(); return; }
  const snapshot = projectSessionSnapshot();
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/jobs?limit=100`, {}, '任务列表加载失败');
    if (!data || !isCurrentProjectSession(snapshot)) return;
    (data.jobs || []).forEach(job => {
      projectJobStateCache.set(job.job_id, job.state);
    });
    projectJobs = data.jobs || [];
    syncJobsToAssetState();
    renderProjectJobs(); renderRoles(); renderScenes(); renderProps();
  } catch (error) { console.error(error); }
}

function stopProjectJobEvents() {
  if (projectJobEventSource) projectJobEventSource.close();
  projectJobEventSource = null;
  clearInterval(projectJobPollTimer);
  clearTimeout(projectJobReconnectTimer);
  projectJobPollTimer = null;
  projectJobReconnectTimer = null;
}

function startProjectJobFallback() {
  clearInterval(projectJobPollTimer);
  projectJobPollTimer = setInterval(loadProjectJobs, 30000);
}

function scheduleProjectJobReconnect(snapshot) {
  if (!isCurrentProjectSession(snapshot) || projectJobReconnectTimer) return;
  const delay = projectJobReconnectDelay;
  projectJobReconnectDelay = Math.min(projectJobReconnectDelay * 2, 15000);
  projectJobReconnectTimer = setTimeout(() => {
    projectJobReconnectTimer = null;
    connectProjectJobEvents(snapshot, false);
  }, delay);
}

function connectProjectJobEvents(snapshot = projectSessionSnapshot(), hydrate = true) {
  if (!isCurrentProjectSession(snapshot)) return;
  if (hydrate) {
    loadProjectJobs().finally(() => {
      if (isCurrentProjectSession(snapshot)) connectProjectJobEvents(snapshot, false);
    });
    return;
  }
  if (projectJobEventSource) projectJobEventSource.close();
  const cursor = projectJobEventCursor ? `?after_event_id=${encodeURIComponent(projectJobEventCursor)}` : '';
  const source = new EventSource(`${API_BASE}/api/projects/${snapshot.projectId}/job-events${cursor}`);
  projectJobEventSource = source;
  source.addEventListener('job', event => {
    if (!isCurrentProjectSession(snapshot) || source !== projectJobEventSource) return;
    try {
      const payload = JSON.parse(event.data);
      projectJobEventCursor = Math.max(projectJobEventCursor, Number(event.lastEventId || payload.event?.event_id || 0));
      handleProjectJobEvent(payload);
    } catch (error) { console.error('任务事件解析失败:', error); }
  });
  source.onopen = () => {
    if (!isCurrentProjectSession(snapshot) || source !== projectJobEventSource) return;
    projectJobReconnectDelay = 1000;
    clearInterval(projectJobPollTimer);
    projectJobPollTimer = null;
  };
  source.onerror = () => {
    if (!isCurrentProjectSession(snapshot) || source !== projectJobEventSource) return;
    source.close();
    projectJobEventSource = null;
    startProjectJobFallback();
    scheduleProjectJobReconnect(snapshot);
  };
}

function startProjectJobPolling() {
  if (!appState.projectId) return;
  const snapshot = projectSessionSnapshot();
  connectProjectJobEvents(snapshot, true);
}

async function refreshForTerminalProjectJob(job) {
  if (job.job_type === 'script.draft.generate' || job.job_type === 'script.draft.revise') {
    await refreshCurrentProjectData({preserveScriptEditor: true, renderShots: false, refreshProduction: false});
    return;
  }
  if (job.job_type === 'script.generate' || job.job_type === 'script.analyze' || job.job_type === 'script.visualize') {
    await refreshCurrentProjectData({preserveScriptEditor: job.job_type === 'script.visualize'});
    return;
  }
  if (job.job_type === 'prompt.review') {
    await loadProductionWorkspace({patch: false});
    return;
  }
  if (['asset.generate', 'character.reference.group', 'character.reference.edit', 'scene.reference.group', 'scene.reference.edit', 'prop.reference.group'].includes(job.job_type)) {
    await refreshCurrentProjectData({renderShots: false, refreshProduction: false});
    return;
  }
  if (['video.generate', 'audio.generate', 'final.assemble'].includes(job.job_type)) {
    await loadProductionWorkspace({patch: true});
  }
}

function handleProjectJobEvent(payload) {
  const job = payload?.job;
  if (!job) return;
  const previous = mergeProjectJob(job);
  renderProjectJobs();
  updateJobDrivenControls();
  patchProductionJobState(job);
  if (['asset.generate', 'character.reference.group', 'character.reference.edit', 'scene.reference.group', 'scene.reference.edit', 'prop.reference.group'].includes(job.job_type)) {
    renderRoles(); renderScenes(); renderProps();
  }
  if (job.job_type === 'prompt.review' && isTerminalJob(job)) {
    refreshForTerminalProjectJob(job).catch(error => console.error('刷新任务结果失败:', error));
  } else if (!isTerminalJob(previous) && job.state === 'succeeded') {
    refreshForTerminalProjectJob(job).catch(error => console.error('刷新任务结果失败:', error));
  }
}

async function submitProjectJob(request) {
  if (!appState.projectId) throw new Error('请先创建或打开项目');
  const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/jobs`, {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(request),
  }, '提交任务失败');
  if (!data) return null;
  const jobs = data.batch?.jobs || (data.job ? [data.job] : []);
  jobs.forEach(job => {
    mergeProjectJob(job);
    patchProductionJobState(job);
  });
  syncJobsToAssetState(); renderProjectJobs(); renderRoles(); renderScenes(); renderProps();
  if (!projectJobEventSource) startProjectJobPolling();
  return data;
}

async function waitForProjectJob(jobId) {
  const snapshot = projectSessionSnapshot();
  while (isCurrentProjectSession(snapshot)) {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/jobs/${jobId}`, {}, '读取任务失败');
    if (!data) return null;
    const job = data.job;
    projectJobs = [job, ...projectJobs.filter(item => item.job_id !== job.job_id)];
    syncJobsToAssetState(); renderProjectJobs();
    if (['succeeded','failed','canceled','interrupted'].includes(job.state)) {
      if (job.state !== 'succeeded') throw new Error(job.error?.message || JOB_STATE_LABELS[job.state] || '任务未完成');
      return job.result || {};
    }
    await new Promise(resolve => setTimeout(resolve, 800));
  }
  return null;
}

async function cancelProjectJob(jobId, skipConfirm=false) {
  if (!skipConfirm && !confirm('终止后将停止本地等待，但供应商任务可能继续执行并产生费用。确认终止？')) return;
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/jobs/${jobId}/cancel`, {method:'POST'}, '终止任务失败');
    if (data?.job) handleProjectJobEvent({job: data.job});
  } catch (error) { alert(error.message); }
}

async function cancelProjectBatch(batchId) {
  if (!confirm('终止本批次后，已完成结果会保留；供应商端已提交任务可能继续计费。确认终止？')) return;
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/job-batches/${batchId}/cancel`, {method:'POST'}, '终止批次失败');
    (data?.batch?.jobs || []).forEach(job => handleProjectJobEvent({job}));
  } catch (error) { alert(error.message); }
}

async function retryProjectJob(jobId) {
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/jobs/${jobId}/retry`, {method:'POST'}, '重试失败');
    if (data?.job) handleProjectJobEvent({job: data.job});
    if (!projectJobEventSource) startProjectJobPolling();
  } catch (error) { alert(error.message); }
}

async function showProjectJobDetails(jobId) {
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/jobs/${jobId}`, {}, '读取任务详情失败');
    if (!data) return;
    const job = data.job;
    const checks = job.quality?.technical_checks || [];
    const events = data.events || [];
    const generationSnapshot = job.payload?.generation_snapshot || job.result?.generation_snapshot || {};
    const promptReview = job.result?.report;
    const accepted_references = generationSnapshot.accepted_references || generationSnapshot.references || [];
    const dropped_references = generationSnapshot.dropped_references || [];
    const referenceDecision = (generationSnapshot.reference_mode || accepted_references.length || dropped_references.length)
      ? `<h4 style="margin:18px 0 7px;font-size:12px">参考图实际提交</h4><div style="color:#d4d4d8">模式：${escapeHtml(generationSnapshot.reference_mode || 'text_to_video')}；已提交 ${accepted_references.length} 张，忽略 ${dropped_references.length} 张</div>${accepted_references.length ? `<div style="margin-top:6px;color:#86efac">${accepted_references.map(item => escapeHtml(item.label || item.reference_id || item.path || '')).join('、')}</div>` : ''}${dropped_references.length ? `<div style="margin-top:6px;color:#fbbf24">${dropped_references.map(item => `${escapeHtml(item.path || item.reference_id || '')}：${escapeHtml(item.reason || '模型限制')}`).join('<br>')}</div>` : ''}`
      : '';
    document.getElementById('project-job-detail-content').innerHTML = `
      <div style="display:grid;grid-template-columns:110px 1fr;gap:7px 12px"><span style="color:#71717a">任务</span><b>${escapeHtml(JOB_TYPE_LABELS[job.job_type] || job.job_type)} · ${escapeHtml(jobTargetLabel(job))}</b><span style="color:#71717a">状态</span><span>${escapeHtml(JOB_STATE_LABELS[job.state] || job.state)}</span><span style="color:#71717a">阶段</span><span>${escapeHtml(JOB_STAGE_LABELS[job.stage] || job.stage || '')}</span><span style="color:#71717a">尝试次数</span><span>${job.attempt}</span>${job.provider ? `<span style="color:#71717a">供应商</span><span>${escapeHtml(job.provider)}</span>` : ''}</div>
      ${job.error ? `<div style="margin-top:16px;padding:10px;border-left:3px solid #ef4444;background:#271719"><b>${escapeHtml(job.error.message)}</b><div style="color:#fca5a5;margin-top:4px">${escapeHtml(job.error.suggestion || '')}</div></div>` : ''}
      ${referenceDecision}
      ${promptReview ? `<h4 style="margin:18px 0 7px;font-size:12px">Kimi 分镜审核</h4><div style="color:${promptReview.passed ? '#86efac' : '#fbbf24'}">总分 ${escapeHtml(String(promptReview.overall_score || 0))} · ${promptReview.passed ? '通过' : '需修改'}</div><div style="margin-top:6px;color:#d4d4d8">${escapeHtml(promptReview.summary || '')}</div>${(promptReview.issues || []).map(item => `<div style="margin-top:6px;padding:7px;background:#27272a"><b>${escapeHtml(item.severity)} · 镜头 ${escapeHtml(String(item.scene_id || '全片'))}</b><br>${escapeHtml(item.description)}<br><span style="color:#a1a1aa">${escapeHtml(item.suggestion)}</span></div>`).join('')}` : ''}
      <h4 style="margin:18px 0 7px;font-size:12px">质量检查</h4>${checks.length ? checks.map(item => `<div style="padding:5px 0;color:${item.passed ? '#86efac' : '#fca5a5'}">${item.passed ? '通过' : '失败'} · ${escapeHtml(item.message)}</div>`).join('') : `<div style="color:#71717a">${escapeHtml(job.quality?.visual_review?.reason || '没有质量报告')}</div>`}
      <h4 style="margin:18px 0 7px;font-size:12px">任务事件</h4>${events.map(event => `<div style="padding:6px 0;border-bottom:1px solid #27272a"><span style="color:#71717a">${escapeHtml(event.created_at || '')}</span><br>${escapeHtml(event.message || event.stage || '')}</div>`).join('') || '<div style="color:#71717a">暂无事件</div>'}`;
    document.getElementById('modal-job-detail').style.display = 'flex';
  } catch (error) { alert(error.message); }
}

function setElementText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}

function setElementHtml(id, value) {
  const element = document.getElementById(id);
  if (element) element.innerHTML = value;
}

function resetProjectSession(overrides = {}) {
  projectSessionToken += 1;
  projectRequestControllers.forEach(controller => controller.abort());
  projectRequestControllers.clear();
  clearTimeout(saveStateTimer);
  clearInterval(apiProgressTimer);
  stopProjectJobEvents();
  saveStateTimer = null;
  apiProgressTimer = null;
  projectJobPollTimer = null;
  projectJobEventCursor = 0;
  projectJobReconnectDelay = 1000;
  projectJobs = [];
  projectJobStateCache = new Map();
  activeImageEditor = null;
  appState = { ...defaultAppState(), ...overrides };
  if (typeof productionData !== 'undefined') productionData = null;
  if (typeof productionSceneId !== 'undefined') productionSceneId = null;
  if (typeof productionPollTimer !== 'undefined' && productionPollTimer) {
    clearInterval(productionPollTimer);
    productionPollTimer = null;
  }

  document.querySelectorAll('[id^="modal-"]').forEach(modal => { modal.style.display = 'none'; });
  document.getElementById('project-jobs-drawer')?.classList.remove('open');
  renderProjectJobs();
  const draftEditor = document.getElementById('script-draft-editor');
  if (draftEditor) draftEditor.value = '';
  const structuredEditor = document.getElementById('structured-script-editor');
  if (structuredEditor) structuredEditor.value = '';
  const scriptResult = document.getElementById('script-result');
  if (scriptResult) scriptResult.style.display = 'none';
  const finalContainer = document.getElementById('final-video-container');
  if (finalContainer) { finalContainer.style.display = 'none'; finalContainer.innerHTML = ''; }
  const preview = document.getElementById('preview-player');
  if (preview) { preview.style.display = ''; preview.innerHTML = '<span>📺 预览区<br><span style="font-size:11px">生成后显示</span></span>'; }
  setElementText('script-source-filename', '');
  setElementText('project-info', '未创建项目');
  setElementText('gen-status', '等待输入');
  setElementHtml('char-status', '');
  setElementHtml('scene-status', '');
  setElementHtml('prop-status', '');
  setElementHtml('produce-status', '');
  setElementHtml('shot-duration-list', '暂无分镜数据');
  setElementHtml('shot-video-list', '');
  setElementHtml('works-list', '<div style="color:#71717a;padding:40px;text-align:center;grid-column:1/-1">📺 暂无作品<br><span style="font-size:12px">完成全部制作流程后，合成的短剧将在这里展示</span></div>');
  endApiProgress();
  setScriptInputMode('idea');
  renderRoles(); renderScenes(); renderProps(); renderShots();
  showAssetTab('characters');
}
const __moduleActions = {
  refreshCurrentProjectData,
  loadProjectJobs,
  stopProjectJobEvents,
  startProjectJobFallback,
  scheduleProjectJobReconnect,
  connectProjectJobEvents,
  startProjectJobPolling,
  refreshForTerminalProjectJob,
  handleProjectJobEvent,
  submitProjectJob,
  waitForProjectJob,
  cancelProjectJob,
  cancelProjectBatch,
  retryProjectJob,
  showProjectJobDetails,
  setElementText,
  setElementHtml,
  resetProjectSession,
};
Object.assign(globalThis.StudioActions, __moduleActions);
export { __moduleActions };
