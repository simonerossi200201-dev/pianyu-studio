window.NodeBar = {
  name: 'NodeBar',
  props: ['node', 'lamps', 'tips', 'subs'],
  emits: ['go'],
  setup() {
    const nodes = [
      { id: 'tasks', k: 'node.tasks', sub: 'node.tasks.sub' },
      { id: 'gemini', k: 'node.gemini', sub: 'node.gemini.sub' },
      { id: 'track', k: 'node.track', sub: 'node.track.sub' },
      { id: 'generate', k: 'node.generate', sub: 'node.generate.sub' },
      { id: 'review', k: 'node.review', sub: 'node.review.sub' },
    ];
    const standalone = { id: 'imgstudio', k: 'node.imgstudio', sub: 'node.imgstudio.sub' };
    return { nodes, standalone, t };
  },
  template: `
  <nav class="nodebar">
    <template v-for="(n,i) in nodes" :key="n.id">
      <div v-if="i>0" class="link" :class="{done: lamps[nodes[i-1].id]==='done'}"></div>
      <button class="node" :class="{active: node===n.id}" @click="$emit('go', n.id)"
              :title="(tips && tips[n.id]) || t(n.sub)">
        <span class="idx">{{ i+1 }}</span>
        <span class="lamp" :class="lamps[n.id]||'idle'"></span>
        <span class="txt"><b>{{ t(n.k) }}</b><small>{{ (subs && subs[n.id]) || t(n.sub) }}</small></span>
      </button>
    </template>
    <div class="nodebar-sep" title="独立模块（不参与 1–5 视频排片流程）"></div>
    <button class="node standalone" :class="{active: node===standalone.id}" @click="$emit('go', standalone.id)"
            :title="(tips && tips[standalone.id]) || t(standalone.sub)">
      <span class="idx standalone-ico">🎨</span>
      <span class="lamp" :class="lamps[standalone.id]||'idle'"></span>
      <span class="txt"><b>{{ t(standalone.k) }}</b><small>{{ (subs && subs[standalone.id]) || t(standalone.sub) }}</small></span>
    </button>
  </nav>`
};
