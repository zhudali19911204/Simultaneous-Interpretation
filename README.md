# Teams 中英同声翻译助手（多供应商配置版）

这是一个 Windows 本地桌面助手：

- 同传使用千问 LiveTranslate 或兼容接口：对方说英文时显示中文字幕，你说中文时输出英文译文和合成语音。
- 同传供应商、实时模型、WebSocket 地址和 API Key 都可在界面中配置。
- 会议纪要支持百炼、DeepSeek、Moonshot/Kimi、智谱、硅基流动、OpenAI、本地 Ollama，以及任意 OpenAI Chat Completions 兼容服务。
- 独立会议助手可查看完整时间线、基于会议记录问答，并按需整理实时重点。
- 同传与会议纪要可以使用不同供应商和不同 API Key；百炼纪要可以复用已保存的百炼同传 Key。
- 音频默认不录制、不落盘；实时音频会发送到你选择的同传服务处理。

## 音频链路

```text
Teams 对方英文
    ↓
实体耳机/扬声器 ──回放捕获──> 所选服务英→中（仅文本）──> 本地中文字幕

你的实体麦克风 ──> 所选服务中→英（文本 + 英文语音）
                                                       ↓
                                              CABLE Input（播放端）
                                                       ↓
                                              CABLE Output（录音端）
                                                       ↓
                                                  Teams 麦克风
```

应用把英文声音**播放到 CABLE Input**，Teams 从 **CABLE Output** 把它当麦克风读取。

## 准备

1. Windows 10 或 Windows 11。
2. Python 3.11 或更高版本（当前项目按 Python 3.12 验证）。
3. 开通阿里云百炼华北2（北京），取得 `DASHSCOPE_API_KEY` 和业务空间 `WorkspaceId`，并开通 `qwen3.5-livetranslate-flash-realtime`。
4. 安装 VB-CABLE，或其他具有一对播放/录音端点的虚拟声卡。

安装 Python 依赖：

```powershell
.\setup.ps1
```

可选：做一次约0.4秒的本地音频并发检查。测试不会保存音频：

```powershell
.\.venv\Scripts\python.exe .\scripts\audio_smoke_test.py
```

## 配置模型供应商

启动应用后，点击主窗口右上角的 **设置** 按钮打开“模型供应商设置”（也可按 `Ctrl+,`）。设置窗口分为“同声翻译”和“AI 会议纪要”两个标签页，主窗口保持以实时字幕为主：

- **同传供应商**：默认“阿里云百炼 LiveTranslate”。只有选择“自定义 LiveTranslate 兼容接口”后，才会显示 `ws://` 或 `wss://` 地址字段。
- **会议纪要 LLM**：选择供应商、模型和 Chat Completions 地址。供应商预设只负责填入常用地址，地址仍可手动修改。
- **附加请求参数 JSON**：用于供应商特有参数，例如 `{"top_p": 0.8}`。不能覆盖 `model`、`messages`、`temperature` 和 `max_tokens`。

两个 API Key 字段都可临时切换显示/隐藏；保存校验失败时，错误会直接显示在设置窗口中，并定位到需要修正的字段。按 `Escape` 可取消设置。

同传实时音频协议没有行业统一标准。“自定义 LiveTranslate 兼容接口”必须兼容本项目当前使用的千问 LiveTranslate WebSocket 事件格式；普通 OpenAI Chat Completions 地址只能用于会议纪要，不能直接用于同传。其他实时协议需要新增对应的适配器。

填写完成后点击“保存配置”。API Key 会保存到当前 Windows 用户的**凭据管理器**，供应商、模型和地址保存到当前用户的应用配置目录；都不会写入项目文件。

点击“清除凭据”可随时从 Windows 凭据管理器删除保存内容。

旧版本保存的百炼 API Key、WorkspaceId 和会议纪要模型会自动继续使用。

也可以仅通过当前 PowerShell 会话临时传入；环境变量优先于已保存的凭据：

```powershell
$env:DASHSCOPE_API_KEY = "你的百炼 API Key"
$env:DASHSCOPE_WORKSPACE_ID = "你的 WorkspaceId"
$env:INTERPRETER_MODEL = "qwen3.5-livetranslate-flash-realtime"
$env:MINUTES_API_KEY = "可选的独立 LLM Key"
$env:MINUTES_API_URL = "https://你的服务/v1/chat/completions"
$env:MINUTES_MODEL = "你的会议纪要模型"
.\run.ps1
```

> 当前代码使用华北2（北京）业务空间域名。新加坡地域的 API Key 和 WorkspaceId 不能直接用于这个地址。

## Teams 和应用设置

1. 在 Teams 的“设备设置”中，把**扬声器**设为实际使用的耳机或扬声器。
2. 把 Teams 的**麦克风**设为 `CABLE Output (VB-Audio Virtual Cable)`。
3. 在本应用中：
   - “你的实体麦克风”：选择实际说话的麦克风；
   - “Teams 扬声器回放捕获”：选择与 Teams 扬声器对应的 loopback/回放捕获；
   - “英文输出”：选择 `CABLE Input (VB-Audio Virtual Cable)`；
   - “英文声音”：可选择 `Tina`、`Cindy`、`Raymond` 等声音。
4. 点击“开始同传”，再点击“测试输出声道”。另一台加入 Teams 的设备应听到短促测试音。
5. 正式会议前，用另一台设备检查英文音量、延迟和回声。

音频路由面板首次启动时展开；同传连接成功后会自动收起，为双字幕区域留出更多空间，随时可点击“展开音频路由”重新打开。如果安装或切换了虚拟声卡，请点击“刷新音频设备”，也可按 `F5`。

## PPT 演示字幕

演示 PowerPoint 时，点击主窗口的“显示演示字幕”（快捷键 `Ctrl+Shift+S`），屏幕底部会出现置顶的中英双语字幕条：中文固定在上、英文固定在下。字幕条保留最近 3 组结果，新内容自动滚动到底部；当前未完成句会实时替换并在长时间无更新时清除，最终结果会持续保留，直到被后续结果从最近 3 组中自然滚出。

- 按住字幕条可拖动到其他位置或显示器；滚轮可查看最近内容，双击字幕条可隐藏。
- 字幕条是独立窗口。希望 Teams 参会者看到字幕时，请共享**整个屏幕**；只共享 PowerPoint 窗口通常不会包含字幕层。
- 放映前建议先共享屏幕、显示字幕条，再说一句测试语句确认字体大小和位置。

## AI 会议纪要

1. 正常开始同传，应用会在内存中记录双方的最终字幕和时间。
2. 会议结束后点击“停止”。
3. 点击窗口底部的“生成 AI 会议纪要”。
4. 纪要窗口会显示会议起止时间，并按标题、列表和正文样式展示；可一键复制原始 Markdown，或保存为 UTF-8 编码的 Markdown 文件。

“会议纪要模型”默认是 `qwen3.5-flash`。切换供应商后，请填写该供应商实际开放的模型名称；模型可用性和费用以对应供应商控制台为准。本地 Ollama 可以不填 API Key。

纪要包含会议概览、核心摘要、决策与结论、行动项、关键讨论、风险与未决问题。短会议一次生成；较长会议会自动分段提炼后再合并。界面会显示本次纪要额外消耗的输入和输出 Token。

会议字幕只保存在当前应用内存中，除非主动保存纪要，否则不会由应用写入文件。“清空记录”会同时删除用于生成纪要的内存转写。

> 当前音频链路只能区分“我”和“对方”，不能可靠识别多位参会者。只有会议内容明确提到姓名、负责人或截止时间时，纪要才会使用这些信息；其余内容标记为“未明确”。

## AI 会议理解助手

点击主窗口底部的“会议助手”打开独立窗口。助手只读取当前内存中已经配对完成的最终字幕，不修改同传连接、PPT 字幕或会议纪要记录：

- **完整时间线**：按时间查看“我/对方”记录，支持角色筛选、关键词高亮和复制，全程不调用网络。
- **会中问答**：输入问题后，助手使用当前重点、最近 15 分钟内容和相关历史记录回答；回答需附 `[时间 我/对方]` 依据，没有可核验依据时会明确拒绝猜测。
- **实时重点**：手动点击“刷新重点”后整理当前议题、结论、行动项、风险和未决问题。自动更新默认关闭；主动开启后，每 5 分钟且至少新增 300 个字符时才调用模型。

问答与实时重点复用“AI 会议纪要”标签页中的供应商、模型、API Key 和附加参数，但不会保存或改写配置。助手 Token 独立统计，网络错误仅显示在助手窗口，不影响同传运行。关闭助手会停止自动刷新；内容只保存在当前应用内存中，“清空记录”会同步清空助手状态。

## 省流量模式

LiveTranslate 的“省流量静音门控”默认开启：

- 本地检测到说话后才上传音频；
- 保留约0.16秒前导音频，避免丢掉开头；
- 继续发送约1秒尾部静音，确保千问服务端收到足够的断句信号；
- 长时间沉默不上传，从而减少输入音频 Token。

如果麦克风音量很小而出现开头漏字，可提高 Windows 麦克风音量，或关闭静音门控。

## 延迟优化

本地音频采集和播放使用约20毫秒缓冲，并为 WebSocket 音频小包启用 TCP `NODELAY`。千问 LiveTranslate 仍需完成服务端断句、翻译和语音合成，官方标称最低延迟约2.8秒，实际会议中通常无法达到零延迟。

如果中文说完后超过5秒仍没有英文声音，可依次检查：

1. 每个意群尽量简短，说完后清晰停顿约1秒；连续长句会推迟服务端断句。
2. 临时关闭“省流量静音门控”比较延迟。若明显改善，说明麦克风音量过低或环境噪声让本地门控判断不稳定；关闭后会持续上传静音并增加 Token。
3. 如果英文字幕已经出现但语音较晚，点击“测试输出声道”，重点检查 CABLE、Teams 降噪和输出设备，而不是网络翻译。
4. 优先使用稳定的有线网络或低延迟 Wi-Fi，并避免 VPN 绕路；当前服务端固定在华北2（北京）。

## Token 与费用观察

千问会在每轮 `response.done` 中返回用量。使用千问时，应用会在顶部累计显示：

- 输入音频 Token
- 输出音频 Token
- 输入及输出文本 Token

当前官方规则中，模型输入音频约为每秒7 Token，输出音频约为每秒12.5 Token。实际账单仍以百炼控制台为准，建议同时设置费用告警。

## 自动重连与诊断

首次启动遇到 DNS、连接超时、`429` 或临时服务端错误时，应用会在 15 秒启动窗口内先自动重试，不再因一次短暂的 `getaddrinfo` 失败立即退出。同传成功启动后，如果某一个翻译方向因网络抖动或服务端临时关闭而断开，应用会保留另一个方向并在后台自动重连。重试间隔依次为 2、5、10、20、30 秒，之后每 30 秒继续尝试；两个方向共享连接频率限制，避免重连风暴。

- 主窗口会显示具体方向和重试次数，PPT 字幕条也会显示简短提示；恢复后提示自动消失。
- 已完成字幕、Token 统计和会议纪要素材会保留。断线期间未送达服务端的音频不会补传，正在处理的半句可能丢失。
- API Key、模型或权限配置无效时不会持续重试，请修正设置后重新开始。
- DNS 持续失败时，错误窗口会明确提示检查北京地域 WorkspaceId、VPN、代理、公司 DNS 或切换手机热点；这类错误发生在请求到达模型服务之前。
- 连接诊断日志位于 `%APPDATA%\SimultaneousInterpreter\logs\interpreter.log`，单文件最大 1 MB并保留 3 份。日志不记录凭据、WorkspaceId、音频、字幕或会议内容。

## 当前行为

- 中译英会话输出文本和英文音频；英译中会话只输出文本，避免不需要的中文音频费用。
- 输入为 16 kHz、16 bit、单声道 PCM；英文输出为 24 kHz、16 bit、单声道 PCM。
- 中间译文用于实时预览，最终译文写入历史区域。
- 停止时会发送结束报文，等待最后一段翻译完成后再关闭连接。
- 心跳超时为20秒；运行中异常断开会按方向独立自动恢复。

## 已知限制

- 官方标称最低同传延迟约2.8秒，实际还受网络、断句、Teams 音频处理和服务负载影响。
- Teams 降噪可能影响合成语音；若对方听到声音断续，可降低噪声抑制强度。
- 使用扬声器外放可能让实体麦克风收到对方声音；优先使用耳机。
- 两个翻译方向会占用两条实时 WebSocket 会话，需满足所选同传供应商的并发限制。
- 生成会议纪要会额外调用一次或多次你选择的文本模型并产生相应 Token 费用。
- 会议助手的问答、手动重点和主动开启的自动重点会额外调用会议纪要模型；仅查看时间线不会产生 LLM 费用。
- 测试和正式会议前应告知参会者音频将由第三方云服务处理，并遵守组织的数据与隐私要求。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

## 安装 Windows 发布版

收到 `TeamsInterpreter-Windows-x64-<版本>.zip` 后，推荐按以下方式安装：

1. 右键 ZIP 文件并选择“全部解压”。不要直接在压缩包预览窗口中运行程序。
2. 打开解压后的目录，双击 `Install.cmd`。
3. 程序会安装到当前用户的 `%LOCALAPPDATA%\TeamsInterpreter`，并创建桌面和开始菜单快捷方式，不需要管理员权限。
4. 从桌面启动 `Teams Interpreter`，检查已保存的 API Key、WorkspaceId、模型和音频设备，然后点击“开始同传”。

如需免安装运行，完整解压后直接打开 `app` 文件夹并双击 `TeamsInterpreter.exe`。覆盖安装新版本不会主动删除当前 Windows 用户已保存的凭据和配置。

如果 Windows SmartScreen 显示“未知发布者”，可确认安装包来源及随包 SHA256 校验文件后，点击“更多信息”→“仍要运行”。这是因为当前内部版本没有代码签名证书。

应用本身无需管理员权限。若要把合成英文语音发送到 Teams，仍需单独安装 VB-CABLE 或兼容虚拟声卡；驱动安装通常需要管理员权限，已安装的设备无需重复安装。

## 打包 Windows 发行版

安装构建依赖并生成 Windows x64 独立发行包：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build_release.ps1
```

输出位于 `release\TeamsInterpreter-Windows-x64-<提交号>.zip`。发布包同时包含 `Install.cmd`、免安装程序、用户指南、构建信息和 SHA256 校验文件；同事无需安装 Python。

## 项目结构

```text
src/main.py
src/simultaneous_interpreter/app.py            # Tkinter 桌面界面
src/simultaneous_interpreter/audio_devices.py  # WASAPI 设备枚举
src/simultaneous_interpreter/credential_store.py # Windows 凭据管理器
src/simultaneous_interpreter/diagnostics.py    # 脱敏滚动连接日志
src/simultaneous_interpreter/meeting_minutes.py # AI 会议纪要
src/simultaneous_interpreter/meeting_assistant.py # 会议问答、检索与增量重点逻辑
src/simultaneous_interpreter/meeting_assistant_window.py # 独立会议助手窗口
src/simultaneous_interpreter/provider_config.py # 供应商预设与配置解析
src/simultaneous_interpreter/qwen_backend.py   # 千问双 WebSocket 会话
src/simultaneous_interpreter/settings_store.py # 本机非敏感设置
src/simultaneous_interpreter/ui_theme.py       # 午夜科技蓝主题与 Markdown 展示解析
src/simultaneous_interpreter/subtitle_overlay.py # PPT 中英双语置顶字幕层
TeamsInterpreter.spec                          # PyInstaller 打包配置
build_release.ps1                              # Windows x64 发行包构建脚本
packaging/                                     # 当前用户安装脚本与发行说明
tests/test_meeting_minutes.py                  # 纪要格式、分段与接口测试
tests/test_meeting_assistant.py                # 时间线、问答、重点与隔离测试
tests/test_provider_config.py                  # 供应商配置测试
tests/test_qwen_backend.py                     # 配置、事件与用量测试
tests/test_ui_theme.py                         # 状态样式与 Markdown 展示解析测试
tests/test_subtitle_overlay.py                  # 中英字幕顺序与文本压缩测试
```
