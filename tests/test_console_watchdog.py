"""控制台 WebView2 看门狗的分支回归测试（v1.3.7）。

不启动 GUI：用假的 Manager 实例把 `_wv2_alive` / `_restart_process` 换成桩，
再把 `time.sleep` 短路，几毫秒就能跑完本来要等几十秒的等待逻辑。

用标准库 unittest（项目 venv 没装 pytest），两种跑法都行：
    python -m unittest discover -s tests -v
    python -m pytest tests/ -q          # 装了 pytest 时

重点守着三个曾经真实踩过的坑：
  * 正常窗口不能被误重启；
  * 「读不到 CoreWebView2」(跨线程抛异常) 不等于坏了；
  * `_on_page` 只是「打算加载哪一页」，黑屏时也是 True，绝不能当健康证据。
"""
from __future__ import annotations

import tempfile
import time
import unittest
import unittest.mock as mock
from pathlib import Path

from desktop import manager as M


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ckt-wd-")
        self._orig = (M.RESTART_FILE, M.log)
        M.RESTART_FILE = Path(self._tmp.name) / "restarts.json"
        M.log = lambda msg: None

    def tearDown(self):
        M.RESTART_FILE, M.log = self._orig
        self._tmp.cleanup()

    def make(self, alive):
        """造一个不跑 __init__ 的 Manager；alive 可以是常量或 callable。"""
        m = M.Manager.__new__(M.Manager)
        m._quitting = False
        m._wv2_revived = 0
        m._overlay_ok = False
        m._on_page = False
        m._wv2_alive = alive if callable(alive) else (lambda: alive)
        m._wv2_reinit = lambda: "已投递"
        m._restart_process = mock.Mock(return_value=True)
        m._console_dead_notice = mock.Mock()
        return m

    def run_watchdog(self, m):
        """短路 sleep，但让 monotonic 跟着推进（deadline 逻辑仍然生效）。"""
        clock = [time.monotonic()]

        def fake_sleep(sec):
            clock[0] += sec

        with mock.patch.object(M.time, "sleep", fake_sleep), \
             mock.patch.object(M.time, "monotonic", lambda: clock[0]):
            m._console_watchdog()

    def test_healthy_window_is_never_restarted(self):
        m = self.make(True)
        self.run_watchdog(m)
        self.assertEqual(m._restart_process.call_count, 0)

    def test_page_callback_means_healthy_even_if_probe_says_failed(self):
        """页面都回报就绪了，就算属性读失败也不该动手。"""
        m = self.make(False)
        m._overlay_ok = True
        self.run_watchdog(m)
        self.assertEqual(m._restart_process.call_count, 0)

    def test_on_page_flag_must_not_be_treated_as_health(self):
        """回归：_on_page 在创建窗口前就设好了，黑屏时同样是 True。

        曾经把它算进 `_console_healthy()`，导致真实 WebView2 初始化失败时
        看门狗直接放行、一条日志都不写，黑窗永远留在屏幕上。
        """
        m = self.make(False)
        m._on_page = True
        m._overlay_ok = False
        self.assertFalse(m._console_healthy())
        self.run_watchdog(m)
        self.assertEqual(m._restart_process.call_count, 1)

    def test_unknown_probe_waits_then_falls_back(self):
        """控件拿不到 / 跨线程读失败 = 未知，等满 30 秒才兜底。"""
        m = self.make(None)
        self.run_watchdog(m)
        self.assertEqual(m._restart_process.call_count, 1)

    def test_reinit_success_stops_restart(self):
        calls = [0]

        def recover():
            calls[0] += 1
            return calls[0] > 15     # 第 2 次重试期间活过来

        m = self.make(recover)
        self.run_watchdog(m)
        self.assertEqual(m._restart_process.call_count, 0)
        self.assertEqual(m._wv2_revived, 1)

    def test_wv2_alive_three_states(self):
        # 用干净实例：make() 会把 _wv2_alive 换成桩，这里要测的正是真方法
        m = M.Manager.__new__(M.Manager)
        m.console = object()
        m._wv2_control = lambda: None
        self.assertIsNone(m._wv2_alive())

        class Boom:
            @property
            def CoreWebView2(self):
                raise RuntimeError("cross-thread access")

        m._wv2_control = lambda: Boom()
        self.assertIsNone(m._wv2_alive(), "跨线程读失败≠控件坏了")

        class Null:
            CoreWebView2 = None

        m._wv2_control = lambda: Null()
        self.assertIs(m._wv2_alive(), False)

        class Fine:
            CoreWebView2 = object()

        m._wv2_control = lambda: Fine()
        self.assertIs(m._wv2_alive(), True)

    def test_restart_is_capped_at_two_per_ten_minutes(self):
        m = self.make(True)
        self.assertTrue(m._restart_allowed())
        self.assertTrue(m._restart_allowed())
        self.assertFalse(m._restart_allowed())

    def test_heal_then_show_displays_without_restart_when_recovered(self):
        m = self.make(True)
        calls = [0]

        def recover():
            calls[0] += 1
            return calls[0] > 3

        m._wv2_alive = recover
        m._wv2_dead = True
        m._console_open = False
        m.console = mock.Mock()
        m.console_boot = mock.Mock()
        with mock.patch.object(M.time, "sleep", lambda *_: None):
            m._heal_console_then_show()
        self.assertEqual(m.console.show.call_count, 1)
        self.assertEqual(m._restart_process.call_count, 0)
        self.assertFalse(m._wv2_dead)


if __name__ == "__main__":
    unittest.main(verbosity=2)
