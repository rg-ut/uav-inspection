
import base64
import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / 'fixtures'
CATEGORY = 'plate-data-package'


def make_package(run, video, seconds, missing=None, forbidden=False):
    video_ref = '/api/v1/plate/media/'
    documents = {
        'timeline.json': {'mission': {'run': run}, 'video': {
            'src': video_ref + 'replay.mp4', 'poster': video_ref + 'poster.jpg', 'seconds': seconds, 'offset_s': 1,
        }, 'phases': [], 'events': []},
        'coverage.json': {'image': video_ref + 'heatmap.png', 'rows': [], 'slots': [], 'coverage': [],
                          'rviz': {'run': run, 'src': video_ref + 'rviz.mp4',
                                   'poster': video_ref + 'poster.jpg', 'seconds': seconds, 'offset_s': -1}},
        'acceptance.json': {'batch': {'mission_id': run}, 'available': True,
                            'vehicles': [{'car': 'test', 'card': 'test.jpg'}], 'summary': [], 'violations': []},
        'media/path.json': {'source': run, 'area': {}, 'cars': [], 'steps': [{'t': 1, 'x': 0, 'y': 0}]},
    }
    files = {name: json.dumps(value).encode() for name, value in documents.items()}
    files.update({'media/replay.mp4': video, 'media/rviz.mp4': video, 'media/poster.jpg': b'image',
                  'media/heatmap.png': b'image', 'cards/test.jpg': b'image'})
    if missing:
        del files[missing]
    if forbidden:
        files['../unwanted.txt'] = b'unwanted'
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as out:
        for name, content in files.items():
            item = tarfile.TarInfo('platedata/' + name)
            item.size = len(content)
            out.addfile(item, io.BytesIO(content))
    return archive.getvalue()


def wrap(package, wrong_digest=False):
    archive = io.BytesIO()
    digest = '0' * 64 if wrong_digest else hashlib.sha256(package).hexdigest()
    with zipfile.ZipFile(archive, 'w') as out:
        out.writestr('handoff/batch.tar.gz', package)
        out.writestr('handoff/SHA256SUMS.txt', digest + '  batch.tar.gz\n')
        out.writestr('handoff/install.sh', 'this file is not extracted')
    return archive.getvalue()


class PlatePackageUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import cv2
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.enterClassContext(patch.dict(os.environ, {'UAV_DATA_DIR': cls.temp.name,
            'UAV_CHUNK_SIZE': '8192'}))
        from assetsapp.api import router as assets_router
        from authapp.api import router as auth_router
        from plateapp.api.detect import router as plate_router
        app = FastAPI()
        app.include_router(assets_router, prefix='/api/v1/assets')
        app.include_router(auth_router, prefix='/api/v1/auth')
        app.include_router(plate_router, prefix='/api/v1/plate')
        cls.client = cls.enterClassContext(TestClient(app))
        cls.second = cls.enterClassContext(TestClient(app))
        cls.reader = cls.enterClassContext(TestClient(app))
        for client, username in ((cls.client, 'operator'), (cls.second, 'second_operator'), (cls.reader, 'reader')):
            response = client.post('/api/v1/auth/register', json={'username': username, 'password': 'unit-password-123'})
            assert response.status_code == 200, response.text
        cls.video = base64.b64decode((FIXTURES / 'video.mp4.b64').read_text())
        path = Path(cls.temp.name) / 'fixture.mp4'
        path.write_bytes(cls.video)
        capture = cv2.VideoCapture(str(path))
        try:
            cls.seconds = capture.get(cv2.CAP_PROP_FRAME_COUNT) / capture.get(cv2.CAP_PROP_FPS)
        finally:
            capture.release()

    def stream(self, content, name='batch.tar.gz'):
        fields = {'category': CATEGORY, 'filename': name, 'size': len(content)}
        init = self.client.post('/api/v1/assets/upload/init', data=fields)
        self.assertEqual(init.status_code, 200, init.text)
        info = init.json()['data']
        identifier = info['uploadId']
        size = info['chunkSize']
        for index, start in enumerate(range(0, len(content), size)):
            response = self.client.post('/api/v1/assets/upload/chunk',
                data={'upload_id': identifier, 'index': index}, files={'file': (name, content[start:start+size])})
            self.assertEqual(response.status_code, 200, response.text)
        return identifier

    def complete(self, identifier):
        return self.client.post('/api/v1/assets/upload/complete', data={'upload_id': identifier})

    def test_publish_restore_retry_and_reject_invalid_packages(self):
        status_url = '/api/v1/assets/plate-data'
        before = self.client.get(status_url).json()['data']['active']['run']
        first = make_package('test_batch_1', self.video, self.seconds)
        status = self.client.get(status_url).json()['data']
        self.assertTrue(status['canPublish'])
        self.assertTrue(self.reader.get(status_url).json()['data']['canPublish'])
        reader_init = self.reader.post('/api/v1/assets/upload/init',
            data={'category': CATEGORY, 'filename': 'batch.tar.gz', 'size': len(first)})
        self.assertEqual(reader_init.status_code, 200)
        first_id = self.stream(wrap(first), 'handoff.zip')
        self.assertEqual(self.second.get('/api/v1/assets/upload/status',
            params={'upload_id': first_id}).status_code, 403)
        response = self.complete(first_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['batch']['run'], 'test_batch_1')
        self.assertEqual(self.complete(first_id).json(), response.json())
        bundle = self.reader.get('/api/v1/plate/replay-bundle').json()['data']
        self.assertEqual(bundle['replay']['mission']['run'], 'test_batch_1')
        self.assertEqual(bundle['coverage']['rviz']['run'], 'test_batch_1')
        self.assertIn('/revisions/' + first_id + '/', bundle['acceptance']['vehicles'][0]['card_url'])
        video = bundle['replay']['video']['src']
        self.assertEqual(self.reader.get(video, headers={'Range': 'bytes=0-63'}).status_code, 206)

        invalid_inputs = [
            ('bad.tar.gz', make_package('missing_rviz', self.video, self.seconds, missing='media/rviz.mp4')),
            ('bad.tar.gz', make_package('missing_card', self.video, self.seconds, missing='cards/test.jpg')),
            ('bad.tar.gz', make_package('bad_path', self.video, self.seconds, forbidden=True)),
            ('bad.zip', wrap(first, wrong_digest=True)),
        ]
        for name, bad in invalid_inputs:
            with self.subTest(name=name, size=len(bad)):
                failure = self.complete(self.stream(bad, name))
                self.assertEqual(failure.status_code, 422, failure.text)
                self.assertEqual(self.client.get(status_url).json()['data']['active']['run'], 'test_batch_1')

        second_id = self.stream(make_package('test_batch_2', self.video, self.seconds))
        self.assertEqual(self.complete(second_id).status_code, 200)
        self.assertEqual(self.client.get(status_url).json()['data']['previous']['run'], 'test_batch_1')
        self.assertEqual(self.reader.get(video, headers={'Range': 'bytes=0-63'}).status_code, 206)
        self.assertEqual(self.reader.post(status_url + '/restore').status_code, 200)
        self.assertEqual(self.client.get(status_url).json()['data']['active']['run'], 'test_batch_1')
        self.assertNotEqual(before, 'test_batch_1')
