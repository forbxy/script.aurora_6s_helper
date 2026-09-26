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

class RecoveryUITests(unittest.TestCase):
    def load_ui(self):
        path=Path(__file__).resolve().parents[1]/'resources/lib/emmc_ui.py'
        spec=importlib.util.spec_from_file_location('emmc_recovery_ui_test',path)
        ui=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'xbmc':types.ModuleType('xbmc'),'xbmcgui':types.ModuleType('xbmcgui')}):spec.loader.exec_module(ui)
        return ui

    def test_repair_confirmation_and_cancel(self):
        from unittest.mock import MagicMock
        for accepted in (False,True):
            ui=self.load_ui();dialog=MagicMock();dialog.select.return_value=list(ui.TITLES).index('repair');dialog.yesno.return_value=accepted
            progress=MagicMock();report={'android_models':['A4111'],'emmc_bytes':64*1024**3,'os_release':{'DISTRO_DEVICE':'Amlogic-no'},'ota_repair':{'partitions':[{'size':1024**3},{'size':40*1024**3},{'size':12*1024**3}]}}
            result={'job':'test','runtime':'test'}
            with patch.object(ui.xbmcgui,'Dialog',return_value=dialog,create=True),patch.object(ui.xbmcgui,'DialogProgress',return_value=progress,create=True),patch.object(ui,'call',side_effect=[report,result]) as call,patch.object(ui,'run_foreground') as foreground:
                ui.main()
                self.assertIn('不主动重置 Android',dialog.yesno.call_args.args[1]);self.assertIn('不修改启动环境或 A/B 槽位',dialog.yesno.call_args.args[1])
                self.assertIn(ui.OPERATION_NOTICE,dialog.yesno.call_args.args[1])
                if accepted:
                    self.assertEqual(call.call_args.args,('submit','repair','--accept-risk'));foreground.assert_called_once_with(result,ui.TITLES['repair'])
                else:
                    self.assertEqual(call.call_count,1);foreground.assert_not_called()

    def test_status_menu_index_after_new_action(self):
        from unittest.mock import MagicMock
        ui=self.load_ui();dialog=MagicMock();dialog.select.return_value=len(ui.TITLES)
        with patch.object(ui.xbmcgui,'Dialog',return_value=dialog,create=True),patch.object(ui,'show_status') as show:
            ui.main();show.assert_called_once()
