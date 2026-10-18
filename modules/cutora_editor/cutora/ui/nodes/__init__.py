# -*- coding: utf-8 -*-
"""Q152-F · 节点编辑器（QGraphicsView）。

items.py  节点卡片 / 端口 / 连线（只认 graph 数据，不认业务）
view.py   画布：网格、滚轮缩放、中键平移、框选
editor.py NodeEditor：绑定当前图层，把手势翻成 fx.graph / fx.node / fx.link 命令（全部可撤销），
          选中节点 → 检查器显示这个节点的参数；查看器（1 / Esc）只影响预览。
"""
