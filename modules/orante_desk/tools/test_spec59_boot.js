// 规格 59：bootstrap 重试 / 超时错误页 / 点重试再走一轮。不启浏览器。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');

function loadStore(apiGet) {
  const ctx = {
    console,
    setTimeout,
    Date,
    location: { href: 'http://127.0.0.1:9400/', origin: 'http://127.0.0.1:9400' },
    navigator: { userAgent: 'node-test' },
    fetch: async () => { throw new Error('no i18n'); },
  };
  ctx.window = ctx;
  ctx.Vue = { reactive: (o) => o };
  ctx.API = {
    get: apiGet,
    post: async () => ({}),
  };
  ctx.document = {
    createElement() { return {}; },
    body: { appendChild() {}, removeChild() {} },
    activeElement: null,
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(ROOT, 'ui', 'js', 'store.js'), 'utf8'), ctx);
  return ctx;
}

function oranteState() {
  return {
    ok: true, version: '2.0.0-dev', accounts: [{ id: 'A1' }], tasks: [],
    settings: {}, jobs: [], root: '/x', flow_models: [],
  };
}

function fail(name, extra) {
  console.error('FAIL ' + name + (extra ? ' ' + extra : ''));
  process.exit(1);
}

async function testRetryThenOk() {
  let n = 0;
  const hist = [];
  const ctx = loadStore(async (url) => {
    if (String(url).indexOf('/api/state') >= 0) {
      n += 1;
      hist.push(ctx.store.boot.status + ':' + ctx.store.boot.attempt);
      if (n < 4) throw new Error('ECONNREFUSED');
      return oranteState();
    }
    if (String(url).indexOf('/api/queue') >= 0) return { on: false };
    throw new Error('unexpected ' + url);
  });
  const ok = await ctx.window.bootstrap({
    sleep: async () => {},
    timeoutMs: 20000,
  });
  if (!ok) fail('bootstrap should succeed');
  if (!ctx.store.loaded) fail('store.loaded');
  if (ctx.store.boot.attempt !== 4) fail('attempt', String(ctx.store.boot.attempt));
  if (ctx.store.boot.status !== 'ok') fail('status', ctx.store.boot.status);
  if (!hist.some(h => h.indexOf('connecting') === 0)) fail('never connecting', JSON.stringify(hist));
  if (ctx.store.boot.status === 'connecting') fail('still connecting');
  console.log('  ok  bootstrap 3 fail + 1 ok, attempt=4, connecting then ok');
}

async function testTimeoutThenRetry() {
  let t = 0;
  const ctx = loadStore(async (url) => {
    if (String(url).indexOf('/api/state') >= 0) throw new Error('down');
    return {};
  });
  const ok = await ctx.window.bootstrap({
    now: () => t,
    sleep: async (ms) => { t += ms; },
    timeoutMs: 20000,
  });
  if (ok) fail('timeout should fail');
  if (ctx.store.boot.status !== 'error') fail('status after timeout', ctx.store.boot.status);
  if (ctx.store.loaded) fail('loaded should stay false');
  if (t < 20000) fail('did not wait 20s of fake time', String(t));
  console.log('  ok  20s timeout → error, loaded=false');

  // 点「重试」再走一轮：第 1 次仍失败、第 2 次成功
  let n = 0;
  t = 0;
  ctx.window.API.get = async (url) => {
    if (String(url).indexOf('/api/state') >= 0) {
      n += 1;
      if (n < 2) throw new Error('down');
      return oranteState();
    }
    return { on: false };
  };
  const ok2 = await ctx.window.bootstrap({
    now: () => t,
    sleep: async (ms) => { t += ms; },
    timeoutMs: 20000,
  });
  if (!ok2) fail('retry should succeed');
  if (!ctx.store.loaded) fail('retry loaded');
  if (ctx.store.boot.status !== 'ok') fail('retry status');
  if (n !== 2) fail('retry attempts', String(n));
  console.log('  ok  点重试重新走一轮后成功');
}

(async () => {
  await testRetryThenOk();
  await testTimeoutThenRetry();
  console.log('OK boot js');
})().catch(e => {
  console.error(e);
  process.exit(1);
});
