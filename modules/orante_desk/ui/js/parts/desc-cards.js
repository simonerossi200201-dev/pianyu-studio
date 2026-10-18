// 换图描述卡片：设置抽屉和收片换图面板共用的编辑器。
window.descCardPreview = function (prompt, n) {
  const lines = String(prompt || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  return lines.slice(0, n || 2).join('\n');
};

window.DescCardEditor = {
  name: 'DescCardEditor',
  props: {
    card: { type: Object, default: null },   // {id,name,prompt}；没 id = 新建
  },
  emits: ['close', 'saved'],
  setup(props, { emit }) {
    const { reactive, computed } = Vue;
    const form = reactive({
      id: (props.card && props.card.id) || '',
      name: (props.card && props.card.name) || '',
      prompt: (props.card && props.card.prompt) || '',
    });
    const busy = Vue.ref(false);
    const isNew = computed(() => !form.id);
    async function save() {
      if (busy.value) return;
      const name = (form.name || '').trim();
      if (!name) return toast(t('set.cards.need_name'), '', 'bad');
      busy.value = true;
      try {
        let r;
        if (form.id) {
          r = await API.post('/api/desc-cards/' + encodeURIComponent(form.id), {
            name, prompt: form.prompt || '',
          });
        } else {
          r = await API.post('/api/desc-cards', { name, prompt: form.prompt || '' });
        }
        toast(t('toast.saved'), '', 'ok');
        emit('saved', r.card);
        emit('close');
      } catch (e) { /* API 已 toast */ }
      finally { busy.value = false; }
    }
    return { form, busy, isNew, save, t };
  },
  template: `
  <div class="dlg-mask dc-edit-mask" @click.self="$emit('close')">
    <div class="dlg dc-dlg" @click.stop>
      <h3>{{ isNew ? t('set.cards.new') : t('set.cards.edit') }}</h3>
      <div class="field">
        <label>{{ t('set.cards.name') }}</label>
        <input v-model="form.name" :placeholder="t('set.cards.name_ph')">
      </div>
      <div class="field">
        <label>{{ t('set.cards.prompt') }}</label>
        <textarea v-model="form.prompt" rows="8" class="dc-ta"></textarea>
      </div>
      <div class="row" style="justify-content:flex-end;margin-top:12px">
        <button class="btn ghost" @click="$emit('close')">{{ t('common.cancel') }}</button>
        <button class="btn primary" :disabled="busy" @click="save">{{ t('common.save') }}</button>
      </div>
    </div>
  </div>
  `,
};
