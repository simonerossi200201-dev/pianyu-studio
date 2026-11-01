// 前端内置 i18n 覆写与默认词条（确保节点 0..5 重排、多账号、Flow模式、提示词总表、外部播放器等词条始终生效）
window.ORANTE_I18N_DEFAULTS = {
  zh: {
    'node.accounts_pool': '多账号',
    'node.accounts_pool.sub': '账号池·分组·轮换',
    'node.tasks': '任务表',
    'node.tasks.sub': '填表·下图·拆段',
    'node.track': '排镜头',
    'node.track.sub': '拆段·图池·排轨',
    'node.track.sub_skip': '可跳过（无文案）',
    'node.gemini': '提示词',
    'node.gemini.sub': '预设库·表格配词',
    'node.gemini.sub_skip': '可跳过（无文案）',
    'node.generate': '生成',
    'node.generate.sub': '批量出片',
    'node.review': '收片',
    'node.review.sub': '视频墙·播放器·重生',
    'node.imgstudio': '制图工坊',
    'node.imgstudio.sub': 'AI生图·入图池',
    'node.tip.accounts_pool_ok': '多账号池与分组轮换管理中心',
    'tasks.col.flow_mode': 'Flow模式',
    'flow.mode.auto': '继承账号',
    'flow.mode.frames': '🎞️ 首尾帧模式 (Frames)',
    'flow.mode.ingredients': '🧩 素材模式 (Ingredients)',
    'theme.dark': '🌙 暗色黑金',
    'theme.light': '☀️ 亮色臻白',
  }
};
