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
    return { nodes, t };
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
  </nav>`
};
