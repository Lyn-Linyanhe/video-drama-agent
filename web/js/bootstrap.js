"use strict";
globalThis.StudioActions = globalThis.StudioActions || {};
if (document.readyState === 'loading') {
  await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once: true}));
}
const moduleVersion = new URL(import.meta.url).search;
await import(`./core/state-api.js${moduleVersion}`);
await import(`./core/media-status.js${moduleVersion}`);
await import(`./components/task-drawer.js${moduleVersion}`);
await import(`./core/job-events.js${moduleVersion}`);
await import(`./core/navigation.js${moduleVersion}`);
await import(`./pages/script.js${moduleVersion}`);
await import(`./pages/assets.js${moduleVersion}`);
await import(`./components/image-editor.js${moduleVersion}`);
await import(`./components/asset-cards.js${moduleVersion}`);
await import(`./pages/shots.js${moduleVersion}`);
await import(`./pages/projects.js${moduleVersion}`);
await import(`./pages/settings.js${moduleVersion}`);
await import(`./pages/production.js${moduleVersion}`);
await import(`./components/audio-timeline.js${moduleVersion}`);
await import(`./pages/production-actions.js${moduleVersion}`);

async function checkApiHealth() {
  const status = document.getElementById('api-status');
  try {
    const response = await fetch(`${window.location.origin}/api/health`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    if (status) status.innerHTML = '<span class="status-dot green"></span>已连接';
  } catch (error) {
    if (status) status.innerHTML = '<span class="status-dot red"></span>连接失败';
    console.error('API 健康检查失败:', error);
    return;
  }
  const results = await Promise.allSettled([
    globalThis.StudioActions.loadProfileList?.(),
    globalThis.StudioActions.loadProjects?.(),
  ]);
  results.filter(result => result.status === 'rejected').forEach(result => console.error('初始化数据加载失败:', result.reason));
}

globalThis.StudioActions.checkApiHealth = checkApiHealth;
globalThis.StudioActions.initializeStudio();
globalThis.StudioActions.installMediaStatusHandling();
void checkApiHealth();
globalThis.dispatchEvent(new CustomEvent('studio:ready'));
