const activeProjectJobs = (...args) => globalThis.StudioActions.activeProjectJobs(...args);
const cancelMatchingJobs = (...args) => globalThis.StudioActions.cancelMatchingJobs(...args);
const escapeHtml = (...args) => globalThis.StudioActions.escapeHtml(...args);
const handleProjectJobEvent = (...args) => globalThis.StudioActions.handleProjectJobEvent(...args);
const mediaUrl = (...args) => globalThis.StudioActions.mediaUrl(...args);
const isMissingMediaPath = (...args) => globalThis.StudioActions.isMissingMediaPath(...args);
const productionCurrent = (...args) => globalThis.StudioActions.productionCurrent(...args);
const projectJsonRequest = (...args) => globalThis.StudioActions.projectJsonRequest(...args);
const submitProjectJob = (...args) => globalThis.StudioActions.submitProjectJob(...args);

let productionAudioDraftDirty = false;
let audioTimelineLoading = false;
let subtitleExportPath = '';

function audioOptions(items, selected, empty) {
  return `<option value="">${empty}</option>${items.map(item => `<option value="${escapeHtml(item.asset_id)}" ${item.asset_id === selected ? 'selected' : ''}>${escapeHtml(item.label || item.asset_id)}</option>`).join('')}`;
}

function audioSceneFor(current) {
  const audio = productionData?.audio || {scenes: []};
  return (audio.scenes || []).find(item => String(item.scene_id) === String(current.scene.scene_id)) || {
    scene_id: current.scene.scene_id,
    dialogue_audios: [],
    sfx_tracks: [],
    bgm_asset_id: '',
    bgm_volume: 0.18,
    bgm_fade_ms: 350,
  };
}

function audioSpeakers() {
  return [...new Set((productionData?.productions || [])
    .flatMap(item => item.scene.dialogue || [])
    .map(line => line.character)
    .filter(Boolean))];
}

function voiceProfileRows(audio) {
  const speakers = audioSpeakers();
  const nativeSpeakers = new Set((audio?.scenes || [])
    .filter(scene => scene.native_audio_enabled)
    .flatMap(scene => (scene.dialogue_audios || []).map(line => line.character).filter(Boolean)));
  if (!speakers.length) return '<div class="audio-empty">项目中暂无需要配音的角色。</div>';
  return speakers.map((character, index) => {
    const profile = audio.voice_profiles?.[character] || {};
    if (nativeSpeakers.has(character)) {
      return `<div class="audio-voice-row" data-audio-voice-row="${escapeHtml(character)}"><div class="audio-voice-name"><strong>${escapeHtml(character)}</strong><span class="audio-ok">Seedance 原生对白</span></div><span class="audio-native-note">无需单独确认音色</span></div>`;
    }
    const provider = profile.provider || 'qwen3';
    const status = profile.confirmed ? '<span class="audio-ok">已确认</span>' : '<span class="audio-warn">待确认</span>';
    return `<div class="audio-voice-row" data-audio-voice-row="${escapeHtml(character)}">
      <div class="audio-voice-name"><strong>${escapeHtml(character)}</strong>${status}</div>
      <select data-audio-voice-provider aria-label="${escapeHtml(character)} 配音服务">
        <option value="qwen3" ${provider === 'qwen3' ? 'selected' : ''}>Qwen3</option>
        <option value="minimax" ${provider === 'minimax' ? 'selected' : ''}>MiniMax</option>
        <option value="edge" ${provider === 'edge' ? 'selected' : ''}>Edge 兜底</option>
      </select>
      <input data-audio-voice-id data-character="${escapeHtml(character)}" value="${escapeHtml(profile.voice || '')}" placeholder="音色 ID" aria-label="${escapeHtml(character)} 音色 ID">
      <button class="btn-secondary btn-sm" onclick="StudioActions.productionSaveVoiceProfile(${index})">确认音色</button>
    </div>`;
  }).join('');
}

function audioLineStatus(line) {
  if (line.timing_conflict) return ['超过镜头时长', '#fca5a5'];
  if (line.needs_regeneration) return ['设定已更新，需重新生成', '#fca5a5'];
  if (line.success) return [`${(line.duration_sec || 0).toFixed(1)} 秒 · ${line.provider || ''}`, '#86efac'];
  return [line.error_message ? `失败：${line.error_message}` : '待生成', '#fbbf24'];
}

function audioDialogueVisualTrack(current, scene) {
  const lines = scene.dialogue_audios || [];
  const totalMs = Math.max(1, Number(current.scene.duration_sec || 3) * 1000);
  if (!lines.length) return '<div class="audio-empty">暂无可视对白片段。</div>';
  return `<div class="audio-dialogue-visual-track" role="list" aria-label="对白时间轨道">${lines.map(line => {
    const start = Math.max(0, Number(line.start_ms || 0));
    const end = Math.max(start + 120, Number(line.end_ms || start + (line.duration_sec || 0.8) * 1000));
    const left = Math.max(0, Math.min(100, start / totalMs * 100));
    const width = Math.max(4, Math.min(100 - left, (end - start) / totalMs * 100));
    const [status, color] = audioLineStatus(line);
    return `<button type="button" class="audio-dialogue-visual-clip" data-audio-visual-line="${escapeHtml(line.line_id)}" style="--clip-left:${left}%;--clip-width:${width}%;--clip-color:${color}" title="${escapeHtml(`${line.character || '旁白'} · ${(start / 1000).toFixed(2)}-${(end / 1000).toFixed(2)} 秒`)}" onclick="document.getElementById('audio-text-${escapeHtml(line.line_id)}')?.scrollIntoView({behavior:'smooth',block:'center'});document.getElementById('audio-text-${escapeHtml(line.line_id)}')?.focus({preventScroll:true})"><span>${escapeHtml(line.character || '旁白')}</span><small>${(start / 1000).toFixed(1)}s</small></button>`;
  }).join('')}</div>`;
}

function audioLineRows(current, scene) {
  const lines = scene.dialogue_audios || [];
  const totalMs = Math.max(1, Number(current.scene.duration_sec || 3) * 1000);
  if (!lines.length) return '<div class="audio-empty">尚未生成台词音频。请先确认项目音色，再点击“生成缺失台词”。</div>';
  return lines.map(line => {
    const busy = activeProjectJobs(job => job.job_type === 'audio.generate' && job.target_id === line.line_id).length;
    const versions = line.versions || [];
    const [status, color] = audioLineStatus(line);
    const start = Number(line.start_ms || 0);
    const end = Math.max(start + 120, Number(line.end_ms || start + (line.duration_sec || 0.8) * 1000));
    const left = Math.max(0, Math.min(100, start / totalMs * 100));
    const width = Math.max(8, Math.min(100 - left, (end - start) / totalMs * 100));
    const nativeScene = Boolean(scene.native_audio_enabled);
    const playback = line.audio_path && !isMissingMediaPath(line.audio_path)
      ? `<audio controls preload="metadata" src="${mediaUrl(line.audio_path)}"></audio>`
      : (line.audio_path ? '<span class="media-missing-notice">音频文件缺失，历史记录仍保留</span>' : (nativeScene ? '<span class="audio-native-note">原生对白随视频轨道</span>' : ''));
    const versionSelect = versions.length > 1
      ? `<select class="audio-version-select" aria-label="配音历史版本" onchange="StudioActions.productionActivateAudioVersion(${current.scene.scene_id},${line.dialogue_index},this.value)">${versions.map((version, index) => `<option value="${escapeHtml(version.version_id)}" ${version.version_id === line.active_version_id ? 'selected' : ''}>版本 ${index + 1}</option>`).join('')}</select>`
      : '';
    return `<article class="audio-dialogue-row" data-audio-line="${escapeHtml(line.line_id)}" style="--clip-left:${left}%;--clip-width:${width}%">
      <div class="audio-dialogue-clip"><span class="audio-time-badge">${(start / 1000).toFixed(2)}s</span><strong>${escapeHtml(line.character || '旁白')}</strong><span class="audio-line-state" style="color:${color}">${escapeHtml(status)}</span></div>
      <textarea id="audio-text-${escapeHtml(line.line_id)}" rows="2" oninput="StudioActions.productionMarkAudioDraft()" aria-label="${escapeHtml(line.character || '旁白')} 台词">${escapeHtml(line.text || '')}</textarea>
      <details class="audio-advanced"><summary>情绪与声音参数</summary><div class="audio-line-controls">
        <label>情绪<select id="audio-emotion-${escapeHtml(line.line_id)}" onchange="StudioActions.productionMarkAudioDraft()"><option value="calm" ${line.emotion === 'calm' ? 'selected' : ''}>平静</option><option value="happy" ${line.emotion === 'happy' ? 'selected' : ''}>高兴</option><option value="sad" ${line.emotion === 'sad' ? 'selected' : ''}>悲伤</option><option value="angry" ${line.emotion === 'angry' ? 'selected' : ''}>愤怒</option><option value="fearful" ${line.emotion === 'fearful' ? 'selected' : ''}>害怕</option><option value="surprised" ${line.emotion === 'surprised' ? 'selected' : ''}>惊讶</option></select></label>
        <label>语速<input id="audio-speed-${escapeHtml(line.line_id)}" type="number" min="0.5" max="2" step="0.05" value="${line.speed || 1}" oninput="StudioActions.productionMarkAudioDraft()"></label>
        <label>音量<input id="audio-volume-${escapeHtml(line.line_id)}" type="number" min="0.1" max="2" step="0.05" value="${line.volume || 1}" oninput="StudioActions.productionMarkAudioDraft()"></label>
        <label>音高<input id="audio-pitch-${escapeHtml(line.line_id)}" type="number" min="-12" max="12" step="1" value="${line.pitch || 0}" oninput="StudioActions.productionMarkAudioDraft()"></label>
        <label>前停顿<input id="audio-lead-${escapeHtml(line.line_id)}" type="number" min="0" max="10000" step="50" value="${line.lead_silence_ms || 0}" oninput="StudioActions.productionMarkAudioDraft()"></label>
        <label>后停顿<input id="audio-tail-${escapeHtml(line.line_id)}" type="number" min="0" max="10000" step="50" value="${line.tail_silence_ms || 0}" oninput="StudioActions.productionMarkAudioDraft()"></label>
      </div></details>
      <div class="audio-line-actions"><button class="btn-secondary btn-sm" onclick="StudioActions.productionSaveAudioLine(${current.scene.scene_id},${line.dialogue_index},'${escapeHtml(line.line_id)}')">保存</button>${nativeScene ? '<span class="audio-native-note">Seedance 原生对白，不重复生成 TTS</span>' : `<button class="btn-secondary btn-sm" onclick="StudioActions.productionGenerateAudioLine('${escapeHtml(line.line_id)}')">${busy ? '终止生成' : '重新生成'}</button>`}${playback}${versionSelect}</div>
    </article>`;
  }).join('');
}

function subtitlePreviewRows(scene) {
  const mode = productionData?.subtitle_mode || 'dialogue';
  const asrReady = ['ready', 'changed'].includes(scene.subtitle_correction_status) && (scene.asr_segments || []).length;
  if (asrReady) {
    const speakers = Object.fromEntries((scene.dialogue_audios || []).map(line => [line.line_id || `${scene.scene_id}:${line.dialogue_index}`, line.character || '旁白']));
    const stamp = value => `${Math.floor(value / 60000)}:${String(Math.floor(value / 1000) % 60).padStart(2, '0')}.${String(value % 1000).padStart(3, '0')}`;
    return scene.asr_segments.map(segment => {
      const base = segment.text || '';
      const text = mode === 'speaker' ? `${speakers[segment.line_id] ? `${speakers[segment.line_id]}：` : ''}${base}` : base;
      return `<div class="subtitle-preview-row"><span>${stamp(Number(segment.start_ms || 0))} - ${stamp(Number(segment.end_ms || 0))}</span><span>${escapeHtml(text)}</span></div>`;
    }).join('');
  }
  const tracks = (scene.dialogue_audios || []).filter(line => line.success);
  if (!tracks.length) return '<div class="audio-empty">生成台词后，这里会显示按真实时间轴计算的字幕。</div>';
  const stamp = value => `${Math.floor(value / 60000)}:${String(Math.floor(value / 1000) % 60).padStart(2, '0')}.${String(value % 1000).padStart(3, '0')}`;
  return tracks.map(line => {
    const text = mode === 'speaker' ? `${line.character}：${line.text}` : line.text;
    return `<div class="subtitle-preview-row"><span>${stamp(line.start_ms || 0)} - ${stamp(line.end_ms || 0)}</span><span>${escapeHtml(text)}</span></div>`;
  }).join('');
}

function audioStatusBar(audio, summary) {
  const subtitleMode = productionData?.subtitle_mode || 'dialogue';
  const timingConflicts = summary?.timing_conflict_count || 0;
  const failedLines = summary?.failed_line_count || 0;
  const pendingLines = summary?.pending_line_count || 0;
  const lineIssue = timingConflicts || failedLines || pendingLines;
  const warning = lineIssue
    ? `<span class="audio-status-warning">${timingConflicts} 个时间冲突 · ${failedLines} 句失败待重试 · ${pendingLines} 句待生成</span>`
    : summary?.can_assemble === false
      ? '<span class="audio-status-warning">尚未满足正式合成条件，请先确认项目音色</span>'
      : '<span class="audio-status-ok">可进入正式合成检查</span>';
  const hasBgm = (audio?.scenes || []).some(scene => scene.bgm_asset_id) ||
    (audio?.bgm_path && !isMissingMediaPath(audio.bgm_path));
  const nativeScenes = (audio?.scenes || []).filter(scene => scene.native_audio_enabled);
  const asrStates = [...new Set(nativeScenes.map(scene => scene.subtitle_correction_status || 'fallback'))];
  const nativeMode = nativeScenes.length
    ? `<span class="audio-status-count">Seedance 原生对白 ${nativeScenes.length} 镜 · 字幕校正 ${escapeHtml(asrStates.join(' / '))}</span>`
    : '';
  let sourceMode = audio?.source_audio_enabled === false
    ? `视频原声关闭 · 使用项目音频混音${hasBgm ? '' : ' · 尚未选择 BGM'}`
    : '兼容旧项目视频原声';
  const asrControl = nativeScenes.length
    ? `<label class="audio-asr-control"><input type="checkbox" ${audio?.asr_enabled ? 'checked' : ''} onchange="StudioActions.productionSetAsr(this.checked)"> ASR 字幕校正</label>`
    : '';
  sourceMode = `${sourceMode}${asrControl}`;
  return `<header class="audio-status-bar"><div><strong>项目音频</strong><span class="audio-status-count">音色 ${summary?.confirmed_voice_count || 0}/${summary?.voice_count || 0}</span><span class="audio-status-count">${sourceMode}</span>${nativeMode}${warning}</div><div class="audio-status-actions"><label>字幕<select id="production-subtitle-mode" onchange="StudioActions.productionSetSubtitleMode(this.value)"><option value="off" ${subtitleMode === 'off' ? 'selected' : ''}>关闭</option><option value="dialogue" ${subtitleMode === 'dialogue' ? 'selected' : ''}>只显示台词</option><option value="speaker" ${subtitleMode === 'speaker' ? 'selected' : ''}>角色名 + 台词</option><option value="narration" ${subtitleMode === 'narration' ? 'selected' : ''}>只显示旁白</option><option value="all" ${subtitleMode === 'all' ? 'selected' : ''}>台词 + 旁白</option></select></label><button class="btn-secondary btn-sm" onclick="StudioActions.productionPreviewAudioMix()">试听混音</button><button class="btn-secondary btn-sm" onclick="StudioActions.productionExportSubtitles()">导出 SRT</button><button class="btn-secondary btn-sm" onclick="StudioActions.productionBurnSubtitles()">烧录到成片</button></div></header>${subtitleNarrationControl()}${productionAudioDraftDirty ? '<div class="audio-draft-notice">本地音频草稿尚未保存，后台更新不会覆盖当前编辑。</div>' : ''}${audio.preview_mix_path ? `<audio controls preload="metadata" class="audio-preview" src="${mediaUrl(audio.preview_mix_path)}"></audio>` : ''}${subtitleExportPath ? `<a class="btn-secondary btn-sm audio-srt-link" href="${mediaUrl(subtitleExportPath)}" download>下载当前 SRT</a>` : ''}`;
}

const SUBTITLE_MODE_LABELS = {
  off: '已关闭',
  dialogue: '只显示台词',
  speaker: '角色名 + 台词',
  narration: '只显示旁白',
  all: '台词 + 旁白',
};

function subtitleModeLabel(mode) {
  return SUBTITLE_MODE_LABELS[mode] || SUBTITLE_MODE_LABELS.dialogue;
}

function subtitleNarrationControl() {
  const mode = productionData?.subtitle_mode || 'dialogue';
  if (mode !== 'narration' && mode !== 'all') return '';
  const text = productionData?.subtitle_narration_text || '';
  return `<div class="audio-narration-block"><label>旁白文本<textarea id="production-subtitle-narration" rows="3" placeholder="每行一句旁白，按顺序铺到无台词的镜头">${escapeHtml(text)}</textarea></label><button class="btn-secondary btn-sm" onclick="StudioActions.productionSaveNarration()">保存旁白</button><span class="audio-status-count">旁白没有配音，只作为字幕叠加</span></div>`;
}

function audioBgmTrack(scene, bgm) {
  return `<section class="audio-track-block"><div class="audio-track-title"><strong>BGM</strong><span>同一素材跨镜头连续播放</span></div><div class="audio-bgm-row"><label>素材<select id="audio-bgm-asset" onchange="StudioActions.productionSaveAudioBgm()">${audioOptions(bgm, scene.bgm_asset_id, '自动/静音')}</select></label><label>音量<input id="audio-bgm-volume" type="number" min="0.01" max="1" step="0.01" value="${scene.bgm_volume || 0.18}" onchange="StudioActions.productionSaveAudioBgm()"></label><label>淡入淡出<input id="audio-bgm-fade" type="number" min="0" max="10000" step="50" value="${scene.bgm_fade_ms || 350}" onchange="StudioActions.productionSaveAudioBgm()"></label></div></section>`;
}

function audioSfxTrack(scene, sfx) {
  const rows = (scene.sfx_tracks || []).map((track, index) => `<div class="audio-sfx-row"><select data-audio-sfx-id aria-label="音效素材">${audioOptions(sfx, track.asset_id, '选择音效')}</select><input data-audio-sfx-start type="number" min="0" step="50" value="${track.start_ms || 0}" title="镜头内开始毫秒"><input data-audio-sfx-volume type="number" min="0.1" max="2" step="0.05" value="${track.volume || 1}" title="音量"><button class="btn-secondary btn-sm" onclick="StudioActions.productionRemoveAudioSfx(${index})">删除</button></div>`).join('');
  return `<section class="audio-track-block"><div class="audio-track-title"><strong>关键音效</strong><span>最多保留本镜需要的动作音效</span></div>${rows || '<div class="audio-empty">本镜尚未添加关键音效。</div>'}<div class="audio-sfx-add"><select id="audio-sfx-add">${audioOptions(sfx, '', '添加关键音效')}</select><button class="btn-secondary btn-sm" onclick="StudioActions.productionAddAudioSfx()">添加音效</button>${rows ? '<button class="btn-secondary btn-sm" onclick="StudioActions.productionSaveAudioSfx()">保存音效时间</button>' : ''}</div></section>`;
}

function audioProjectSidebar(audio) {
  const library = audio.library_assets || [];
  const libraryRows = library.map(item => `<div class="audio-library-row"><div><strong>${escapeHtml(item.kind.toUpperCase())}</strong><span>${escapeHtml(item.label || item.asset_id)}</span></div><audio controls preload="metadata" src="${mediaUrl(item.audio_path)}"></audio><a class="btn-secondary btn-sm" href="${mediaUrl(item.audio_path)}" download>下载</a><button class="btn-secondary btn-sm" onclick="StudioActions.productionDeleteAudioLibrary('${escapeHtml(item.asset_id)}')">删除</button></div>`).join('') || '<div class="audio-empty">暂无项目音频素材。</div>';
  return `<aside class="audio-project-sidebar"><section class="audio-sidebar-section"><div class="audio-track-title"><strong>项目角色音色</strong><span>确认后才能批量生成台词</span></div>${voiceProfileRows(audio)}</section><section class="audio-sidebar-section"><div class="audio-track-title"><strong>素材库</strong><button class="btn-secondary btn-sm" onclick="StudioActions.productionAutoSelectAudio()">自动选择素材</button></div><div class="audio-upload"><input id="audio-library-file" type="file" accept="audio/*"><select id="audio-library-kind"><option value="bgm">BGM</option><option value="sfx">关键音效</option></select><input id="audio-library-label" placeholder="素材名称"><input id="audio-library-tags" placeholder="标签，逗号分隔"><button class="btn-secondary btn-sm" onclick="StudioActions.productionUploadAudioLibrary()">上传</button></div><div class="audio-library-list">${libraryRows}</div></section></aside>`;
}

function productionAudioTimelineHtml(current) {
  const audio = productionData?.audio || {scenes: [], library_assets: [], voice_profiles: {}};
  const scene = audioSceneFor(current);
  const library = audio.library_assets || [];
  const bgm = library.filter(item => item.kind === 'bgm');
  const sfx = library.filter(item => item.kind === 'sfx');
  const summary = productionData?.audio_summary || {};
  return `<section id="production-audio-timeline" class="audio-timeline"><div class="audio-workspace-scroll">${audioStatusBar(audio, summary)}<div class="audio-scope-note"><strong>父场景音频时间轴</strong><span>镜头 ${current.scene.scene_id} 的对白、BGM 和 SFX 按父场景管理；当前视频子镜头沿用此音频范围。</span></div><div class="audio-workspace-grid"><main class="audio-track-panel"><div class="audio-track-heading"><div><h4>镜头 ${current.scene.scene_id} 音频时间轴</h4><span>${escapeHtml(current.scene.location || '')} · ${Number(current.scene.duration_sec || 0).toFixed(1)} 秒</span></div><span class="audio-ruler-label">台词使用真实音频时长</span></div><div class="audio-ruler"><span>0s</span><span>${(Number(current.scene.duration_sec || 3) / 2).toFixed(1)}s</span><span>${Number(current.scene.duration_sec || 3).toFixed(1)}s</span></div><section class="audio-track-block"><div class="audio-track-title"><strong>对白轨道</strong><span>点击片段定位到下方编辑行</span></div>${audioDialogueVisualTrack(current, scene)}</section><section class="audio-track-block"><div class="audio-track-title"><strong>台词编辑</strong><span>保存后按真实音频时长重新排布</span></div><div class="audio-dialogue-list">${audioLineRows(current, scene)}</div></section>${audioBgmTrack(scene, bgm)}${audioSfxTrack(scene, sfx)}<section class="audio-track-block audio-subtitle-block"><div class="audio-track-title"><strong>字幕预览</strong><span>${subtitleModeLabel(productionData?.subtitle_mode)}</span></div>${subtitlePreviewRows(scene)}</section></main>${audioProjectSidebar(audio)}</div></div></section>`;
}

function captureProductionAudioDraft(panel) {
  const workspace = panel?.querySelector('#production-audio-timeline');
  if (!workspace) return null;
  const fields = [...workspace.querySelectorAll('input[id], textarea[id], select[id]')]
    .filter(item => item.type !== 'file')
    .map(item => ({id: item.id, value: item.value, scrollTop: item.scrollTop, selectionStart: item.selectionStart, selectionEnd: item.selectionEnd}));
  const details = [...workspace.querySelectorAll('details[open]')].map(item => item.dataset.audioDetails || item.querySelector('summary')?.textContent || '');
  const scroll = workspace.querySelector('.audio-workspace-scroll');
  return {fields, active: document.activeElement?.id || '', scrollTop: scroll?.scrollTop || 0, details};
}

function restoreProductionAudioDraft(panel, snapshot) {
  const workspace = panel?.querySelector('#production-audio-timeline');
  if (!workspace || !snapshot) return;
  snapshot.fields.forEach(saved => {
    const field = workspace.querySelector(`#${CSS.escape(saved.id)}`);
    if (!field) return;
    field.value = saved.value;
    field.scrollTop = saved.scrollTop || 0;
    if (field.id === snapshot.active) {
      field.focus();
      if (typeof field.setSelectionRange === 'function' && saved.selectionStart != null) field.setSelectionRange(saved.selectionStart, saved.selectionEnd);
    }
  });
  workspace.querySelectorAll('details').forEach(item => {
    const key = item.dataset.audioDetails || item.querySelector('summary')?.textContent || '';
    item.open = snapshot.details.includes(key);
  });
  const scroll = workspace.querySelector('.audio-workspace-scroll');
  if (scroll) scroll.scrollTop = snapshot.scrollTop || 0;
}

async function loadProjectAudioTimeline() {
  if (audioTimelineLoading || !appState.projectId) return;
  audioTimelineLoading = true;
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/audio-timeline`, {}, '加载音频时间轴失败');
    if (data?.audio) { productionData.audio = data.audio; renderProductionAudioTimeline(); }
  } catch (_) {
    /* The production workspace remains usable without optional audio data. */
  } finally { audioTimelineLoading = false; }
}

function renderProductionAudioTimeline() {
  const panel = document.querySelector('#shot-production-workspace #production-audio-workspace');
  const current = productionCurrent();
  if (!panel || !current) return;
  const existing = panel.querySelector('#production-audio-timeline');
  if (productionAudioDraftDirty && existing) {
    let notice = panel.querySelector('.audio-draft-notice');
    if (!notice) {
      notice = document.createElement('div');
      notice.className = 'audio-draft-notice';
      notice.textContent = '服务端音频数据已更新，当前未保存草稿仍保留。';
      existing.querySelector('.audio-workspace-scroll')?.prepend(notice);
    }
    notice.hidden = false;
    return;
  }
  const draft = productionAudioDraftDirty ? captureProductionAudioDraft(panel) : null;
  existing?.remove();
  panel.insertAdjacentHTML('beforeend', productionAudioTimelineHtml(current));
  restoreProductionAudioDraft(panel, draft);
  if (!productionData?.audio) loadProjectAudioTimeline();
}

function productionMarkAudioDraft() { productionAudioDraftDirty = true; }

function resetProductionAudioDraft() {
  productionAudioDraftDirty = false;
  subtitleExportPath = '';
}

async function productionSaveAudioLine(sceneId, dialogueIndex, lineId) {
  const get = name => document.getElementById(`${name}-${lineId}`);
  const payload = {text:get('audio-text').value, emotion:get('audio-emotion').value, speed:Number(get('audio-speed').value), volume:Number(get('audio-volume').value), pitch:Number(get('audio-pitch').value), lead_silence_ms:Number(get('audio-lead').value), tail_silence_ms:Number(get('audio-tail').value)};
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/audio-timeline/lines/${sceneId}/${dialogueIndex}`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}, '保存配音失败');
    if (data?.audio) { productionAudioDraftDirty = false; productionData.audio = data.audio; renderProductionAudioTimeline(); }
  } catch (error) { alert(error.message); }
}

async function productionGenerateAudioLine(lineId) {
  const active = activeProjectJobs(job => job.job_type === 'audio.generate' && job.target_id === lineId);
  if (active.length) return cancelMatchingJobs(active);
  try { await submitProjectJob({job_type:'audio.generate',target_type:'audio_line',target_id:lineId,payload:{line_id:lineId}}); }
  catch (error) { alert(error.message); }
}

async function productionActivateAudioVersion(sceneId, dialogueIndex, versionId) {
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/audio-timeline/lines/${sceneId}/${dialogueIndex}/activate`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version_id:versionId})}, '切换配音版本失败');
    if (data?.audio) { productionData.audio = data.audio; renderProductionAudioTimeline(); }
  } catch (error) { alert(error.message); }
}

async function productionSaveVoiceProfile(index) {
  const voice = document.querySelectorAll('#production-audio-timeline [data-audio-voice-id]')[index];
  const provider = document.querySelectorAll('#production-audio-timeline [data-audio-voice-provider]')[index];
  if (!voice || !provider) return;
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/audio-timeline/voice-profiles/${encodeURIComponent(voice.dataset.character)}`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({voice:voice.value,provider:provider.value,confirmed:true})}, '保存角色音色失败');
    if (data?.audio) { productionData.audio = data.audio; renderProductionAudioTimeline(); }
  } catch (error) { alert(error.message); }
}

async function productionUploadAudioLibrary() {
  const input = document.getElementById('audio-library-file');
  if (!input?.files?.[0]) return;
  const form = new FormData();
  form.append('file', input.files[0]);
  form.append('kind', document.getElementById('audio-library-kind').value);
  form.append('label', document.getElementById('audio-library-label').value);
  form.append('tags', document.getElementById('audio-library-tags').value);
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/audio-library/upload`, {method:'POST',body:form}, '上传音频失败');
    if (data?.audio) { productionData.audio = data.audio; renderProductionAudioTimeline(); }
  } catch (error) { alert(error.message); }
}

async function productionDeleteAudioLibrary(assetId) {
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/audio-library/${encodeURIComponent(assetId)}`, {method:'DELETE'}, '删除音频素材失败');
    if (data?.audio) { productionData.audio = data.audio; renderProductionAudioTimeline(); }
  } catch (error) { alert(error.message); }
}

async function productionAutoSelectAudio() {
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/audio-timeline/auto-select`, {method:'POST'}, '自动选择音频失败');
    if (data?.audio) { productionData.audio = data.audio; renderProductionAudioTimeline(); }
  } catch (error) { alert(error.message); }
}

async function productionPreviewAudioMix() {
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/audio-timeline/preview`, {method:'POST'}, '创建混音预览失败');
    if (data?.job) handleProjectJobEvent({job:data.job});
  } catch (error) { alert(error.message); }
}

async function productionExportSubtitles() {
  const mode = productionData?.subtitle_mode || 'dialogue';
  if (mode === 'off') return alert('请先选择一种字幕模式。');
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/audio-timeline/subtitles?mode=${encodeURIComponent(mode)}`, {}, '导出 SRT 失败');
    subtitleExportPath = data.subtitle_path || '';
    renderProductionAudioTimeline();
  } catch (error) { alert(error.message); }
}

async function productionBurnSubtitles() {
  const mode = productionData?.subtitle_mode || 'dialogue';
  if (mode === 'off') return alert('请先选择一种字幕模式。');
  if (!productionData?.output_video_path) return alert('请先合成成片，再烧录字幕。');
  if (!confirm('将按当前字幕模式生成一份新的成片（原成片不会被覆盖），继续？')) return;
  try {
    const data = await projectJsonRequest(
      `${API_BASE}/api/projects/${appState.projectId}/subtitles/burn?mode=${encodeURIComponent(mode)}`,
      {method:'POST'}, '烧录字幕失败',
    );
    subtitleExportPath = data.subtitle_path || subtitleExportPath;
    if (data.output_video_path) productionData.output_video_path = data.output_video_path;
    alert(`已生成带字幕成片：${data.output_video_path}`);
  } catch (error) { alert(error.message); }
}

async function productionSaveNarration() {
  const field = document.getElementById('production-subtitle-narration');
  if (!field) return;
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/production/settings`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({subtitle_narration_text:field.value})}, '保存旁白失败');
    if (!data) return;
    productionData = data;
    renderProductionAudioTimeline();
  } catch (error) { alert(error.message); }
}

async function productionSaveAudioBgm() {
  const assetId = document.getElementById('audio-bgm-asset').value;
  const payload = {asset_id:assetId,muted:!assetId,volume:Number(document.getElementById('audio-bgm-volume').value),fade_ms:Number(document.getElementById('audio-bgm-fade').value)};
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/audio-timeline/scenes/${productionSceneId}/bgm`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}, '保存 BGM 失败');
    if (data?.audio) { productionData.audio = data.audio; renderProductionAudioTimeline(); }
  } catch (error) { alert(error.message); }
}

function productionAudioSfxTracks() {
  return [...document.querySelectorAll('#production-audio-timeline [data-audio-sfx-id]')]
    .map((field,index) => ({asset_id:field.value,start_ms:Number(document.querySelectorAll('#production-audio-timeline [data-audio-sfx-start]')[index].value),volume:Number(document.querySelectorAll('#production-audio-timeline [data-audio-sfx-volume]')[index].value)}))
    .filter(item => item.asset_id);
}

async function productionSaveAudioSfx(tracks = productionAudioSfxTracks()) {
  try {
    const data = await projectJsonRequest(`${API_BASE}/api/projects/${appState.projectId}/audio-timeline/scenes/${productionSceneId}/sfx`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({tracks})}, '保存音效失败');
    if (data?.audio) { productionData.audio = data.audio; renderProductionAudioTimeline(); }
  } catch (error) { alert(error.message); }
}

function productionAddAudioSfx() {
  const assetId = document.getElementById('audio-sfx-add')?.value;
  const tracks = productionAudioSfxTracks();
  if (!assetId || tracks.length >= 2 || tracks.some(item => item.asset_id === assetId)) return;
  tracks.push({asset_id:assetId,start_ms:0,volume:1});
  productionSaveAudioSfx(tracks);
}

function productionRemoveAudioSfx(index) { productionSaveAudioSfx(productionAudioSfxTracks().filter((_, itemIndex) => itemIndex !== index)); }

const __moduleActions = {productionAudioTimelineHtml,audioDialogueVisualTrack,productionMarkAudioDraft,resetProductionAudioDraft,captureProductionAudioDraft,restoreProductionAudioDraft,renderProductionAudioTimeline,productionSaveAudioLine,productionGenerateAudioLine,productionActivateAudioVersion,productionSaveVoiceProfile,productionUploadAudioLibrary,productionDeleteAudioLibrary,productionAutoSelectAudio,productionPreviewAudioMix,productionExportSubtitles,productionBurnSubtitles,productionSaveNarration,productionSaveAudioBgm,productionAudioSfxTracks,productionSaveAudioSfx,productionAddAudioSfx,productionRemoveAudioSfx};
Object.assign(globalThis.StudioActions, __moduleActions);
Object.defineProperties(globalThis, {productionAudioDraftDirty:{configurable:true,get:() => productionAudioDraftDirty,set:value => {productionAudioDraftDirty = value;}}});
export {__moduleActions};
