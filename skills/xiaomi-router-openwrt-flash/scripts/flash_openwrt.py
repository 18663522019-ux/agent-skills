#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小米 AX3000T (RD03, MT7981) —— 严格按 OpenWrt 官方文档刷入 initramfs 引导固件

参考: https://openwrt.org/toh/xiaomi/ax3000t   (Installation -> stock layout)

官方步骤(原厂 stock 布局):
  /proc/cmdline 里 firmware=1 -> ubiformat /dev/mtd8 -y -f <initramfs-factory.ubi>
                                 nvram set flag_boot_rootfs=0 ; flag_last_success=0
  /proc/cmdline 里 firmware=0 -> ubiformat /dev/mtd9 -y -f <initramfs-factory.ubi>
                                 nvram set flag_boot_rootfs=1 ; flag_last_success=1
  公共项: boot_wait=on / uart_en=1 / flag_boot_success=1
          flag_try_sys1_failed=0 / flag_try_sys2_failed=0 / nvram commit

★★★ 与网上多数脚本的关键区别 ★★★
  常见写法是 `mtd -e ubi write <file> ubi`（或走 install_fw.py 的 install_method=400）。
  对 NAND 而言这**不是**正确的 UBI 写入方式：坏块表 / EC 头 / VID 头都不处理，
  结果是镜像看似写进去了、却每次都被恢复模式接管（表症：传完不启动、反复回恢复模式）。
  官方要求用 `ubiformat`，本脚本照做。

  并且只擦写"非当前运行槽"的那一个 mtd —— 原厂系统完整保留在另一槽，
  即使这一步失败，设备仍能从原槽正常启动，不会变砖。

用法:
  # 1) 先在原厂系统里开 SSH（xmir-patcher/connect.py，root 密码会被设为 root）
  # 2) 把 initramfs-factory.ubi 放进 <xmir-patcher>/../firmware/
  # 3) python flash_openwrt.py
"""
import os
import re
import sys
import glob
import time
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
# 假定本脚本位于 xmir-patcher/ 目录内；若不是，请用环境变量 XMIR_DIR 指定
XMIR_DIR = os.environ.get('XMIR_DIR', HERE)
os.chdir(XMIR_DIR)
sys.path.insert(0, XMIR_DIR)

import xmir_base
from gateway import *

# firmware/ 默认在 xmir-patcher 的上一级（与本仓库 router-flash/ 的布局一致）
FW_DIR = os.path.abspath(os.environ.get('FW_DIR', os.path.join(XMIR_DIR, '..', 'firmware')))
REMOTE_UBI = '/tmp/openwrt-initramfs.ubi'


def md5_file(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for blk in iter(lambda: f.read(chunk), b''):
            h.update(blk)
    return h.hexdigest()


def pick_ubi():
    cands = []
    for f in glob.glob(os.path.join(FW_DIR, '*initramfs-factory.ubi')):
        # ubootmod 版本只适用于已刷 OpenWrt U-Boot 的机型，stock 布局必须用原生那份
        if 'ubootmod' not in os.path.basename(f):
            cands.append(f)
    if len(cands) != 1:
        die('firmware/ 下应只有 1 个原生 initramfs-factory.ubi, 实际找到: %s' % cands)
    return cands[0]


def step(n, total, text):
    print('\n' + '-' * 62)
    print('[%d/%d] %s' % (n, total, text))
    print('-' * 62)


def main():
    fw = pick_ubi()
    fw_md5 = md5_file(fw)

    print('=' * 62)
    print('  小米 AX3000T (RD03) —— 官方方式刷入 OpenWrt 引导固件')
    print('  镜像: %s' % os.path.basename(fw))
    print('  大小: %d 字节' % os.path.getsize(fw))
    print('  MD5 : %s' % fw_md5)
    print('=' * 62)

    # ---------- 0. 复用 connect.py 已开启的 SSH 通道 ----------
    step(0, 6, '连接已开启的 SSH 通道 (connect.py 完成后 root/root, 不在此处再做 web 登录)')
    gw = create_gateway(timeout=10, die_if_sshOk=False, die_if_ftpOk=False,
                        web_login=False, try_telnet=False)
    print('SSH 通道已就绪 :', gw.ip_addr, 'port', gw.ssh_port, 'user =', gw.login)

    # ---------- 1. 判定当前运行槽 ----------
    step(1, 6, '读取启动参数, 判定当前运行槽位')
    cmdline = (gw.run_cmd('cat /proc/cmdline') or '').strip()
    print('/proc/cmdline =', cmdline)
    m = re.search(r'firmware=(\d+)', cmdline)
    if not m:
        die('无法从 /proc/cmdline 判定 firmware 槽位, 已中止(未做任何写入)')
    cur = int(m.group(1))
    if cur == 1:
        target_mtd, flag = 8, 0
    else:
        target_mtd, flag = 9, 1
    print('当前运行槽 firmware=%d  ->  将写入 /dev/mtd%d, flag_boot_rootfs=%d' % (cur, target_mtd, flag))

    mtd_tab = gw.run_cmd('cat /proc/mtd') or ''
    print(mtd_tab.strip())
    if ('mtd%d:' % target_mtd) not in mtd_tab:
        die('目标 /dev/mtd%d 不存在, 已中止' % target_mtd)

    # ---------- 2. 环境自检 ----------
    step(2, 6, '自检: ubiformat / nvram / 空余空间')
    which = (gw.run_cmd('which ubiformat nvram') or '').strip()
    print('which ubiformat nvram ->', which)
    if 'ubiformat' not in which:
        die('设备上找不到 ubiformat, 已中止(未做任何写入)')
    free = (gw.run_cmd('df -k /tmp | tail -n1') or '').strip()
    print('df /tmp ->', free)

    # ---------- 3. 上传镜像并校验 ----------
    step(3, 6, '上传 initramfs 镜像到 /tmp 并校验 MD5')
    gw.run_cmd('rm -f %s' % REMOTE_UBI)
    ok = gw.upload(fw, REMOTE_UBI, md5chk=True, verbose=1)
    if not ok:
        die('镜像上传失败或 MD5 不一致, 已中止(未做任何写入)')
    rsize = (gw.run_cmd('wc -c < %s' % REMOTE_UBI) or '').strip()
    print('远端文件大小 = %s  (本地 %d)' % (rsize, os.path.getsize(fw)))
    if rsize != str(os.path.getsize(fw)):
        die('远端/本地大小不一致, 已中止')

    # ---------- 4. ubiformat 写入目标槽 ----------
    step(4, 6, 'ubiformat 写入 /dev/mtd%d (官方指定方式)' % target_mtd)
    out = gw.run_cmd('ubiformat /dev/mtd%d -y -f %s' % (target_mtd, REMOTE_UBI), timeout=600)
    print(out)
    low = (out or '').lower()
    if 'error' in low or 'failed' in low:
        die('ubiformat 报告错误, 已中止(未修改启动标志, 设备仍会从原槽启动)')
    print('>>> ubiformat 完成')

    # ---------- 5. 设置启动标志 ----------
    step(5, 6, '写入启动标志并 commit')
    for c in [
        'nvram set boot_wait=on',
        'nvram set uart_en=1',
        'nvram set flag_boot_rootfs=%d' % flag,
        'nvram set flag_last_success=%d' % flag,
        'nvram set flag_boot_success=1',
        'nvram set flag_try_sys1_failed=0',
        'nvram set flag_try_sys2_failed=0',
        'nvram commit',
    ]:
        gw.run_cmd(c)
        print('  ', c)
    for k in ['boot_wait', 'uart_en', 'flag_boot_rootfs', 'flag_last_success',
              'flag_boot_success', 'flag_try_sys1_failed', 'flag_try_sys2_failed']:
        v = (gw.run_cmd('nvram get %s' % k) or '').strip()
        print('   回读 %-22s = %s' % (k, v))

    # ---------- 6. 重启 ----------
    step(6, 6, '重启进入 OpenWrt initramfs')
    time.sleep(1)
    gw.run_cmd('reboot', reboot=True)
    print('\n>>> 已下发 reboot。')
    print('    约 60~90 秒后, 路由器应启动到 OpenWrt 临时系统 (192.168.1.1)。')
    print('    网线请保持在路由器中间的 LAN 口, 不要插 WAN 口。')
    print('    接下来执行 sysupgrade 永久化。')


if __name__ == '__main__':
    main()
