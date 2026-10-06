#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小米路由器 TFTP 软砖救援服务器（DHCP + TFTP 二合一）—— 按 OpenWrt 官方 wiki 参数

OpenWrt wiki (toh/xiaomi/ax3000t) 原文关键点：
  1) 恢复时路由器在 **LAN 口**通过 DHCP 拿 IP（网线不能插 WAN 口）；
  2) TFTP 服务器（= DHCP 服务器所在机器）IP 官方给的是 **192.168.31.100**
     （另有部分机型为 192.168.10.100，可用 `fw_printenv | grep serverip` 查）；
     官方 Linux 示例：dnsmasq --dhcp-range=192.168.31.2,192.168.31.254 --enable-tftp
  3) 路由器请求的文件名 = **它自己被分配的 IP 转十六进制 + .img**
     （例：192.168.31.2 -> C0A81F02.img；192.168.1.100 -> C0A80164.img）
     wiki 特别注明 "the file name is a very important part"，且会随已装固件版本变化。
  4) LED：橙闪 = 进入恢复并下载固件；**蓝闪 = 刷写成功、可重启**；**白灯常亮 = 文件被拒**。
  5) 刷完 bootloader 会 **halt（停机）**，必须手动断电再上电才会重启 —— 千万别按 Reset，
     按了就又重新进恢复模式，看起来像"刷失败了"。

★ 本服务器刻意实现的两个细节（都是踩过坑才加的）★

  A) **绝不无条件重发**。早期版本用一个后台线程每 0.3 秒重发"最后一块"，
     等于持续往 bootloader 灌重复包 —— 接收端缓冲错位、镜像校验失败，
     但服务器侧照样收到末块 ACK。表症非常迷惑：**每次都显示"传输完成"，路由器却始终刷不进去**。
     正确做法是标准超时重传：只有在 ACK_TIMEOUT 内没等到**期望的** ACK 才重发，
     正常路径上零重复包。（注意要忽略客户端重发的旧 ACK，只认期望的那个块号。）

  B) **强制 blksize = 512**。部分 MTK bootloader 会在 RRQ 里"请求"大 blksize（如 1456），
     但接收缓冲仍按 512 处理，导致块内容错位。用 RFC1350 默认值零协商风险，
     代价只是传输慢一点（23MB 约 15 秒）。

  另外：每个 RRQ 起独立线程 + 独立临时端口（TID），不复用共享 socket ——
  共享 socket 会导致上一轮会话的残留状态污染下一轮。

用法：
    # 1) 把原厂固件 .bin 放在本脚本同目录，或设 FIRMWARE 环境变量指定
    # 2) 给电脑网卡配静态 IP：HOST_IP（默认 192.168.31.100/24）
    #    Windows 下还需放行入站 UDP 67/69（防火墙默认只放行 67）
    # 3) python tftp_rescue_server.py
    #    -> 路由器断电，按住 Reset 通电，橙灯开始闪后松手，全程别碰
    #    -> 看到蓝灯闪 = 刷写成功 -> 拔电源、等 10 秒、直接插回（不要碰 Reset）
"""
import os
import sys
import socket
import struct
import threading
import time

# ---------- 可通过环境变量覆盖的参数 ----------
HOST_IP = os.environ.get("HOST_IP", "192.168.31.100")   # 服务器地址（配在电脑网卡上）
ROUTER_IP = os.environ.get("ROUTER_IP", "192.168.31.2")  # 分配给路由器的地址
BOOTFILE = os.environ.get("BOOTFILE", "")                # 留空则按 ROUTER_IP 自动推导

BASEDIR = os.path.dirname(os.path.abspath(__file__))
FIRMWARE = os.environ.get("FIRMWARE", os.path.join(BASEDIR, "stock-firmware.bin"))
LOGPATH = os.environ.get("LOG", os.path.join(BASEDIR, "rescue.log"))

BLKSIZE = 512        # RFC1350 默认值，兼容性最高（见上文 ★B）
ACK_TIMEOUT = 1.5    # 秒；超时未收到期望 ACK 才重发（正常路径零重复包，见上文 ★A）
MAX_RETRY = 20       # 单块最多重传次数


def _auto_bootfile(ip):
    """路由器请求的文件名 = 自己被分配 IP 的十六进制 + .img"""
    return "".join("%02X" % int(x) for x in ip.split(".")) + ".img"


if not BOOTFILE:
    BOOTFILE = _auto_bootfile(ROUTER_IP)

_log_lock = threading.Lock()


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = "[%s] %s" % (ts, msg)
    with _log_lock:
        print(line, flush=True)
        try:
            with open(LOGPATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


# ===================== DHCP =====================
def dhcp_options_parse(data):
    opts = {}
    j = 240
    while j < len(data):
        if data[j] == 255:
            break
        if data[j] == 0:
            j += 1
            continue
        if j + 1 >= len(data):
            break
        opt = data[j]
        ln = data[j + 1]
        opts[opt] = data[j + 2:j + 2 + ln]
        j += 2 + ln
    return opts


def build_dhcp_reply(xid, mac, msg_type, is_request):
    pkt = bytearray(240)
    pkt[0] = 2      # BOOTREPLY
    pkt[1] = 1      # ethernet
    pkt[2] = 6      # hlen
    pkt[4:8] = xid
    pkt[10] = 0x80  # broadcast flag
    pkt[16:20] = socket.inet_aton(ROUTER_IP)   # yiaddr  = 给路由器的地址
    pkt[20:24] = socket.inet_aton(HOST_IP)     # siaddr  = next-server (TFTP)
    pkt[28:34] = mac
    pkt[236:240] = b'\x63\x82\x53\x63'
    o = bytearray()
    o += bytes([53, 1, 5 if is_request else 2])                 # ACK / OFFER
    o += bytes([54, 4]) + socket.inet_aton(HOST_IP)             # server-id
    o += bytes([51, 4]) + struct.pack("!I", 86400)              # lease
    o += bytes([1, 4]) + socket.inet_aton("255.255.255.0")      # mask
    o += bytes([3, 4]) + socket.inet_aton(HOST_IP)              # router
    o += bytes([6, 4]) + socket.inet_aton(HOST_IP)              # dns
    o += bytes([66, len(HOST_IP)]) + HOST_IP.encode()           # tftp-server-name
    o += bytes([67, len(BOOTFILE)]) + BOOTFILE.encode()         # bootfile-name
    o += bytes([255])
    return bytes(pkt) + bytes(o)


def dhcp_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # ⚠️ Windows 下必须开 SO_BROADCAST，否则发广播包直接 WinError 10013
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.bind(("0.0.0.0", 67))
    except OSError as e:
        log("DHCP 绑定失败: %s" % e)
        return
    log("DHCP 服务已启动 (UDP/67): 路由器将获配 %s, 服务器 %s" % (ROUTER_IP, HOST_IP))
    seen = set()
    while True:
        try:
            data, addr = sock.recvfrom(4096)
        except OSError:
            break
        if len(data) < 240 or data[0] != 1:
            continue
        if data[236:240] != b'\x63\x82\x53\x63':
            continue
        xid = data[4:8]
        mac = data[28:34]
        opts = dhcp_options_parse(data)
        mt = opts.get(53, b'\x00')[0]
        if mt in (1, 3):
            tag = "DISCOVER" if mt == 1 else "REQUEST"
            key = (mac.hex(), mt)
            if key not in seen:
                seen.add(key)
                log("收到路由器 DHCP %s (MAC=%s) -> 分配 %s" % (tag, mac.hex(), ROUTER_IP))
            try:
                sock.sendto(build_dhcp_reply(xid, mac, mt, mt == 3), ("255.255.255.255", 68))
            except OSError as e:
                log("DHCP 回复失败: %s" % e)


# ===================== TFTP =====================
def handle_transfer(client, fname, req_opts):
    """每个 RRQ 一个独立线程 + 独立临时端口（TID），逐块等 ACK，超时重传。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 0))
    s.settimeout(ACK_TIMEOUT)
    filesz = os.path.getsize(FIRMWARE)
    t0 = time.time()
    f = None
    try:
        log("★ 路由器请求固件: 文件名=%r (忽略命名差异, 一律返回 %d 字节)"
            % (fname, filesz))
        if req_opts:
            log("  客户端协商选项: %s" % req_opts)
        f = open(FIRMWARE, "rb")

        # 客户端请求了选项 -> 用 OACK 应答；blksize 固定回 512
        if req_opts:
            oack = bytearray(struct.pack("!H", 6))
            if 'blksize' in req_opts:
                oack += b"blksize\x00" + str(BLKSIZE).encode() + b"\x00"
            if 'tsize' in req_opts:
                oack += b"tsize\x00" + str(filesz).encode() + b"\x00"
            pkt = bytes(oack)
            for attempt in range(MAX_RETRY):
                s.sendto(pkt, client)
                try:
                    r, a = s.recvfrom(2048)
                except socket.timeout:
                    continue
                if a[0] != client[0] or a[1] != client[1]:
                    continue
                if len(r) >= 4 and struct.unpack("!H", r[:2])[0] == 4 \
                        and struct.unpack("!H", r[2:4])[0] == 0:
                    break
                if len(r) >= 2 and struct.unpack("!H", r[:2])[0] == 5:
                    log("  客户端返回 ERROR: %r" % r[4:])
                    return
            else:
                log("  OACK 未获确认, 放弃本次传输")
                return

        block = 1
        sent_blocks = 0
        retries = 0
        while True:
            chunk = f.read(BLKSIZE)
            if not chunk:
                break
            pkt = struct.pack("!HH", 3, block) + chunk
            ok = False
            for attempt in range(MAX_RETRY):
                s.sendto(pkt, client)
                if attempt > 0:
                    retries += 1
                try:
                    r, a = s.recvfrom(2048)
                except socket.timeout:
                    continue
                if a[0] != client[0] or a[1] != client[1]:
                    continue
                if len(r) < 4:
                    continue
                opc = struct.unpack("!H", r[:2])[0]
                if opc == 5:
                    log("  客户端返回 ERROR(块%d): %r" % (block, r[4:]))
                    return
                if opc == 4 and struct.unpack("!H", r[2:4])[0] == (block & 0xFFFF):
                    ok = True
                    break
                # 其余（含客户端重发的旧 ACK）一律忽略，只认期望的块号
            if not ok:
                log("  ✗ 块 %d 重传 %d 次仍无 ACK, 传输中止" % (block, MAX_RETRY))
                return
            sent_blocks += 1
            if sent_blocks % 2000 == 0:
                log("  进度: %d 块 / %.1f MB / 已用 %.0f 秒 / 重传 %d 次"
                    % (sent_blocks, block * BLKSIZE / 1048576.0, time.time() - t0, retries))
            if len(chunk) < BLKSIZE:
                log("✔ 末块 %d 已确认 —— 固件传输彻底完成 (%d 块, %.1f MB, 用时 %.1f 秒, 重传 %d 次)"
                    % (block, sent_blocks, filesz / 1048576.0, time.time() - t0, retries))
                log(">>> 现在应看到灯由橙闪转为【蓝闪】= 刷写成功；若【白灯常亮】= 文件被拒")
                log(">>> 收尾：拔电源 -> 等 10 秒 -> 直接插回。**不要按 Reset**")
                break
            block = (block + 1) & 0xFFFF
    except Exception as e:
        log("传输异常: %r" % e)
    finally:
        try:
            if f:
                f.close()
        except Exception:
            pass
        try:
            s.close()
        except Exception:
            pass


def tftp_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", 69))
    except OSError as e:
        log("TFTP 绑定失败: %s" % e)
        return
    log("TFTP 服务已启动 (UDP/69): 提供 %s (%d 字节), 块大小 %d"
        % (os.path.basename(FIRMWARE), os.path.getsize(FIRMWARE), BLKSIZE))
    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except OSError:
            break
        if len(data) < 4:
            continue
        if struct.unpack("!H", data[:2])[0] != 1:   # 只要 RRQ
            continue
        end = data.find(b'\x00', 2)
        if end < 0:
            continue
        fname = data[2:end].decode('ascii', 'ignore')
        rest = data[end + 1:]
        mode_end = rest.find(b'\x00')
        rest = rest[mode_end + 1:] if mode_end >= 0 else b''
        parts = [p for p in rest.decode('ascii', 'ignore').split('\x00') if p]
        req_opts = {}
        for k in range(0, len(parts) - 1, 2):
            req_opts[parts[k].lower()] = parts[k + 1]
        log("收到 TFTP 请求 (来自 %s:%d)" % (addr[0], addr[1]))
        threading.Thread(target=handle_transfer, args=(addr, fname, req_opts), daemon=True).start()


def _supervise(fn, name):
    """服务线程守护：内部异常退出后 1 秒自动重启。

    踩过的坑：曾经 TFTP 线程在某次传输结束后抛异常静默死亡，
    进程和端口都还在（全局 sock 未释放），DHCP 线程照常工作 ——
    表现是路由器能拿到 IP 却再也请求不到固件，极难发现。
    """
    while True:
        try:
            fn()
        except Exception as e:
            log("%s 线程异常, 1 秒后自动重启: %r" % (name, e))
            time.sleep(1)


def main():
    if not os.path.exists(FIRMWARE):
        log("错误: 找不到固件 %s" % FIRMWARE)
        sys.exit(1)
    log("=" * 62)
    log("小米路由器 TFTP 救援服务器 —— 按 OpenWrt 官方参数")
    log("  服务器地址 : %s" % HOST_IP)
    log("  路由器获配 : %s  -> 其请求的文件名应为 %s" % (ROUTER_IP, BOOTFILE))
    log("  固件       : %s (%d 字节)" % (os.path.basename(FIRMWARE), os.path.getsize(FIRMWARE)))
    log("  块大小     : %d 字节 / 每块独立超时重传 / 正常路径零重复包" % BLKSIZE)
    log("=" * 62)
    log(">>> 操作: 网线插 LAN 口 -> 路由器按住 Reset 通电 -> 橙灯闪后松手 -> 全程不要碰")
    threading.Thread(target=_supervise, args=(dhcp_server, "DHCP"), daemon=True).start()
    threading.Thread(target=_supervise, args=(tftp_server, "TFTP"), daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("服务器已停止")


if __name__ == "__main__":
    main()
