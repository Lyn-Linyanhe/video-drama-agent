const fs = require('fs');

const debugPort = Number(process.argv[2] || 9223);
const pageUrl = process.argv[3] || 'http://127.0.0.1:18889/';
const screenshotPath = process.argv[4] || 'asset-ui-smoke.png';

async function connect() {
  const targets = await fetch(`http://127.0.0.1:${debugPort}/json/list`).then(response => response.json());
  const target = targets.find(item => item.type === 'page' && item.url === pageUrl)
    || targets.find(item => item.type === 'page' && item.url === 'about:blank')
    || targets.find(item => item.type === 'page');
  if (!target) throw new Error('No Edge page target is available');
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, {once: true});
    socket.addEventListener('error', reject, {once: true});
  });
  let nextId = 1;
  const pending = new Map();
  const exceptions = [];
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const {resolve, reject} = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result);
    }
    if (message.method === 'Runtime.exceptionThrown') {
      exceptions.push(message.params.exceptionDetails.text || 'Runtime exception');
    }
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, {resolve, reject});
    socket.send(JSON.stringify({id, method, params}));
  });
  return {socket, send, exceptions};
}

async function main() {
  const cdp = await connect();
  await cdp.send('Runtime.enable');
  await cdp.send('Page.enable');
  await cdp.send('Page.navigate', {url: pageUrl});
  await new Promise(resolve => setTimeout(resolve, 1800));
  if (cdp.exceptions.length) throw new Error(cdp.exceptions.join('\n'));
  const evaluate = async expression => {
    const response = await cdp.send('Runtime.evaluate', {returnByValue: true, expression});
    if (response.exceptionDetails) {
      throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
    }
    return response.result.value;
  };

  const result = await evaluate(`(() => {
      appState = {...StudioActions.defaultAppState(), projectId:'old-project', stateJson:'old-state', scriptText:'old-script',
        roles:[{name:'旧角色'}], scenes:[{name:'旧场景'}], props:[{name:'旧道具'}], shots:[{}],
        characterImages:[{}], sceneImages:[{}], propImages:[{}], videoAssets:[{}], audioAssets:[{}],
        finalVideoPath:'old.mp4'};
      productionData = {productions:[{}]};
      activeImageEditor = {assetType:'scene', assetId:'old', viewType:'wide'};
      document.getElementById('script-input').value = '旧剧本';
      document.getElementById('works-list').innerHTML = '<video></video>';
      StudioActions.resetProjectSession({projectId:'a1b2c3d4e5f6'});
      return {
        projectId: appState.projectId,
        arraysEmpty: ['roles','scenes','props','shots','characterImages','sceneImages','propImages','videoAssets','audioAssets'].every(key => appState[key].length === 0),
        stateCleared: appState.stateJson === '' && appState.scriptText === '' && appState.finalVideoPath === '',
        domCleared: document.getElementById('script-input').value === '' && !document.getElementById('works-list').querySelector('video'),
        productionCleared: productionData === null && activeImageEditor === null,
      };
    })()`);
  if (result.projectId !== 'a1b2c3d4e5f6' || !result.arraysEmpty || !result.stateCleared || !result.domCleared || !result.productionCleared) {
    throw new Error(`Project reset regression: ${JSON.stringify(result)}`);
  }

  const rendered = await evaluate(`(() => {
      appState.projectId = 'a1b2c3d4e5f6';
      appState.stateJson = '{}';
      appState.roles = [{name:'林夏',gender:'女',description:'测试角色'}];
      appState.scenes = [{sceneId:1,locationId:'loc_preview',name:'地下室',description:'旧铁门',shotIds:[1,3]}];
      appState.props = [{propId:'old_key',name:'旧钥匙',description:'黄铜',shotIds:[1],isHandheld:true}];
      StudioActions.renderRoles(); StudioActions.renderScenes(); StudioActions.renderProps();
      StudioActions.navigateTo('assets'); StudioActions.showAssetTab('scenes');
      return {
        roleCards: document.querySelectorAll('#role-cards .card').length,
        sceneCards: document.querySelectorAll('#scene-cards .card').length,
        propCards: document.querySelectorAll('#prop-cards .card').length,
        sceneText: document.getElementById('scene-cards').innerText,
      };
    })()`);
  if (rendered.roleCards !== 1 || rendered.sceneCards !== 1 || rendered.propCards !== 1
      || !rendered.sceneText.includes('用于分镜 1、3') || !rendered.sceneText.includes('待生成')) {
    throw new Error(`Asset card regression: ${JSON.stringify(rendered)}`);
  }

  const screenshot = await cdp.send('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
  fs.writeFileSync(screenshotPath, Buffer.from(screenshot.data, 'base64'));
  if (cdp.exceptions.length) throw new Error(cdp.exceptions.join('\n'));
  cdp.socket.close();
  process.stdout.write(JSON.stringify({reset: result, rendered, screenshotPath}));
}

main().catch(error => {
  console.error(error.stack || error.message);
  process.exit(1);
});
