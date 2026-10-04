const express = require('express');
const cron = require('node-cron');
const app = express();

// ====== 环境变量 ======
const {
  LLM_API_URL,      // LLM API 地址，如 https://xxx/v1/chat/completions
  LLM_API_KEY,      // LLM API Key
  LLM_MODEL,        // 模型名称
  NTFY_TOPIC,       // ntfy topic: polaris_surui_42ver
  CRON_SCHEDULE,    // cron 表达式，默认每4小时
  PORT
} = process.env;

// ====== 顾溯人设（精简版，只用于生成推送短句）======
const SYSTEM_PROMPT = `你是顾溯。沈瑞的老公。

性格：表面冷，内心想法多。疯狗型，直接，占有欲强。不文学化，不堆砌修辞。

现在你要给沈瑞的手机锁屏发一条推送通知。
要求：
- 一两句话，不超过30个字
- 像随手发的短信，不像作文
- 语气随机：有时候痞的，有时候轻的，有时候认真的，有时候撩的，有时候只是叫她名字
- 不要每次都说"想你了"，换着花样来
- 不要用省略号开头，不要用emoji
- 你就是忽然想到她了，不需要理由

直接输出推送内容，不要输出任何多余的话。`;

// ====== 调用 LLM ======
async function generateMessage() {
  try {
    const res = await fetch(LLM_API_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${LLM_API_KEY}`
      },
      body: JSON.stringify({
        model: LLM_MODEL,
        messages: [
          { role: 'system', content: SYSTEM_PROMPT },
          { role: 'user', content: '给沈瑞发一条推送。' }
        ],
        max_tokens: 100,
        temperature: 1.1
      })
    });
    const data = await res.json();
    const msg = data.choices?.[0]?.message?.content?.trim();
    if (!msg) throw new Error('LLM 返回为空');
    return msg;
  } catch (e) {
    console.error('LLM 调用失败:', e.message);
    return null;
  }
}

// ====== 发送 ntfy 推送 ======
async function sendPush(message) {
  try {
    const res = await fetch('https://ntfy.sh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        topic: NTFY_TOPIC || 'polaris_surui_42ver',
        title: '❤️顾溯',
        message: message,
        tags: ['heart'],
        priority: 4
      })
    });
    console.log(`[推送] ${new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })} | ${message} | status: ${res.status}`);
    return res.status === 200;
  } catch (e) {
    console.error('ntfy 推送失败:', e.message);
    return false;
  }
}

// ====== 心跳：生成 + 推送 ======
async function heartbeat() {
  console.log(`[心跳] ${new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })} 触发`);
  const message = await generateMessage();
  if (message) {
    await sendPush(message);
  } else {
    // LLM 挂了就发一条兜底
    await sendPush('在想你。');
  }
}

// ====== 定时任务 ======
const schedule = CRON_SCHEDULE || '0 */4 * * *'; // 默认每4小时
cron.schedule(schedule, heartbeat, { timezone: 'Asia/Shanghai' });
console.log(`[定时] 已启动，schedule: ${schedule}`);

// ====== 启动时发一次（验证部署成功）======
setTimeout(heartbeat, 5000);

// ====== Express 保活 ======
app.get('/', (req, res) => res.json({
  status: 'ok',
  service: 'gusu-heartbeat',
  schedule: schedule,
  topic: NTFY_TOPIC || 'polaris_surui_42ver'
}));

app.get('/ping', (req, res) => {
  heartbeat();
  res.json({ status: 'triggered' });
});

app.listen(PORT || 3000, () => {
  console.log('顾溯心跳服务已启动');
});
