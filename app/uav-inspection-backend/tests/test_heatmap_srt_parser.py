import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from heatmapapp.utils.srt_parser import parse_dji_srt_file


class HeatmapSrtParserTests(unittest.TestCase):
    def parse(self, content):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'flight.srt'
            path.write_text(content, encoding='utf-8-sig')
            return parse_dji_srt_file(str(path))

    def test_multiline_dji_timestamp_and_gps_are_parsed_together(self):
        data = self.parse('1\n00:00:00,000 --> 00:00:00,999\n<font>FrameCnt: 31\n'
                          '2026-10-27 17:00:01.000\n[latitude: 24.8334] [longitude: 102.8399] '
                          '[rel_alt: 20 abs_alt: 1900] [gb_yaw: 30 gb_pitch: -45 gb_roll: 2]</font>\n')
        self.assertEqual(data[31]['latitude'], 24.8334)
        self.assertEqual(data[31]['altitude'], 20)
        self.assertEqual(data[31]['pitch'], -45)
        self.assertEqual(data[31]['timestamp'], '2026-10-27 17:00:01.000')

    def test_compact_subtitles_use_sequence_when_frame_count_is_absent(self):
        data = self.parse('7\n00:00:00,000 --> 00:00:01,000\n'
                          '[latitude: 24] [longitude: 102] [rel_alt: 10] [gb_pitch: -90]\n')
        self.assertEqual(data[7]['frameId'], 7)
        self.assertEqual(data[7]['yaw'], 0)

    def test_bad_or_missing_gps_blocks_do_not_abort_following_valid_blocks(self):
        data = self.parse('not-a-number\n00:00:00,000 --> 00:00:01,000\ninvalid\n \n'
                          '2\n00:00:01,000 --> 00:00:02,000\nFrameCnt: 2\nno GPS\n\n'
                          '3\n00:00:02,000 --> 00:00:03,000\nFrameCnt: 3\n'
                          '[latitude: 24] [longitude: 102] [rel_alt: 5] [gb_pitch: -45]\n')
        self.assertEqual(list(data), [3])


if __name__ == '__main__':
    unittest.main()
