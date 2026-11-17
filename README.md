# Gusu Heartbeat

这个仓库包含原有的 Node.js 心跳程序，以及新增的独立 Python 陪伴运行程序。两套程序分别启动和配置。

## 心跳 · Heartbeat Companion

[打开中文说明与启动教程](heartbeat-companion/README.md)

新程序支持模型自主预约一次性唤醒、持久记忆与计划、读取上传的照片、保存文字作品，以及查看后台活动的中文网页。默认运行不调用模型的脚本演示；真实模式需要配置模型 API 密钥与模型 ID。

进入 `heartbeat-companion` 目录后，按其中的 README 启动。网页可以关闭，后台服务需要持续运行。当前主动消息留在新程序的网页内，还没有接入原 Node.js 程序或手机推送。

## 原有程序

仓库根目录的 `index.js` 和 `package.json` 保留原有用途。新增 Python 程序不需要通过根目录的 npm 命令启动。
