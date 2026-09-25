"""Preserve Linux extended attributes when the system rsync lacks -A/-X."""
import os
from pathlib import Path


def attributes(path):
    return {name:os.getxattr(path,name,follow_symlinks=False).hex()
            for name in sorted(os.listxattr(path,follow_symlinks=False))}


def copy_attributes(source,dest,update=None):
    source=Path(source);dest=Path(dest)
    if source.is_symlink() or dest.is_symlink():raise ValueError('Unsafe attribute-copy root')
    def copy(relative):
        src=source/relative;dst=dest/relative
        if src.is_symlink()!=dst.is_symlink():raise ValueError('属性复制时文件类型不一致：'+str(relative))
        wanted=attributes(src);existing=attributes(dst)
        for name in existing.keys()-wanted.keys():os.removexattr(dst,name,follow_symlinks=False)
        for name,value in wanted.items():
            if existing.get(name)!=value:os.setxattr(dst,name,bytes.fromhex(value),follow_symlinks=False)
        if attributes(dst)!=wanted:raise ValueError('扩展属性读回不一致：'+str(relative))
    # Walk the copied destination: excluded files must not be resurrected.
    copy(Path('.'));count=0
    def fail(error):raise error
    for base,dirs,files in os.walk(dest,followlinks=False,onerror=fail):
        if Path(base)==dest:dirs[:]=[n for n in dirs if n!='lost+found']
        for name in dirs+files:
            copy((Path(base)/name).relative_to(dest));count+=1
            if update and count%100==0:update('copying','复制并核对扩展属性：%d 项'%count)
    if update:update('copying','扩展属性复制与核对完成')
