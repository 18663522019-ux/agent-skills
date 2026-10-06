# Changelog

本仓库所有值得记录的改动。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [1.1.0] — 2026-10-06

新增第三个技能：给小米 AX3000T 刷 OpenWrt 并把无线性能榨到超过它的千兆有线口。
这次实战中间**真砖了一次**，靠 TFTP 救回来、换正确方式重刷成功——所以这个技能的重点
不在"怎么刷"，而在"哪几个判断会把设备刷成砖"，以及"怎么把一个砖掉的设备捞回来"。

### 新增

- **`xiaomi-router-openwrt-flash`** — 小米/红米 MT7981 机型刷 OpenWrt + 性能榨干 + 变砖救援
  - **UBI 镜像必须用 `ubiformat`**：`mtd -e write` 对 NAND 坏块/EC 头处理错误，
    是「上传成功、校验一致、重启后永远回恢复模式」的头号真凶
  - **判启动槽只看开机后实时的 `/proc/cmdline`**：旧备份里的 `firmware=` 会被救砖流程翻转
    （实战中备份里是 `firmware=1`，救砖后实测变成 `firmware=0`）
  - **TFTP 服务器绝不能每 0.3 秒无条件重发**：会向 bootloader 灌重复包致缓冲错位，
    表症是「每次都显示传输完成、路由器却始终刷不进去」。改为标准超时重传
    （1.5s 未收到**期望的** ACK 才重发），并忽略客户端旧 ACK
  - 三态指示灯语义（橙闪=下载中 / **蓝闪=刷写成功** / 白常亮=文件被拒），
    以及 stock U-Boot 刷完会 **halt** —— 收尾必须「断电后直接插回，不碰 Reset」
  - **WED 无线硬件卸载**：MT7981 最大收益项，但 `mt7915e` **不支持运行时 rmmod/insmod**
    （rmmod 返回 0 却抛内核 WARNING，modprobe 参数不生效），只能写配置 + 重启，
    且必须配开机自检回滚守护
  - 双频同名会让设备连到 2.4G（协商速率 2402 → 287 Mbps）；2.4G 信道必须用
    survey **差值法**实测（累计值直读无意义）
  - 连 OpenWrt 的 SSH 必须换姿势：便携 Python 的旧 libssh2 与 dropbear 做
    KEX 不兼容，改用系统 OpenSSH + `SSH_ASKPASS_REQUIRE=force`
  - 5 个可直接运行的脚本：`flash_openwrt.py`（含 6 步自检）、`wed_guard.sh`、
    `wed_diag.sh`、`chscan_24g.sh`、`tftp_rescue_server.py`（DHCP+TFTP 二合一）
  - `references/troubleshooting.md`：按「表症 → 真因 → 处置」组织的完整踩坑记录

- 实测性能数据（iperf3，客户端 Intel AX201 160MHz）：
  4 流上行 675 → **1270 Mbps（+88%）**，单流下行 628 → **822 Mbps（+31%）**。
  AX3000T 的 4 个有线口都是千兆，所以无线 160MHz 已经**超过它自己的有线口**。
- 补充「什么时候该停手」的判据：功率顶国标（5G 23dBm / 2.4G 20dBm）、
  hostapd 配置全项最优、flowtable 真实生效、温度正常 —— 都满足即无参数空间，
  余量只剩物理手段（摆放、有线回程 mesh）。

### 修正

- `README` 里的 `git clone` 地址与实际仓库不一致（指向了另一个账号），已更正为
  `https://github.com/xfnylqt/agent-skills.git`。
- `.gitignore` 补充排除路由器固件/备份/运行日志（`*.bin`、`*.ubi`、`*.fip`、`rescue.log` 等），
  避免大体积固件与设备标识入库。

---

## [1.0.0] — 2026-10-03

首次发布，收录两个技能：一个解决"小程序数据抓不到"，一个解决"抓到数据后页面太卡"。

### 新增

- **`miniprogram-saz-extract`** — 从微信小程序抓包文件（`.saz`）提取结构化数据
  - `.saz` 解包与会话清单分析；识别 HTTPS CONNECT 隧道等噪音
  - **chunked + gzip 双重编码**的解码实现（`dechunk` + `gunzip`），以及必须用 `latin-1` 保字节的原因
  - 埋点与数据接口的区分信号清单
  - **反推 H5 低代码平台的组件接口**：定位「一个通用接口 + body 里的 `code` 字段」这一模式，找到一条即可穷举整个接口族
  - 无语义字段名（`fieldId1` / `fieldId102`）的语义反推方法
  - 经纬度网格扫描穷举全量站点
  - 阿里云 `x-oss-process` 与腾讯云 `imageMogr2` 的缩放语法差异
  - 工程细节：token 时效、限速防封、断点续跑与清缓存时机

- **`singlefile-data-page-perf`** — 单文件大数据页面性能优化
  - 数据 `zlib → 字节异或 → base64` 压缩内联，配套原生 `atob` + `Uint8Array` 解密
  - 折叠面板懒渲染、多视图容器切换、分块渲染
  - 滚动卡顿的两个高频真凶：吸顶栏 `backdrop-filter` 与屏幕外元素布局（`content-visibility`）
  - 「聚合层坐标 ≠ 明细层坐标」这个导致定位功能**静默失效**的通用坑
  - 每项手段都附实测数字与测量方法

- 仓库基础设施：`README`（含优化前后对比图与流程图）、`LICENSE`（MIT）、`.gitignore`（排除抓包文件）、`CONTRIBUTING`（含脱敏要求）

### 说明

- 技能内所有示例均已脱敏：真实域名、平台组件名与品牌名替换为占位符，方法论与代码逻辑完整保留。
- 两个技能均来自同一次真实交付，文中数字为实际测量值。

---

## 版本约定

- **主版本号**：技能结构或工作流发生不兼容变化
- **次版本号**：新增技能，或现有技能补充新方法
- **修订号**：修正错误、补充说明、优化措辞
