"""Identity evidence for layout/boot repair, NOT Android boot-slot selection."""
from android_identity import read_vendor_models


def read_models(super_row):
    evidence = {'method': 'read-only-vendor-identity', 'slots': {}}
    models = set()
    for slot in (0, 1):
        label = '_' + 'ab'[slot]
        try:
            found = read_vendor_models(super_row, slot=slot)
        except ValueError as exc:
            # Invalid LP/ext4 geometry or an unreadable vendor is not evidence
            # of a model. Never extend its mapping or substitute the live DTB.
            evidence['slots'][label] = {'error': str(exc)}
            continue
        evidence['slots'][label] = {'models': found}
        if not found or any(model not in ('A4111', 'A4112') for model in found):
            raise ValueError('修复读取到不支持或缺失的 Android 机型：'+label)
        models.update(found)
    if len(models) != 1:
        raise ValueError('无法从 Android vendor 唯一确认 A4111/A4112（两槽不可读或机型冲突）：'+str(evidence['slots']))
    return sorted(models), evidence
