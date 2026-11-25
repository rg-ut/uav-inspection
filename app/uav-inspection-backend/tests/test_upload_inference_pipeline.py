"""Exercise actual video decoding and OpenCV DNN, without production assets or Torch.

The 777-byte ONNX fixture computes an input-dependent score for one fixed box.
It tests data flow, never business-model accuracy. Run this suite separately so
UAV_DATA_DIR is established before application modules are imported.
"""
import base64
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires ffmpeg/ffprobe')
class UploadInferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {'UAV_DATA_DIR': cls.tmp.name, 'UAV_WALL_ADMINS': 'admin'})
        cls.env.start()
        from fastapi.testclient import TestClient
        from heatmapapp.main import app
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'admin123'})
        path = Path(cls.tmp.name) / 'sample.avi'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 12, (96, 64))
        assert writer.isOpened()
        for _ in range(12):
            writer.write(np.full((64, 96, 3), 100, np.uint8))
        writer.release()
        cls.movie = path.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.env.stop()
        cls.tmp.cleanup()

    def data(self, response):
        self.assertEqual(response.status_code, 200, response.text[:500])
        return response.json().get('data', response.json())

    def test_actual_onnx_upload_preview_and_background_analysis(self):
        api = '/api/v1/wall/workflow'
        model = base64.b64decode((Path(__file__).parent / 'fixtures/preview_detector.onnx.b64').read_text())
        version = self.data(self.client.post(api + '/models', files={'file': ('pipeline-fixture.onnx', model)}))['id']
        check = self.data(self.client.post('/api/v1/assets/check/wall'))
        self.assertTrue(check['model_ready'], check)
        upload = self.data(self.client.post(api + '/uploads', json={
            'name': 'sample.avi', 'size': len(self.movie), 'fingerprint': hashlib.sha256(self.movie).hexdigest(),
            'task_name': 'DNN pipeline fixture', 'building': 'test', 'facade': 'test',
        }))
        self.data(self.client.put(api + '/uploads/' + upload['id'] + '/parts/0', files={'file': ('part', self.movie)}))
        task = self.data(self.client.post(api + '/uploads/' + upload['id'] + '/complete'))['id']
        url = api + '/tasks/' + task
        for color, expected in [(0, 0), (255, 1)]:
            _, jpeg = cv2.imencode('.jpg', np.full((64, 96, 3), color, np.uint8))
            result = self.data(self.client.post(url + '/detect', json={
                'model': version, 'sensitivity': .05, 'image': base64.b64encode(jpeg).decode(),
            }))
            self.assertEqual(len(result['boxes']), expected)
            self.assertEqual(result['detections'], [
                {'bbox': box, 'name': 'TileSpalling', 'label': '面砖脱落'} for box in result['boxes']
            ])
            self.assertEqual((result['width'], result['height']), (96, 64))
        self.data(self.client.post(url + '/runs', json={'model': version, 'sensitivity': .75, 'mode': 'all'}))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = self.data(self.client.get(url))
            if result['runs'][0]['state'] not in {'queued', 'running'}:
                break
            time.sleep(.05)
        self.assertEqual(result['runs'][0]['state'], 'completed', result['runs'])
        self.assertEqual(result['runs'][0]['frame_count'], 12)
        self.assertEqual(result['counts']['pending'], 1)

    def test_task_uploads_convert_video_and_reject_broken_files(self):
        for module in ('power', 'telecom', 'rose'):
            with self.subTest(module=module):
                files = {'video': ('sample.avi', self.movie)}
                if module != 'power':
                    files['srt'] = ('flight.srt', b'1\n00:00:00,000 --> 00:00:01,000\nfixture\n')
                result = self.data(self.client.post(f'/api/v1/{module}/tasks/upload',
                    data={'task_id': 'converted-fixture', 'task_name': 'fixture'}, files=files))
                url = result.get('url') or result.get('video_url') or result['video_path']
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                path = Path(self.tmp.name) / f'{module}-download.mp4'
                path.write_bytes(response.content)
                capture = cv2.VideoCapture(str(path))
                try:
                    self.assertTrue(capture.read()[0])
                    self.assertEqual(int(capture.get(cv2.CAP_PROP_FRAME_COUNT)), 12)
                finally:
                    capture.release()
        broken = self.client.post('/api/v1/power/tasks/upload', files={'video': ('broken.mp4', b'not-video')})
        self.assertEqual(broken.status_code, 422)

    def test_video_only_tasks_remain_visible_without_subtitles(self):
        for module in ('telecom', 'rose'):
            result = self.data(self.client.post(f'/api/v1/{module}/tasks/upload',
                data={'task_id': 'without-subtitles'}, files={'video': ('sample.avi', self.movie)}))
            self.assertEqual(result.get('srt_url', result.get('srt_path')), '')
            endpoint = '/api/v1/telecom/tasks' if module == 'telecom' else '/api/v1/rose/tasks/list'
            listed = self.data(self.client.get(endpoint))['tasks']
            self.assertTrue(any(item['task_id'] == result['task_id'] and not item['srt_name'] for item in listed))

    def test_material_center_small_and_chunked_video_uploads_are_playable(self):
        from assetsapp import api
        result = self.data(self.client.post('/api/v1/assets/upload', data={'category': 'power-media'},
            files={'files': ('center-small.avi', self.movie)}))
        self.assertEqual(result['saved'][0]['name'], 'center-small.avi')
        with patch.object(api, 'CHUNK_SIZE', 8192):
            upload = self.data(self.client.post('/api/v1/assets/upload/init', data={
                'category': 'power-media', 'filename': 'center-chunked.avi', 'size': len(self.movie),
            }))
            for index, start in enumerate(range(0, len(self.movie), 8192)):
                self.data(self.client.post('/api/v1/assets/upload/chunk', data={
                    'upload_id': upload['uploadId'], 'index': index,
                }, files={'file': ('part', self.movie[start:start + 8192])}))
            self.data(self.client.post('/api/v1/assets/upload/complete', data={'upload_id': upload['uploadId']}))
        videos = self.data(self.client.get('/api/v1/power/videos'))['videos']
        for name in ['center-small.mp4', 'center-chunked.mp4']:
            video = next(item for item in videos if item['name'] == name)
            response = self.client.get(video['url'])
            self.assertEqual(response.status_code, 200)
            path = Path(self.tmp.name) / ('download-' + name)
            path.write_bytes(response.content)
            capture = cv2.VideoCapture(str(path))
            try:
                self.assertTrue(capture.read()[0])
            finally:
                capture.release()


class ZModelReplacementTests(unittest.TestCase):
    def test_replacement_reloads_and_does_not_fall_back_to_old_model_on_error(self):
        from powerapp import detector
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'weights.pt'
            path.write_bytes(b'first')
            with patch.object(detector, 'MODEL_PATH', path), patch.object(detector, 'load_yolo_model') as load:
                instance = detector.PowerDetector()
                instance.model = None
                instance.load_model()
                instance.load_model()
                self.assertEqual(load.call_count, 1)
                fresh = path.with_suffix('.tmp')
                fresh.write_bytes(b'second')
                fresh.replace(path)
                instance.load_model()
                self.assertEqual(load.call_count, 2)
                path.unlink()
                from optional_inference import InferenceUnavailableError
                load.side_effect = InferenceUnavailableError('missing')
                with self.assertRaises(InferenceUnavailableError):
                    instance.load_model()
                self.assertIsNone(instance.model)

    def test_class_order_is_resolved_by_name_and_incompatible_weights_fail(self):
        from optional_inference import class_mapping
        from types import SimpleNamespace
        self.assertEqual(class_mapping(SimpleNamespace(names={0: 'Antenna', 1: 'Station'}),
                                       {3: 'Station', 4: 'Antenna'}, {3: '基站', 4: '天线'}), {0: 4, 1: 3})
        with self.assertRaisesRegex(ValueError, '类别'):
            class_mapping(SimpleNamespace(names={0: 'person'}), {3: 'Station'}, {3: '基站'})


if __name__ == '__main__':
    unittest.main()
