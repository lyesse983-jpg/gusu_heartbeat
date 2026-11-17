"""Behavioural checks for durable appointments and coherent background turns."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PIL import Image

from companion.config import Config
from companion.engine import Engine
from companion.store import Store, StaleRun, utcnow


def reply(text='', calls=()):
    return {'text': text, 'calls': list(calls), 'raw': {}}


def call(name, arguments, call_id='one'):
    return {'id': call_id, 'name': name, 'arguments': arguments}


class ScriptedProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def complete(self, system, messages, tools):
        self.requests.append((system, json.loads(json.dumps(messages)), tools))
        if not self.responses:
            raise AssertionError('Unexpected model request')
        return self.responses.pop(0)


class StoreBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_pending_appointment_survives_restart_and_is_replaced(self):
        first = self.store.schedule(utcnow() + timedelta(hours=1), 'first project', 0)
        restored = Store(self.root)
        self.assertEqual(restored.next_wake()['id'], first['id'])
        second = restored.schedule(utcnow() + timedelta(hours=3), 'different reason', 0)
        self.assertEqual(restored.next_wake()['id'], second['id'])
        self.assertEqual(restored.rows('SELECT status FROM wakes WHERE id=?', (first['id'],))[0]['status'], 'replaced')
        self.assertIsNone(restored.claim_due())

    def test_only_one_worker_can_claim_due_appointment(self):
        wake = self.store.schedule(utcnow() - timedelta(seconds=1), 'due work', 0)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.store.claim_due(), range(4)))
        claimed = [item for item in results if item]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]['id'], wake['id'])

    def test_search_returns_original_chinese_evidence_and_escapes_wildcards(self):
        revision = self.store.add_user('我正在调侧脸，完成了 80%。')
        self.store.memory('侧脸作品', '用户正在调侧脸', 'user:original', revision)
        result = self.store.recall('侧脸')
        self.assertEqual(result['messages'][0]['content'], '我正在调侧脸，完成了 80%。')
        self.assertTrue(result['messages'][0]['id'])
        self.assertEqual(len(self.store.recall('%')['messages']), 1)
        self.assertEqual(self.store.recall('_')['messages'], [])

    def test_opening_store_does_not_interrupt_another_live_runtime(self):
        run_id = self.store.start_run('wake')
        Store(self.root)
        row = self.store.rows('SELECT status FROM runs WHERE id=?', (run_id,))[0]
        self.assertEqual(row['status'], 'running')
        self.store.recover_interrupted()
        row = self.store.rows('SELECT status FROM runs WHERE id=?', (run_id,))[0]
        self.assertEqual(row['status'], 'interrupted')

    def test_second_runtime_cannot_acquire_same_data_directory(self):
        from companion.process_lock import ProcessLock
        first = ProcessLock(self.root / 'runtime.lock')
        second = ProcessLock(self.root / 'runtime.lock')
        first.acquire()
        try:
            with self.assertRaises(RuntimeError):
                second.acquire()
        finally:
            first.release()
        second.acquire()
        second.release()


class EngineBehaviourTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.config = Config(data_dir=Path(self.temp.name), mode='live', model='test', api_key='test-key')
        self.provider = ScriptedProvider([])
        self.engine = Engine(self.config, self.store, self.provider)

    async def asyncTearDown(self):
        await self.engine.stop()
        self.temp.cleanup()

    async def test_chat_model_can_choose_a_nonperiodic_one_time_wake(self):
        self.provider.responses = [reply(calls=[call('schedule_wake', {'minutes': 97, 'purpose': '继续看用户的侧脸作品'})]), reply('等你新图。')]
        result = await self.engine.chat('我去调侧脸，晚点给你看')
        self.assertTrue(result['ok'])
        wake = self.store.next_wake()
        delta = (datetime.fromisoformat(wake['due_at']) - utcnow()).total_seconds() / 60
        self.assertAlmostEqual(delta, 97, delta=0.1)
        self.assertEqual(wake['purpose'], '继续看用户的侧脸作品')
        messages = self.store.snapshot()['messages']
        self.assertEqual([m['role'] for m in messages], ['user', 'assistant'])
        context = json.loads(self.provider.requests[0][1][0]['content'])
        self.assertEqual(context['recent_messages'][-1]['content'], '我去调侧脸，晚点给你看')
        self.assertTrue(context['recent_messages'][-1]['id'])
        self.assertEqual(context['trigger'], 'chat')

    async def test_context_retains_provenance_plans_and_actual_activity(self):
        rev = self.store.add_user('这张照片是在澳门拍的')
        self.store.memory('澳门照片', '用户说照片摄于澳门', 'user:source-message', rev)
        self.store.plan('做小合集', '已经选好两张；下一步读原图', revision=rev)
        self.store.activity('photo_read', '实际读取：photo-1', 'old-run')
        _, _, payload = self.engine.context('wake', '继续小合集')
        context = json.loads(payload)
        self.assertEqual(context['memories'][0]['source'], 'user:source-message')
        self.assertIn('下一步读原图', context['plans'][0]['detail'])
        self.assertEqual(context['recent_actions'][0]['kind'], 'photo_read')
        self.assertEqual(context['purpose'], '继续小合集')

    async def test_stale_run_cannot_send_or_schedule(self):
        rev = self.store.revision()
        self.store.add_user('先别做了')
        with self.assertRaises(StaleRun):
            self.engine.append_message('outdated message', 'proactive', rev, 'old-run')
        with self.assertRaises(StaleRun):
            await self.engine.tool('schedule_wake', {'minutes': 10, 'purpose': 'outdated'}, rev, 'old-run', 'wake')
        self.assertIsNone(self.store.next_wake())
        self.assertFalse(any(m['role'] == 'assistant' for m in self.store.snapshot()['messages']))

    async def test_new_user_input_cancels_wake_and_replies_to_latest_chat(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        class BlockingProvider:
            async def complete(inner, system, messages, tools):
                context = json.loads(messages[0]['content'])
                if context['trigger'] == 'wake':
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        cancelled.set()
                        raise
                return reply('收到最新消息。')
        self.engine.provider = BlockingProvider()
        self.engine.wake_now('旧计划')
        await asyncio.wait_for(entered.wait(), 1)
        result = await self.engine.chat('现在我们聊别的')
        self.assertTrue(result['ok'])
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.store.snapshot()['messages'][-1]['content'], '收到最新消息。')
        self.assertTrue(any(a['kind'] == 'cancelled' for a in self.store.snapshot()['activities']))

    async def test_concurrent_user_turns_do_not_duplicate_latest_reply(self):
        entered = asyncio.Event()
        cancellation_started = asyncio.Event()
        release = asyncio.Event()
        chat_requests = []

        class SlowCancellationProvider:
            async def complete(inner, system, messages, tools):
                context = json.loads(messages[0]['content'])
                if context['trigger'] == 'wake':
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        cancellation_started.set()
                        try:
                            await release.wait()
                        except asyncio.CancelledError:
                            pass
                        raise
                chat_requests.append(context)
                return reply('只回答最后一条。')

        self.engine.provider = SlowCancellationProvider()
        self.engine.wake_now('旧的后台活动')
        await asyncio.wait_for(entered.wait(), 1)
        first = asyncio.create_task(self.engine.chat('第一条'))
        await asyncio.wait_for(cancellation_started.wait(), 1)
        second = asyncio.create_task(self.engine.chat('第二条'))
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), 1)
        self.assertTrue(all(result['ok'] for result in results))
        self.assertTrue(results[0].get('superseded'))
        self.assertEqual(len(chat_requests), 1)
        self.assertEqual(chat_requests[0]['recent_messages'][-1]['content'], '第二条')
        assistants = [m for m in self.store.snapshot()['messages'] if m['role'] == 'assistant']
        self.assertEqual(len(assistants), 1)

    async def test_claimed_old_wake_does_not_run_against_new_conversation(self):
        wake = self.store.schedule(utcnow() - timedelta(seconds=1), '旧预约', 0)
        claimed = self.store.claim_due()
        self.store.add_user('改变计划')
        await self.engine.run('wake', claimed['purpose'], wake_id=wake['id'], expected_revision=claimed['revision'])
        self.assertEqual(self.provider.requests, [])
        status = self.store.rows('SELECT status FROM wakes WHERE id=?', (wake['id'],))[0]['status']
        self.assertEqual(status, 'cancelled')

    async def test_quiet_hours_keep_work_silent_but_manual_wake_can_send_once(self):
        tz = ZoneInfo('Asia/Shanghai')
        self.assertTrue(self.engine.quiet(datetime(2026, 1, 1, 2, 0, tzinfo=tz)))
        self.assertFalse(self.engine.quiet(datetime(2026, 1, 1, 14, 0, tzinfo=tz)))
        with patch.object(self.engine, 'quiet', return_value=True):
            blocked = await self.engine.tool('send_message', {'content': '夜间主动消息'}, 0, 'night', 'wake')
            self.assertFalse(blocked['sent'])
            artifact = await self.engine.tool('save_artifact', {'title': '夜间草稿', 'content': '实际保存的内容'}, 0, 'night', 'wake')
            self.assertTrue(artifact['saved'])
            restored = await self.engine.tool('read_artifact', {'id': artifact['id']}, 0, 'later-run', 'wake')
            self.assertEqual(restored['content'], '实际保存的内容')
            self.engine.manual = True
            sent = await self.engine.tool('send_message', {'content': '用户手动唤醒后的消息'}, 0, 'manual', 'wake')
            duplicate = await self.engine.tool('send_message', {'content': '第二条不应重复发送'}, 0, 'manual', 'wake')
            self.assertTrue(sent['sent'])
            self.assertFalse(duplicate['sent'])
        self.assertEqual(len(self.store.snapshot()['messages']), 1)
        self.assertEqual(self.store.snapshot()['artifacts'][0]['content'], '实际保存的内容')

    async def test_photo_read_returns_bytes_from_uploaded_copy(self):
        image = BytesIO()
        Image.new('RGB', (3, 3), (20, 40, 60)).save(image, 'JPEG')
        raw = image.getvalue()
        (self.store.root / 'photos' / 'photo.jpg').write_bytes(raw)
        self.store.add_photo('photo', '侧脸', '用户自己的配文', 'photo.jpg')
        result = await self.engine.tool('read_photo', {'id': 'photo'}, 0, 'read', 'wake')
        self.assertEqual(base64.b64decode(result['_image']['data']), raw)
        self.assertEqual(result['_image']['mime'], 'image/jpeg')
        self.assertEqual(result['caption'], '用户自己的配文')
        self.assertTrue(any(a['kind'] == 'photo_read' for a in self.store.snapshot()['activities']))
        with self.assertRaises(ValueError):
            await self.engine.tool('read_photo', {'id': 'not-present'}, 0, 'read', 'wake')

    async def test_photo_search_can_find_older_items_beyond_dashboard_window(self):
        self.store.add_photo('old', '唯一旧照片', '早期的一张相片', 'old.jpg')
        for index in range(105):
            self.store.add_photo(f'new-{index}', f'较新的照片{index}', '', f'new-{index}.jpg')
        self.assertNotIn('old', [photo['id'] for photo in self.store.snapshot()['photos']])
        result = await self.engine.tool('search_photos', {'query': '唯一旧照片'}, 0, 'search', 'wake')
        self.assertEqual([photo['id'] for photo in result['photos']], ['old'])

    async def test_no_appointment_means_no_model_calls(self):
        await self.engine.start()
        await asyncio.sleep(0.03)
        await self.engine.stop()
        self.assertEqual(self.provider.requests, [])

    async def test_daily_budget_blocks_further_network_calls(self):
        self.store.update_settings({'daily_budget': 1})
        self.provider.responses = [reply(calls=[call('search_memory', {'query': '作品'})])]
        result = await self.engine.chat('继续')
        self.assertFalse(result['ok'])
        self.assertIn('调用上限', result['error'])
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(self.store.usage_today(self.engine.local_now().date().isoformat()), 1)

    async def test_invalid_schedule_is_rejected_without_replacing_existing_plan(self):
        prior = self.store.schedule(utcnow() + timedelta(hours=1), 'valid', 0)
        for arguments in ({'minutes': 0, 'purpose': 'bad'}, {'at': '2026-01-01T12:00:00', 'purpose': 'bad'}, {'minutes': 2, 'at': '2026-01-01T12:00:00Z', 'purpose': 'bad'}):
            with self.assertRaises(ValueError):
                await self.engine.tool('schedule_wake', arguments, 0, 'bad', 'wake')
        self.assertEqual(self.store.next_wake()['id'], prior['id'])
