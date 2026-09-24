# 本地视频制作与素材剪辑

适用：已有素材剪辑、字幕画面、图文短片、演示动画。
1. 读取已有素材；没有素材时可用本地绘图制作画面，再用 video.slideshow 编码 MP4。
2. video.slideshow / video.cut 等动作依赖本机 FFmpeg；先读取动作参数和 readiness。
   编码器可通过 system.health 获取已安装的 ffmpeg 路径。Python 脚本可用 `imageio_ffmpeg.get_ffmpeg_exe()` 获取内置编码器；不要只凭 PATH、cv2 或 imageio 不存在就认定不能编码。
   制作逐帧动画时，先用 python.run 和 Pillow 生成并保存帧，再调用 video.slideshow，args.images 传按顺序排列的真实帧路径，args.frame_rate 设置帧率。72 帧在 24fps 下是 3 秒；seconds_per_image 用于静态幻灯片，不与帧率同时指定。
3. 按用户要求设置时长、比例、帧率、字幕和声音；只有已配置服务才能调用外部生成模型。
4. 用 video.info 或解码检查时长、分辨率、帧率及可播放性；输出实际 MP4。
   video.info 可用内置 imageio_ffmpeg 探测，不要求系统另装 ffprobe。编码失败时保留具体错误，改正参数或修复脚本；不能把退出码强行改成零来代替检查 MP4。
5. 明确本地产出是图文/动画/剪辑；不能把它表述成外部模型生成的实拍视频。

执行规则：
- 通过当前字典的 omni_body 协议调用 action/target/args；系统负责权限，规程不能扩大权限。
- 先观察输入，再决定依赖输入的下一步。不要一次猜出整条流程的参数。
- 遇到不确定参数时调用 system.action_schema，仅查询需要的动作；不要反复查询已知模式。
- 大文件按章节或模块写入，单次工具参数保持较小；截断的 JSON 不能执行。
- 最终核对磁盘上的结果和用户要求，返回实际路径；不需要额外模型自我评分或发布审批。
- 本地文件交付只需验证文件，不调用外部发送操作，不假称已上传或已发布。
