"""Record traceback and encoding context without changing Kodi's global locale."""
import json
import locale
import os
import sys
import traceback


def exception_details(context):
    info = {
        'python': sys.version,
        'preferred_encoding': locale.getpreferredencoding(False),
        'filesystem_encoding': sys.getfilesystemencoding(),
        'utf8_mode': sys.flags.utf8_mode,
        'locale': {key: os.environ.get(key) for key in
                   ('LANG', 'LC_ALL', 'LC_CTYPE', 'PYTHONUTF8', 'PYTHONIOENCODING')},
    }
    return '[Aurora6S] ' + context + '\n' + json.dumps(info, ensure_ascii=True) + '\n' + traceback.format_exc()
