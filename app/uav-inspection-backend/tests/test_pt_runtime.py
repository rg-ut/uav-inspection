"""Opt-in real CPU tensor inference: UAV_TEST_PT=1 python -m unittest discover -s tests -p test_pt_runtime.py.

Requires requirements-inference.txt. Generates untrained five-class YOLO weights
locally; checks upload/loading/API integration, never recognition accuracy.
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@unittest.skipUnless(os.environ.get('UAV_TEST_PT') == '1', 'opt-in CPU runtime test')
class PTRuntimeTests(unittest.TestCase):
    def test_each_module_uploads_and_runs_actual_pt_weights(self):
        import yaml
        import ultralytics
        from ultralytics import YOLO
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            'UAV_DATA_DIR': folder, 'UAV_WALL_ADMINS': 'admin', 'YOLO_AUTOINSTALL': 'false',
        }):
            config = yaml.safe_load((Path(ultralytics.__file__).parent / 'cfg/models/11/yolo11.yaml').read_text())
            config['nc'] = 5
            config['scale'] = 'n'
            source = Path(folder) / 'yolo11n-fixture.yaml'
            source.write_text(yaml.safe_dump(config))
            model = YOLO(str(source))
            from heatmapapp.main import app
            from assetsapp.api import MODEL_FILENAMES
            with TestClient(app) as client:
                login = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'admin123'})
                self.assertEqual(login.status_code, 200)
                for module, labels in {
                    'power': ['Wire', 'WirePole'], 'telecom': ['Antenna', 'Station'],
                    'pruning': ['Unpruned', 'PrunedTree', 'Pruned'], 'rose': ['bud', 'picked', 'rose'],
                    'heatmap': ['person'], 'wall': ['TileSpalling'],
                }.items():
                    with self.subTest(module=module):
                        model.model.names = dict(enumerate(labels + [f'Unused{i}' for i in range(5 - len(labels))]))
                        path = Path(folder) / f'{module}.pt'
                        model.save(str(path))
                        category = module + '-model'
                        response = client.post('/api/v1/assets/upload', data={'category': category},
                            files={'files': (MODEL_FILENAMES[category], path.read_bytes())})
                        self.assertEqual(response.status_code, 200, response.text[:300])
                        result = client.post('/api/v1/assets/check/' + module)
                        self.assertEqual(result.status_code, 200, result.text[:300])
                        self.assertTrue(result.json()['data']['model_ready'], result.text)

                # The roof workbench also accepts PT from an ordinary signed-in operator.
                registered = client.post('/api/v1/auth/register',
                    json={'username': 'roof_operator', 'password': 'fixture-password-123'})
                self.assertEqual(registered.status_code, 200, registered.text[:300])
                model.model.names = dict(enumerate(['杂草堆积', '屋面脱皮', '积水区域', '其他', '待核实']))
                path = Path(folder) / 'roof.pt'
                model.save(str(path))
                uploaded = client.post('/api/v1/assets/upload', data={'category': 'roof-model'},
                                       files={'files': ('best.pt', path.read_bytes())})
                self.assertEqual(uploaded.status_code, 200, uploaded.text[:300])
                state = client.get('/api/v1/roof/model').json()
                self.assertEqual(state['name'], 'roof-best.pt')
                self.assertTrue(state['ready'])
                invalid = client.post('/api/v1/roof/model', files={'file': ('invalid.pt', b'not a checkpoint')})
                self.assertEqual(invalid.status_code, 422, invalid.text[:300])
                check = client.post('/api/v1/assets/check/roof')
                self.assertTrue(check.json()['data']['model_ready'], check.text)


if __name__ == '__main__':
    unittest.main()
