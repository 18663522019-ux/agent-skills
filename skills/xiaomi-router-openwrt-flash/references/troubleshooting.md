# 排障与踩坑全记录

本文按「表症 → 真因 → 处置」组织。多数条目来自一次真实刷机（小米 AX3000T / RD03 / MT7981B，
原厂 1.0.98 → OpenWrt 25.12.5），其中包含一次完整的变砖与救援。

---

## 一、刷机阶段

### 1.1 传完不启动 / 反复掉回恢复模式 —— 刷机变砖头号真凶

**表症**：镜像上传成功、MD5 校验一致、`ubiformat`/`mtd write` 都报告成功，
重启后却始终回到恢复模式，两个网段地址（原厂 `192.168.31.1`、OpenWrt `192.168.1.1`）都不应答。

**真因**：用 `mtd -e ubi write <file> ubi`（或封装它的 `install_fw.py`，`install_method=400`）
向 NAND 上的 UBI 分区写镜像。这条路径**不处理 NAND 的坏块表、EC 头、VID 头**，
写进去的镜像在 bootloader 眼里不是合法 UBI，于是每次都被判定为启动失败。

**处置**：原厂系统里就有 `ubiformat`（`/usr/sbin/ubiformat`），用它：

```sh
# 目标槽按 /proc/cmdline 的 firmware= 定，见 1.2
ubiformat /dev/mtd9 -y -f /tmp/openwrt-...-initramfs-factory.ubi
```

自检：刷前先 `which ubiformat nvram`，两者都必须存在。

---

### 1.2 判错启动槽，写到正在运行的分区上

**表症**：刷写过程一切正常，重启后原厂/OpenWrt 全没了，或行为与预期相反。

**真因**：拿**旧备份里的 nvram 值**当判槽依据。这个值会被救援流程翻转 ——
本次实战中，15:12 的备份里是 `firmware=1 mtd=ubi1`，而 16:59 一次 TFTP 救砖之后
开机实测变成 `firmware=0 mtd=ubi`。用旧值判槽 = 写到正在运行的那一槽上。

**处置**：只认**开机后实时读到的**值：

```sh
cat /proc/cmdline | tr ' ' '\n' | grep firmware=
```

| 实测值 | 写入目标 | 需要设的 flag |
|---|---|---|
| `firmware=0` | `ubiformat /dev/mtd9` | `flag_boot_rootfs=1`、`flag_last_success=1` |
| `firmware=1` | `ubiformat /dev/mtd8` | `flag_boot_rootfs=0`、`flag_last_success=0` |

**为什么要严格只擦非当前槽**：原厂系统完整留在另一槽。哪怕这一步彻底失败，
设备仍能从原槽正常启动 —— 这是「最坏情况可回退」的物理保障，不要图省事擦两槽。

---

### 1.3 TFTP 只能救砖，不能装 OpenWrt

**表症**：按网上教程用 TFTP 传 `openwrt-...-initramfs-factory.ubi`，传完不认。

**真因**：官方只认可两条安装路径 —— ① 原厂系统内用 API-RCE 漏洞开 SSH 后刷；
② 拆机走 UART。**TFTP 的能力边界是把原厂 `.bin` 写回去救砖**。
只有已经刷了 OpenWrt U-Boot 的机型，U-Boot 才会自己去 `192.168.1.254` 拉
`...-ubootmod-initramfs-recovery.itb` 自救。stock U-Boot 不认那个文件。

---

### 1.4 网线插错口

- 刷 initramfs 阶段：必须插**中间的 LAN 口**，插 WAN 口不通。
- TFTP 恢复阶段：官方原文是 "asks for an IP address on the **LAN ports** via DHCP"。
- AX3000T 面板：背面从左到右 4 个 RJ45，**第 1 个 = WAN，第 2/3/4 = LAN**。
  （硬件上四口同规格、丝印统标 WAN/LAN 支持盲插，但**固件里角色固定**。）

---

### 1.5 空密码 SSH 连不上

initramfs 阶段的 OpenWrt root **无密码**，可以直接登。但：

- 用 `ssh -o BatchMode=yes root@192.168.1.1` —— OpenSSH 会先尝试 `none` 认证，能过。
- **`libssh2` / `ssh2-python`（xmir-patcher 自带的便携 Python 走的就是它）传空密码会直接失败**，
  这一步不能用 `gateway.py` 的连接逻辑，必须用系统 CLI ssh。

---

### 1.6 便携 Python 的旧 libssh2 与 OpenWrt 25.12 的 dropbear 做 SSH 会 KexFailureError

**表症**：xmir-patcher 自带的便携 Python 连原厂固件一切正常，连刷完的 OpenWrt 直接抛 KEX 算法集不兼容。

**处置**：连 OpenWrt 一律改用系统 OpenSSH 客户端 + 非交互喂密码。
本机没有 `sshpass`，用 `SSH_ASKPASS_REQUIRE=force`（OpenSSH 8.4+）：

```bash
printf '#!/bin/sh\necho <密码>\n' > /tmp/askpass.sh && chmod +x /tmp/askpass.sh
DISPLAY=:0 SSH_ASKPASS=/tmp/askpass.sh SSH_ASKPASS_REQUIRE=force \
  ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no \
      -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      root@192.168.1.1 '<命令>'
```

---

### 1.7 scp 传文件到 dropbear 失败

dropbear 默认不带 `sftp-server`，scp 走 sftp 协议会报错。用 cat 管道最稳：

```bash
cat openwrt-...-squashfs-sysupgrade.bin | ssh root@192.168.1.1 'cat > /tmp/fw.bin'
ssh root@192.168.1.1 'md5sum /tmp/fw.bin; wc -c < /tmp/fw.bin'   # 与本地核对后再继续
```

`sysupgrade -n` 后出现 `Commencing upgrade. Closing all shell sessions.` +
`ubus call system sysupgrade ... (Connection failed)` 是**正常**的，正在重启。

---

### 1.8 SHA256 校验脚本误报 FAIL

**表症**：明明下载完整的固件，`grep " $f\$"` 逐行比对却报不匹配。

**真因**：官方 `sha256sums` 里文件名**带 `*` 前缀**（`sha256sum` 的 binary 模式标记），
且分隔用的是**两个空格**。用 shell 字符串匹配很容易踩空。

**处置**：用 Python 按空白分割，并剥掉 `*`：

```python
for line in open('sha256sums'):
    parts = line.split(None, 1)
    if len(parts) == 2:
        h, name = parts[0], parts[1].strip().lstrip('*')
```

---

### 1.9 下载到的 ubootmod 镜像体积不对

曾出现下载「成功」但文件只有 2.9MB（应为 10.3MB）—— 典型的传输截断。
**必须**用同目录 `sha256sums` 校验，不能只看文件是否存在。

---

## 二、TFTP 救援阶段

### 2.1 服务器收到末块 ACK，路由器却还在恢复模式

**表症**：日志显示传输完成、重复块 0，但路由器刷不进去，灯不变蓝。

**真因（本次实战的真凶）**：TFTP 服务器有一个后台线程**每 0.3 秒无条件重发最后一个包**。
这等于持续往 bootloader 灌重复包 —— MTK bootloader 的接收缓冲错位、镜像校验失败，
但服务器侧照样能收到末块 ACK。**表症和真因完全脱节**，极易误判为"文件不对"。

**处置**：改成标准超时重传 —— 只有 1.5 秒内没等到**期望的** ACK 才重发。
注意还要**忽略客户端重发的旧 ACK**，只认期望块号。正常路径上应该零重复包。

> 教训：修掉这个 bug 之后，即使 blksize 协商到 1456 也能一次刷成。
> 当时曾怀疑 blksize 1456 导致错位，是误判。

### 2.2 `WinError 10013`

Windows 下 UDP socket 发广播包必须显式开 `SO_BROADCAST`：

```python
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
```

### 2.3 「本机自测能通」≠「对方能连上我的服务端口」

TFTP 走 UDP/69，Windows 防火墙默认不放过入站（UDP/67 因为有系统规则反而放行）。
本机 loopback 自测通过完全不能说明 LAN 入站可用，**必须单独验证入站**。
需要管理员权限放行 UDP 67、69。

### 2.4 DHCP 回复从错误的网卡发出去

**表症**：服务器收到了路由器的 DISCOVER，路由器却拿不到 IP。

**真因**：电脑同时有 Wi-Fi（默认路由）和有线网卡。如果网卡不在目标网段，
Windows 会把 DHCP 广播回复从默认路由那张网卡（Wi-Fi）发出去。

**处置**：跑救援的电脑必须有线网卡配**同网段的静态 IP**。

### 2.5 文件名必须跟得上分配的 IP

路由器请求的文件名 = **它自己被分配的 IP 转十六进制 + `.img`**。
`192.168.31.2` → `C0A81F02.img`，`192.168.1.100` → `C0A80164.img`。
服务器 IP 与分配网段只要自洽即可（`192.168.1.x` 与 `192.168.31.x` 两套都实测可用），
但**文件名不能错**。最省事的做法是"见名即答"，无论请求什么名字都返回原厂固件。

### 2.6 TFTP 服务线程静默死亡

**表症**：路由器能通过 DHCP 拿到 IP，但此后再无「请求固件」记录。进程还在、端口还在。

**真因**：TFTP 线程在上一轮传输结束时抛异常退出，而全局 socket 引用未释放，
`bind` 看着正常；DHCP 是另一个线程所以照常工作。

**处置**：给每个服务线程套一层 supervisor，异常退出后 1 秒自动重启。

---

## 三、指示灯语义（官方 wiki 原文）

| 灯态 | 含义 |
|---|---|
| 橙灯闪 | 进入恢复模式，正在下载固件 |
| **蓝灯闪** | **刷写成功，可以重启** |
| 白灯常亮 | 固件文件被拒，需要换文件 |

### ★ 最容易被误读的一条：蓝灯闪之后必须手动断电，且不能按 Reset

stock U-Boot 在 TFTP 刷完之后会 **halt（停机），不会自动重启**。

本次实战在这里绕了最长的一段弯路：把「蓝灯闪」当成「正在写入」，
把「断电重启后又出现 DHCP」当成「刷写失败循环」。
**实际是** —— 蓝灯闪已经成功了，但重启时**误按了 Reset**，于是又进了恢复模式，
看起来就像失败。

**正确收尾**：拔电源 → 等 10 秒 → 直接插回电源，**全程不要碰 Reset**。

---

## 四、性能调优阶段

### 4.1 WED 启用失败：参数写错了文件

- `/etc/modules.d/mt7915e` 是**加载列表**（写模块名）。
- 模块参数必须写 **`/etc/modules.conf`**：`options mt7915e wed_enable=1`

### 4.2 WED 不能热启用 —— 驱动不支持运行时重载

**表症**：`rmmod mt7915e` 返回 0 看着像成功，但 `modprobe mt7915e wed_enable=1` 之后
`cat /sys/module/mt7915e/parameters/wed_enable` 依然是 `N`。

**证据**：`dmesg` 里有 `WARNING ... Comm: rmmod`，栈里含 `mt7915e+0x...`。
驱动没有实现正确的卸载路径。

**处置**：唯一正确的启用方式是**写配置 + 重启**。
并且因为重启有风险（尤其当只能靠 Wi-Fi 管理路由器时），必须带自检回滚 ——
见 `scripts/wed_guard.sh`。

**成功判据**：
- `cat /sys/module/mt7915e/parameters/wed_enable` → `Y`
- `dmesg | grep -i "attaching wed"` → `mt798x-wmac: attaching wed device 0 version 2`
- 前置检查：`/sys/bus/platform/devices/15010000.wed` 存在且已绑驱动；
  `/sys/kernel/debug/wed0/` 下有 `rxinfo` / `txinfo`

### 4.3 后台脚本被 SSH 会话回收

`nohup ... &` 或裸 `&` 起的后台进程，SSH 一断就被回收。用：

```sh
start-stop-daemon -S -b -x /root/wed_guard.sh
```

### 4.4 双频同名导致设备连到 2.4G

**表症**：协商速率从 2402 Mbps 掉到 287 Mbps。

**真因**：2.4G 与 5G 同名时 Windows 会选**信号更强**的那个，也就是 2.4G。

**诊断口诀**：`netsh wlan show interfaces` 看「**信道**」那一栏 —— 是 1/6/11 就是连错频段了。

**处置**：2.4G 改名（如 `<SSID>-2G`），5G 保持原 SSID。

### 4.5 信道忙率直读没有意义

`iw dev <if> survey dump` 的 `channel active/busy time` 是**上电以来的累计值**。
必须两次采样求差：`忙率 = (busy2-busy1)*100/(active2-active1)`。
接口名会随驱动重载在 `phy0-ap0` / `phy6-ap0` 之间变化，脚本里按 SSID 反查，不要写死。
见 `scripts/chscan_24g.sh`。

2.4G 保持 **HE20（20MHz）**：40MHz 在 1–13 信道法域内会占掉大半频段，拥挤环境反而更差。

### 4.6 测速被代理污染

沙箱/终端里存在 `http_proxy`、`https_proxy` 时，`curl` 默认走代理 → 测速失真，
`curl -6` 还会因为代理只听 IPv4 而误报 IPv6 故障。
**测真实网速/连通性必须 `curl --noproxy '*'`。**

### 4.7 别去找 CPU 调频器

MT7981 的 `mt7915e` **没有 `cpufreq`**（CPU 定频 1.3GHz），没有调速器可调，别浪费时间去翻。

### 4.8 `noscan` 在 AP 模式下不生效

`noscan` 是 **STA 模式**专用选项。AP 模式下写进 uci 不会报错，但也不会起作用。

### 4.9 波束成形与 160MHz 先查再改

`he_su_beamformer` / `he_su_beamformee` / `he_mu_beamformer` 与 `he_oper_chwidth=2`（160MHz）
在 hostapd 的运行配置 `/var/run/hostapd-phy*.conf` 里**默认就是开的**。
先 `grep` 一遍再决定要不要写 uci。

---

## 五、排障顺序（别跳步）

1. `netsh interface show interface` 看**物理链路**是否 connected
   —— 相当比例的"连不上"是网线/网卡没起来。本次实战中就误判过两回。
2. `arp -a` 看目标 IP 是否回应、MAC 是不是同一台机器。
   同机多网段时，MAC 一致即可确认是同一台设备（如 `192.168.1.100` 与 `192.168.31.1`
   都指向同一 MAC，说明还是那台路由器）。
3. `ping` + `curl -i http://<ip>/` 判当前跑的是哪个系统
   （原厂 nginx「小米路由器」页 vs OpenWrt LuCI）。
4. 只有在**第 1、2 步都通**的情况下，才去怀疑固件/参数。

---

## 六、无路可走时的兜底：UART

TFTP 走不通（bootloader 不响应、写不进去、或启动标志被改乱）时，UART 是 100% 能上岸的路。
只差一根 USB-TTL 串口线（CH340 / CP2102，约 15 元）。

需要提前备好并校验三个文件：

| 文件 | 作用 | 本机实测体积 |
|---|---|---|
| `...-ubootmod-preloader.bin` | BL2 | 230,232 B |
| `...-ubootmod-bl31-uboot.fip` | FIP | 804,948 B |
| `...-initramfs-kernel.bin` | 引导内核 | 9,051,380 B |

流程（社区验证）：`mtk_uartboot -s COMx --payload bl2-...-ram.bin --aarch64 --fip bl31-uboot.fip`
→ 串口 115200 进 U-Boot 菜单 → 从 TFTP 写 BL2/FIP → 重启后即可刷 OpenWrt，或用备份还原原厂。

---

## 七、几条通用的可复用规律

1. **判槽只看开机后实时状态**，任何"备份里的值"都可能已经过期。
2. **只擦非当前运行槽** —— 留一条物理退路，比任何脚本保险都可靠。
3. **UBI-on-NAND 必须用 `ubiformat`**，`mtd write` 不行。
4. **TFTP 服务器绝不无条件重发**，一律超时重传。
5. **排障第一步永远是物理链路**。
6. **本机能收到对方广播 ≠ 对方能连我的服务端口**，入站方向必须单独验证。
7. **刷机类操作，先备份**。15 分区、247MB 的全量 dump 是最后一道保险。
   （有趣的是本次实战最终没用上 —— 但正是因为有它，才敢反复试。）
