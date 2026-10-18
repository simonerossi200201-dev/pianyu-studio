relight —— 室内视频重新打光内核（独立工具，不属于 Robota）
====================================================================
思路借鉴 GR3EN（光源布置 + 深度/法线 + 重新着色 + 时间一致），但不用任何生成式大模型，
主流程完全是确定性的数学打光，所以每帧结果稳定、不闪烁，16GB 显卡轻松跑。

运行环境
  使用 ComfyUI 现有的 venv（只读调用，不往里装任何东西）：
  D:\0_LLM\ComfyUI\venv\Scripts\python.exe
  ffmpeg 固定用 D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe

用到的模型（全部离线、本地）
  深度：Intel DPT-Hybrid (MiDaS 3.0)，Intel（美国），Apache-2.0
        位置 D:\0_LLM\tools\relight\models\dpt-hybrid-midas
  抠人：CUTORA 的 SAM 2.1 base_plus（只读引用，Meta，Apache-2.0）
        不可用时自动退回 OpenCV 人脸检测（OpenCV 5 无 Haar，用肤色块定位）+ GrabCut
  法线：由深度图梯度（限幅防黑边）+ 人物轮廓“体积”法线合成（无额外模型）
  深度在 384x384 推理（DPT-Hybrid 固定尺寸），显存约 0.65 GB；显存不足 3 GB 时自动用 CPU

用法
  set PYTHONUTF8=1
  python relight.py <输入视频> --light left --temp 4500 --strength 0.6
  常用参数：
    --light      主光方向：left / right / top / top-left / top-right / front
    --temp       主光色温 K（1800–12000），--fill-temp 补光色温（默认 6500）
    --strength   打光强度 0–1（0 = 不改）
    --key/--fill/--rim   主光、补光、轮廓光强度（0–3）
    --ambient    环境光（越大阴影越浅，默认 0.25）
    --contrast   明暗反差倍数 0.5–5（默认 2.5，越大立体感越强）
    --relief     人物体积感 0–2（默认 1，用抠像轮廓补足深度模型的扁平）
    --softness   光的软硬 0–1（越大越柔）
    --skin-keep  肤色保护 0–1（越大脸部颜色越不变）
    --window     窗户高光压缩 0–1，--window-thr 窗户判定亮度阈值（线性亮度，默认 0.55）
    --seconds    只处理前 N 秒
    --sam ""     不用 SAM，直接用 OpenCV 方案
    --debug      额外输出一张调试图（法线、深度、遮罩、光照层）
    -o 输出目录（默认 D:\0_LLM\Outputs\relight），--name 输出文件名

输出
  <name>.mp4        打光结果（保留原音频）
  <name>_sbs.mp4    左原片 / 右结果 的对比视频
  <name>_debug.jpg  （加 --debug 时）

安全
  - 不用 shell=True，不用 eval/exec；ffmpeg 以参数列表调用
  - 路径含特殊字符直接拒绝；拒绝覆盖输入文件
  - 运行时强制离线（HF_HUB_OFFLINE=1），不联网
  - 所有数值参数都被钳制在安全范围

IC-Light
  --iclight 目前只检查 IC-Light 是否装好，关键帧精修尚未接入（避免闪烁风险），后续版本再加。
