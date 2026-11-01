window.NodeBar = {
  name: 'NodeBar',
  props: ['node', 'lamps', 'tips', 'subs'],
  emits: ['go'],
  setup() {
    const nodes = [
      { id: 'accounts_pool', idx: 0, k: 'node.accounts_pool', sub: 'node.accounts_pool.sub', defLabel: '多账号', defSub: '账号池·分组·轮换' },
      { id: 'tasks', idx: 1, k: 'node.tasks', sub: 'node.tasks.sub', defLabel: '任务表', defSub: '填表·下图·拆段' },
      { id: 'track', idx: 2, k: 'node.track', sub: 'node.track.sub', defLabel: '排镜头', defSub: '拆段·图池·排轨' },
      { id: 'gemini', idx: 3, k: 'node.gemini', sub: 'node.gemini.sub', defLabel: '提示词', defSub: '预设库·表格配词' },
      { id: 'generate', idx: 4, k: 'node.generate', sub: 'node.generate.sub', defLabel: '生成', defSub: '批量出片' },
      { id: 'review', idx: 5, k: 'node.review', sub: 'node.review.sub', defLabel: '收片', defSub: '视频墙·播放器·重生' },
    ];
    const standalone = { id: 'imgstudio', k: 'node.imgstudio', sub: 'node.imgstudio.sub', defLabel: '制图工坊', defSub: 'AI生图·入图池' };
    function labelOf(n) {
      const v = t(n.k);
      return (!v || v === n.k) ? n.defLabel : v;
    }
    function subOf(n, subs) {
      if (subs && subs[n.id]) return subs[n.id];
      const v = t(n.sub);
      return (!v || v === n.sub) ? n.defSub : v;
    }
    return { nodes, standalone, t, labelOf, subOf };
  },
  template: `
  <nav class="nodebar">
    <template v-for="(n,i) in nodes" :key="n.id">
      <div v-if="i>0" class="link" :class="{done: lamps[nodes[i-1].id]==='done'}"></div>
      <button class="node" :class="{active: node===n.id}" @click="$emit('go', n.id)"
              :title="(tips && tips[n.id]) || subOf(n, subs) + ' (Alt+' + n.idx + ')'">
        <span class="idx">{{ n.idx }}</span>
        <span class="lamp" :class="lamps[n.id]||'idle'"></span>
        <span class="txt"><b>{{ labelOf(n) }}</b><small>{{ subOf(n, subs) }}</small></span>
      </button>
    </template>
    <div class="nodebar-sep" title="独立模块（不参与 0–5 视频排片流程）"></div>
    <button class="node standalone" :class="{active: node===standalone.id}" @click="$emit('go', standalone.id)"
            :title="(tips && tips[standalone.id]) || subOf(standalone, subs)">
      <span class="idx standalone-ico">🎨</span>
      <span class="lamp" :class="lamps[standalone.id]||'idle'"></span>
      <span class="txt"><b>{{ labelOf(standalone) }}</b><small>{{ subOf(standalone, subs) }}</small></span>
    </button>
  </nav>`
};
