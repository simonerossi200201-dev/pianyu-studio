# 36 · （作废）已拆成三份

本文件已作废，不要执行。内容拆到：

- `specs/36-anchor-bridge-foundation.md` — 探针 + 数据模型 + 尾帧提取器
- `specs/37-anchor-bridge-pipeline.md`   — 流水线调度 + 首尾帧提交 + 模式开关
- `specs/38-anchor-bridge-review.md`     — 尾帧检查台 + 收片墙 + A/B 验收

原因：Flow 首尾帧模式出语音这件事已经确认，不需要再做「探针不过就停」的门槛；
同时把效率设计（流水线、自动放行、自动换锚点）拆成独立可验收的一份。
