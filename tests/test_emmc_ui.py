"""The Kodi interpreter may use ASCII even when the worker emits UTF-8."""
import importlib.util
import json
import locale
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

class EncodingTests(unittest.TestCase):
    def test_status_and_error_under_ascii_locale(self):
        path=Path(__file__).resolve().parents[1]/'resources/lib/emmc_ui.py'
        spec=importlib.util.spec_from_file_location('emmc_ui_test',path)
        ui=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'xbmc':types.ModuleType('xbmc'),'xbmcgui':types.ModuleType('xbmcgui')}):
            spec.loader.exec_module(ui)
        with tempfile.TemporaryDirectory() as folder:
            worker=Path(folder)/'worker.py'
            worker.write_text("import json,sys\nif sys.argv[1]=='error':\n print('备份错误',file=sys.stderr);sys.exit(1)\nprint(json.dumps({'message':'正在备份'},ensure_ascii=False))\n",encoding='utf-8')
            with patch.object(ui,'WORKER',worker),patch.object(locale,'getencoding',return_value='ANSI_X3.4-1968'),patch.dict(ui.os.environ,{'LC_ALL':'C','PYTHONUTF8':'0','PYTHONCOERCECLOCALE':'0'}):
                self.assertEqual(ui.call('status'),{'message':'正在备份'})
                with self.assertRaisesRegex(RuntimeError,'备份错误'):ui.call('error')
