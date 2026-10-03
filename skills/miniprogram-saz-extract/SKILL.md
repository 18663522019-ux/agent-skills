---
name: miniprogram-saz-extract
description: 从微信小程序抓包文件（.saz）里提取结构化数据——解包、解码（chunked+gzip）、区分数据接口与埋点、反推 H5 低代码平台的组件接口，再把数据落成可用的 JSON。当用户想抓某个微信小程序（尤其带"站内美食""商城""会员权益"等内嵌 H5 模块）的店铺/商品/菜单数据，或需要在沙箱里解析 Fiddler 抓包结果时使用。触发词：小程序抓包、saz 解析、Fiddler 抓包分析、抓小程序数据、getDynamicData、组件接口、小程序数据提取。
agent_created: true
---

# 从 .saz 提取微信小程序数据

微信小程序的数据**经常无法从公网直接抓取**——很多接口域名（如 `api.prod.xxx.tech`）走微信 HTTPDNS，
公网 DNS 查不到（NXDOMAIN），只有小程序运行环境能解析。此时唯一可靠的路径是让用户在 Fiddler 里
**File → Save → All Sessions** 存成 `.saz`，再由你在沙箱内解包。

## 关键认知

1. **`.saz` 本质是 zip**，条目形如 `raw/NN_c.txt`（请求）与 `raw/NN_s.txt`（响应）。
2. **埋点 ≠ 数据接口**。`/cd?d=...`、`tongji-collector`、`/report`、`/telemetry`、`getfiddler` 都是埋点，
   跳过它们，但**埋点里常有宝贵线索**：`pageCode`、`widgetName`（如"站内美食"）、`appId`。
3. **内嵌 H5 的低代码平台**（`webify`/`designer`/`lowcode` 这类）常把数据都塞进一个通用接口，
   靠 **body 里的 `code` 字段**决定取什么。找到一条就能穷举出整个接口族。
4. **`/pub/` 前缀不等于免鉴权**，仍可能需要小程序登录态。别在猜接口上耗太久。

## 标准流程

### 1) 解包并列出全部会话

```python
import zipfile, re
z = zipfile.ZipFile(SAZ)
for n in sorted(x for x in z.namelist() if x.endswith("_c.txt")):
    raw = z.read(n).decode("latin-1", "ignore")
    head, body = raw.split("\r\n\r\n", 1) if "\r\n\r\n" in raw else (raw, "")
    first = head.split("\n")[0].strip()
    if first.startswith("CONNECT") or "127.0.0.1" in first:
        continue                      # HTTPS 隧道与本地代理噪音
    print(n, len(body), first[:130])
    if 0 < len(body) < 400:
        print("   body:", body[:250].replace("\n", " "))
```

> HTTPS CONNECT 会话**看不到路径**（只有 `host:443`）。若关键请求只以 CONNECT 形式出现，
> 说明 Fiddler 没解密该域，需让用户确认 HTTPS 解密已开启，或改抓非 HTTPS 站点。

### 2) 正确解码响应体（两个必踩的坑）

响应常是 **chunked + gzip 双重编码**，直接 `json.loads` 必然失败：

```python
def dechunk(b):
    out, i = b"", 0
    while i < len(b):
        j = b.find(b"\r\n", i)
        if j < 0: break
        try: size = int(b[i:j].split(b";")[0], 16)
        except Exception: break
        if size == 0: break
        out += b[j+2:j+2+size]; i = j + 2 + size + 2
    return out

def read_resp(z, idx):
    s = z.read("raw/%s_s.txt" % idx).decode("latin-1")      # 必须 latin-1，保字节
    head, body = s.split("\r\n\r\n", 1) if "\r\n\r\n" in s else (s, "")
    raw = body.encode("latin-1", "ignore")
    if "chunked" in head.lower(): raw = dechunk(raw)
    if "gzip" in head.lower():
        try: raw = gzip.decompress(raw)
        except Exception: pass
    return raw.decode("utf-8", "ignore")
```

**要点**：请求/响应的 header 与 body 用 `latin-1` 解码来保字节，body 再按声明编码处理。

### 3) 找出数据接口

按信号强度排查响应：

| 信号 | 含义 |
|---|---|
| `Content-Type: application/json` 且体积 > 1KB | 大概率是数据 |
| body 里含 `"code":"customizedXxx"` / `"pageSize"` / `"entityId"` | 低代码平台组件接口 |
| body 里含 `"eventType"` / `"spm"` / `"spendTime"` | 埋点，跳过 |
| URL 含 `getDynamicData` / `getPageData` / `integration` | 组件接口入口 |
| URL 含 `shopcart` / `shop` / `product` / `goods` | 商城数据 |

### 4) 穷举同族接口

锁定一个组件接口后，**照抄它的 URL 与 header**，只改 body 的 `code` 逐个试探；
参数名从已抓到的那条里抄（如 `shopId`、`stationCode`、`fieldId101`）。
返回 `"BizDataProcessor is null"` 或 `success:false` 即该 code 不存在。

已见过的一组真实命名（某出行平台的站内美食模块）：

```
customizedXxxStationNearest     附近车站（传 lat/lng，可用网格扫描穷举全国站点）
customizedXxxGetStationInfo     车站详情
customizedXxxShopList           店铺列表（stationCode 或 entityId）
customizedXxxShopCategoryList   商品分类
customizedXxxProductList        商品列表 = 菜单/套餐
```

**网格扫描拿全量站点**：以经纬度网格（如 0.5° 步长）反复调"附近站点"接口，
按站点 code 去重即可得全量清单（实测 1600+ 个采样点 → 170+ 个站）。

### 5) 提取字段（面对无语义字段名）

这类接口的字段常是 `fieldId1`、`fieldId102` 这种无语义命名，**必须靠值反推**：

```python
# 打印每条记录的字段与样例值，人工判断哪个是店名/位置/时间/图片
for k, v in rec.items():
    print("%-16s %s" % (k, str(v)[:80]))
# 判断窍门：定长的大写字母+数字串像门店码 / 站点码；
# 含 "http" 的是图片；形如 "07:00"/"20:00" 的是营业时间；纯中文长串是名称或地址
```

### 6) 图片缩放（不同图床语法不同）

原图动辄数 MB，必须让服务端缩放，否则页面直接被拖垮：

```python
def thumb(u, w=300, h=300):
    if not u or "?" in u:                       # 已带处理参数，别叠加
        return u
    if "x-oss-process" in u:
        return re.sub(r"w_\d+,h_\d+", "w_%d,h_%d" % (w, h), u)
    if "myqcloud.com" in u:          # 腾讯云数据万象的图床域名特征
        return "%s?imageMogr2/thumbnail/%dx%d" % (u, w, h)   # 腾讯云数据万象
    return "%s?x-oss-process=image/resize,m_fill,w_%d,h_%d" % (u, w, h)  # 阿里云 OSS
```

**验证方法**：同一 URL 带/不带参数各 curl 一次，比 `size_download`。
注意 **`x-oss-process` 对腾讯云无效**（返回原图，HTTP 200 但体积不变），反之亦然。

## 工程注意事项

- **token 有时效**：通常几小时。抓到后尽快用；写入脚本时用环境变量（`API_TOKEN=xxx python xxx.py`），别硬编码。
- **限速**：`MIN_INTERVAL=0.25~0.5s` + curl 子进程（比 urllib 稳）。并发过高会被封 IP。
  被封的表现是请求超时后全量失败，等几分钟自动解封。
- **断点续跑**：每 N 条落盘缓存 `{id: result}`，重跑时命中缓存直接跳过。
  接口参数调整后记得**清缓存**，否则旧结果不会刷新。
- **让用户只做一件事**：不要让他逐条复制请求，只要 `.saz`。解包、定位、对齐字段全在沙箱内完成。

## 沙箱侧提醒

- `raw.githubusercontent.com` 常不可达；`cdn.jsdelivr.net` 一般可达。
- Git Bash 里 Python 解析 `/tmp/xxx` 与实际 Windows 路径不一致，**统一用 Windows 绝对路径**。
- 脚本别命名成 `inspect.py`（会遮蔽标准库）。
