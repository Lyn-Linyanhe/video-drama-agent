"use strict";
import {formatApiHealth} from './core/api-health.js';

globalThis.StudioActions = globalThis.StudioActions || {};
if (document.readyState === 'loading') {
  await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once: true}));
}

async function checkApiHealth() {
  const status = document.getElementById('api-status');
  const provider = document.getElementById('api-provider');
  try {
    const response = await fetch(`${window.location.origin}/api/health`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const health = formatApiHealth(await response.json());
    if (status) status.innerHTML = `<span class="status-dot ${health.statusClass}"></span>${health.statusLabel}`;
    if (provider) provider.textContent = health.providerLabel;
  } catch (error) {
    const health = formatApiHealth({status: 'error'});
    if (status) status.innerHTML = `<span class="status-dot ${health.statusClass}"></span>${health.statusLabel}`;
    if (provider) provider.textContent = health.providerLabel;
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
void checkApiHealth();

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

globalThis.StudioActions.initializeStudio();
globalThis.StudioActions.installMediaStatusHandling();
void globalThis.StudioActions.loadProjects?.();
globalThis.dispatchEvent(new CustomEvent('studio:ready'));
