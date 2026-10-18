autograde —— 透明、确定性的逐镜头自动调色工具（独立工具，未接入 Robota）
位置：D:\0_LLM\tools\autograde\
运行环境：本目录 venv\（只有 numpy + opencv）；ffmpeg 固定用 D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe

用法：
  venv\Scripts\python.exe autograde.py <输入视频> [-o 输出.mp4] [--strength 0-1]
        [--reference 参考图.png] [--ref-strength 0-1] [--no-shot-match] [--regions]
        [--llm] [--llm-vision] [--llm-model gemma4:12b] [--llm-host http://127.0.0.1:11434]
        [--deflicker] [--scene-thr 0.45] [--no-sbs] [--no-legal-check] [--cpu-encode]
  不写 -o 时输出为 <输入名>_graded.mp4。

输出（都在输出 mp4 所在文件夹）：
  xxx.mp4                    调好色的视频（保留原音频；有 NVENC 就用显卡编码）
  xxx_before_after.mp4       左原片 / 右调色 对比
  xxx_report.json            每个镜头的测量值、参数、肤色保护、Rec.709 检查、耗时
  xxx_autograde\shotNNN.cube 每个镜头一张 33 点 3D LUT（可直接导入达芬奇）

做了什么（对照达芬奇 / Premiere / Final Cut 的自动调色）：
  1. 镜头切换检测（类似达芬奇的场景切割）。
  2. 自动影调：按百分位定黑点、白点，伽马把中灰调到 0.40（类似 Auto Color / Auto Tone）。
  3. 稳健白平衡：灰度世界 + 白点法，忽略过曝（如窗户）、高饱和和肤色像素（类似 Balance Color）。
  4. 肤色保护：用 OpenCV 自带的人脸检测（无需下载）取肤色，校正后肤色色相必须落在肤色线附近，否则自动减弱白平衡。
  5. 高光柔化压窗户、只抬最暗的阴影、柔和 S 曲线、自然饱和度（vibrance，对肤色减弱）。
  6. 镜头匹配：所有镜头向中位风格靠拢，保持一致（类似 Shot Match / Match Color）；--no-shot-match 关闭。
  7. --reference：参考图 Lab 空间均值/方差匹配（Reinhard，类似 Lumetri Color Match）。
  8. 每个镜头只用一套固定 LUT，同一镜头内不会闪烁；结果完全可复现。--deflicker 可额外去闪。
  9. Rec.709 合法范围检查（Y 16–235），结果写进报告。
 可选：
  --regions：逐帧区域调色。人脸椭圆遮罩 + 过曝窗户遮罩（时间平滑）；只在窗户区域压高光并保留细节，
             人脸保留原肤色色度。（SAM 2.1 需要 torch，不符合“只用 numpy/opencv”，所以未接入。）
  --llm：本机 Ollama 模型读测量 JSON，只能返回固定格式里的小幅数字微调；--llm-vision 额外给它看一张缩略图拼板。
  两者默认关闭。

安全说明：
  - 只依赖 numpy 和 opencv；ffmpeg 只用固定路径的 CUTORA ffmpeg.exe，不从 PATH 查找。
  - 调用 ffmpeg 一律用参数列表，从不使用 shell=True，从不 eval/exec 任何文本。
  - 滤镜只用固定模板（format/split/trim/setpts/lut3d/concat/deflicker/scale/hstack），所有数值都限制在安全范围。
    放进滤镜的路径若含 ' " [ ] , ; = % 等字符会直接拒绝。
  - AI 只能返回固定 JSON 结构里的数字（布尔、字符串、NaN 都丢弃），逐项截断到小范围；
    它的文字从不执行、从不拼进命令。
  - 网络只允许 http://127.0.0.1 / localhost（Ollama），其他地址直接拒绝；不走代理、不跟随重定向。
  - 不会覆盖输入文件；所有输出只写在输出 mp4 所在的文件夹内。
