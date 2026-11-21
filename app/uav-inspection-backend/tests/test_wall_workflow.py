import hashlib
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image


class WallWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.environment = patch.dict(os.environ, {
            'UAV_DATA_DIR': cls.temp.name,
            'UAV_WALL_ADMINS': 'admin',
            'UAV_WALL_LIVE_DEVICES': '{"m4t":{"name":"Matrice 4T","whep":"http://gateway/flight/whep"}}',
            'UAV_WALL_TELEMETRY_KEY': 'test-bridge',
        })
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)
        from heatmapapp.main import app
        cls.client = cls.enterClassContext(TestClient(app))
        cls.app = app

    def login(self, client, username):
        path = '/login' if username == 'admin' else '/register'
        password = 'admin123' if username == 'admin' else 'inspection123'
        result = client.post('/api/v1/auth' + path, json={'username': username, 'password': password})
        self.assertEqual(result.status_code, 200, result.text)

    def check(self, result):
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['data']

    def test_end_to_end_and_access(self):
        from wallapp import analysis, intake

        api = '/api/v1/wall/workflow'
        self.assertEqual(self.client.get(api + '/tasks').status_code, 401)
        self.login(self.client, 'admin')
        maintenance = TestClient(self.app)
        visitor = TestClient(self.app)
        self.login(maintenance, 'repairer')
        self.login(visitor, 'visitor')
        # 模型上传不再限制管理员：普通账号可直接上传，但无效文件仍按 422 拒绝。
        self.assertEqual(visitor.post(api + '/models', files={'file': ('model.onnx', b'x')}).status_code, 422)
        self.assertEqual(self.client.post(api + '/models', files={'file': ('bad.onnx', b'x')}).status_code, 422)

        path = Path(self.temp.name) / 'wall-sample.avi'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 12, (96, 64))
        self.assertTrue(writer.isOpened())
        for _ in range(20):
            writer.write(np.zeros((64, 96, 3), np.uint8))
        writer.release()
        video = path.read_bytes()
        payload = {'name': 'wall-sample.avi', 'size': len(video), 'fingerprint': hashlib.sha256(video).hexdigest(),
                   'task_name': '巡检样片', 'building': '2 号楼', 'facade': '北立面'}
        with patch.object(intake, 'CHUNK', len(video) // 2 + 1):
            upload = self.check(self.client.post(api + '/uploads', json=payload))
            base = api + '/uploads/' + upload['id']
            self.assertEqual(visitor.put(base + '/parts/0', files={'file': ('part', b'x')}).status_code, 404)
            self.check(self.client.put(base + '/parts/0', files={'file': ('part', video[:intake.CHUNK])}))
            resumed = self.check(self.client.post(api + '/uploads', json=payload))
            self.assertEqual(resumed['received'], [0])
            self.assertEqual(self.client.post(base + '/complete').status_code, 409)
            self.check(self.client.put(base + '/parts/1', files={'file': ('part', video[intake.CHUNK:])}))
            task_id = self.check(self.client.post(base + '/complete'))['id']
            self.assertEqual(self.check(self.client.post(base + '/complete'))['id'], task_id)
        task = api + '/tasks/' + task_id
        self.assertEqual(visitor.get(task + '/video').status_code, 404)
        self.assertEqual(self.client.get(task + '/video', headers={'Range': 'bytes=0-10'}).status_code, 206)
        self.assertIsNone(self.check(self.client.get(task))['task']['subtitle_url'])
        srt = b'1\n00:00:00,000 --> 00:00:01,000\n[latitude: 24.8] [longitude: 102.8]\n'
        self.check(self.client.put(task + '/subtitle', files={'file': ('flight.srt', srt)}))
        self.check(self.client.put(task + '/subtitle-offset', json={'seconds': 0.2}))
        self.assertEqual(self.check(self.client.get(task))['task']['offset'], 0.2)

        analysis.MODEL_DIR.mkdir(parents=True, exist_ok=True)
        version = 'c' * 64
        analysis.model_path(version).write_bytes(b'test fixture')
        boxes = [
            {'name': 'TileSpalling', 'bbox': [4, 5, 20, 25], 'conf': 0.82},
            {'name': 'TileSpalling', 'bbox': [45, 15, 72, 39], 'conf': 0.91},
        ]
        with patch.object(analysis, 'onnx_predictor', return_value=lambda *_: boxes):
            self.check(self.client.post(task + '/runs', json={'model': version, 'sensitivity': 0.75, 'mode': 'all'}))
            for _ in range(100):
                result = self.check(self.client.get(task))
                if result['runs'][0]['state'] not in {'queued', 'running'}:
                    break
                time.sleep(0.05)
        self.assertEqual(result['runs'][0]['state'], 'completed')
        self.assertEqual(result['runs'][0]['frame_count'], 20)
        self.assertEqual(result['counts']['pending'], 2)
        ids = []
        for finding in result['findings']:
            ids.append(finding['id'])
            self.assertEqual(self.client.get(finding['image_url']).status_code, 200)
            self.check(self.client.patch(task + '/findings/' + finding['id'],
                                         json={'state': 'confirmed', 'floor': '2 层'}))
        order = self.check(self.client.post(task + '/orders', json={'finding_ids': ids}))
        self.assertEqual(self.check(self.client.post(task + '/orders', json={'finding_ids': ids}))['id'], order['id'])
        order_path = task + '/orders/' + order['id']
        self.check(self.client.patch(order_path, json={'action': 'dispatch', 'assignee': 'repairer', 'due_on': '2027-01-10'}))
        self.assertEqual(self.check(self.client.get(task))['counts']['assigned'], 2)
        self.assertFalse(self.check(maintenance.get(task))['can_manage'])
        self.assertEqual(visitor.patch(order_path, json={'action': 'accept'}).status_code, 404)
        self.check(maintenance.patch(order_path, json={'action': 'accept'}))
        self.assertEqual(maintenance.patch(order_path, json={'action': 'submit', 'completion': '已维修'}).status_code, 422)
        photo = io.BytesIO()
        Image.new('RGB', (96, 64)).save(photo, 'JPEG')
        for kind in ('before', 'after'):
            self.check(maintenance.post(order_path + '/photos/' + kind, files={'file': ('photo.jpg', photo.getvalue())}))
        self.check(maintenance.patch(order_path, json={'action': 'submit', 'completion': '已维修'}))
        self.assertEqual(maintenance.patch(order_path, json={'action': 'complete'}).status_code, 403)
        self.check(self.client.patch(order_path, json={'action': 'complete'}))
        self.assertEqual(self.check(self.client.get(task))['counts']['completed'], 2)
        self.assertIn(order['id'], self.client.get(task + '/export').text)
        report = self.client.get(task + '/report')
        self.assertEqual(report.status_code, 200, report.text[:300] if report.status_code != 200 else '')
        self.assertTrue(report.content.startswith(b'%PDF-'))
        # 一键清除：删除全部发现，清理工单关联，工单本身保留。
        cleared = self.check(self.client.delete(task + '/findings'))
        self.assertEqual(cleared['deleted'], 2)
        detail = self.check(self.client.get(task))
        self.assertEqual(detail['findings'], [])
        self.assertEqual(detail['counts'], {'pending': 0, 'confirmed': 0, 'assigned': 0, 'completed': 0})
        self.assertEqual(len(detail['orders']), 1)
        self.assertEqual(detail['orders'][0]['finding_ids'], [])
        self.assertEqual(self.check(self.client.delete(task + '/findings'))['deleted'], 0)
        self.client.post('/api/v1/auth/logout')

    def test_live_telemetry_and_gateway(self):
        from wallapp.api import live

        api = '/api/v1/wall/workflow/live'
        with TestClient(self.app) as client:
            self.login(client, 'admin')
            items = self.check(client.get(api + '/devices'))
            self.assertEqual(items[0]['id'], 'm4t')
            payload = {'latitude': 0.0, 'longitude': 102.8, 'elevation': 32.5, 'timestamp': 12345}
            self.assertEqual(client.put(api + '/m4t/telemetry', json=payload).status_code, 401)
            headers = {'x-wall-telemetry-key': 'test-bridge'}
            self.assertEqual(client.put(api + '/m4t/telemetry', json=payload, headers=headers).status_code, 204)
            self.assertEqual(self.check(client.get(api + '/m4t/telemetry'))['elevation'], 32.5)
            with patch.object(live, 'upstream', side_effect=[(b'v=0', '/flight/whep/session'), (b'', None)]) as relay:
                response = client.post(api + '/m4t/whep', content='v=0')
                self.assertEqual(response.status_code, 201, response.text)
                self.assertEqual(client.delete(response.headers['Location']).status_code, 204)
                self.assertEqual(relay.call_count, 2)
