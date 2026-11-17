# 心跳 · Heartbeat Companion

一个可以保存记忆、继续手头计划、浏览已上传相册，并安排下一次唤醒的单用户 AI 陪伴程序。它带有中文网页界面，运行数据保存在本地 SQLite 中。

**默认启动的是脚本演示。**演示不会调用模型，也不代表 AI 自主决定时间；接入支持工具调用的模型后，才会由模型选择下一次唤醒的时间和行动。

## 可以做什么

- **自主预约一次性唤醒**：模型调用 `schedule_wake` 选择时间和原因，可以重新安排或取消。后台每秒检查数据库，到期时才调用模型。
- **接着上次做事**：聊天、记忆、计划、预约、活动记录和文字作品持续保存。重启后能恢复未执行的预约。
- **醒来带着上下文**：加载近期原始聊天、记忆、计划、最近活动和本次唤醒原因；旧聊天可按关键词继续检索。
- **实际读取相册**：检索已上传照片的标题、配文，再打开选中的图片；实时模式会把图片作为视觉输入交给模型。
- **安静地做事**：后台可以保存计划和 Markdown 文字作品，需要联系你时再发送到网页聊天。醒来不必发消息。
- **查看后台记录**：界面展示真实的照片读取、记忆检索、计划修改、预约和作品保存操作。
- **自定义交流方式**：在设置中编辑交流风格，保存后在下一次运行生效。

## 先跑演示

推荐使用 **Python 3.12**。在项目文件夹中打开终端。

### Windows

先确认已经安装 Python 3.12，然后依次运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m companion
```

以上方式直接使用虚拟环境中的 Python，不需要激活脚本。后续启动只需运行最后一行。

也可以在 PowerShell 中运行 `scripts/start.ps1`；它会在首次启动时创建环境、安装依赖并复制配置样例。

### macOS / Linux

确保 `python3 --version` 为 3.12 或更新版本，然后运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m companion
```

也可以运行 `bash scripts/start.sh`。

服务启动后，打开 **http://127.0.0.1:8765**。

1. 发一条消息，查看演示生成的计划和下一次预约。
2. 在相册中上传一张测试照片，填入标题和配文。
3. 点击手动唤醒，查看照片读取记录和生成的文字作品。
4. 在设置中修改交流风格、安静时段或每日调用上限。

演示记录会明确注明由脚本生成；演示读取图片文件，但不会解释图片视觉内容。

## 接入真实模型

打开项目根目录下的 `.env`，修改以下配置：

```dotenv
COMPANION_MODE=live
COMPANION_PROVIDER=anthropic
COMPANION_API_KEY=在这里填写你的API密钥
COMPANION_MODEL=填写服务商提供的准确模型ID
COMPANION_BASE_URL=
```

- `anthropic` 使用 Claude Messages API，地址留空时连接官方服务。
- `openai` 使用 OpenAI 兼容的 Chat Completions API；第三方兼容服务可填写自己的 API 前缀，例如 `https://你的服务地址/v1`。
- 模型需要支持**工具调用**；要看照片，还需要支持**视觉输入**。兼容服务对这些能力的支持可能不同。
- 密钥只放在服务器环境或 `.env` 中；网页不接收模型 API 密钥。
- 这里使用 API 服务与其计费，ChatGPT / Claude 网页订阅不自动提供本程序所需的 API 额度。

保存配置后重启程序，再聊天或手动唤醒。实时模式缺少密钥或模型 ID 时会拒绝启动，避免把演示误当成真实模型运行。

已上传的聊天上下文、记忆，以及模型主动打开的照片，会发送到你配置的模型服务。请按自己的需求选择服务商和上传内容。

## 心跳怎样运行

每次聊天或后台活动结束前，模型可以设置一条下一次预约，包含时间和继续做事的原因。下一次唤醒时，程序重建上下文，再让模型决定继续工作、发消息、重新预约，或暂时等待。

预约是一次性的，没有固定间隔的自动模型调用。没有预约时，调度器只检查数据库，不会定时找模型聊天。计划、记忆和作品是明确保存的记录，活动日志展示实际工具操作，不展示模型的私有推理。

新用户消息会取消旧预约并中止正在进行的旧活动；发送前还会检查聊天版本，避免旧活动接着过期话题发消息。同一次运行最多发送一条主动消息。自动消息有十分钟冷却，并在连续两条未获回应后停止继续发送。

程序关闭、电脑睡眠或断网时，无法正常继续模型调用。电脑恢复、程序重启后，仍处于待执行状态的过期预约会运行一次；中断中的任务会标记为中断，不会盲目重发。网页可以关闭，**服务进程需要保持运行**。

## 设置与数据

| 项目 | 默认值 / 行为 |
| --- | --- |
| 时区 | `Asia/Shanghai`，通过 `COMPANION_TIMEZONE` 修改 |
| 监听地址 | `127.0.0.1:8765` |
| 运行模式 | `demo` |
| 安静时段 | 23:00–08:00，限制自动发消息；后台仍可做事 |
| 手动唤醒 | 属于明确触发，可绕过自动消息的安静时段与冷却 |
| 每日调用上限 | 24 次模型请求，包括聊天和后台请求；不是金额上限 |
| 单次运行步骤 | 最多 8 次模型请求；多步工具调用会消耗多次请求 |
| 自主预约范围 | 1 分钟至 30 天；具体时间由模型选择 |
| 照片 | 单张最多 10 MiB、2400 万像素；转换为最长边 1600 像素的 JPEG |
| 作品 | 当前支持持久保存与下载文字 / Markdown 内容 |
| 数据目录 | `data/`，可通过 `COMPANION_DATA_DIR` 修改 |

在设置中暂停心跳会取消待执行预约并中止当前活动；恢复开关后，可以聊天或手动唤醒，让模型重新安排下一次预约。安静时段开始和结束同时留空可关闭该限制。

数据目录包括 `companion.sqlite3` 和标准化后的照片。备份前先停止程序，再复制整个数据目录；恢复时将目录放回原位置。改用新数据目录会创建一份空白记录。

`.env`、数据库、照片和虚拟环境已加入 `.gitignore`。仓库保存代码和配置样例，运行数据应单独备份。

## 长期开着：Docker

本机演示可以先用普通启动方式。需要自动重启服务时，可使用 Docker Compose：

1. 复制 `.env.example` 为 `.env`，按需要填写模型配置。
2. 在 `.env` 中设置独立的长随机 `COMPANION_ACCESS_TOKEN`，例如用下面命令生成后复制进去：

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

3. 启动：

   ```bash
   docker compose up -d --build
   ```

4. 打开 http://127.0.0.1:8765，在页面设置中填写访问令牌。

Compose 使用命名卷 `companion-data` 保存运行数据，容器重建后仍保留。端口默认只映射到本机，容器内部监听 `0.0.0.0`，因此启动时要求设置访问令牌。日志与停止命令：

```bash
docker compose logs -f
docker compose stop
```

一个数据目录只运行一个服务进程。不要给 Uvicorn 增加多 worker；程序还有数据目录锁，防止多个调度器重复执行。

## 当前范围

这是一个可自托管的初版，不是手机里的常驻聊天插件。主动消息保存在本程序的网页聊天中，目前没有手机推送、微信 / QQ 接入，也不会直接读取手机相册。相册来自你上传的副本，既有聊天和其他产品的记忆不会自动导入。

当前的旧聊天检索是关键词匹配，不是向量检索；自动载入上下文有数量限制，更早的细节需要模型主动检索。模型能否提出有意思的计划、是否选择唤醒、说话是否自然，仍取决于模型、交流风格和已有上下文。程序提供执行与记录机制。

## 开发与检查

```bash
python -m unittest discover -s tests -v
python -m compileall -q companion tests
node --check companion/static/app.js
```

测试使用临时目录和模拟模型响应，不需要真实 API 密钥。GitHub Actions 会执行以上检查。

主要文件：

```text
companion/
  app.py          HTTP 接口、照片上传与静态页面
  engine.py       上下文、工具执行、自主预约和后台循环
  store.py        SQLite 持久记录
  provider.py     Claude / OpenAI 兼容接口适配与视觉输入
  process_lock.py 单调度器进程锁
  static/         中文页面
```

接口参考：[Claude Messages API](https://platform.claude.com/docs/en/api/http/messages)、[Claude 工具调用](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)、[OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat/create)。
