const PROVIDER_LABELS = {
  seedance: 'Seedance',
  happyhorse: '快乐小马',
  wan: '万相',
  kling: '可灵',
  qwen: '通义千问',
  mock: 'Mock 模式',
};

function formatApiHealth(payload) {
  const isOnline = payload?.status === 'ok';
  const provider = String(payload?.provider || '').trim().toLowerCase();
  const providerLabel = isOnline
    ? (PROVIDER_LABELS[provider] || (provider ? String(payload.provider).trim() : '服务已连接'))
    : '服务不可用';
  return {
    statusClass: isOnline ? 'green' : 'red',
    statusLabel: isOnline ? '在线' : '离线',
    providerLabel,
  };
}

export {formatApiHealth};
