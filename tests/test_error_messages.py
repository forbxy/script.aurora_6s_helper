"""UI summaries must retain actionable failures without traceback/code leakage."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch,MagicMock
LIB=Path(__file__).resolve().parents[1]/'resources/lib'
sys.path.insert(0,str(LIB))
from diagnostics import user_message

class ErrorMessageTests(unittest.TestCase):
    def test_reported_mount_traceback(self):
        raw='Traceback (most recent call last):\n  File "inspect_storage.py", line 29, in inspect\n    raise ValueError("Nested filesystem encountered: " + str(path))\nValueError: Nested filesystem encountered: /storage/videos/115open'
        message=user_message(RuntimeError(raw))
        self.assertIn('/storage/videos/115open',message)
        self.assertIn('卸载',message)
        self.assertIn('不要删除',message)
        for code in ('Traceback','raise ValueError','line 29'):
            self.assertNotIn(code,message)
    def test_chained_trace_uses_last_error(self):
        raw='Traceback (most recent call last):\nValueError: old\n\nDuring handling of the above exception, another exception occurred:\n\nTraceback (most recent call last):\nRuntimeError: 部署失败，已恢复原文件。备份：/storage/saved'
        self.assertEqual(user_message(RuntimeError(raw)),'部署失败，已恢复原文件。备份：/storage/saved')
    def test_expected_chinese_messages_preserved(self):
        for text in ('外置空间不足：需约 20.0 GiB 可用空间（本次操作暂存）','原厂系统分区标志与支持的布局不一致','备份与本机身份/容量不匹配：cid'):
            self.assertEqual(user_message(ValueError(text)),text)
    def test_unknown_programming_errors_hide_frames(self):
        for error in (KeyError('foo'),RuntimeError('Traceback (most recent call last):\n  File "x.py", line 2\nKeyError: foo')):
            msg=user_message(error)
            self.assertIn('KeyError',msg);self.assertIn('日志',msg)
            self.assertNotIn('Traceback',msg);self.assertNotIn('x.py',msg)
    def test_os_error_and_timeout(self):
        self.assertIn('空间不足',user_message(OSError(28,'No space left on device')))
        self.assertIn('不支持',user_message('[Errno 95] Operation not supported'))
        self.assertIn('超时',user_message(subprocess.TimeoutExpired(['tool'],90)))
    def test_multiline_fsck_failure_not_dumped(self):
        message=user_message('Read-only filesystem check failed: ce_storage\nlong fsck diagnostics')
        self.assertIn('ce_storage',message);self.assertNotIn('long fsck',message)
    def test_failed_status_logs_raw_but_dialog_is_clean(self):
        spec=importlib.util.spec_from_file_location('emmc_ui_errors',LIB/'emmc_ui.py')
        ui=importlib.util.module_from_spec(spec)
        xbmc=types.ModuleType('xbmc');xbmc.log=MagicMock();xbmc.LOGERROR=4
        gui=types.ModuleType('xbmcgui');dialog=MagicMock();gui.Dialog=MagicMock(return_value=dialog)
        with patch.dict(sys.modules,{'xbmc':xbmc,'xbmcgui':gui}):spec.loader.exec_module(ui)
        raw='Traceback (most recent call last):\nValueError: Nested filesystem encountered: /storage/videos/115open'
        with patch.object(ui,'call',return_value={'phase':'needs-recovery','message':raw,'job':'/storage/job'}):ui.show_status()
        xbmc.log.assert_called_once();self.assertIn(raw,xbmc.log.call_args.args[0])
        message=dialog.ok.call_args.args[1]
        self.assertNotIn('Traceback',message);self.assertIn('可能已修改 eMMC',message);self.assertIn('/storage/job/worker.log',message)

if __name__=='__main__':unittest.main()
