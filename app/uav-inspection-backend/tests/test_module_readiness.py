"""Run separately: python -m unittest discover -s tests -p test_module_readiness.py.

Fixtures are deliberately not real trained weights. Tests verify installation
paths and prerequisite reporting; they make no claim about recognition quality.
"""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ModuleReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='uav-module-readiness-')
        cls.root = Path(cls.temporary.name) / 'data'
        cls.environment = patch.dict(os.environ, {'UAV_DATA_DIR': str(cls.root), 'ROSE_MODEL_PATH': ''})
        cls.environment.start()
        from fastapi.testclient import TestClient
        from heatmapapp.main import app
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.environment.stop()
        cls.temporary.cleanup()

    def setUp(self):
        from deployment_paths import DATA_ROOT
        self.assertEqual(DATA_ROOT, self.root)
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir()

    def write(self, relative, content=b'fixture'):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def status(self, dependencies=False):
        with patch('deployment_readiness.inference_dependencies_available', return_value=dependencies), \
             patch('heatmapapp.main.inference_dependencies_available', return_value=dependencies):
            response = self.client.get('/api/deployment/status')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['data']

    def upload(self, category, name, subdir=''):
        return self.client.post('/api/v1/assets/upload', data={'category': category, 'subdir': subdir},
                                files={'files': (name, b'fixture', 'application/octet-stream')})

    def test_scene_and_one_model_do_not_hide_other_module_requirements(self):
        self.write('uav-inspection-backend/fendaidata/scenes/fendai-flower.ksplat')
        self.write('uav-inspection-backend/heatmapweight/renliu.pt')
        data = self.status(dependencies=True)
        self.assertTrue(data['data_available'])
        self.assertTrue(data['inference_available'])  # Legacy summary remains compatible.
        modules = data['modules']
        self.assertEqual(len(modules), 12)
        self.assertTrue(modules['fendai']['configured'])
        self.assertFalse(modules['telecom']['configured'])
        self.assertIn('telecom-model', [item['category'] for item in modules['telecom']['missing']])
        self.assertFalse(modules['heatmap']['configured'])  # No video installed.
        self.assertEqual(modules['pest']['mode'], 'simulation')
        self.assertTrue(modules['pest']['configured'])  # Simulation needs no uploaded media or YOLO.
        self.assertEqual(modules['roof']['mode'], 'offline-results')

    def test_dedicated_model_uploads_match_detector_paths(self):
        from deployment_readiness import MODEL_PATHS
        categories = self.client.get('/api/v1/assets/categories').json()['data']
        from assetsapp.api import MODEL_FILENAMES
        for category, filename in MODEL_FILENAMES.items():
            with self.subTest(category=category):
                response = self.upload(category, filename)
                self.assertEqual(response.status_code, 200, response.text)
                key = category.removesuffix('-model')
                self.assertEqual(MODEL_PATHS[key]().read_bytes(), b'fixture')
                metadata = next(item for item in categories if item['key'] == category)
                self.assertEqual(self.root / metadata['target'] / filename, MODEL_PATHS[key]())
        installed = self.status()['models']
        self.assertTrue(all(installed[category.removesuffix('-model')] for category in MODEL_FILENAMES))

    def test_model_categories_reject_wrong_names_and_subdirectories_for_both_upload_protocols(self):
        from assetsapp.api import MODEL_FILENAMES
        for category, filename in MODEL_FILENAMES.items():
            for name, subdir in [('detector.py', ''), ('best.pt', ''), (filename, 'nested'), (filename, '../')]:
                with self.subTest(category=category, name=name, subdir=subdir):
                    self.assertEqual(self.upload(category, name, subdir).status_code, 422)
                    response = self.client.post('/api/v1/assets/upload/init', data={
                        'category': category, 'filename': name, 'size': '7', 'subdir': subdir,
                    })
                    self.assertEqual(response.status_code, 422, response.text)

    def test_chunked_model_is_visible_only_after_completion(self):
        from assetsapp import api
        from deployment_readiness import MODEL_PATHS
        with patch.object(api, 'CHUNK_SIZE', 4):
            init = self.client.post('/api/v1/assets/upload/init', data={
                'category': 'telecom-model', 'filename': 'station2-best.pt', 'size': '7',
            }).json()['data']
            for index, content in enumerate([b'fixt', b'ure']):
                response = self.client.post('/api/v1/assets/upload/chunk',
                    data={'upload_id': init['uploadId'], 'index': str(index)}, files={'file': ('chunk', content)})
                self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(self.status()['models']['telecom'])
            response = self.client.post('/api/v1/assets/upload/complete', data={'upload_id': init['uploadId']})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(MODEL_PATHS['telecom']().read_bytes(), b'fixture')
            self.assertTrue(self.status()['models']['telecom'])

    def test_telecom_missing_runtime_is_separate_from_missing_data_and_model(self):
        self.write('uav-inspection-backend/telecom-tasks/demo/video.mp4')
        self.write('uav-inspection-backend/telecom-tasks/demo/video.srt')
        self.upload('telecom-model', 'station2-best.pt')
        module = self.status()['modules']['telecom']
        self.assertFalse(module['configured'])
        self.assertEqual(len(module['missing']), 1)
        self.assertIn('推理依赖', module['missing'][0]['label'])
        self.assertTrue(self.status(dependencies=True)['modules']['telecom']['configured'])

    def test_measurement_uploads_restore_overview_and_scene_on_the_same_task(self):
        from vegetationapp.api import TASKS
        key = TASKS[0]['key']
        files = [
            ('tree-instances', 'summary.json', key, b'{}'),
            ('tree-instances', 'tree_instances.csv', key, b'tree_id,points\n'),
            ('measurement-data', 'shrub_summary.json', key, b'{}'),
            ('measurement-data', 'green_area_summary.json', key, b'{}'),
            ('measurement-data', 'scene.json', f'pointcloud_web/{key}', b'{}'),
            ('measurement-data', 'scene.ply', f'pointcloud_web/{key}', b'ply\n'),
            ('models', 'model.json', 'green_area_gnb_v1', b'{}'),
        ]
        for category, name, subdir, content in files:
            response = self.client.post('/api/v1/assets/upload', data={'category': category, 'subdir': subdir},
                                        files={'files': (name, content)})
            self.assertEqual(response.status_code, 200, response.text)
        modules = self.status()['modules']
        self.assertTrue(modules['height']['configured'])
        self.assertTrue(modules['area']['configured'])
        self.assertEqual(len(self.client.get('/api/v1/vegetation/overview').json()['data']['tasks']), 1)
        scene = self.client.get(f'/api/v1/vegetation/tasks/{key}/scene')
        self.assertEqual(scene.status_code, 200)
        self.assertEqual(self.client.get(scene.json()['data']['point_cloud_url']).content, b'ply\n')

    def test_rose_legacy_and_environment_model_paths_match_health_resolution(self):
        self.write('uav-inspection-backend/roseapp/best.pt')
        self.assertTrue(self.status()['models']['rose'])
        custom = self.write('custom/rose.pt')
        with patch.dict(os.environ, {'ROSE_MODEL_PATH': str(custom)}):
            self.assertTrue(self.status()['models']['rose'])

    def test_roof_results_must_belong_to_a_task_with_video(self):
        self.write('uav-inspection-backend/roofdata/media/video-task/video.mp4')
        self.write('uav-inspection-backend/roofdata/reports/other-task/defects.json', b'[]')
        self.write('uav-inspection-backend/roofdata/reports/other-task/telemetry.json', b'[]')
        self.assertFalse(self.status()['modules']['roof']['configured'])
        self.write('uav-inspection-backend/roofdata/reports/video-task/defects.json', b'[]')
        self.write('uav-inspection-backend/roofdata/reports/video-task/telemetry.json', b'[]')
        self.assertTrue(self.status()['modules']['roof']['configured'])


if __name__ == '__main__':
    unittest.main()
