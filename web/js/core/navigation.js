const closeImageEditor = (...args) => globalThis.StudioActions.closeImageEditor(...args);
const showAssetTab = (...args) => globalThis.StudioActions.showAssetTab(...args);

let saveStateTimer = null;
let activeImageEditor = null;
let apiProgressTimer = null;
let apiProgressStartedAt = 0;
let apiProgressTotal = 0;

function beginApiProgress(label, total = 0) {
  apiProgressTotal = total;
  apiProgressStartedAt = Date.now();
  const panel = document.getElementById('api-progress');
  panel.style.display = 'block';
  document.getElementById('api-progress-label').textContent = label;
  document.getElementById('api-progress-fill').style.width = total ? '0%' : '12%';
  const tick = () => {
    const elapsed = Math.floor((Date.now() - apiProgressStartedAt) / 1000);
    document.getElementById('api-progress-time').textContent = `已等待 ${elapsed} 秒`;
    if (!apiProgressTotal) document.getElementById('api-progress-fill').style.width = `${Math.min(92, 12 + elapsed * 1.4)}%`;
  };
  clearInterval(apiProgressTimer);
  tick();
  apiProgressTimer = setInterval(tick, 1000);
}

function updateApiProgress(completed, total, label) {
  apiProgressTotal = total;
  document.getElementById('api-progress-label').textContent = label;
  document.getElementById('api-progress-fill').style.width = `${Math.max(4, Math.round(completed / total * 100))}%`;
}

function endApiProgress() {
  clearInterval(apiProgressTimer);
  apiProgressTimer = null;
  document.getElementById('api-progress').style.display = 'none';
}

// ═══════════════════════════════════════════════════════════════
//  页面导航
// ═══════════════════════════════════════════════════════════════
function navigateTo(pageName) {
  if (pageName === 'produce') pageName = 'shots';
  if (pageName === 'characters' || pageName === 'scenes') {
    showAssetTab(pageName);
    pageName = 'assets';
  }
  if (pageName !== 'assets') closeImageEditor();
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const target = document.getElementById('page-' + pageName);
  if (target) target.classList.add('active');

  // 侧边栏高亮
  document.querySelectorAll('.sidebar-nav a').forEach(a => a.classList.remove('active'));
  const link = document.querySelector(`[data-page="${pageName}"]`);
  if (link) link.classList.add('active');

  // 步骤导航更新
  updateStepNav(pageName);
}

document.querySelectorAll('.sidebar-nav a').forEach(a => {
  a.addEventListener('click', e => { e.preventDefault(); navigateTo(a.dataset.page); });
});
document.querySelectorAll('.step-item').forEach(s => {
  s.addEventListener('click', e => navigateTo(s.dataset.page));
});

function updateStepNav(current) {
  const steps = ['script','assets','shots','works'];
  const idx = steps.indexOf(current);
  document.querySelectorAll('.step-item').forEach((el, i) => {
    el.classList.remove('active','done');
    if (i < idx) el.classList.add('done');
    if (i === idx) el.classList.add('active');
  });
  document.querySelectorAll('.step-divider').forEach((el, i) => {
    el.classList.toggle('done', i < idx);
  });
}
const __moduleActions = {
  beginApiProgress,
  updateApiProgress,
  endApiProgress,
  navigateTo,
  updateStepNav,
};
Object.assign(globalThis.StudioActions, __moduleActions);
Object.defineProperties(globalThis, {
  saveStateTimer: { configurable: true, get: () => saveStateTimer, set: value => { saveStateTimer = value; } },
  activeImageEditor: { configurable: true, get: () => activeImageEditor, set: value => { activeImageEditor = value; } },
  apiProgressTimer: { configurable: true, get: () => apiProgressTimer, set: value => { apiProgressTimer = value; } },
  apiProgressStartedAt: { configurable: true, get: () => apiProgressStartedAt, set: value => { apiProgressStartedAt = value; } },
  apiProgressTotal: { configurable: true, get: () => apiProgressTotal, set: value => { apiProgressTotal = value; } },
});
export { __moduleActions };
