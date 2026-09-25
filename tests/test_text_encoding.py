"""Exercise real decoding in an ASCII-locale child, as in affected Kodi builds."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

LIB = Path(__file__).resolve().parents[1] / 'resources/lib'


class EncodingTests(unittest.TestCase):
    def ascii_child(self, body):
        script = 'import sys\nsys.path.insert(0, %r)\n' % str(LIB)
        script += textwrap.dedent(body)
        result = subprocess.run([sys.executable, '-c', script], capture_output=True,
                                encoding='utf-8', env={**os.environ, 'LC_ALL':'C',
                                'PYTHONCOERCECLOCALE':'0', 'PYTHONUTF8':'0'})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_repair_command_utf8_output_and_input_file(self):
        self.ascii_child(r'''
            import locale,tempfile
            from pathlib import Path
            import repair
            assert locale.getpreferredencoding(False).lower() in ('ascii','ansi_x3.4-1968')
            p=repair.run(sys.executable,'-c',"print('12345678\\u6781\\u5149')")
            assert p.stdout == '12345678\u6781\u5149\n'
            with tempfile.TemporaryDirectory() as tmp:
                config=Path(tmp)/'config.ini'
                config.write_text('# \u6781\u5149\n',encoding='utf-8')
                assert config.read_text(encoding='utf-8') == '# \u6781\u5149\n'
        ''')

    def test_identity_helper_error_is_decoded_without_ascii_failure(self):
        self.ascii_child(r'''
            import tempfile
            from pathlib import Path
            from unittest.mock import patch
            import device
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'resources/emmc').mkdir(parents=True)
                helper=root/'resources/emmc/read_hardware_model.py'
                helper.write_text("import sys;sys.stderr.write('12345678\\u6781\\u5149');sys.exit(1)",encoding='utf-8')
                with patch.object(device,'__file__',str(root/'resources/lib/device.py')),patch.object(device,'ANDROID_PROPERTIES',()):
                    try:device.android_board()
                    except device.AndroidIdentityUnavailable as exc:
                        assert '12345678\u6781\u5149' in str(exc)
                    else:raise AssertionError('expected helper error')
        ''')

    def test_kernel_config_comments_and_diagnostic_traceback(self):
        self.ascii_child(r'''
            import gzip,tempfile
            from pathlib import Path
            from unittest.mock import patch
            import device,diagnostics
            with tempfile.TemporaryDirectory() as tmp:
                config=Path(tmp)/'config.gz'
                with gzip.open(config,'wt',encoding='utf-8') as f:
                    f.write('# \u6781\u5149\n'+'\n'.join(device.NG_FLAGS)+'\n')
                with patch.object(device,'KERNEL_CONFIG',config),patch.object(device.platform,'release',return_value=device.NG_RELEASE):
                    device.check_ng_kernel()
            try:raise ValueError('\u6d4b\u8bd5')
            except ValueError:
                details=diagnostics.exception_details('lighting')
                assert 'Traceback' in details and 'ValueError' in details and 'preferred_encoding' in details
        ''')
