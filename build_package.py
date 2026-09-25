"""Build a Kodi-installable ZIP in dist/ using addon.xml metadata."""
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent
EXCLUDED_DIRS = {'.git', 'dist', '.idea', '.vscode', '__pycache__',
                 '.pytest_cache', '.mypy_cache', '.ruff_cache'}
EXCLUDED_FILES = {'.gitignore', '.DS_Store'}


def get_addon_info():
    root = ET.parse(ROOT / 'addon.xml').getroot()
    addon_id, version = root.get('id'), root.get('version')
    for name, value in (('id', addon_id), ('version', version)):
        if not value or value in ('.', '..') or any(c in value for c in '/\\:'):
            raise ValueError('Invalid addon.xml ' + name)
    return addon_id, version


def zip_addon(addon_id, version):
    dist_dir = ROOT / 'dist'
    dist_dir.mkdir(exist_ok=True)
    zip_path = dist_dir / f'{addon_id}-{version}.zip'
    temporary = zip_path.with_suffix('.zip.tmp')
    print(f'Building package for {addon_id} v{version}')
    print(f'Output: {zip_path}')
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for base, dirs, files in os.walk(ROOT):
                dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS
                                 and not (Path(base) == ROOT and d == 'tests'))
                for name in sorted(files):
                    path = Path(base) / name
                    if (path == Path(__file__).resolve() or name in EXCLUDED_FILES
                            or path.suffix in ('.pyc', '.pyo')):
                        continue
                    relative = path.relative_to(ROOT)
                    print(f'Adding: {relative.as_posix()}')
                    zipf.write(path, f'{addon_id}/{relative.as_posix()}')
        os.replace(temporary, zip_path)
    finally:
        temporary.unlink(missing_ok=True)
    print('Package creation completed successfully.')
    return zip_path


if __name__ == '__main__':
    try:
        zip_addon(*get_addon_info())
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
