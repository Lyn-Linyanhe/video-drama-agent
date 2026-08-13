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

function captureProductionEditorState(root) {
  const fields = ['production-prompt', 'production-negative', 'production-feedback', 'production-style-prefix', 'production-global-negative', 'production-duration'];
  const values = {};
  fields.forEach(id => {
    const element = root.querySelector(`#${id}`);
    if (!element) return;
    values[id] = {
      value: element.value, scrollTop: element.scrollTop,
      selectionStart: typeof element.selectionStart === 'number' ? element.selectionStart : null,
      selectionEnd: typeof element.selectionEnd === 'number' ? element.selectionEnd : null,
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
    element.value = state.value;
    element.scrollTop = state.scrollTop;
    if (state.focused) {
      element.focus({preventScroll: true});
      if (state.selectionStart !== null) element.setSelectionRange(state.selectionStart, state.selectionEnd);
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
  return `<div class="reference-chip" draggable="true" ondragstart="StudioActions.productionDragStart(${index})" ondragover="event.preventDefault()" ondrop="StudioActions.productionDropReference(${index})"><img src="${image}" alt=""><span>${escapeHtml(reference.label || reference.reference_id)}</span><button title="移除参考" onclick="StudioActions.productionRemoveReference(${index})">×</button></div>`;
}

function promptReviewHtml(review) {
  const status = review?.status || 'pending';
  const labels = {
    pending: '待审核',
    generating: 'Kimi 审核中',
    ready: '审核完成',
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
    ? '<div class="production-reference-warning" style="margin-top:6px;color:#fbbf24;font-size:11px">连续镜头会优先使用上一镜尾帧；当前模型不会同时提交普通参考图。</div>'
    : '';
  const referenceCount = production.references.length + (
    production.continuity_enabled && currentCapability.multi_reference && currentCapability.continuity ? 1 : 0
  );
  const referenceWarning = continuityWarning || (referenceCount > maxReferenceImages
    ? `<div class="production-reference-warning" style="margin-top:6px;color:#fbbf24;font-size:11px">当前模型最多接受 ${maxReferenceImages} 张参考图，超出的图片不会发送；任务详情会列出被忽略的图片。</div>`
    : '');
  const modelOptions = Object.keys(videoCapabilities[videoPolicy.provider]?.models || {}).map(model => `<option value="${model}" ${model === videoPolicy.model ? 'selected' : ''}>${model}</option>`).join('');
  const resolutionOptions = (current.video_capability?.resolutions || ['720p','1080p']).map(value => `<option value="${value}" ${value === videoPolicy.resolution ? 'selected' : ''}>${value}</option>`).join('');
  const continuityHint = production.continuity_enabled ? (production.continuity_stale ? '上一镜已变化，需重新生成' : (production.continuity_tail_frame_path ? `引用镜头 ${production.continuity_source_scene_id} 尾帧` : '生成时引用上一镜尾帧')) : '独立镜头';
  const catalog = productionData.asset_catalog || {characters:[], scenes:[], props:[]};
  const assetGrid = (assets, empty) => assets.length ? `<div class="asset-grid">${assets.map(item => `<button class="asset-item" onclick="StudioActions.productionAddReference('${StudioActions.escapeHtml(item.reference_id)}')"><img src="${productionImageUrl(item)}" alt=""><span>${escapeHtml(item.label)}</span></button>`).join('')}</div>` : `<div style="font-size:11px;color:#71717a">${empty}</div>`;
  const preview = activeVideo ? `<video controls src="${activeVideo}"></video>` : `<div><div style="font-size:28px;margin-bottom:8px">${production.generation_status === 'generating' ? '◌' : '▶'}</div><div>${productionStatusLabel(production.generation_status)}</div>${production.error_message ? `<div style="font-size:11px;color:#fca5a5;margin-top:8px">${escapeHtml(production.error_message)}</div>` : ''}</div>`;
  const editorSnapshot = captureProductionEditorState(root);
  root.innerHTML = `
    <div class="production-toolbar" style="margin-bottom:14px"><h3 style="margin:0 auto 0 0">分镜制作</h3><button class="btn-primary btn-sm" onclick="StudioActions.productionGeneratePending()">${activeVideoJobs.length ? `终止本批次 ${batchCompleted}/${batchJobs.length || activeVideoJobs.length}` : '一键准备参考图并生成视频'}</button><button class="btn-secondary btn-sm" id="btn-gen-audio" onclick="StudioActions.productionGenerateAudio()">${activeAudioJob ? '终止台词生成' : (audioSummary.failed_line_count ? '重试失败台词' : '生成缺失台词')}</button><button class="btn-secondary btn-sm" id="btn-assemble" onclick="StudioActions.productionAssembleVideo()">${activeFinalJob ? '终止合成' : '合成作品'}</button><span id="production-global-status" style="font-size:11px;color:#a1a1aa"></span></div>
    ${promptQuality.needs_refresh ? '<div class="production-panel" style="margin:0 0 10px;padding:10px;color:#fbbf24;font-size:12px">当前项目含旧版非中文分镜提示词。已有视频与手动编辑将保留；确认需要更新时，请重新解析剧本以生成新版中文分镜。</div>' : ''}
    ${promptReviewHtml(promptReview)}
    <details class="production-panel" style="margin:0 0 10px;padding:12px"><summary style="cursor:pointer">提示词策略</summary><div style="display:grid;gap:8px;margin-top:10px"><input id="production-style-prefix" class="production-script" style="min-height:38px" placeholder="全项目风格前缀" value="${escapeHtml(promptPolicy.style_prefix || '')}"><textarea id="production-global-negative" class="production-script" style="min-height:58px" placeholder="全项目负面约束">${escapeHtml(promptPolicy.global_negative_prompt || '')}</textarea><button class="btn-secondary btn-sm" onclick="StudioActions.productionSavePromptPolicy()">保存提示词策略</button></div></details>
    <div class="production-shell">
      <aside class="production-panel production-assets"><h4>资产库</h4><h5>角色形象</h5>${assetGrid(catalog.characters, '请先生成角色形象图')}<h5>场景参考</h5>${assetGrid(catalog.scenes, '请先生成场景参考图')}<h5>道具参考</h5>${assetGrid(catalog.props, '当前镜头没有可用关键道具图')}</aside>
      <main class="production-panel production-editor"><div class="production-header"><div><h3 style="margin:0">片段 ${scene.scene_id}</h3><div style="font-size:11px;color:#a1a1aa;margin-top:4px">${escapeHtml(scene.location)} · 可见角色：${escapeHtml((scene.characters || []).join('、') || '无')}</div></div><div class="production-meta"><label class="production-chip">时长 <input id="production-duration" type="number" min="3" max="30" value="${prompt.duration_sec}" style="width:42px;background:transparent;border:0;color:inherit"></label><span class="production-chip">${productionStatusLabel(production.generation_status)}</span></div></div>
        <div><div style="font-size:12px;color:#a1a1aa;margin-bottom:6px">参考素材（可拖拽排序）</div><div class="reference-row">${production.references.map(productionReferenceHtml).join('') || '<span style="font-size:11px;color:#71717a">从左侧资产库选择角色图或场景图</span>'}</div></div>
        <div style="display:grid;grid-template-columns:1fr 1fr 110px;gap:8px;align-items:end"><label style="font-size:12px">视频服务<select id="production-video-provider" onchange="StudioActions.productionVideoProviderChanged()">${providerOptions}</select></label><label style="font-size:12px">模型<select id="production-video-model" onchange="StudioActions.productionVideoModelChanged()">${modelOptions}</select></label><label style="font-size:12px">清晰度<select id="production-video-resolution">${resolutionOptions}</select></label></div><div style="font-size:11px;color:#a1a1aa">成本档位：${escapeHtml(current.video_capability?.cost_tier || '未标注')}</div>
        <div style="display:flex;align-items:center;gap:12px;font-size:12px;color:#d4d4d8"><label><input id="production-continuity" type="checkbox" ${production.continuity_enabled ? 'checked' : ''}> 承接上一镜尾帧</label><span style="color:${production.continuity_stale ? '#fbbf24' : '#a1a1aa'}">${escapeHtml(continuityHint)}</span><span class="production-chip" style="margin-left:auto">新任务采用硬切</span></div>
        <textarea id="production-prompt" class="production-script" placeholder="视频生成 Prompt">${escapeHtml(prompt.prompt)}</textarea>
        ${currentPromptReviewHtml}
        <textarea id="production-negative" class="production-feedback" placeholder="镜头负面约束">${escapeHtml(prompt.negative_prompt || '')}</textarea>
        <div class="production-feedback" style="white-space:pre-wrap;min-height:68px"><div style="font-size:11px;color:#a1a1aa;margin-bottom:4px">最终发送 · ${escapeHtml(effective.provider || '')} · ${negativeMode === 'merged' ? '负面约束已合并到主提示词' : '使用独立负面提示词参数'}</div>${finalPromptPreview}</div>
        <textarea id="production-feedback" class="production-feedback" placeholder="用口语描述希望 AI 如何修改当前视频 Prompt"></textarea>
        <div class="production-actions"><button class="btn-secondary btn-sm" onclick="StudioActions.productionRevisePrompt()">AI 修改 Prompt</button><button class="btn-secondary btn-sm" onclick="StudioActions.productionSaveCurrent()">保存编辑</button><button class="btn-primary" onclick="StudioActions.productionGenerateCurrent()">${currentVideoJob ? '终止' : production.generation_status === 'failed' || production.generation_status === 'canceled' ? '重试生成' : '生成视频'}</button></div>
      </main>
      <aside class="production-panel production-preview"><div style="display:flex;justify-content:space-between;align-items:center"><h4 style="margin:0">视频预览</h4><span id="production-preview-download">${activeVideo ? `<a class="btn-secondary btn-sm" href="${activeVideo}" download>下载</a>` : ''}</span></div><div class="preview-stage">${preview}</div><div><div style="font-size:12px;color:#a1a1aa;margin-bottom:6px">历史版本</div><div id="production-version-list" class="version-list">${production.video_versions.map((item,index) => `<button class="version-item ${item.version_id === production.active_version_id ? 'active' : ''}" onclick="StudioActions.productionActivateVersion('${item.version_id}')"><video muted preload="metadata" src="${productionVideoUrl(item)}"></video><span>版本 ${index + 1}</span></button>`).join('') || '<span style="font-size:11px;color:#71717a">暂无生成版本</span>'}</div></div></aside>
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
  const referenceRow = root.querySelector('.reference-row');
  if (referenceRow) {
    referenceRow.insertAdjacentHTML('beforebegin', `<div class="production-reference-capability" style="margin:0 0 6px;color:#a1a1aa;font-size:11px">参考图能力：最多 ${maxReferenceImages} 张 · ${escapeHtml(referenceModeHint)}</div>`);
    if (referenceWarning) referenceRow.insertAdjacentHTML('afterend', referenceWarning);
  }
  updateProductionVideoSelection();
  renderProductionAudioTimeline();
  restoreProductionEditorState(root, editorSnapshot);
}

async function productionSaveCurrent() {
  const current = productionCurrent(); if (!current) return false;
  const payload = {prompt: document.getElementById('production-prompt').value, negative_prompt: document.getElementById('production-negative').value, duration_sec: Number(document.getElementById('production-duration').value), references: current.production.references.map(item => ({reference_id:item.reference_id,source:item.source || 'asset'})), video_override:{provider:document.getElementById('production-video-provider').value,model:document.getElementById('production-video-model').value,resolution:document.getElementById('production-video-resolution').value}, continuity_enabled:document.getElementById('production-continuity').checked};
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
    continuityControl.title = supportsContinuity ? '' : '当前模型不支持连续镜头承接';
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
    const labels = {text_to_video:'纯文生视频', first_frame:'首帧', first_last_frame:'首尾帧', multi_reference:'多参考图'};
    const modes = Array.isArray(selectedCapability.reference_modes) ? selectedCapability.reference_modes : [];
    hint.textContent = `参考图能力：最多 ${limit} 张 · ${modes.map(mode => labels[mode] || mode).join(' / ') || (limit > 0 ? '单参考图' : '不支持参考图')}`;
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
  produceStatus('<span style="color:#93c5fd">正在提交当前片段任务...</span>');
  try { await submitProjectJob({job_type:'video.generate',target_type:'shot',target_id:String(productionSceneId),payload:{scene_id:productionSceneId}}); }
  catch(error){alert(error.message);}
}
async function productionGeneratePending() {
  const active = activeProjectJobs(job => job.job_type === 'video.generate');
  if (active.length) return cancelMatchingJobs(active);
  produceStatus('<span style="color:#93c5fd">正在创建未完成片段任务...</span>');
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
  try { await submitProjectJob({job_type:'audio.generate',target_type:'project'}); produceStatus('<span style="color:#93c5fd">台词生成任务已提交；BGM/音效来自项目素材库</span>'); }
  catch(error){produceStatus(`<span style="color:#ef4444">${escapeHtml(error.message)}</span>`);}
}
async function productionAssembleVideo() {
  const active = activeProjectJobs(job => job.job_type === 'final.assemble');
  if (active.length) return cancelMatchingJobs(active);
  try { await submitProjectJob({job_type:'final.assemble',target_type:'project'}); produceStatus('<span style="color:#93c5fd">成片合成任务已提交</span>'); }
  catch(error){produceStatus(`<span style="color:#ef4444">${escapeHtml(error.message)}</span>`);}
}
async function productionActivateVersion(versionId) { try { const data=await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/production/shots/${productionSceneId}/activate-version`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version_id:versionId})},'切换失败'); if(!data)return; productionData=data; renderProductionWorkspace(); } catch(error){alert(error.message);} }
async function productionRevisePrompt() { const feedback=document.getElementById('production-feedback').value.trim(); if(!feedback)return; try { const data=await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/production/shots/${productionSceneId}/revise-prompt`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({feedback})},'AI 修改失败'); if(!data)return; productionData=data; renderProductionWorkspace(); } catch(error){alert(error.message);} }

function initializeStudio() {
setupProductionWorkspace();

// ── 初始化 ──
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

console.log('🎬 AI 短剧创作平台已就绪');
console.log('   API 地址:', API_BASE);
console.log('   内置提示词模板:', Object.keys(PROMPT_TEMPLATES).length, '套');
}
const __moduleActions = {
  initializeStudio,
  captureProductionEditorState,
  restoreProductionEditorState,
  patchProductionJobState,
  patchProductionWorkspace,
  loadProductionWorkspace,
  productionReferenceHtml,
  promptReviewHtml,
  renderProductionWorkspace,
  productionSaveCurrent,
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
  productionReviewPrompts,
  productionGenerateAudio,
  productionAssembleVideo,
  productionActivateVersion,
  productionRevisePrompt,
};
Object.assign(globalThis.StudioActions, __moduleActions);
export { __moduleActions };
