"""/api/v1/plate/dataset：报告「网站实际在放哪一批」。

为什么值得一个测试：这个接口存在的唯一目的就是给部署核对用，一旦它报错或
报得含糊，运维就会拿旧批次当新批次上线——而页面上两者只差顶栏一行任务编号。
所以两个分支（运行期数据包 / 镜像内兜底批次）都要钉住。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


class PlateDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 与 test_plate_replay_fallback 同样的隔离：DATA_ROOT 在首次导入时缓存，
        # 不先钉住 UAV_DATA_DIR 会把账号库写进真实数据目录。
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.environment = patch.dict(os.environ, {'UAV_DATA_DIR': cls.temp.name})
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)
        from heatmapapp.main import app
        cls.client = cls.enterClassContext(TestClient(app))

    def test_reports_packaged_fallback_batch(self):
        """空运行期目录：应报 packaged，并指出 path.json 缺失、路线是现场重建的。"""
        result = self.client.get('/api/v1/plate/dataset')
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()['data']

        self.assertEqual(data['source'], 'packaged')
        self.assertEqual(data['data_dir'], data['packaged_dir'])
        self.assertEqual(data['mission']['run'], 'p9_v2_final_20261114')
        # 镜像内批次没有 GOTO 日志，/path 会现场拼一条示意航线
        self.assertTrue(data['path']['fallback'])
        self.assertEqual(data['path']['steps'], 0)
        # 也没有 RViz 分屏录屏，/coverage 用主回放顶上
        self.assertTrue(data['rviz']['fallback'])
        self.assertIn('media/path.json', data['missing'])
        self.assertGreater(data['cards'], 0)

    def test_reports_runtime_package(self):
        """运行期目录里放一份完整包：应报 runtime、批次取自 timeline、无缺失。"""
        from plateapp.api import detect

        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / 'platedata'
            _write(pkg / 'timeline.json', {
                'mission': {'run': 'mission_test_20260101', 'id': 'mission_test_20260101'},
                'video': {'seconds': 12.5, 'offset_s': 3.5},
            })
            _write(pkg / 'coverage.json', {
                'source': {'map_dir': 'map_test'},
                'rviz': {'run': 'mission_test_20260101', 'offset_s': -1.5},
            })
            _write(pkg / 'acceptance.json', {'vehicles': [{'car': 'car_0'}]})
            _write(pkg / 'media' / 'path.json', {
                'source': 'amov007:mission_test', 'steps': [{'t': 10.0}, {'t': 20.0}],
            })
            for name in ('replay.mp4', 'heatmap.png', 'poster.jpg'):
                (pkg / 'media' / name).write_bytes(b'x')
            (pkg / 'cards').mkdir(parents=True)
            (pkg / 'cards' / 'car_0.jpg').write_bytes(b'x')

            media = pkg / 'media'
            with patch.multiple(
                detect,
                DATA_DIR=pkg,
                MEDIA_DIR=media,
                CARDS_DIR=pkg / 'cards',
                TIMELINE_PATH=pkg / 'timeline.json',
                COVERAGE_PATH=pkg / 'coverage.json',
                ACCEPTANCE_PATH=pkg / 'acceptance.json',
                PATH_PATH=media / 'path.json',
                _RUNTIME_DATA_DIR=pkg,
            ):
                data = detect._dataset_summary()

        self.assertEqual(data['source'], 'runtime')
        self.assertEqual(data['mission']['run'], 'mission_test_20260101')
        self.assertEqual(data['video'], {'seconds': 12.5, 'offset_s': 3.5})
        self.assertEqual(data['rviz'], {
            'run': 'mission_test_20260101', 'offset_s': -1.5, 'fallback': False})
        self.assertEqual(data['path'], {
            'source': 'amov007:mission_test', 'steps': 2, 'fallback': False})
        self.assertEqual(data['coverage_map'], 'map_test')
        self.assertEqual(data['cards'], 1)
        self.assertEqual(data['vehicles'], 1)
        self.assertEqual(data['missing'], [])

    def test_missing_lists_each_absent_entry(self):
        """半份包：缺什么要逐项列出来，否则页面静默少一块没人发现。"""
        from plateapp.api import detect

        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / 'platedata'
            _write(pkg / 'timeline.json', {'mission': {'run': 'mission_half'}})
            with patch.multiple(
                detect,
                DATA_DIR=pkg,
                MEDIA_DIR=pkg / 'media',
                CARDS_DIR=pkg / 'cards',
                TIMELINE_PATH=pkg / 'timeline.json',
                COVERAGE_PATH=pkg / 'coverage.json',
                ACCEPTANCE_PATH=pkg / 'acceptance.json',
                PATH_PATH=pkg / 'media' / 'path.json',
                _RUNTIME_DATA_DIR=pkg,
            ):
                data = detect._dataset_summary()

        self.assertEqual(data['source'], 'runtime')
        self.assertIn('cards/', data['missing'])
        self.assertIn('coverage.json', data['missing'])
        self.assertIn('media/replay.mp4', data['missing'])
        self.assertNotIn('timeline.json', data['missing'])


if __name__ == '__main__':
    unittest.main()
