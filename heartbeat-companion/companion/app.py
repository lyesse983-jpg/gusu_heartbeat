"""Local single-user HTTP app. One worker owns the durable wake scheduler."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
from io import BytesIO
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import re
from urllib.parse import urlparse

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

from .config import Config
from .engine import Engine
from .provider import Provider
from .store import Store, ident, stamp


class ChatInput(BaseModel):
    content: str = Field(min_length=1, max_length=12000)


class WakeInput(BaseModel):
    purpose: str = Field(default='手动查看', min_length=1, max_length=500)


class MemoryInput(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=3000)
    source: str = Field(default='user', min_length=1, max_length=500)


class PlanInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    detail: str = Field(min_length=1, max_length=4000)
    status: str = 'open'


class SettingsInput(BaseModel):
    enabled: bool | None = None
    quiet_start: str | None = None
    quiet_end: str | None = None
    daily_budget: int | None = Field(default=None, ge=1, le=500)


class PersonaInput(BaseModel):
    content: str = Field(max_length=8000)


def create_app(config=None, provider=None):
    config = config or Config.from_env()
    store = Store(config.data_dir)
    model = provider or (Provider(config.provider, config.api_key, config.model, config.base_url) if config.mode == 'live' else None)
    engine = Engine(config, store, model)

    @asynccontextmanager
    async def lifespan(app):
        # Lock across processes too: duplicate workers must never duplicate background work.
        from .process_lock import ProcessLock
        lock = ProcessLock(config.data_dir / 'runtime.lock')
        lock.acquire()
        try:
            store.recover_interrupted()
            await engine.start()
            yield
        finally:
            await engine.stop()
            lock.release()

    app = FastAPI(title='Heartbeat Companion', lifespan=lifespan)
    app.state.store, app.state.engine = store, engine

    @app.middleware('http')
    async def protect(request: Request, call_next):
        host = request.headers.get('host', '').split(':')[0].strip('[]')
        if host not in ('127.0.0.1', 'localhost', 'testserver', '::1') and not config.access_token:
            return JSONResponse({'detail': '远程访问需要设置 COMPANION_ACCESS_TOKEN。'}, status_code=403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin and urlparse(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail': '拒绝跨站写入。'}, status_code=403)
            length = request.headers.get('content-length')
            if length and (not length.isdigit() or int(length) > 11 * 1024 * 1024):
                return JSONResponse({'detail': '请求超过大小限制。'}, status_code=413)
        if request.url.path.startswith('/api/') and config.access_token:
            wanted = 'Bearer ' + config.access_token
            if not hmac.compare_digest(request.headers.get('authorization', ''), wanted):
                return JSONResponse({'detail': '需要有效的访问令牌。'}, status_code=401)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        response.headers['Cache-Control'] = 'no-store'
        return response

    static = Path(__file__).parent / 'static'
    app.mount('/static', StaticFiles(directory=static), name='static')

    @app.get('/')
    async def index():
        return FileResponse(static / 'index.html')

    @app.get('/api/health')
    async def health():
        return {'ok': True, 'mode': config.mode, 'scheduler': bool(engine.ticker and not engine.ticker.done())}

    @app.get('/api/state')
    async def state():
        snap = store.snapshot()
        with store.db() as c:
            row = c.execute("SELECT value FROM meta WHERE key='persona'").fetchone()
        return dict(snap, mode=config.mode, provider=config.provider, model=config.model or '未配置', enabled=snap['settings']['enabled'], busy=engine.busy, timezone=config.timezone, usage_today=store.usage_today(datetime.now(ZoneInfo(config.timezone)).date().isoformat()), persona=row[0] if row else '')

    @app.post('/api/chat')
    async def chat(data: ChatInput):
        if not data.content.strip():
            raise HTTPException(422, '请输入消息。')
        result = await engine.chat(data.content.strip())
        return JSONResponse(result, status_code=200 if result.get('ok') else 502)

    @app.post('/api/wake')
    async def wake(data: WakeInput):
        try:
            engine.wake_now(data.purpose)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return {'ok': True}

    @app.post('/api/settings')
    async def settings(data: SettingsInput):
        update = data.model_dump(exclude_none=True)
        for key in ('quiet_start', 'quiet_end'):
            if key in update and update[key] and not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', update[key]):
                raise HTTPException(422, '时间格式需要是 HH:MM。留空可关闭安静时段。')
        value = store.snapshot()['settings'] | update
        if bool(value['quiet_start']) != bool(value['quiet_end']):
            raise HTTPException(422, '请同时填写安静时段的开始与结束，或同时留空。')
        import json
        with store.db() as c:
            c.execute("UPDATE meta SET value=? WHERE key='settings'", (json.dumps(value),))
            if update.get('enabled') is False:
                c.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
                c.execute("UPDATE wakes SET status='cancelled' WHERE status='pending'")
        if update.get('enabled') is False and engine.busy:
            engine.active.cancel()
            with suppress(asyncio.CancelledError):
                await engine.active
        store.activity('settings', '更新心跳设置。恢复开关后，可聊天或手动唤醒以重新安排。')
        return {'ok': True, 'settings': value}

    @app.post('/api/persona')
    async def persona(data: PersonaInput):
        with store.db() as c:
            c.execute("INSERT INTO meta VALUES ('persona',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (data.content,))
        store.activity('settings', '更新交流风格，下次运行生效。')
        return {'ok': True}

    @app.post('/api/photos')
    async def upload(file: UploadFile = File(...), title: str = Form(''), caption: str = Form('')):
        if len(title) > 160 or len(caption) > 2000:
            raise HTTPException(422, '标题最多 160 字，配文最多 2000 字。')
        payload = await file.read(10 * 1024 * 1024 + 1)
        await file.close()
        if len(payload) > 10 * 1024 * 1024:
            raise HTTPException(413, '照片需要小于 10 MB。')
        try:
            with Image.open(BytesIO(payload)) as original:
                if original.width * original.height > 24_000_000:
                    raise ValueError('照片像素过大，请缩小后上传。')
                image = ImageOps.exif_transpose(original).convert('RGB')
                image.thumbnail((1600, 1600))
                photo_id = ident()
                filename = photo_id + '.jpg'
                image.save(store.root / 'photos' / filename, 'JPEG', quality=88)
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            raise HTTPException(422, '无法读取照片。支持常见图片，最多 2400 万像素。')
        title = title.strip() or '未命名照片'
        store.add_photo(photo_id, title, caption.strip(), filename)
        store.activity('upload', f'加入相册：{title}')
        return {'id': photo_id, 'title': title, 'caption': caption.strip(), 'created_at': stamp(), 'url': f'/api/photos/{photo_id}/image'}

    @app.get('/api/photos/{photo_id}/image')
    async def image(photo_id: str):
        item = store.photo(photo_id)
        if not item:
            raise HTTPException(404, '照片不存在。')
        return FileResponse(store.root / 'photos' / item['filename'], media_type='image/jpeg')

    @app.delete('/api/photos/{photo_id}')
    async def delete_photo(photo_id: str):
        item = store.photo(photo_id)
        if not item:
            raise HTTPException(404, '照片不存在。')
        store.delete('photos', photo_id)
        (store.root / 'photos' / item['filename']).unlink(missing_ok=True)
        return {'ok': True}

    @app.post('/api/memories')
    async def memory(data: MemoryInput):
        try:
            result = await engine.tool('save_memory', data.model_dump(), current_revision(), 'user-edit', 'chat')
            return {'ok': True, **result}
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.delete('/api/memories/{item_id}')
    async def delete_memory(item_id: str):
        store.delete('memories', item_id)
        return {'ok': True}

    @app.post('/api/plans')
    async def plan(data: PlanInput):
        try:
            result = await engine.tool('save_plan', data.model_dump(), current_revision(), 'user-edit', 'chat')
            return {'ok': True, **result}
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.delete('/api/plans/{item_id}')
    async def delete_plan(item_id: str):
        store.delete('plans', item_id)
        return {'ok': True}

    def current_revision():
        with store.db() as c:
            return int(c.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])

    return app
