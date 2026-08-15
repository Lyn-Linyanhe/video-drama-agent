const escapeHtml = (...args) => globalThis.StudioActions.escapeHtml(...args);
const getStateObject = (...args) => globalThis.StudioActions.getStateObject(...args);
const isMissingMediaPath = (...args) => globalThis.StudioActions.isMissingMediaPath(...args);

function pathToUrl(localPath) {
  if (!localPath) return '';
  // 统一分隔符
  const p = localPath.replace(/\\/g, '/');
  const idx = p.toLowerCase().indexOf('/output/');
  if (idx >= 0) {
    const suffix = p.slice(idx + '/output/'.length);
    return `/output/${repairUtf8Mojibake(suffix)}`;
  }
  // 如果不在 output 下，尝试只取文件名
  return '/output/' + p.split('/').pop();
}

function repairUtf8Mojibake(value) {
  // Older snapshots can contain UTF-8 bytes decoded as Latin-1.  Browser
  // requests should use the real project directory name when that happens.
  if (!value || /[\u3400-\u9fff]/.test(value) || typeof TextDecoder === 'undefined') return value;
  try {
    const bytes = new Uint8Array(Array.from(value, char => char.charCodeAt(0) & 0xff));
    const repaired = new TextDecoder('utf-8', {fatal: true}).decode(bytes);
    return /[\u3400-\u9fff]/.test(repaired) ? repaired : value;
  } catch {
    return value;
  }
}

function mediaUrl(path, versionId = '') {
  if (!path) return '';
  const url = /^https?:\/\//i.test(path) ? path : pathToUrl(path);
  if (!versionId) return url;
  return `${url}${url.includes('?') ? '&' : '?'}v=${encodeURIComponent(versionId)}`;
}

function scannedCharacterImages(groups) {
  return Object.entries(groups || {}).flatMap(([characterName, views]) =>
    Object.entries(views || {}).map(([viewType, imagePath]) => ({
      character_name: characterName,
      view_type: viewType,
      image_path: imagePath,
    }))
  );
}

function scannedSceneImages(groups) {
  return Object.entries(groups || {}).flatMap(([sceneName, views]) => {
    const match = sceneName.match(/^scene_(\d+)$/i);
    const sceneId = match ? Number(match[1]) : 0;
    return Object.entries(views || {}).map(([viewType, imagePath]) => ({
      scene_id: sceneId,
      location_id: sceneName.startsWith('loc_') ? sceneName : '',
      view_type: viewType,
      image_path: imagePath,
    }));
  });
}

function scannedPropImages(groups) {
  return Object.entries(groups || {}).flatMap(([propId, views]) =>
    Object.entries(views || {}).map(([viewType, imagePath]) => ({ prop_id: propId, view_type: viewType, image_path: imagePath }))
  );
}

function mergeAssets(primary, fallback, key) {
  const seen = new Set((primary || []).map(item => item[key]).filter(Boolean));
  return [...(primary || []), ...(fallback || []).filter(item => !seen.has(item[key]))];
}

function mergeImageAssets(scanned, saved, identity) {
  const savedByKey = new Map((saved || []).map(item => [identity(item), item]));
  const withCurrentAvailability = item => {
    const merged = { ...item };
    if (merged.image_path) merged.media_missing = isMissingMediaPath(merged.image_path);
    return merged;
  };
  const merged = (scanned || []).map(item => withCurrentAvailability({ ...savedByKey.get(identity(item)), ...item }));
  const scannedKeys = new Set((scanned || []).map(identity));
  return [...merged, ...(saved || []).filter(item => !scannedKeys.has(identity(item))).map(withCurrentAvailability)];
}

function characterAssetKey(name) {
  const chinese = String(name || '').match(/[\u3400-\u9fff]+/g);
  return chinese ? chinese.join('') : String(name || '').replace(/\s*\([^)]*\)\s*/g, '').trim().toLowerCase();
}

function renderRecoveredMedia() {
  const state = getStateObject();
  const videoAssets = appState.videoAssets || [];
  const durationList = document.getElementById('shot-duration-list');
  if (durationList && !videoAssets.length) durationList.innerHTML = '暂无分镜数据';
  if (durationList && videoAssets.length && !appState.shots.length) {
    durationList.innerHTML = videoAssets.map((asset, index) =>
      `<div style="padding:2px 0">镜头${index + 1}: ${asset.duration_sec || '?'}s</div>`
    ).join('');
  }

  const videoList = document.getElementById('shot-video-list');
  if (videoList) {
    const playableAssets = videoAssets.filter(asset => {
      const source = asset.local_path || asset.path || asset.video_url;
      return source && !isMissingMediaPath(source);
    });
    videoList.innerHTML = playableAssets.map((asset, index) => {
      const source = asset.local_path || asset.path || asset.video_url;
      return `<div style="font-size:11px;color:#a1a1aa">镜头 ${asset.scene_id || index + 1}<video controls preload="metadata" style="display:block;width:100%;margin-top:4px;border-radius:6px" src="${mediaUrl(source)}"></video></div>`;
    }).join('');
  }

  const finalPath = appState.finalVideoPath || state?.output_video_path;
  const finalAvailable = finalPath && !appState.finalVideoMissing && !isMissingMediaPath(finalPath);
  const finalContainer = document.getElementById('final-video-container');
  if (finalAvailable && finalContainer) {
    finalContainer.style.display = 'block';
    finalContainer.innerHTML = `<video controls style="width:100%;max-width:300px;border-radius:8px" src="${mediaUrl(finalPath)}"></video>`;
    document.getElementById('preview-player').style.display = 'none';
  } else if (finalContainer) {
    finalContainer.style.display = 'none';
    finalContainer.innerHTML = '';
    document.getElementById('preview-player').style.display = '';
  }

  const works = document.getElementById('works-list');
  if (works && finalAvailable) {
      works.innerHTML = `<div class="card" style="grid-column:span 2"><h4 style="margin-bottom:10px">${escapeHtml(appState.projectName || '已完成短剧')}</h4><video controls style="width:100%;max-height:420px;border-radius:8px" src="${mediaUrl(finalPath)}"></video></div>`;
  } else if (works) {
    works.innerHTML = '<div style="color:#71717a;padding:40px;text-align:center;grid-column:1/-1">📺 暂无作品<br><span style="font-size:12px">完成全部制作流程后，合成的短剧将在这里展示</span></div>';
  }
}

function restoreProjectMedia(state, scannedAssets) {
  const stateCharacters = state?.character_images?.characters || [];
  const stateScenes = state?.scene_images?.scenes || [];
  const stateProps = state?.prop_images?.props || [];
  const scannedCharacters = scannedCharacterImages(scannedAssets?.characters);
  const scannedScenes = scannedSceneImages(scannedAssets?.scenes);
  const scannedProps = scannedPropImages(scannedAssets?.props);

  appState.characterImages = mergeImageAssets(
    scannedCharacters,
    stateCharacters,
    item => `${characterAssetKey(item.character_name)}:${item.view_type}`,
  );
  appState.sceneImages = mergeImageAssets(
    scannedScenes,
    stateScenes,
    item => `${item.location_id || item.scene_id}:${item.view_type}`,
  );
  appState.propImages = mergeImageAssets(scannedProps, stateProps, item => `${item.prop_id}:${item.view_type}`);
  appState.videoAssets = state?.generated_assets?.shots || scannedAssets?.videos || [];
  appState.audioAssets = state?.audio_assets?.scenes || scannedAssets?.audio || [];
  appState.finalVideoPath = state?.output_video_path || scannedAssets?.final?.[0]?.path || '';
}
const __moduleActions = {
  pathToUrl,
  repairUtf8Mojibake,
  mediaUrl,
  scannedCharacterImages,
  scannedSceneImages,
  scannedPropImages,
  mergeAssets,
  mergeImageAssets,
  characterAssetKey,
  renderRecoveredMedia,
  restoreProjectMedia,
};
Object.assign(globalThis.StudioActions, __moduleActions);
export { __moduleActions };
