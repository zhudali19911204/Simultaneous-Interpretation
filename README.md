# Teams 中英同声翻译助手（多供应商配置版）

这是一个 Windows 本地桌面助手：

- 同传使用千问 LiveTranslate 或兼容接口：对方说英文时显示中文字幕，你说中文时输出英文译文和合成语音。
- 同传供应商、实时模型、WebSocket 地址和 API Key 都可在界面中配置。
- 会议纪要支持百炼、DeepSeek、Moonshot/Kimi、智谱、硅基流动、OpenAI、本地 Ollama，以及任意 OpenAI Chat Completions 兼容服务。
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

启动应用后，点击主窗口右上角的 **⚙ 齿轮按钮**打开“模型供应商设置”。设置窗口分为两套独立配置，主窗口平时只显示音频路由和字幕：

- **同传供应商**：默认“阿里云百炼 LiveTranslate”。选择“自定义 LiveTranslate 兼容接口”后，可填写完整的 `ws://` 或 `wss://` 地址。
- **会议纪要 LLM**：选择供应商、模型和 Chat Completions 地址。供应商预设只负责填入常用地址，地址仍可手动修改。
- **附加请求参数 JSON**：用于供应商特有参数，例如 `{"top_p": 0.8}`。不能覆盖 `model`、`messages`、`temperature` 和 `max_tokens`。

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

如果安装或切换了虚拟声卡，请点击“刷新音频设备”。

## AI 会议纪要

1. 正常开始同传，应用会在内存中记录双方的最终字幕和时间。
2. 会议结束后点击“停止”。
3. 点击窗口底部的“生成 AI 会议纪要”。
4. 纪要窗口支持一键复制，或保存为 UTF-8 编码的 Markdown 文件。

“会议纪要模型”默认是 `qwen3.5-flash`。切换供应商后，请填写该供应商实际开放的模型名称；模型可用性和费用以对应供应商控制台为准。本地 Ollama 可以不填 API Key。

纪要包含会议概览、核心摘要、决策与结论、行动项、关键讨论、风险与未决问题。短会议一次生成；较长会议会自动分段提炼后再合并。界面会显示本次纪要额外消耗的输入和输出 Token。

会议字幕只保存在当前应用内存中，除非主动保存纪要，否则不会由应用写入文件。“清空记录”会同时删除用于生成纪要的内存转写。

> 当前音频链路只能区分“我”和“对方”，不能可靠识别多位参会者。只有会议内容明确提到姓名、负责人或截止时间时，纪要才会使用这些信息；其余内容标记为“未明确”。

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

## 当前行为

- 中译英会话输出文本和英文音频；英译中会话只输出文本，避免不需要的中文音频费用。
- 输入为 16 kHz、16 bit、单声道 PCM；英文输出为 24 kHz、16 bit、单声道 PCM。
- 中间译文用于实时预览，最终译文写入历史区域。
- 停止时会发送结束报文，等待最后一段翻译完成后再关闭连接。

## 已知限制

- 官方标称最低同传延迟约2.8秒，实际还受网络、断句、Teams 音频处理和服务负载影响。
- Teams 降噪可能影响合成语音；若对方听到声音断续，可降低噪声抑制强度。
- 使用扬声器外放可能让实体麦克风收到对方声音；优先使用耳机。
- 两个翻译方向会占用两条实时 WebSocket 会话，需满足所选同传供应商的并发限制。
- 生成会议纪要会额外调用一次或多次你选择的文本模型并产生相应 Token 费用。
- 测试和正式会议前应告知参会者音频将由第三方云服务处理，并遵守组织的数据与隐私要求。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

## 项目结构

```text
src/main.py
src/simultaneous_interpreter/app.py            # Tkinter 桌面界面
src/simultaneous_interpreter/audio_devices.py  # WASAPI 设备枚举
src/simultaneous_interpreter/credential_store.py # Windows 凭据管理器
src/simultaneous_interpreter/meeting_minutes.py # AI 会议纪要
src/simultaneous_interpreter/provider_config.py # 供应商预设与配置解析
src/simultaneous_interpreter/qwen_backend.py   # 千问双 WebSocket 会话
src/simultaneous_interpreter/settings_store.py # 本机非敏感设置
tests/test_meeting_minutes.py                  # 纪要格式、分段与接口测试
tests/test_provider_config.py                  # 供应商配置测试
tests/test_qwen_backend.py                     # 配置、事件与用量测试
```
