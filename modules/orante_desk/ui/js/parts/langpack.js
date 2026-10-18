// 规格 92：按语种改提示词。向导第 5 步和多模态面板共用这一份逻辑。
// 只管调接口和拷贝，界面各画各的。
window.LangPackLogic = (function () {
  async function state() {
    return API.get('/api/lang/state', { silent: true });
  }
  async function exportPack(lang, scope) {
    return API.get('/api/lang/export?lang=' + encodeURIComponent(lang || '')
                   + '&scope=' + encodeURIComponent(scope || 'core'));
  }
  async function apply(text, lang) {
    return API.post('/api/lang/import', { text: text || '', lang: lang || '' });
  }
  async function restore() {
    return API.post('/api/lang/restore', {});
  }
  // 剪贴板在 pywebview 里偶尔没有权限，失败时调用方退回"自己全选复制"
  async function copy(text) {
    if (!text) return false;
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand && document.execCommand('copy');
        document.body.removeChild(ta);
        return !!ok;
      } catch (e2) { return false; }
    }
  }
  return { state, exportPack, apply, restore, copy };
})();
