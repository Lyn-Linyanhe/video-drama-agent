const mediaUrl = (...args) => globalThis.StudioActions.mediaUrl(...args);

let missingMediaPaths = new Set();

function mediaPathKey(path) {
  return String(path || '').replace(/\\/g, '/').toLowerCase();
}

function isMissingMediaPath(path) {
  return missingMediaPaths.has(mediaPathKey(path));
}

function markMissingFields(value) {
  if (!value || typeof value !== 'object') return;
  if (Array.isArray(value)) {
    value.forEach(markMissingFields);
    return;
  }
  Object.values(value).forEach(markMissingFields);
  const mediaPath = value.image_path || value.audio_path || value.local_path || value.output_video_path;
  if (mediaPath) value.media_missing = isMissingMediaPath(mediaPath);
}

function markMissingMediaPaths(paths = [], ...roots) {
  missingMediaPaths = new Set((paths || []).map(mediaPathKey).filter(Boolean));
  roots.forEach(markMissingFields);
  if (typeof appState !== 'undefined') {
    markMissingFields(appState);
    appState.finalVideoMissing = isMissingMediaPath(appState.finalVideoPath);
  }
}

function missingMediaNotice(kind) {
  const notice = document.createElement('span');
  notice.className = 'media-missing-notice';
  notice.textContent = `${kind}文件缺失，历史记录已保留`;
  notice.style.cssText = 'display:block;padding:8px;color:#fbbf24;font-size:11px;background:#27272a;border-radius:4px';
  return notice;
}

function handleMissingMedia(event) {
  const element = event.target;
  if (!(element instanceof HTMLImageElement || element instanceof HTMLAudioElement || element instanceof HTMLVideoElement)) return;
  if (element.dataset.mediaMissingHandled) return;
  const source = (element.getAttribute('src') || element.currentSrc || element.src).split('?')[0];
  const knownMissing = [...missingMediaPaths].some(path => mediaUrl(path).split('?')[0] === source);
  if (!knownMissing) return;
  element.dataset.mediaMissingHandled = 'true';
  const kind = element instanceof HTMLImageElement ? '图片' : element instanceof HTMLAudioElement ? '音频' : '视频';
  element.replaceWith(missingMediaNotice(kind));
}

function installMediaStatusHandling() {
  document.addEventListener('error', handleMissingMedia, true);
}

const __moduleActions = {
  isMissingMediaPath,
  markMissingMediaPaths,
  installMediaStatusHandling,
};
Object.assign(globalThis.StudioActions, __moduleActions);
export { __moduleActions };
