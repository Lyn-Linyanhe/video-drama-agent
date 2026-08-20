const escapeHtml = (...args) => globalThis.StudioActions.escapeHtml(...args);

const PROVIDER_MODELS = {
  llm: {
    anthropic: ['claude-sonnet-4-6','claude-opus-4-8','claude-haiku-4-5-20251001'],
    openai: ['gpt-4o','gpt-4o-mini','gpt-4-turbo'],
    deepseek: ['deepseek-v4-flash','deepseek-v4-pro'],
    kimi: ['kimi-k2.6','kimi-k2.5','kimi-k2','moonshot-v1'],
    qwen: ['qwen-plus','qwen-turbo','qwen-max'],
  },
};

const SETTINGS_DEFAULTS = {
  profile_name: '默认配置', http_proxy: '',
  llm_provider: 'deepseek', llm_model: 'deepseek-v4-flash', llm_key: '', llm_base_url: '',
  llm_timeout: '1800', llm_stream: 'true', llm_temperature: '0.25', llm_top_p: '0.9',
  kimi_prompt_review_enabled: true, kimi_prompt_review_api_key: '', kimi_prompt_review_model: 'kimi-k2.6', kimi_prompt_review_base_url: 'https://api.moonshot.cn/v1',
  review_llm_enabled: true, review_llm_provider: '', review_llm_model: 'qwen-vl-max', review_llm_api_key: '', review_llm_base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  tts_provider: 'edge', tts_key: '', tts_base_url: '', tts_model: '',
  minimax_api_key: '', minimax_model: 'MiniMax/speech-2.8-turbo', minimax_timeout: '45', minimax_default_male_voice: '', minimax_default_female_voice: '',
  dashscope_tts_provider: 'qwen3', qwen_tts_model: 'qwen3-tts-flash', qwen_timeout: '45', qwen_default_male_voice: 'Ethan', qwen_default_female_voice: 'Cherry', edge_tts_fallback: true,
  tts_voice: 'zh-CN-XiaoxiaoNeural', tts_speed: '1.0', tts_timeout: '30',
  bgm_provider: 'mock', bgm_key: '', bgm_base_url: '', bgm_duration: '60', bgm_timeout: '120',
  default_video_api: 'mock', video_base_url: '', kling_api_key: '', kling_access_key: '',
  kling_secret_key: '', kling_model: 'kling-v2-6', kling_mode: 'std',
  happyhorse_api_key: '', happyhorse_base_url: 'https://api.apiyi.com',
  happyhorse_model: 'happyhorse-1.1-i2v', happyhorse_resolution: '1080p',
  ark_api_key: '', ark_base_url: 'https://ark.cn-beijing.volces.com',
  ark_model: 'doubao-seedance-2-0-fast-260128', ark_resolution: '1080p',
  image_gen_api: 'mock', image_gen_key: '', kling_image_model: 'kling-v1',
};

let settingsProfiles = {};
let activeSettingsProfile = '';
let currentSettingsProfile = {...SETTINGS_DEFAULTS};
let settingsSecretConfigured = {};
const clearedSecretFields = new Set();

async function loadVisualReviewCapabilities() {
  const target = document.getElementById('cfg-visual-review-capability');
  if (!target) return;
  try {
    const data = await settingsJson(await fetch(`${API_BASE}/api/settings/visual-review-capabilities`));
    const provider = data.provider || '未配置';
    const model = data.model || '未配置';
    const endpoint = data.endpoint_host ? ` · ${data.endpoint_host}` : '';
    target.textContent = data.configured
      ? `${provider} / ${model}${endpoint}${data.legacy_inferred ? ' · 继承百炼视频凭证' : ''}`
      : (data.status === 'disabled' ? '已关闭' : `${provider} / ${model} · 未配置 API Key`);
  } catch (error) {
    target.textContent = `无法读取：${error.message}`;
  }
}

async function testVisualReview() {
  const statusEl = document.getElementById('cfg-visual-review-test');
  if (!statusEl) return;
  statusEl.className = 'settings-test-status testing';
  statusEl.textContent = '正在检查...';
  try {
    const result = await settingsJson(await fetch(`${API_BASE}/api/settings/test-visual-review`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        provider: document.getElementById('cfg-review-provider')?.value || '',
        model: document.getElementById('cfg-review-model')?.value || '',
        key: document.getElementById('cfg-review-key')?.value || '',
        base_url: document.getElementById('cfg-review-base')?.value || '',
      }),
    }));
    statusEl.className = `settings-test-status ${result.success ? 'success' : 'error'}`;
    statusEl.textContent = result.detail || (result.success ? '视觉模型连接正常' : '视觉模型连接失败');
    loadVisualReviewCapabilities();
  } catch (error) {
    statusEl.className = 'settings-test-status error';
    statusEl.textContent = error.message || '视觉模型连接失败';
  }
}

function showSettingsSection(section) {
  document.querySelectorAll('[data-settings-panel]').forEach(panel => panel.classList.toggle('active', panel.dataset.settingsPanel === section));
  document.querySelectorAll('[data-settings-section]').forEach(button => button.classList.toggle('active', button.dataset.settingsSection === section));
}

function onProviderChange(type) {
  if (type === 'llm') {
    const p = document.getElementById('cfg-llm-provider').value;
    const sel = document.getElementById('cfg-llm-model');
    const previous = sel.value;
    const models = PROVIDER_MODELS.llm[p] || [];
    sel.innerHTML = models.map(m => `<option value="${m}">${m}</option>`).join('');
    if (models.includes(previous)) sel.value = previous;
    const defaultBase = p === 'deepseek' ? 'https://api.deepseek.com/v1' : p === 'kimi' ? 'https://api.moonshot.cn/v1' : p === 'qwen' ? 'https://dashscope.aliyuncs.com/compatible-mode/v1' : '留空使用服务商默认地址';
    document.getElementById('cfg-llm-base').placeholder = defaultBase;
    document.getElementById('cfg-nav-llm').textContent = p === 'deepseek' ? 'DeepSeek' : p === 'kimi' ? 'Kimi' : p === 'qwen' ? '通义千问' : p === 'openai' ? 'OpenAI' : 'Anthropic';
  }
  if (type === 'video') {
    const selector = document.getElementById('cfg-video-api');
    selector.querySelector('option[value="kling"]')?.remove();
    const p = selector.value === 'kling' ? 'mock' : selector.value;
    if (selector.value !== p) selector.value = p;
    document.getElementById('cfg-video-kling-fields').style.display = 'none';
    document.getElementById('cfg-video-happyhorse-fields').style.display = p === 'happyhorse' ? 'block' : 'none';
    document.getElementById('cfg-video-seedance-fields').style.display = p === 'seedance' ? 'block' : 'none';
    const note = document.getElementById('video-mode-note');
    note.classList.toggle('live', p !== 'mock');
    note.textContent = p === 'mock'
      ? '占位模式只生成本地测试视频，不会调用外部 API。'
      : p === 'seedance'
        ? 'Seedance 使用火山方舟 Ark API Key；分镜页也可逐镜头切换模型。'
        : '视频任务可在分镜页选择快乐小马、万相 2.6 或 Seedance；万相仅支持官方 DashScope/百炼连接。';
    document.getElementById('cfg-nav-video').textContent = p === 'mock' ? '占位模式' : p === 'seedance' ? 'Seedance' : '百炼视频';
  }
  if (type === 'image') {
    const p = document.getElementById('cfg-img-api').value;
    const provider = document.getElementById('cfg-img-api');
    if (![...provider.options].some(option => option.value === 'wan')) provider.add(new Option('Wan 2.7 (reuse DashScope credentials)', 'wan'));
    const modelSelect = document.getElementById('cfg-img-model');
    const previousModel = modelSelect.value;
    const models = p === 'wan' ? ['wan2.7-image-pro', 'wan2.7-image'] : p === 'kling' ? ['kling-v1', 'kling-v1-5'] : ['mock'];
    modelSelect.innerHTML = models.map(model => `<option value="${model}">${model}</option>`).join('');
    modelSelect.value = models.includes(previousModel) ? previousModel : models[0];
    document.getElementById('cfg-img-key-field').style.display = p === 'kling' ? 'block' : 'none';
    document.getElementById('cfg-img-model-field').style.display = 'block';
    const note = document.getElementById('img-mode-note');
    note.classList.toggle('live', p === 'kling');
    note.textContent = p === 'kling' ? '真实图片生成已启用，保存后角色、场景和道具统一使用此连接。' : '占位模式用于无成本调试，只生成带标签的本地示意图。';
    document.getElementById('cfg-nav-image').textContent = p === 'kling' ? '可灵' : '占位模式';
  }
  if (type === 'image' && document.getElementById('cfg-img-api').value === 'wan') {
    const baseUrl = document.getElementById('cfg-happy-base').value || '';
    const enabled = baseUrl.toLowerCase().includes('dashscope.aliyuncs.com');
    const note = document.getElementById('img-mode-note');
    note.classList.toggle('live', enabled);
    note.textContent = enabled
      ? 'Wan 2.7 reuses the active DashScope credentials. Projects can select Wan, Kling, or Mock independently.'
      : 'Wan requires the official DashScope endpoint: dashscope.aliyuncs.com.';
  }
  if (type === 'tts') {
    const provider = document.getElementById('cfg-tts-provider').value;
    document.getElementById('cfg-qwen-tts-fields').style.display = provider === 'qwen3' ? 'block' : 'none';
    document.getElementById('cfg-minimax-tts-fields').style.display = provider === 'minimax' ? 'block' : 'none';
  }
}

// ── 密码显示切换 ──
function togglePwd(id, button) {
  const el = document.getElementById(id);
  el.type = el.type === 'password' ? 'text' : 'password';
  if (button) button.textContent = el.type === 'password' ? '显示' : '隐藏';
}

function syncSecretInput(input) {
  const configured = Boolean(settingsSecretConfigured[input.dataset.secretField]);
  const wrapper = input.closest('.settings-secret');
  wrapper.classList.toggle('has-saved-secret', configured);
  wrapper.classList.toggle('marked-clear', clearedSecretFields.has(input.dataset.secretField));
  if (clearedSecretFields.has(input.dataset.secretField)) {
    input.placeholder = '保存后清除已配置密钥';
  } else if (configured) {
    input.placeholder = '已配置，留空保持不变';
  }
  const clearButton = wrapper.querySelector('.clear-secret');
  if (clearButton) clearButton.textContent = clearedSecretFields.has(input.dataset.secretField) ? '撤销清除' : '清除';
}

function clearSavedSecret(inputId) {
  const input = document.getElementById(inputId);
  const field = input.dataset.secretField;
  if (clearedSecretFields.has(field)) clearedSecretFields.delete(field);
  else { clearedSecretFields.add(field); input.value = ''; }
  syncSecretInput(input);
}

// ── 收集所有配置字段 ──
function collectSettings(profileName) {
  const data = {
    ...SETTINGS_DEFAULTS,
    ...currentSettingsProfile,
    profile_name: profileName || document.getElementById('cfg-profile-name').value || '默认配置',
    http_proxy: document.getElementById('cfg-proxy').value,
    llm_provider: document.getElementById('cfg-llm-provider').value,
    llm_model: document.getElementById('cfg-llm-model').value,
    llm_key: document.getElementById('cfg-llm-key').value,
    llm_base_url: document.getElementById('cfg-llm-base').value,
    kimi_prompt_review_enabled: document.getElementById('cfg-kimi-review-enabled').checked,
    kimi_prompt_review_api_key: document.getElementById('cfg-kimi-review-key').value,
    kimi_prompt_review_model: document.getElementById('cfg-kimi-review-model').value,
    kimi_prompt_review_base_url: document.getElementById('cfg-kimi-review-base').value,
    review_llm_enabled: document.getElementById('cfg-review-enabled').checked,
    review_llm_provider: document.getElementById('cfg-review-provider').value,
    review_llm_model: document.getElementById('cfg-review-model').value,
    review_llm_api_key: document.getElementById('cfg-review-key').value,
    review_llm_base_url: document.getElementById('cfg-review-base').value,
    default_video_api: document.getElementById('cfg-video-api').value,
    kling_api_key: document.getElementById('cfg-kling-api-key').value,
    kling_access_key: document.getElementById('cfg-kling-access-key').value,
    kling_secret_key: document.getElementById('cfg-kling-secret-key').value,
    kling_model: document.getElementById('cfg-video-model').value,
    kling_mode: document.getElementById('cfg-video-mode').value,
    happyhorse_api_key: document.getElementById('cfg-happy-key').value,
    happyhorse_base_url: document.getElementById('cfg-happy-base').value,
    happyhorse_model: document.getElementById('cfg-happy-model').value,
    happyhorse_resolution: document.getElementById('cfg-happy-resolution').value,
    ark_api_key: document.getElementById('cfg-ark-key').value,
    ark_base_url: document.getElementById('cfg-ark-base').value,
    ark_model: document.getElementById('cfg-ark-model').value,
    ark_resolution: document.getElementById('cfg-ark-resolution').value,
    image_gen_api: document.getElementById('cfg-img-api').value,
    image_gen_key: document.getElementById('cfg-img-key').value,
    image_gen_model: document.getElementById('cfg-img-model').value,
    kling_image_model: document.getElementById('cfg-img-model').value,
    minimax_model: document.getElementById('cfg-minimax-model').value,
    minimax_timeout: document.getElementById('cfg-minimax-timeout').value,
    minimax_default_male_voice: document.getElementById('cfg-minimax-male').value,
    minimax_default_female_voice: document.getElementById('cfg-minimax-female').value,
    dashscope_tts_provider: document.getElementById('cfg-tts-provider').value,
    qwen_tts_model: document.getElementById('cfg-qwen-model').value,
    qwen_timeout: document.getElementById('cfg-qwen-timeout').value,
    qwen_default_male_voice: document.getElementById('cfg-qwen-male').value,
    qwen_default_female_voice: document.getElementById('cfg-qwen-female').value,
    edge_tts_fallback: document.getElementById('cfg-edge-fallback').checked,
    clear_secret_fields: [...clearedSecretFields],
  };
  delete data._secret_configured;
  return data;
}

function populateSettings(p) {
  const set = (id, val) => { const el = document.getElementById(id); if (el && val !== undefined) el.value = val; };
  currentSettingsProfile = {...SETTINGS_DEFAULTS, ...p};
  settingsSecretConfigured = p._secret_configured || {};
  clearedSecretFields.clear();
  set('cfg-proxy', currentSettingsProfile.http_proxy);
  set('cfg-llm-provider', currentSettingsProfile.llm_provider); onProviderChange('llm');
  const modelSelect = document.getElementById('cfg-llm-model');
  if (currentSettingsProfile.llm_model && ![...modelSelect.options].some(option => option.value === currentSettingsProfile.llm_model)) modelSelect.add(new Option(currentSettingsProfile.llm_model, currentSettingsProfile.llm_model));
  set('cfg-llm-model', currentSettingsProfile.llm_model);
  set('cfg-llm-base', currentSettingsProfile.llm_base_url);
  const kimiReviewEnabled = document.getElementById('cfg-kimi-review-enabled');
  if (kimiReviewEnabled) kimiReviewEnabled.checked = currentSettingsProfile.kimi_prompt_review_enabled !== false;
  const kimiReviewModel = document.getElementById('cfg-kimi-review-model');
  if (currentSettingsProfile.kimi_prompt_review_model && ![...kimiReviewModel.options].some(option => option.value === currentSettingsProfile.kimi_prompt_review_model)) kimiReviewModel.add(new Option(currentSettingsProfile.kimi_prompt_review_model, currentSettingsProfile.kimi_prompt_review_model));
  set('cfg-kimi-review-model', currentSettingsProfile.kimi_prompt_review_model || 'kimi-k2.6');
  set('cfg-kimi-review-base', currentSettingsProfile.kimi_prompt_review_base_url || 'https://api.moonshot.cn/v1');
  set('cfg-review-provider', currentSettingsProfile.review_llm_provider || '');
  set('cfg-review-model', currentSettingsProfile.review_llm_model || 'qwen-vl-max');
  set('cfg-review-base', currentSettingsProfile.review_llm_base_url || 'https://dashscope.aliyuncs.com/compatible-mode/v1');
  const reviewEnabled = document.getElementById('cfg-review-enabled');
  if (reviewEnabled) reviewEnabled.checked = currentSettingsProfile.review_llm_enabled !== false;
  set('cfg-video-api', ['mock','happyhorse','seedance','seedence'].includes(currentSettingsProfile.default_video_api) ? (currentSettingsProfile.default_video_api === 'seedence' ? 'seedance' : currentSettingsProfile.default_video_api) : 'mock');
  set('cfg-video-model', currentSettingsProfile.kling_model);
  set('cfg-video-mode', currentSettingsProfile.kling_mode);
  set('cfg-happy-base', currentSettingsProfile.happyhorse_base_url);
  set('cfg-happy-model', currentSettingsProfile.happyhorse_model);
  set('cfg-happy-resolution', currentSettingsProfile.happyhorse_resolution);
  set('cfg-ark-base', currentSettingsProfile.ark_base_url || 'https://ark.cn-beijing.volces.com');
  const arkModelSelect = document.getElementById('cfg-ark-model');
  if (currentSettingsProfile.ark_model && ![...arkModelSelect.options].some(option => option.value === currentSettingsProfile.ark_model)) arkModelSelect.add(new Option(currentSettingsProfile.ark_model, currentSettingsProfile.ark_model));
  set('cfg-ark-model', currentSettingsProfile.ark_model || 'doubao-seedance-2-0-fast-260128');
  set('cfg-ark-resolution', currentSettingsProfile.ark_resolution || currentSettingsProfile.happyhorse_resolution || '1080p');
  set('cfg-minimax-model', currentSettingsProfile.minimax_model);
  set('cfg-minimax-timeout', currentSettingsProfile.minimax_timeout);
  set('cfg-minimax-male', currentSettingsProfile.minimax_default_male_voice);
  set('cfg-minimax-female', currentSettingsProfile.minimax_default_female_voice);
  set('cfg-tts-provider', currentSettingsProfile.dashscope_tts_provider || 'qwen3');
  set('cfg-qwen-model', currentSettingsProfile.qwen_tts_model || 'qwen3-tts-flash');
  set('cfg-qwen-timeout', currentSettingsProfile.qwen_timeout || '45');
  set('cfg-qwen-male', currentSettingsProfile.qwen_default_male_voice || 'Ethan');
  set('cfg-qwen-female', currentSettingsProfile.qwen_default_female_voice || 'Cherry');
  const edgeFallback = document.getElementById('cfg-edge-fallback');
  if (edgeFallback) edgeFallback.checked = currentSettingsProfile.edge_tts_fallback !== false;
  onProviderChange('tts');
  onProviderChange('video');
  const imageProviderSelect = document.getElementById('cfg-img-api');
  if (![...imageProviderSelect.options].some(option => option.value === 'wan')) imageProviderSelect.add(new Option('Wan 2.7 (reuse DashScope credentials)', 'wan'));
  set('cfg-img-api', ['mock','kling','wan'].includes(currentSettingsProfile.image_gen_api) ? currentSettingsProfile.image_gen_api : 'mock');
  onProviderChange('image');
  set('cfg-img-model', currentSettingsProfile.kling_image_model || (currentSettingsProfile.image_gen_api === 'wan' ? 'wan2.7-image-pro' : 'kling-v1'));
  document.querySelectorAll('[data-secret-field]').forEach(input => { input.value = ''; input.type = 'password'; syncSecretInput(input); });
  document.getElementById('cfg-nav-network').textContent = currentSettingsProfile.http_proxy ? '已配置代理' : '直连';
}

function setSettingsStatus(message, type='') {
  const status = document.getElementById('cfg-status');
  status.textContent = message;
  status.style.color = type === 'error' ? '#fca5a5' : type === 'success' ? '#86efac' : '#a1a1aa';
}

async function settingsJson(res) {
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) throw new Error(data.detail || `请求失败 (HTTP ${res.status})`);
  return data;
}

// ── 保存 / 加载 / 删除 ──
async function saveApiConfig() {
  const data = collectSettings();
  const button = document.getElementById('cfg-save-button');
  if (!data.profile_name.trim()) return setSettingsStatus('请先填写配置名称', 'error');
  button.disabled = true;
  button.textContent = '正在应用...';
  setSettingsStatus('正在保存并热加载配置...');
  try {
    const res = await fetch(`${API_BASE}/api/settings`, {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data),
    });
    const r = await settingsJson(res);
    activeSettingsProfile = r.active_profile;
    populateSettings(r.profile);
    document.getElementById('cfg-profile-name').value = r.active_profile;
    document.getElementById('cfg-active-label').innerHTML = '<span class="status-dot green"></span>' + escapeHtml(r.active_profile) + ' · 已生效';
    setSettingsStatus(r.message, 'success');
    await loadProfileList(r.active_profile, false);
  } catch(e) {
    setSettingsStatus(`保存失败：${e.message}`, 'error');
  } finally {
    button.disabled = false;
    button.textContent = '保存并应用';
  }
}

async function loadApiProfile() {
  const name = document.getElementById('cfg-profile-select').value;
  if (!name) return;
  try {
    const res = await fetch(`${API_BASE}/api/settings/${encodeURIComponent(name)}`);
    const p = await settingsJson(res);
    populateSettings(p);
    document.getElementById('cfg-profile-name').value = name;
    const isActive = name === activeSettingsProfile;
    document.getElementById('cfg-active-label').innerHTML = `<span class="status-dot ${isActive ? 'green' : 'yellow'}"></span>${escapeHtml(name)} · ${isActive ? '已生效' : '待应用'}`;
    setSettingsStatus(isActive ? '已加载当前生效配置' : '配置已加载，点击“保存并应用”后生效', isActive ? 'success' : '');
  } catch(e) {
    setSettingsStatus(`加载失败：${e.message}`, 'error');
  }
}

async function deleteApiProfile() {
  const name = document.getElementById('cfg-profile-select').value;
  if (!name || !confirm('确认删除档案 "' + name + '"？')) return;
  try {
    await settingsJson(await fetch(`${API_BASE}/api/settings/${encodeURIComponent(name)}`, {method:'DELETE'}));
    if (name === activeSettingsProfile) activeSettingsProfile = '';
    populateSettings(SETTINGS_DEFAULTS);
    document.getElementById('cfg-profile-name').value = '';
    document.getElementById('cfg-active-label').innerHTML = '<span class="status-dot gray"></span>未加载配置';
    setSettingsStatus(`已删除配置“${name}”`, 'success');
    await loadProfileList('', false);
  } catch(e) {
    setSettingsStatus(`删除失败：${e.message}`, 'error');
  }
}

async function loadProfileList(preferredName='', loadActive=true) {
  loadVisualReviewCapabilities();
  try {
    const data = await settingsJson(await fetch(`${API_BASE}/api/settings`));
    settingsProfiles = data.profiles || {};
    activeSettingsProfile = data.active_profile || '';
    const sel = document.getElementById('cfg-profile-select');
    const names = Object.keys(settingsProfiles);
    sel.replaceChildren(new Option(names.length ? '选择配置' : '暂无配置', ''));
    names.forEach(name => sel.add(new Option(name, name)));
    const selected = preferredName || (loadActive ? activeSettingsProfile : '');
    if (selected && settingsProfiles[selected]) sel.value = selected;
    if (loadActive && activeSettingsProfile && settingsProfiles[activeSettingsProfile]) {
      populateSettings(settingsProfiles[activeSettingsProfile]);
      document.getElementById('cfg-profile-name').value = activeSettingsProfile;
      document.getElementById('cfg-active-label').innerHTML = '<span class="status-dot green"></span>' + escapeHtml(activeSettingsProfile) + ' · 已生效';
    } else if (!activeSettingsProfile) {
      document.getElementById('cfg-active-label').innerHTML = '<span class="status-dot gray"></span>未加载配置';
    }
  } catch(e) { setSettingsStatus(`配置列表加载失败：${e.message}`, 'error'); }
}

// ── 导入导出 ──
function exportProfile() {
  const name = document.getElementById('cfg-profile-select').value;
  if (!name) return alert('请先选择要导出的档案');
  window.open(`${API_BASE}/api/settings/${encodeURIComponent(name)}/export`, '_blank');
}

function importProfile(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async function(e) {
    try {
      const data = JSON.parse(e.target.result);
      data.profile_name = data.profile_name || file.name.replace('.json','');
      const res = await fetch(`${API_BASE}/api/settings/import`, {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data),
      });
      const r = await settingsJson(res);
      setSettingsStatus(`${r.message}。密钥不会从脱敏导出文件中恢复。`, 'success');
      await loadProfileList(data.profile_name, false);
      document.getElementById('cfg-profile-select').value = data.profile_name;
      await loadApiProfile();
    } catch(ex) {
      setSettingsStatus(`导入失败：${ex.message}`, 'error');
    } finally {
      input.value = '';
    }
  };
  reader.readAsText(file);
}

// ── 测试连接 ──
async function testConnection(type) {
  const statusEl = document.getElementById(`cfg-${type}-test`);
  statusEl.className = 'settings-test-status testing';
  statusEl.textContent = '正在检查...';
  let body = {profile_name: document.getElementById('cfg-profile-name').value};
  if (type === 'llm') {
    body = {...body, provider: document.getElementById('cfg-llm-provider').value, model: document.getElementById('cfg-llm-model').value, key: document.getElementById('cfg-llm-key').value, base_url: document.getElementById('cfg-llm-base').value};
  } else if (type === 'tts') {
    const provider = document.getElementById('cfg-tts-provider').value;
    body = {...body, provider, model: provider === 'minimax' ? document.getElementById('cfg-minimax-model').value : document.getElementById('cfg-qwen-model').value, key: document.getElementById('cfg-happy-key').value, base_url: document.getElementById('cfg-happy-base').value};
  } else if (type === 'image') {
    const provider = document.getElementById('cfg-img-api').value;
    body = {...body, provider, model: document.getElementById('cfg-img-model').value, key: provider === 'wan' ? document.getElementById('cfg-happy-key').value : document.getElementById('cfg-img-key').value, base_url: provider === 'wan' ? document.getElementById('cfg-happy-base').value : ''};
  } else if (type === 'video') {
    const provider = document.getElementById('cfg-video-api').value;
    body = provider === 'seedance'
      ? {...body, provider, key: document.getElementById('cfg-ark-key').value, base_url: document.getElementById('cfg-ark-base').value, model: document.getElementById('cfg-ark-model').value}
      : provider === 'happyhorse'
      ? {...body, provider, key: document.getElementById('cfg-happy-key').value, base_url: document.getElementById('cfg-happy-base').value, model: document.getElementById('cfg-happy-model').value}
      : {...body, provider, key: document.getElementById('cfg-kling-api-key').value || document.getElementById('cfg-kling-access-key').value, secret: document.getElementById('cfg-kling-secret-key').value, model: document.getElementById('cfg-video-model').value};
  } else if (type === 'bgm') {
    body = {...body, provider: 'mock'};
  }
  try {
    const res = await fetch(`${API_BASE}/api/test/${type}`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
    const r = await settingsJson(res);
    statusEl.className = `settings-test-status ${r.success ? 'success' : 'error'}`;
    statusEl.textContent = r.success ? r.detail || '连接正常' : `${r.detail || '连接失败'}${r.status ? ` (HTTP ${r.status})` : ''}`;
  } catch(e) {
    statusEl.className = 'settings-test-status error';
    statusEl.textContent = e.message;
  }
}
const __moduleActions = {
  showSettingsSection,
  onProviderChange,
  togglePwd,
  syncSecretInput,
  clearSavedSecret,
  collectSettings,
  populateSettings,
  setSettingsStatus,
  settingsJson,
  saveApiConfig,
  loadApiProfile,
  deleteApiProfile,
  loadProfileList,
  exportProfile,
  importProfile,
  testConnection,
  loadVisualReviewCapabilities,
  testVisualReview,
};
Object.assign(globalThis.StudioActions, __moduleActions);
Object.defineProperties(globalThis, {
  PROVIDER_MODELS: { configurable: true, get: () => PROVIDER_MODELS },
  SETTINGS_DEFAULTS: { configurable: true, get: () => SETTINGS_DEFAULTS },
  settingsProfiles: { configurable: true, get: () => settingsProfiles, set: value => { settingsProfiles = value; } },
  activeSettingsProfile: { configurable: true, get: () => activeSettingsProfile, set: value => { activeSettingsProfile = value; } },
  currentSettingsProfile: { configurable: true, get: () => currentSettingsProfile, set: value => { currentSettingsProfile = value; } },
  settingsSecretConfigured: { configurable: true, get: () => settingsSecretConfigured, set: value => { settingsSecretConfigured = value; } },
  clearedSecretFields: { configurable: true, get: () => clearedSecretFields },
});
export { __moduleActions };
