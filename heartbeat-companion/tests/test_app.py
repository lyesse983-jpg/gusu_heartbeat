"""Local HTTP integration tests with disposable photos and databases."""
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from companion.app import create_app
from companion.config import Config


class AppBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(data_dir=Path(self.temp.name), mode='demo')
        self.app = create_app(self.config)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def test_demo_chat_saves_plan_and_appt_and_state_reports_mode(self):
        self.assertEqual(self.client.get('/api/health').status_code, 200)
        response = self.client.post('/api/chat', json={'content': '你好，我去调照片'})
        self.assertEqual(response.status_code, 200)
        state = self.client.get('/api/state').json()
        self.assertEqual(state['mode'], 'demo')
        self.assertIsNotNone(state['next_wake'])
        self.assertEqual(len(state['plans']), 1)
        self.assertEqual(state['usage_today'], 0)
        self.assertIn('演示模式', state['messages'][-1]['content'])
        self.assertEqual(self.client.post('/api/chat', json={'content': '   '}).status_code, 422)

    def test_photo_upload_converts_pixels_to_jpeg_and_deletes_copy(self):
        png = BytesIO()
        Image.new('RGBA', (4, 5), (100, 20, 80, 128)).save(png, 'PNG')
        response = self.client.post('/api/photos', files={'file': ('sample.png', png.getvalue(), 'image/png')}, data={'title': '测试图', 'caption': '这是配文'})
        self.assertEqual(response.status_code, 200)
        photo = response.json()
        image_response = self.client.get(photo['url'])
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response.headers['content-type'], 'image/jpeg')
        with Image.open(BytesIO(image_response.content)) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertEqual(image.size, (4, 5))
        state_photo = self.client.get('/api/state').json()['photos'][0]
        self.assertEqual(state_photo['caption'], '这是配文')
        self.assertNotIn('filename', state_photo)
        self.assertEqual(self.client.delete('/api/photos/' + photo['id']).status_code, 200)
        self.assertEqual(self.client.get(photo['url']).status_code, 404)
        self.assertEqual(list((Path(self.temp.name) / 'photos').iterdir()), [])
        invalid = self.client.post('/api/photos', files={'file': ('fake.jpg', b'not an image', 'image/jpeg')})
        self.assertEqual(invalid.status_code, 422)

    def test_memory_persona_and_settings_survive_api_roundtrip(self):
        saved = self.client.post('/api/memories', json={'key': '偏好', 'content': '喜欢自然私聊', 'source': 'user'})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.client.post('/api/persona', json={'content': '温柔直接，少旁白'}).status_code, 200)
        self.assertEqual(self.client.post('/api/plans', json={'title': '照片合集', 'detail': '下一步选图', 'status': 'open'}).status_code, 200)
        self.assertEqual(self.client.post('/api/settings', json={'quiet_start': '22:30', 'quiet_end': '07:30', 'daily_budget': 12}).status_code, 200)
        state = self.client.get('/api/state').json()
        self.assertEqual(state['persona'], '温柔直接，少旁白')
        self.assertEqual(state['memories'][0]['content'], '喜欢自然私聊')
        self.assertEqual(state['settings']['daily_budget'], 12)
        self.assertEqual(self.client.delete('/api/memories/' + state['memories'][0]['id']).status_code, 200)
        self.assertEqual(self.client.delete('/api/plans/' + state['plans'][0]['id']).status_code, 200)
        self.assertEqual(self.client.post('/api/settings', json={'quiet_start': '25:00'}).status_code, 422)
        self.assertEqual(self.client.post('/api/settings', json={'daily_budget': 0}).status_code, 422)
        self.assertEqual(self.client.post('/api/plans', json={'title': 'x', 'detail': 'x', 'status': 'bogus'}).status_code, 422)
        self.assertEqual(self.client.post('/api/memories', json={'key': '   ', 'content': '内容', 'source': 'user'}).status_code, 422)
        self.assertEqual(self.client.post('/api/memories', json={'key': '键', 'content': '   ', 'source': 'user'}).status_code, 422)

    def test_pause_cancels_pending_appointment_and_does_not_automatically_recreate(self):
        self.client.post('/api/chat', json={'content': '演示'})
        self.assertIsNotNone(self.client.get('/api/state').json()['next_wake'])
        self.assertEqual(self.client.post('/api/settings', json={'enabled': False}).status_code, 200)
        state = self.client.get('/api/state').json()
        self.assertFalse(state['enabled'])
        self.assertIsNone(state['next_wake'])
        self.client.post('/api/settings', json={'enabled': True})
        self.assertIsNone(self.client.get('/api/state').json()['next_wake'])

    def test_cross_site_writes_and_unconfigured_remote_hosts_are_rejected(self):
        response = self.client.post('/api/chat', json={'content': 'untrusted'}, headers={'origin': 'https://unrelated.example'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get('/api/state', headers={'host': 'remote.example'}).status_code, 403)
        self.assertEqual(self.client.get('/api/state').json()['messages'], [])


class TokenAuthTests(unittest.TestCase):
    def test_token_required_for_api_but_not_static_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(Config(data_dir=Path(temp), access_token='local-test-token'))
            with TestClient(app) as client:
                self.assertEqual(client.get('/api/state').status_code, 401)
                self.assertEqual(client.get('/api/health').status_code, 401)
                self.assertEqual(client.get('/api/state', headers={'Authorization': 'Bearer wrong'}).status_code, 401)
                response = client.get('/api/state', headers={'Authorization': 'Bearer local-test-token', 'host': 'remote.example'})
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('local-test-token', response.text)
