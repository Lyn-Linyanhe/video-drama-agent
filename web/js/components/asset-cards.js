const escapeHtml = (...args) => globalThis.StudioActions.escapeHtml(...args);
const confirmSceneMaster = (...args) => globalThis.StudioActions.confirmSceneMaster(...args);
const generateAssetCard = (...args) => globalThis.StudioActions.generateAssetCard(...args);
const mediaUrl = (...args) => globalThis.StudioActions.mediaUrl(...args);
const openCharacterImageEditor = (...args) => globalThis.StudioActions.openCharacterImageEditor(...args);
const openSceneImageEditor = (...args) => globalThis.StudioActions.openSceneImageEditor(...args);

function assetPreviewMarkup(image, label, missing = false) {
  if (image?.image_path) {
    return `<img src="${mediaUrl(image.image_path, image.version_id)}" class="asset-card-preview-image" alt="${escapeHtml(label)}" onerror="StudioActions.handleAssetCardImageError(this)">`;
  }
  const title = missing ? '图片文件缺失' : '尚未生成';
  const hint = missing ? '可重新生成当前资产或选择历史版本' : '点击生成按钮创建图片';
  return `<div class="asset-preview-empty${missing ? ' is-missing' : ''}"><strong>${title}</strong><span>${hint}</span></div>`;
}

function handleAssetCardImageError(image) {
  const wrapper = image?.closest?.('.asset-card-preview');
  if (!wrapper) return;
  wrapper.innerHTML = '<div class="asset-preview-empty is-error"><strong>图片加载失败</strong><span>可重新生成当前资产</span></div>';
  const status = wrapper.closest('[data-asset-card]')?.querySelector('[data-asset-status]');
  if (status) {
    status.textContent = '图片加载失败，可重新生成';
    status.style.color = '#ef4444';
  }
}

function renderRoles() {
  const container = document.getElementById('role-cards');
  if (!appState.roles.length) {
    container.innerHTML = '<div style="color:#71717a;padding:40px;text-align:center;grid-column:1/-1">暂无角色</div>';
    return;
  }
  const imgMap = {};
  (appState.characterImages || []).forEach(img => {
    if (!imgMap[img.character_name]) imgMap[img.character_name] = [];
    imgMap[img.character_name].push(img);
  });
  container.innerHTML = appState.roles.map((r, i) => {
    const imgs = imgMap[r.name] || [];
    // The server-side missing list can be stale after a moved/renamed asset.
    // Keep any persisted path renderable and let the image error handler decide.
    const availableImgs = imgs.filter(item => item.image_path);
    const missing = imgs.length > 0 && availableImgs.length === 0;
    const mainImg = availableImgs.find(x => x.view_type === 'front_half') || availableImgs.find(x => x.view_type === 'portrait') || availableImgs[0];
    const encodedId = encodeURIComponent(r.name);
    const error = appState.assetErrors[`character:${r.name}`];
    const busy = appState.assetBusy[`character:${r.name}`];
    const ready = availableImgs.length >= 4;
    const status = busy ? '生成中' : error ? '生成失败' : missing ? '文件缺失，可重新生成' : ready ? '已生成' : imgs.length ? '待补齐' : '待生成';
    return `
    <div class="card" data-asset-card style="position:relative;cursor:pointer" onclick="StudioActions.openCharacterImageEditor(${i})">
      <div class="player-placeholder asset-card-preview" style="max-height:180px;margin-bottom:12px;font-size:11px;overflow:hidden">
        ${assetPreviewMarkup(mainImg, r.name, missing)}
      </div>
      <h4 style="margin-bottom:4px">${escapeHtml(r.name)}</h4>
      <div style="font-size:11px;color:#a1a1aa;display:flex;flex-wrap:wrap;gap:4px">
        <span class="tag">${escapeHtml(r.gender || '未知')}</span>
        <span class="tag">${escapeHtml(r.identity || '角色')}</span>
        <span class="tag">${escapeHtml(r.personality?.slice(0,12) || '')}</span>
      </div>
      <div style="font-size:11px;color:#71717a;margin-top:6px;line-height:1.5">${escapeHtml(r.description?.slice(0,80) || '')}</div>
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:8px"><span data-asset-status style="font-size:10px;color:${error ? '#ef4444' : ready ? '#10b981' : '#f59e0b'}">${status} · ${availableImgs.length}/4</span><button class="btn-secondary btn-sm" onclick="event.stopPropagation();StudioActions.generateAssetCard('character','${encodedId}',${Boolean(mainImg?.image_path)})">${busy ? '终止' : mainImg?.image_path ? '重新生成' : '生成'}</button></div>
    </div>`;
  }).join('');
}

function renderScenes() {
  const container = document.getElementById('scene-cards');
  if (!appState.scenes.length) {
    container.innerHTML = '<div style="color:#71717a;padding:40px;text-align:center;grid-column:1/-1">暂无场景</div>';
    return;
  }
  const imgMap = {};
  (appState.sceneImages || []).forEach(img => {
    const baseKey = String(img.location_id || img.scene_id);
    const stateKey = `${baseKey}|${img.state_id || ''}`;
    if (!imgMap[stateKey]) imgMap[stateKey] = [];
    imgMap[stateKey].push(img);
    if (!img.state_id) {
      if (!imgMap[baseKey]) imgMap[baseKey] = [];
      imgMap[baseKey].push(img);
    }
  });
  container.innerHTML = appState.scenes.map((s, i) => {
    const sceneId = s.locationId || Number(s.sceneId) || i + 1;
    const stateId = s.stateId || '';
    const imgs = imgMap[`${sceneId}|${stateId}`] || (!stateId ? (imgMap[String(sceneId)] || []) : []);
    // Do not hide a persisted path solely because an older status snapshot marked it missing.
    const availableImgs = imgs.filter(item => item.image_path);
    const missing = imgs.length > 0 && availableImgs.length === 0;
    const mainImg = availableImgs.find(item => item.view_type === 'scene_center') || availableImgs[0];
    const pendingMaster = availableImgs.find(item => item.view_type === 'scene_center' && item.is_master && item.needs_confirmation);
    const confirmedMaster = availableImgs.find(item => item.view_type === 'scene_center' && item.is_master && !item.needs_confirmation);
    const encodedId = encodeURIComponent(String(sceneId));
    const error = appState.assetErrors[`scene:${sceneId}:${stateId}`] || appState.assetErrors[`scene:${sceneId}`];
    const busy = appState.assetBusy[`scene:${sceneId}:${stateId}`] || appState.assetBusy[`scene:${sceneId}`];
    const derivedCount = Math.max(0, ...imgs.map(item => Number(item.group_view_count || 0)));
    const groupCount = pendingMaster ? 1 : confirmedMaster ? Math.max(1, 1 + derivedCount) : (imgs.some(item => String(item.view_type).startsWith('scene_')) ? 1 : 2);
    const ready = !pendingMaster && availableImgs.length >= groupCount;
    const stale = imgs.some(item => item.is_stale);
    const status = busy ? '生成中' : error ? '生成失败' : missing ? '文件缺失，可重新生成' : stale ? '设定已更新，可重新生成' : pendingMaster ? '待确认母版' : ready ? '已生成' : confirmedMaster ? '待生成所需视图' : imgs.length ? '待补齐' : '待生成';
    const action = pendingMaster
      ? `<button class="btn-secondary btn-sm" onclick="event.stopPropagation();StudioActions.confirmSceneMaster('${encodedId}','${escapeHtml(pendingMaster.version_id || '')}','${escapeHtml(pendingMaster.state_id || '')}')">确认母版</button>`
      : `<button class="btn-secondary btn-sm" onclick="event.stopPropagation();StudioActions.generateAssetCard('scene','${encodedId}',${Boolean(mainImg?.image_path)},'${escapeHtml(stateId)}')">${busy ? '终止' : mainImg?.image_path ? '重新生成' : '生成'}</button>`;
    return `
    <div class="card" data-asset-card style="cursor:pointer" onclick="StudioActions.openSceneImageEditor(${i})">
      <div class="player-placeholder asset-card-preview" style="max-height:160px;margin-bottom:12px;font-size:11px;overflow:hidden">
        ${assetPreviewMarkup(mainImg, s.name, missing)}
      </div>
      <h4 style="margin-bottom:4px">${escapeHtml(s.name)}</h4>
      <div style="font-size:11px;color:#a1a1aa">${escapeHtml([s.time, s.weather, s.light].filter(Boolean).join(' / ') || s.stateLabel || '')}</div>
      <div style="font-size:11px;color:#71717a;margin-top:6px;line-height:1.5">${escapeHtml((s.description || '').slice(0,60))}</div>
      <div style="font-size:10px;color:#71717a;margin-top:5px">用于分镜 ${escapeHtml((s.shotIds || []).join('、') || '未绑定')}</div>
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:8px"><span data-asset-status style="font-size:10px;color:${error ? '#ef4444' : ready ? '#10b981' : '#f59e0b'}">${status} · ${availableImgs.length}/${groupCount}</span>${action}</div>
    </div>`;
  }).join('');
}

const __moduleActions = {
  assetPreviewMarkup,
  handleAssetCardImageError,
  renderRoles,
  renderScenes,
};
Object.assign(globalThis.StudioActions, __moduleActions);
export { __moduleActions };
