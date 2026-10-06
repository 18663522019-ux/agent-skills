---
name: xiaomi-router-openwrt-flash
description: 小米/红米路由器（AX3000T/RD03 等 MT7981 filogic 机型）全流程：从原厂固件刷 OpenWrt、永久化、刷完后的性能榨干调优（WED 硬件卸载 / 160MHz / 硬件 NAT 分载 / BBR / 信道实测），以及变砖后的 TFTP/UART 救援。当用户要"给小米路由器刷 OpenWrt""提升路由信号/网速""把路由器性能榨干""Wi-Fi 变慢/协商速率低""路由器变砖了救回来""刷机后进恢复模式循环"时使用。触发词：刷路由器、小米路由器刷机、AX3000T、RD03、OpenWrt、xmir-patcher、变砖、救砖、ubiformat、TFTP 恢复、initramfs、sysupgrade、WED、wed_enable、160MHz、硬件NAT、flow_offloading、iperf3 测速、无线调优。
agent_created: true
---

# 小米/红米路由器刷 OpenWrt 全流程

## 配套文件

| 文件 | 用途 |
|---|---|
| `scripts/flash_openwrt.py` | 按官方文档用 `ubiformat` 刷 initramfs，带 6 步自检（判槽 → 自检 → 校验 → 写入 → 标志 → 重启） |
| `scripts/wed_guard.sh` | WED 开机自检回滚守护（挂 rc.local，失败自动回滚并重启） |
| `scripts/wed_diag.sh` | WED 诊断，用来证明 mt7915e 不支持运行时热重载 |
| `scripts/chscan_24g.sh` | 2.4G 信道占用率实测（survey 差值法） |
| `scripts/tftp_rescue_server.py` | TFTP 软砖救援服务器（DHCP + TFTP 二合一，按官方参数） |
| `references/troubleshooting.md` | 排障与踩坑全记录（表症 → 真因 → 处置），卡住时先翻这份 |

---

## 0. 核心判据（先记这几条，能省几小时）

| 判断 | 结论 |
|---|---|
| **官方只认两条安装路径** | ① 原厂系统内用 **API-RCE 开 SSH** 再刷；② 拆机 **UART**。**TFTP 只能刷回原厂 .bin 救砖，官方不支持用 TFTP 直刷 `initramfs-factory.ubi`**（只有已刷 OpenWrt U-Boot 的机型才认 `ubootmod-initramfs-recovery.itb`） |
| **UBI 镜像必须用 `ubiformat`** | `mtd -e xxx write file xxx` 对 NAND 坏块/EC 头/VID 处理不正确 → 镜像写坏 → 反复掉恢复模式。**这是"传完却不启动"的头号真凶** |
| **判槽只看开机后实时 cmdline** | `cat /proc/cmdline` 里的 `firmware=`，**不要拿旧备份的 nvram 当依据**（救砖后 flag 会被翻转） |
| `firmware=0` | `ubiformat /dev/mtd9` + `flag_boot_rootfs=1`、`flag_last_success=1` |
| `firmware=1` | `ubiformat /dev/mtd8` + `flag_boot_rootfs=0`、`flag_last_success=0` |
| **只擦非当前运行槽** | 原厂完整留在另一槽，最坏情况可回退，不要动"正在跑"的那个 |
| 空密码 SSH | 用 `ssh -o BatchMode=yes root@192.168.1.1`（OpenSSH 会先试 none-auth）。**libssh2 / ssh2-python 传空密码会直接失败**，别用 paramiko/ssh2-python 走这一步 |
| **WED 不能热启用** | `mt7915e` 不支持运行时 rmmod/insmod，**只能写配置 + 重启**，且必须带自检回滚 |

## 1. 准备：工具与固件

- 工具：GitHub `openwrt-xiaomi/xmir-patcher`（自带便携 Python，SSH 靠 `ssh2-python`，**没有 paramiko**；入口 `run.bat` / `menu.py`）。
- 固件：官方 `downloads.openwrt.org/releases/<ver>/targets/mediatek/filogic/`。国内走清华镜像。
  - 要下三个：`...-initramfs-factory.ubi`（stock 布局用）、`...-squashfs-sysupgrade.bin`、可选 `...-ubootmod-*`。
  - **必须用同目录 `sha256sums` 校验**。注意其中文件名带 `*` 前缀（`sha256sum` 的 binary 模式标记），比中要用 `line.split(None,1)` 且 `lstrip('*')`。
  - 国内直连 `downloads.openwrt.org` 极慢（10 分钟下不完 10MB），换 `mirrors.tuna.tsinghua.edu.cn/openwrt/` 秒下。
- **AX3000T 有硬件变体**（MT7531 / Airoha AN8855 交换芯片、ESMT/Winbond/FORESEE NAND）。**用 >= 25.12 的镜像可覆盖全部变体**；不要用老版本。另注意新型号 **RD03v2 是 Qualcomm 方案，完全不支持 OpenWrt**（包装条码尾号 706330 / SKU DVB4510CN）。
  - 从备份的 `mtd5_FIP.bin` 里能看到 U-Boot 同时支持 mt7531/an8855/多种 NAND，**不能据此判断本机颗粒**，直接上最新镜像即可。

## 2. 开 SSH（原厂系统内）

1. 浏览器走完原厂初始化向导，设好管理密码 —— **出厂态所有写接口都返回 HTTP 403**，没有 stok 什么都做不了。
2. `xmir-patcher` 里跑 `connect.py`，把密码喂到 stdin：
   ```bash
   printf '<管理密码>\n' | xmir-patcher/python/python.exe connect.py
   ```
   - 它会自动按顺序试 `start_binding` / `arn_switch` / `set_mac_filter` / `datacenter7` 四种漏洞，命中即开 dropbear，并把 **root 密码设为 `root`**。
   - 结果里出现 `#### SSH 服务已成功开启! ####` 即成功。密码错会明确报 `ERR=401`。
   - 关键 API：`create_gateway(timeout, die_if_sshOk, die_if_ftpOk, web_login, ssh_port, try_telnet)`——`web_login` 传字符串即当密码；`gw.run_cmd(cmd, timeout=, reboot=)`、`gw.upload(local, remote, md5chk=True)`（内部 `ssh.scp_send64`）。

## 3. 刷 initramfs（官方命令，逐条执行）

**直接跑 `scripts/flash_openwrt.py`**，它把下面这些全部包好了（含 6 步自检和失败即中止）。
手工执行时的命令如下：

```sh
# 目标槽按 /proc/cmdline 的 firmware= 定，见第 0 节
ubiformat /dev/mtd9 -y -f /tmp/openwrt-...-initramfs-factory.ubi

nvram set boot_wait=on
nvram set uart_en=1
nvram set flag_boot_rootfs=1        # firmware=0 用 1；firmware=1 用 0
nvram set flag_last_success=1       # 同上
nvram set flag_boot_success=1
nvram set flag_try_sys1_failed=0
nvram set flag_try_sys2_failed=0
nvram commit
reboot
```

- 原厂必须有 `ubiformat`（在 `/usr/sbin`），刷前先 `which ubiformat nvram` 自检。
- **上传前算 MD5，上传后回读 MD5 与字节数**，一致才继续。
- 镜像 10 MB 级，`/tmp`（tmpfs）通常 100+ MB，够。
- 重启后：**192.168.31.1 消失、192.168.1.1 出现**（可用 `arp -a` 看 MAC 确认还是那台机器）。网线必须插**中间 LAN 口，不能插 WAN 口**。
- 判别 initramfs：LuCI 页面 `Date: Thu, 01 Jan 1970`（无 RTC）。

## 4. 永久化 sysupgrade

```bash
# dropbear 没有 sftp-server，scp 易踩坑；用 cat 管道最稳
cat openwrt-...-squashfs-sysupgrade.bin | ssh -o BatchMode=yes root@192.168.1.1 'cat > /tmp/fw.bin'
ssh root@192.168.1.1 'md5sum /tmp/fw.bin; wc -c < /tmp/fw.bin'   # 与本地一致再继续
ssh root@192.168.1.1 'sysupgrade -n /tmp/fw.bin'
```

- 输出 `Commencing upgrade. Closing all shell sessions.` + `ubus call system sysupgrade ... (Connection failed)` = 正常，正在重启。
- **判据：永久系统有 `/rom` 目录、`/` 是 `overlayfs:/overlay on / type overlay`；initramfs 没有。**
- 永久化后分区布局会变成 OpenWrt 标准布局（如 `mtd8=ubi_kernel` + `mtd9=ubi` 大分区），不再原厂的双 34M 对称槽。

### 4b. 连 OpenWrt 的 SSH 必须换姿势

便携 Python 自带的旧 libssh2 与 OpenWrt 25.12 的 dropbear 会 `KexFailureError`
（它连小米原厂固件的 dropbear 却是正常的）。改用系统 OpenSSH + `SSH_ASKPASS_REQUIRE=force`：

```bash
printf '#!/bin/sh\necho <密码>\n' > /tmp/askpass.sh && chmod +x /tmp/askpass.sh
DISPLAY=:0 SSH_ASKPASS=/tmp/askpass.sh SSH_ASKPASS_REQUIRE=force \
  ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no \
      -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@192.168.1.1 '<cmd>'
```

（本机没有 `sshpass`；`SSH_ASKPASS_REQUIRE=force` 是 OpenSSH 8.4+ 的非交互密码方案。）

### 4c. 网段冲突会让整台路由器连带光猫一起断网

原厂 LAN 是 `192.168.1.1/24`，而光猫 WAN 常见也是 `192.168.1.x/24` —— 撞车后表现为
"路由器刷成功了但上不了网"。刷完先把 LAN 改到别的网段：

```sh
uci set network.lan.ipaddr='192.168.2.1'
uci commit network && /etc/init.d/network restart
```

## 5. 收尾常用命令

```sh
uci set wireless.radio1.htmode=HE160      # 5G 160MHz
uci set wireless.radio1.channel=36        # 36-64 段不需 DFS
uci set wireless.radio0.country=CN; uci set wireless.radio1.country=CN
uci set firewall.@defaults[0].flow_offloading=1
uci set firewall.@defaults[0].flow_offloading_hw=1   # MT7981 硬件 PPE 分载
uci commit wireless; uci commit firewall
```

- **OpenWrt 默认 Wi-Fi 是 `disabled=1`**（不是开放热点），所以刷完不会裸奔。
- 检查外网：`ubus call network.interface.wan status`；看物理口：`cat /sys/class/net/<iface>/operstate`。AX3000T 的 br-lan 是 `lan2/lan3/lan4`，WAN 单独一个口，**WAN 没插线时 `operstate=lowerlayerdown`**。
- `passwd` 设置弱密码会提示 `Bad password: too weak`，但**照样设置成功**，不是失败。

## 5b. 性能榨干（刷完 OpenWrt 之后，按收益排序）

### ① WED 无线硬件卸载 —— 收益最大，也最容易做错
让 Wi-Fi 收发的包由硬件在 Wi-Fi ↔ 以太网之间直接搬运、绕开 CPU。MT7981 **出厂默认关闭**。

```sh
# 唯一正确的启用方式：写配置 + 重启
echo "options mt7915e wed_enable=1" >> /etc/modules.conf
sed -i 's|^mt7915e.*|mt7915e wed_enable=1|' /etc/modules.d/mt7915e
reboot
```

- ⚠️ **不要试图 rmmod/insmod 热启用**：`rmmod mt7915e` 返回 0 但内核抛 `WARNING ... Comm: rmmod`（栈含 `mt7915e+0x...`），且 `modprobe mt7915e wed_enable=1` 的**参数不会生效**（重载后 `wed_enable` 仍是 `N`）。这个驱动不支持运行时重载。可跑 `scripts/wed_diag.sh` 把证据留档。
- ⚠️ **参数写 `/etc/modules.conf`，不是 `/etc/modules.d/`**（后者是加载列表，只写模块名）。
- 前置检查（都应有值）：`/sys/bus/platform/devices/15010000.wed` 存在且已绑驱动、`/sys/kernel/debug/wed0/` 下有 `rxinfo/txinfo`、`modinfo mt7915e` 有 `parm: wed_enable (bool)`。
- **成功判据**：`cat /sys/module/mt7915e/parameters/wed_enable` → `Y`，且 `dmesg | grep -i "attaching wed"` → `mt798x-wmac: attaching wed device 0 version 2`。
- **重启有风险，务必带自检回滚**（路由器只能靠 Wi-Fi 管时尤其重要）：用 `scripts/wed_guard.sh` —— 写 `/root/wed_guard.sh` 挂到 `/etc/rc.local`，开机后每 5 秒查 `iw dev | grep -c ap0`，180 秒内 ≥2 个 AP 接口 → 保留并把自身从 rc.local 删除；否则删掉 modules.conf 里的 wed 行并 `reboot`。用 `start-stop-daemon -S -b -x /root/wed_guard.sh` 起（`nohup`/`&` 会被 SSH 会话回收）。
- 实测收益（AX3000T，电脑 AX201 160MHz）：单流下行 +31%、4 流上行 +88%。

### ② 双频必须分开命名，否则设备会连到 2.4G
2.4G 与 5G **同名时 Windows 会选信号更强的 2.4G**，协商速率从 2402 Mbps 直接掉到 287 Mbps。
```sh
uci set wireless.default_radio0.ssid='<SSID>-2G'   # radio0=2G, radio1=5G
uci commit wireless && wifi reload
```
诊断口诀：`netsh wlan show interfaces` 看"**信道**"那一栏 —— 是 1/6/11 就是连错频段了。

### ③ 2.4G 信道：必须用 survey **差值法**实测，不能凭感觉
`iw dev <if> survey dump` 的 `channel active/busy time` 是**累计值**，直读无意义。两次采样求差：

```sh
D() { iw dev $1 survey dump | awk '/frequency/{c=$2} /channel active time/{a[c]=$4} /channel busy time/{b[c]=$4} END{for(k in a) if(a[k]>0) print k,a[k],b[k]}'; }
# 采样1 → sleep 15 → 采样2，差值公式：忙率 = (busy2-busy1)*100/(active2-active1)
```
- 直接跑 `scripts/chscan_24g.sh` 即可（`SSID` / `CAT` 环境变量可覆盖 SSID 与待测信道）。
- 接口名会随驱动重载变化（`phy0-ap0` ↔ `phy6-ap0`），脚本里动态取：`IF=$(iw dev | awk '/Interface/{i=$2} /ssid <你的2.4G名>/{print i}')`。
- 实测参考（居民区）：ch1≈74%、ch6≈66%、**ch11≈58%（最优）**。
- 2.4G 保持 **HE20**（20MHz）—— 40MHz 在 1-13 信道法域内会占用大半频段，拥挤环境反而更差。

### ④ 转发与内核调优（一条条都可持久化）
```sh
# 硬件 NAT 分载（确认 nft 里真的出现 flags offload，devices 应含 wan/lan*/phy*-ap0）
uci set firewall.@defaults[0].flow_offloading=1
uci set firewall.@defaults[0].flow_offloading_hw=1
# 数据包引导
uci set network.@globals[0].packet_steering=1
# DNS 缓存
uci set dhcp.@dnsmasq[0].cachesize=10000
# 内核参数 → /etc/sysctl.conf
net.ipv4.tcp_congestion_control=bbr
net.ipv4.tcp_fastopen=3
net.ipv4.tcp_slow_start_after_idle=0
net.core.netdev_max_backlog=4096
net.netfilter.nf_conntrack_max=32768
net.core.rmem_max=1048576
```
> 注意：MT7981 的 `mt7915e` **没有 cpufreq**（CPU 定频 1.3GHz），别去找调速器。
> `noscan` 是 **STA 模式**专用选项，AP 模式下写 uci 不生效，别浪费时间。
> 波束成形（`he_su_beamformer/he_su_beamformee/he_mu_beamformer=1`）与 `he_oper_chwidth=2`（160MHz）在 hostapd 运行配置 `/var/run/hostapd-phy*.conf` 里默认就是开的，先查再改。

### ⑤ 实测参考基线（iperf3，10s/项）

| 项目 | 调优前 | 开 WED + 全套调优后 | 变化 |
|---|---|---|---|
| 单流上行 | 912 Mbps | 1010 Mbps | +11% |
| 单流下行 | 628 Mbps | 822 Mbps | **+31%** |
| 4 流上行 | 675 Mbps | **1270 Mbps** | **+88%** |
| 4 流下行 | 602 Mbps | 757 Mbps | +26% |

注意 **AX3000T 的 WAN 与 3 个 LAN 口都是千兆**（机内交换芯片到 CPU 才 2.5Gbps），
所以有线吞吐天花板 1 Gbps；**无线 160MHz 反而能跑到 1.27 Gbps**，已经超过有线口。
> iperf3 到路由器本机测的是"终端↔路由器"路径，**不经过 WAN↔LAN 转发链**，所以它不能完全代表上网转发性能 —— 但足以区分"链路能力"与"CPU 瓶颈"。

### ⑥ 什么时候算"到顶了"（避免继续空转）

达到下列状态后，**速度侧已经没有参数空间**：

- 发射功率顶到国标上限：`iw reg get`（country CN）→ 2400-2483MHz = 20dBm、**5150-5250MHz = 23dBm**；
  实测 `iw dev` 报 5G 23.00dBm、2.4G 20.00dBm，**已顶格**。
- hostapd 运行配置全项最优：VHT160/HE160、SU+MU Beamformer/Beamformee 全开、
  SHORT-GI-160、MAX-MPDU-11454、`he_rts_threshold=1023`。
- 温度正常（实测 56.7°C）。
- flowtable 的 `flags offload` 在 nft 里真实生效（devices 含 wan/lan*/phy*-ap0）。

**唯一剩下的余量在覆盖侧，且只能靠物理手段**：抬高/居中摆放、有线回程 mesh。

> 5.8GHz（ch149-161）法规允许 **33dBm**，比我们所在的 5.1GHz 段高 10dB。
> 但代价是：① 只能 80MHz（速率砍半）② 频率更高、穿墙更差 ③ mt7976 的 PA 未必真给到 33dBm。
> 属于"覆盖 vs 速度"的取舍项，**要实测再决定**。

## 6. 救砖（TFTP / UART）

**直接跑 `scripts/tftp_rescue_server.py`**（DHCP + TFTP 二合一，已按官方参数实现）。
手工用 dnsmasq 也可以：

```sh
dnsmasq --no-daemon --listen-address=192.168.31.100 --bind-interfaces \
        --dhcp-range=192.168.31.2,192.168.31.254 --enable-tftp --tftp-root=/tmp/tftp
```

- **文件名 = 路由器自己被分配的 IP 转十六进制 + `.img`**（`192.168.31.2` → `C0A81F02.img`，
  `192.168.1.100` → `C0A80164.img`）。wiki 特别注明 "the file name is a very important part"。
  服务器 IP 与分配网段自洽即可（`192.168.1.x` 与 `192.168.31.x` 两套都实测可用）。
  也可从 `fw_printenv | grep serverip` 看预设服务器 IP；最省事的做法是"见名即答"。
- **PC 网卡必须在该网段有静态 IP**，否则 Windows 会把 DHCP 回复从默认路由那张网卡（Wi-Fi）发出去。
- 进恢复模式：**网线插 LAN 口** → 按住 Reset → 通电 → **橙灯开始闪**再松手，**全程不要碰**。
- **刷完 bootloader 会 halt，不会自动重启。** 收尾动作：**拔电源 → 等 10 秒 → 直接插回电源，绝对不碰 Reset**。
- LED：**橙闪 = 下载中；蓝闪 = 刷写成功可重启；白灯常亮 = 文件被拒（换文件）**。
- 自写 TFTP 服务器两个必踩坑：① Windows 发广播包要 `SO_BROADCAST`，否则 `WinError 10013`；
  ② **绝不能每 0.3 秒无条件重发** —— 会往 bootloader 灌重复包导致缓冲错位、校验失败
  （表症是"服务器收到末块 ACK，路由器却还在恢复模式"）。改成标准超时重传（~1.5s 无期望 ACK 才重发）。
- 入站方向要单独验证：本机 loopback 自测能通**不代表** LAN 入站能通，Windows 防火墙
  默认放行 UDP/67 但不放行 UDP/69，需管理员放行。
- **UART 兜底（100% 能上岸）**：需一根 USB-TTL（CH340/CP2102，约 15 元）。stock u-boot 控制台
  → `load` initramfs-kernel 到 `0x48000000` → 启动 → sysupgrade。三件套
  `ubootmod-preloader.bin(BL2,230232B)`、`ubootmod-bl31-uboot.fip(FIP,804948B)`、
  `initramfs-kernel.bin(9051380B)` 要提前下好并校验。

## 7. 排障顺序（别跳步）

1. `netsh interface show interface` 看**物理链路**是否 connected —— 相当比例的"连不上"是网线/网卡没起来。
2. `arp -a` 看目标 IP 是否回应、MAC 是不是同一台机器（同机多网段时靠 MAC 确认）。
3. `ping` + `curl -i http://<ip>/` 判当前跑的是哪个系统（原厂 nginx「小米路由器」页 vs OpenWrt LuCI）。
4. 只有在**第 1、2 步都通**的情况下才怀疑固件/参数。
5. **测速/连通性一律加 `curl --noproxy '*'`** —— 终端里的 `http_proxy` 会污染结果，
   并让 `curl -6` 误报 IPv6 故障（代理只听 IPv4）。

更细的逐条踩坑记录见 `references/troubleshooting.md`。
