// 所有 HTTP 都经这里。返回后端的 {ok,...}；ok=false 时自动 toast，并 throw。
// opts.silent：起动重试不要 toast，否则每 0.3s 弹一次用户也看不见（会被自动关掉）。
window.API = (() => {
  async function call(method, url, data, opts) {
    const opt = { method, headers: {} };
    if (data !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(data); }
    let res;
    try { res = await fetch(url, opt); } catch (e) {
      if (!(opts && opts.silent)) window.toast && toast('连不上 Orante 后端', e.message, 'bad');
      throw e;
    }
    const ct = res.headers.get('content-type') || '';
    const body = ct.includes('json') ? await res.json() : await res.text();
    if (body && body.ok === false) {
      if (!(opts && opts.silent)) window.toast && toast(body.error, body.hint, 'bad');
      const err = new Error(body.error);
      err.body = body;
      err.hint = body.hint || '';
      throw err;
    }
    return body;
  }
  return {
    get: (u, opts) => call('GET', u, undefined, opts),
    post: (u, d, opts) => call('POST', u, d || {}, opts),
    patch: (u, d, opts) => call('PATCH', u, d || {}, opts),
    del: (u, opts) => call('DELETE', u, undefined, opts),
    job: (kind, task_id, args) => call('POST', '/api/jobs', Object.assign({ kind, task_id }, args || {})),
  };
})();
