"""编码回归调度：已知 GPU 故障时不得启动新的用例。"""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from run_video_encode_test import HardwareFault, run_cases


class EncodeRunnerTests(unittest.TestCase):
    def test_gpu_fault_prevents_launch_for_main10_and_eight_bit(self):
        for label, options in (
            ('hardware-hevc', ['--hardware', 'hevc']),
            ('main10-hardware-hevc', ['--hardware', 'hevc', '--main10']),
            ('main10-auto-hevc', ['--auto', 'hevc', '--main10']),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as work:
                monitor = Mock()
                monitor.check.side_effect = HardwareFault('GPU reset')
                with patch('run_video_encode_test.subprocess.Popen') as launch:
                    launch.return_value.returncode = -9
                    with self.assertRaises(HardwareFault):
                        run_cases(SimpleNamespace(timeout=1), Path(work), 'unused',
                                  [(label, options)], monitor, [])
                    launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
