# 一键跑队列 —— 你只需要点一个文件

（2026-09-19 重写：编号已从 T 改成 Q，运行器换成带预防针的 v2）

## 平时点这个
```
D:\0_Dev\CUTORA\RUN_GROK_QUEUE2.bat
```
双击就跑，不用改任何东西。队列在 `tools\grok-queue.txt`，跑完一条删一条、
追加到 `tools\grok-done.txt`，一直跑到队列空。

## 如果旧的 RUN_GROK_QUEUE.bat 还在跑
**不要关它那个窗口**（关了等于把 Grok 拦腰砍断）。点这个：
```
D:\0_Dev\CUTORA\SWITCH_TO_V2.bat
```
它会静静等旧窗口把当前这条跑完、松开锁，然后自动接手，从队列下一条继续，
往后就是 v2 的行为了。

## v1 和 v2 的差别，就一句话
- v1：某条任务撞红线 → **整个队列停在那里**，你上班回来发现一晚上白过
- v2：某条任务撞红线 → **只隔离这一条**，先验基线，该回滚就回滚，然后继续跑下一条

细节写在 `docs\plan\QUEUE_SAFETY_NET.md`。

## 上班时看进度
手机上看这一个文件就够：
```
D:\0_Dev\CUTORA\docs\plan\queue\_progress.txt
```
每条会有 `START Qnn` / `END Qnn (exit 0, ok=True stop=False)` 两行带时间戳。

想看细节：
- `docs\plan\queue\_done\Qnn.ok` —— Grok 自己写的结论（HEAD + 测试名 + 证据）
- `docs\plan\queue\_log\Qnn.log` —— 完整输出
- `docs\plan\queue\_BLOCKED.md` —— 今晚有没有任务被隔离，一行一条
- `tools\grok-done.txt` —— 已完成的清单

## 三条保险
- **基线红线**：golden 十二例或 UI 六例任何一例哈希变了，Grok 必须写 `_done\Qnn.stop`，
  **绝不允许自己重录基线** —— 这条对 Grok、对运行器、对任何自动化都一样。
- **撞线不停摆**：v2 把撞线的那条隔离掉，验基线、必要时回滚（原始提交留在
  `rescue/Qnn-<时间>` 标签上，未提交的改动留在 stash 里），然后继续下一条。
  只有「回滚完基线还是红」才会真停。
- **随时叫停**：在 `docs\plan\queue\_done\` 里新建一个空文件叫 `_HALT`，跑完当前这条就停。

## 被隔离的任务怎么放回去
1. 看 `docs\plan\queue\_blocked\Qnn.report.md`
2. 我把裁决写进 `docs\plan\queue\_blocked\Qnn.ruling.md`
3. 跑 `runtime\venv\Scripts\python.exe tools\queue_requeue.py Qnn`
   —— 它只往队尾追加，正在跑的第一行永远不动
