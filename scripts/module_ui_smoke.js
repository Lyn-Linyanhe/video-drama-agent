const debugPort = Number(process.argv[2] || 9223);
const pageUrl = process.argv[3] || 'http://127.0.0.1:18888/';

async function connect() {
  const targets = await fetch(`http://127.0.0.1:${debugPort}/json/list`).then(response => response.json());
  const target = targets.find(item => item.type === 'page') || targets[0];
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, {once: true});
    socket.addEventListener('error', reject, {once: true});
  });
  let id = 1;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const item = pending.get(message.id);
      pending.delete(message.id);
      message.error ? item.reject(new Error(message.error.message)) : item.resolve(message.result);
    }
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const requestId = id++;
    pending.set(requestId, {resolve, reject});
    socket.send(JSON.stringify({id: requestId, method, params}));
  });
  return {socket, send};
}

async function main() {
  const cdp = await connect();
  await cdp.send('Runtime.enable');
  await cdp.send('Page.enable');
  await cdp.send('Network.enable');
  await cdp.send('Network.setCacheDisabled', {cacheDisabled: true});
  await cdp.send('Network.clearBrowserCache');
  await cdp.send('Page.navigate', {url: pageUrl});
  await new Promise(resolve => setTimeout(resolve, 1200));
  const expression = `(async () => {
    let importError = '';
    try { await import('/static/js/bootstrap.js?smoke=' + Date.now()); }
    catch (error) { importError = error.stack || error.message || String(error); }
    await new Promise(resolve => setTimeout(resolve, 300));
    return {
      importError,
      readyState: document.readyState,
      actions: Object.keys(globalThis.StudioActions || {}),
      defaultAppState: typeof globalThis.defaultAppState,
      actionDefaultAppState: typeof globalThis.StudioActions?.defaultAppState,
      appState: typeof globalThis.appState,
      loadProjects: typeof globalThis.StudioActions?.loadProjects,
      initializeStudio: typeof globalThis.StudioActions?.initializeStudio,
      projectCards: document.querySelectorAll('#project-list .card').length,
      resources: performance.getEntriesByType('resource').map(item => item.name).filter(name => name.includes('/static/js/')),
    };
  })()`;
  const response = await cdp.send('Runtime.evaluate', {
    expression, awaitPromise: true, returnByValue: true,
  });
  if (response.exceptionDetails) throw new Error(response.exceptionDetails.text);
  const result = response.result.value;
  process.stdout.write(JSON.stringify(result, null, 2));
  cdp.socket.close();
  if (result.importError || result.defaultAppState !== 'undefined'
      || result.actionDefaultAppState !== 'function' || result.appState !== 'object'
      || result.loadProjects !== 'function' || result.initializeStudio !== 'function') process.exit(1);
}

main().catch(error => {
  console.error(error.stack || error.message);
  process.exit(1);
});
