"""Read original Android identity only; no install policy or environment access."""
import json
import os
from pathlib import Path
import stat
import sys

from aurora_emmc import MIB, parse_mpt
from android_identity import read_vendor_models


def read_models():
    disk = Path('/sys/block/mmcblk0')
    dev = os.stat('/dev/mmcblk0')
    if not stat.S_ISBLK(dev.st_mode):
        raise ValueError('eMMC is not a block device')
    if (disk / 'dev').read_text().strip() != '%d:%d' % (os.major(dev.st_rdev), os.minor(dev.st_rdev)):
        raise ValueError('eMMC device identity mismatch')
    capacity = int((disk / 'size').read_text()) * 512
    with open('/dev/mmcblk0', 'rb', buffering=0) as source:
        source.seek(36 * MIB)
        table = parse_mpt(source.read(4096), capacity)
    rows = [row for row in table['partitions'] if row['name'] == 'super']
    if len(rows) != 1:
        raise ValueError('Cannot identify original super partition')
    # This reader checks the live super device against the MPT, validates LP
    # checksums/extents and mounts only vendor as ro,noload, then cleans up.
    return read_vendor_models(rows[0])


if __name__ == '__main__':
    try:
        print(json.dumps(read_models()))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
