const extractShots = (...args) => globalThis.StudioActions.extractShots(...args);
const generateScript = (...args) => globalThis.StudioActions.generateScript(...args);
const syncJobsToAssetState = (...args) => globalThis.StudioActions.syncJobsToAssetState(...args);

// ═══════════════════════════════════════════════════════════════
//  内置 AI 提示词模板（7 套，与后端 src/prompts/templates.py 同步）
// ═══════════════════════════════════════════════════════════════
const PROMPT_TEMPLATES = {}; /*
  generateScript: `你是一名资深的AI短剧编剧...（完整模板见后端 prompts/templates.py）`,
  extractRoles: `你是一名专业的剧本信息提取工程师...（见后端）`,
  extractScenes: `你是一名专业的剧本信息提取工程师...（见后端）`,
  extractShots: `你是一名专业的分镜师...（见后端）`,
  generateRolePrompt: `你是一名专业的AI绘图提示词工程师...（见后端）`,
  generateScenePrompt: `你是一名专业的AI绘图提示词工程师...（见后端）`,
  generateShotPrompt: `你是一名专业的AI绘图提示词工程师...（见后端）`
};

// ═══════════════════════════════════════════════════════════════
//  全局状态
// ═══════════════════════════════════════════════════════════════
// 自动检测：前端与 API 同服务器，直接用当前页面 origin
*/
const API_BASE = window.location.origin;
function defaultAppState() {
  return {
    projectId: '', projectName: '', videoRatio: '9:16', genreTags: [], artStyle: '',
    visualStyleId: 'cinematic_cel', styleModifiers: [], visualStyles: [], audioMode: 'auto',
    scriptInputMode: 'idea', sourceFilename: '', currentScript: null,
    scriptPhase: 'source_required', scriptDraftText: '', scriptDraftInstruction: '', draftJobId: '',
    stateJson: '', scriptText: '', roles: [], scenes: [], props: [], shots: [],
    characterImages: [], sceneImages: [], propImages: [], videoAssets: [], audioAssets: [],
    finalVideoPath: '', assetErrors: {}, assetBusy: {},
  };
}
let appState = defaultAppState();
let projectSessionToken = 0;
const projectRequestControllers = new Set();
let projectJobs = [];
let projectJobPollTimer = null;
let projectJobStateCache = new Map();
let projectJobEventSource = null;
let projectJobEventCursor = 0;
let projectJobReconnectTimer = null;
let projectJobReconnectDelay = 1000;

function isTerminalJob(job) {
  return ['succeeded', 'failed', 'canceled', 'interrupted'].includes(job?.state);
}

function mergeProjectJob(job) {
  if (!job?.job_id) return null;
  const previous = projectJobs.find(item => item.job_id === job.job_id) || null;
  projectJobs = [job, ...projectJobs.filter(item => item.job_id !== job.job_id)];
  projectJobStateCache.set(job.job_id, job.state);
  syncJobsToAssetState();
  return previous;
}

function projectSessionSnapshot() {
  return { token: projectSessionToken, projectId: appState.projectId };
}

function isCurrentProjectSession(snapshot) {
  return snapshot.token === projectSessionToken && snapshot.projectId === appState.projectId;
}

function sessionController() {
  const controller = new AbortController();
  projectRequestControllers.add(controller);
  return controller;
}

function releaseSessionController(controller) {
  projectRequestControllers.delete(controller);
}

function readableApiDetail(detail, fallbackMessage) {
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map(item => {
      if (typeof item === 'string') return item;
      if (item && typeof item === 'object') {
        const location = Array.isArray(item.loc) ? item.loc.filter(part => part !== 'body').join('.') : '';
        const message = item.msg || item.message || item.detail || '';
        return location && message ? `${location}: ${message}` : String(message);
      }
      return '';
    }).filter(Boolean);
    return messages.join('；') || fallbackMessage;
  }
  if (detail && typeof detail === 'object') {
    const message = detail.message || detail.error || detail.detail;
    if (typeof message === 'string' && message.trim()) return message;
    try { return JSON.stringify(detail); } catch { return fallbackMessage; }
  }
  return fallbackMessage;
}

async function projectJsonRequest(url, options = {}, fallbackMessage = '请求失败') {
  const snapshot = projectSessionSnapshot();
  const controller = sessionController();
  try {
    const response = await fetch(url, {...options, signal: controller.signal});
    const contentType = response.headers.get('content-type') || '';
    const body = await response.text();
    let data = null;
    if (body) {
      if (contentType.includes('application/json')) {
        try {
          data = JSON.parse(body);
        } catch {
          throw new Error(`${fallbackMessage}：服务端返回了无效 JSON`);
        }
      } else {
        try { data = JSON.parse(body); } catch { data = { detail: body }; }
      }
    }
    if (!isCurrentProjectSession(snapshot)) return null;
    if (!response.ok) {
      const fallback = `${fallbackMessage}（HTTP ${response.status}）`;
      throw new Error(readableApiDetail(data?.detail, fallback));
    }
    return data;
  } catch (error) {
    if (error.name === 'AbortError' || !isCurrentProjectSession(snapshot)) return null;
    throw error;
  } finally {
    releaseSessionController(controller);
  }
}
const __moduleActions = {
  defaultAppState,
  isTerminalJob,
  mergeProjectJob,
  projectSessionSnapshot,
  isCurrentProjectSession,
  sessionController,
  releaseSessionController,
  readableApiDetail,
  projectJsonRequest,
};
Object.assign(globalThis.StudioActions, __moduleActions);
Object.defineProperties(globalThis, {
  PROMPT_TEMPLATES: { configurable: true, get: () => PROMPT_TEMPLATES },
  API_BASE: { configurable: true, get: () => API_BASE },
  appState: { configurable: true, get: () => appState, set: value => { appState = value; } },
  projectSessionToken: { configurable: true, get: () => projectSessionToken, set: value => { projectSessionToken = value; } },
  projectRequestControllers: { configurable: true, get: () => projectRequestControllers },
  projectJobs: { configurable: true, get: () => projectJobs, set: value => { projectJobs = value; } },
  projectJobPollTimer: { configurable: true, get: () => projectJobPollTimer, set: value => { projectJobPollTimer = value; } },
  projectJobStateCache: { configurable: true, get: () => projectJobStateCache, set: value => { projectJobStateCache = value; } },
  projectJobEventSource: { configurable: true, get: () => projectJobEventSource, set: value => { projectJobEventSource = value; } },
  projectJobEventCursor: { configurable: true, get: () => projectJobEventCursor, set: value => { projectJobEventCursor = value; } },
  projectJobReconnectTimer: { configurable: true, get: () => projectJobReconnectTimer, set: value => { projectJobReconnectTimer = value; } },
  projectJobReconnectDelay: { configurable: true, get: () => projectJobReconnectDelay, set: value => { projectJobReconnectDelay = value; } },
});
export { __moduleActions };
