const cancelProjectJob = (...args) => globalThis.StudioActions.cancelProjectJob(...args);
const escapeHtml = (...args) => globalThis.StudioActions.escapeHtml(...args);
const loadProjectJobs = (...args) => globalThis.StudioActions.loadProjectJobs(...args);
const retryProjectJob = (...args) => globalThis.StudioActions.retryProjectJob(...args);
const showProjectJobDetails = (...args) => globalThis.StudioActions.showProjectJobDetails(...args);

const JOB_STATE_LABELS = {
  queued: '排队中', running: '生成中', validating: '校验中', succeeded: '已完成',
  failed: '失败', canceled: '已终止', interrupted: '已中断',
};
const JOB_STAGE_LABELS = {
  queued: '等待执行', preparing: '准备输入', submitting_provider: '提交供应商',
  waiting_provider: '等待供应商', downloading: '下载结果', validating: '质量校验',
  visual_review: '视觉审核', saving: '保存结果', completed: '完成', failed: '失败', canceled: '已终止', interrupted: '已中断',
};
const JOB_TYPE_LABELS = {
  'script.generate': '生成剧本', 'script.draft.generate': '生成剧本草稿', 'script.draft.revise': 'AI 改写草稿', 'script.analyze': '确认并解析剧本', 'script.visualize': '生成视觉与分镜提示词', 'asset.generate': '生成图片',
  'character.reference.group': '角色参考组图', 'character.reference.edit': '角色参考修订', 'scene.reference.group': '场景参考组图', 'scene.reference.edit': '场景参考修订', 'prop.reference.group': '道具参考视图',
  'video.generate': '生成视频', 'audio.generate': '生成缺失台词', 'final.assemble': '合成作品',
};

function jobTargetLabel(job) {
  if (!job.target_id) return appState.projectName || '当前项目';
  const [identity, view] = String(job.target_id).split(':');
  return view ? `${identity} · ${view}` : job.target_type === 'shot' ? `片段 ${identity}` : identity;
}

function activeProjectJobs(predicate = () => true) {
  return projectJobs.filter(job => ['queued','running','validating'].includes(job.state) && predicate(job));
}

function syncJobsToAssetState() {
  appState.assetBusy = {};
  appState.assetErrors = {};
  const orderedJobs = [...projectJobs].sort((a, b) => String(a.updated_at || '').localeCompare(String(b.updated_at || '')));
  orderedJobs.forEach(job => {
    const isSingleAssetJob = job.job_type === 'asset.generate';
    const isCharacterGroup = job.job_type === 'character.reference.group' && job.target_type === 'character';
    const isSceneGroup = job.job_type === 'scene.reference.group' && job.target_type === 'scene';
    const isSceneEdit = job.job_type === 'scene.reference.edit' && job.target_type === 'scene';
    const isPropGroup = job.job_type === 'prop.reference.group' && job.target_type === 'prop';
    if (!isSingleAssetJob && !isCharacterGroup && !isSceneGroup && !isSceneEdit && !isPropGroup) return;
    const assetId = String(job.target_id || '').split(':')[0];
    const assetType = isCharacterGroup ? 'character' : (isSceneGroup || isSceneEdit) ? 'scene' : isPropGroup ? 'prop' : job.target_type;
    const stateSuffix = assetType === 'scene' && job.payload?.state_id ? `:${job.payload.state_id}` : '';
    const key = `${assetType}:${assetId}${stateSuffix}`;
    if (['queued','running','validating'].includes(job.state)) appState.assetBusy[key] = true;
    if (job.state === 'failed' || job.state === 'interrupted') appState.assetErrors[key] = job.error?.message || '生成失败';
    if (job.state === 'succeeded') delete appState.assetErrors[key];
  });
}

function renderProjectJobs() {
  const trigger = document.getElementById('project-jobs-trigger');
  const count = activeProjectJobs().length;
  trigger.disabled = !appState.projectId;
  document.getElementById('project-jobs-count').textContent = String(count);
  document.getElementById('project-jobs-project').textContent = appState.projectName || (appState.projectId ? `项目 ${appState.projectId.slice(0,8)}` : '尚未打开项目');
  const root = document.getElementById('project-jobs-list');
  if (!projectJobs.length) { root.innerHTML = '<div class="project-jobs-empty">当前项目暂无任务</div>'; updateJobDrivenControls(); return; }
  const groups = [
    ['进行中', job => ['queued','running','validating'].includes(job.state)],
    ['需要处理', job => ['failed','interrupted','canceled'].includes(job.state)],
    ['已完成', job => job.state === 'succeeded'],
  ];
  root.innerHTML = groups.map(([title, predicate]) => {
    const jobs = projectJobs.filter(predicate);
    if (!jobs.length) return '';
    return `<div class="project-job-group-title">${title} · ${jobs.length}</div>${jobs.map(job => {
      const warning = job.quality?.warnings?.[0] || (job.quality?.visual_review?.status === 'skipped' && job.state === 'succeeded' ? job.quality.visual_review.reason : '');
      return `<div class="project-job-item"><div class="project-job-main"><div><div class="project-job-name">${escapeHtml(JOB_TYPE_LABELS[job.job_type] || job.job_type)} · ${escapeHtml(jobTargetLabel(job))}</div><div class="project-job-meta">${escapeHtml(JOB_STAGE_LABELS[job.stage] || job.stage || '')}${job.attempt > 1 ? ` · 第 ${job.attempt} 次尝试` : ''}</div></div><span class="project-job-state ${job.state}">${JOB_STATE_LABELS[job.state] || job.state}</span></div>${['queued','running','validating'].includes(job.state) ? `<div class="project-job-track"><span style="width:${Math.max(3,job.progress?.percent||0)}%"></span></div>` : ''}${job.error?.message ? `<div class="project-job-error">${escapeHtml(job.error.message)}${job.error.suggestion ? `<br>${escapeHtml(job.error.suggestion)}` : ''}</div>` : ''}${warning ? `<div class="project-job-warning">质量：${escapeHtml(warning)}</div>` : ''}<div class="project-job-actions"><button onclick="StudioActions.showProjectJobDetails('${job.job_id}')">详情</button>${job.can_cancel ? `<button onclick="StudioActions.cancelProjectJob('${job.job_id}')">终止</button>` : ''}${job.can_retry ? `<button onclick="StudioActions.retryProjectJob('${job.job_id}')">重试</button>` : ''}</div></div>`;
    }).join('')}`;
  }).join('');
  updateJobDrivenControls();
}

function updateJobDrivenControls() {
  const assetButtons = {character:'btn-gen-roles', scene:'btn-gen-scenes', prop:'btn-gen-props'};
  Object.entries(assetButtons).forEach(([assetType, buttonId]) => {
    const button = document.getElementById(buttonId);
    if (!button) return;
    const active = activeProjectJobs(job => job.job_type === 'asset.generate' && job.target_type === assetType);
    if (!active.length) { button.textContent = '生成缺失图片'; return; }
    const batchId = active.find(job => job.batch_id)?.batch_id;
    const all = batchId ? projectJobs.filter(job => job.batch_id === batchId) : active;
    const completed = all.filter(job => ['succeeded','failed','canceled','interrupted'].includes(job.state)).length;
    button.textContent = batchId ? `终止本批次 ${completed}/${all.length}` : '终止生成';
  });
  const scriptGenerate = document.getElementById('btn-gen-script');
  const scriptAnalyze = document.getElementById('btn-analyze-script');
  if (scriptGenerate) scriptGenerate.textContent = activeProjectJobs(job => job.job_type === 'script.generate').length ? '终止生成' : 'AI 生成剧本';
  if (scriptAnalyze) scriptAnalyze.textContent = activeProjectJobs(job => job.job_type === 'script.analyze').length ? '终止解析' : 'AI 解析剧本';
  if (activeImageEditor) {
    const prefix = `${activeImageEditor.assetId}:`;
    const related = activeProjectJobs(job => (
      (job.job_type === 'asset.generate' && job.target_type === activeImageEditor.assetType && String(job.target_id).startsWith(prefix))
      || (activeImageEditor.assetType === 'character' && job.job_type === 'character.reference.group' && job.target_type === 'character' && String(job.target_id) === activeImageEditor.assetId)
      || (activeImageEditor.assetType === 'scene' && job.job_type === 'scene.reference.edit' && job.target_type === 'scene' && String(job.target_id).startsWith(prefix) && String(job.payload?.state_id || '') === (activeImageEditor.stateId || ''))
    ));
    const current = related.find(job => job.target_id === `${activeImageEditor.assetId}:${activeImageEditor.viewType}`);
    const groupJob = related.find(job => job.job_type === 'character.reference.group');
    const singleButton = document.getElementById('btn-image-regenerate');
    const allButton = document.getElementById('btn-image-generate-all');
    if (singleButton && current) singleButton.textContent = '终止';
    if (allButton && groupJob) {
      allButton.disabled = false;
      allButton.textContent = '终止四视图任务';
    }
  }
}

function toggleProjectJobs(force) {
  if (!appState.projectId) return;
  const drawer = document.getElementById('project-jobs-drawer');
  drawer.classList.toggle('open', force === undefined ? !drawer.classList.contains('open') : Boolean(force));
  if (drawer.classList.contains('open')) loadProjectJobs();
}
const __moduleActions = {
  jobTargetLabel,
  activeProjectJobs,
  syncJobsToAssetState,
  renderProjectJobs,
  updateJobDrivenControls,
  toggleProjectJobs,
};
Object.assign(globalThis.StudioActions, __moduleActions);
Object.defineProperties(globalThis, {
  JOB_STATE_LABELS: { configurable: true, get: () => JOB_STATE_LABELS },
  JOB_STAGE_LABELS: { configurable: true, get: () => JOB_STAGE_LABELS },
  JOB_TYPE_LABELS: { configurable: true, get: () => JOB_TYPE_LABELS },
});
export { __moduleActions };
