---
name: singlefile-data-page-perf
description: 单文件 HTML 页面（数据内联、无后端）加载慢/切换卡/滚动卡时的系统优化方法。涵盖数据压缩内联、原生 API 解密、DOM 懒渲染、多视图容器切换、分块渲染五类手段及各自的量化收益。当用户反馈"网页卡顿""切换卡""加载慢"，或需要把几 MB 的数据塞进一个可离线打开的 HTML 时使用。触发词：页面卡顿、切换卡顿、加载慢、单文件页面优化、内联大数据、DOM 太多、首屏慢、懒渲染、分块渲染。
agent_created: true
---

# 单文件大数据页面的性能优化

「数据全部内联进一个 HTML」的页面（离线可用、防盗版方便）天然会遇到三重瓶颈：
**下载体积大 → 解密慢 → DOM 太多**。三者要分别治，别只盯着一个。

先量化基线，再动手：

```python
# playwright 里量：体积 / DOM 节点 / 各交互耗时
print(os.path.getsize(page)/1024, "KB")
print(pg.evaluate("()=>document.getElementsByTagName('*').length"), "节点")
# 交互耗时务必用页面内 performance.now() 计时，别用外部 sleep 估算
r = pg.evaluate("""()=>{const t=performance.now();
  document.querySelector('.tab').click(); return performance.now()-t;}""")
```

## 一、下载体积：压缩后再内联

**常见的错误做法**：把 JSON 逐字符异或成"看似乱码的字符串"直接内联。
异或结果常落进 CJK 码位，UTF-8 下 **每字符 3 字节**，页面会膨胀到 3 倍以上。

**正确做法**：`JSON → UTF-8 → zlib 压缩 → 字节异或 → base64`

```python
import zlib, base64
_KEY = b"SX2026LT"
def crypt(plain_text: str) -> str:
    raw = zlib.compress(plain_text.encode("utf-8"), 9)
    k = _KEY
    x = bytes(b ^ k[i % len(k)] for i, b in enumerate(raw))
    return base64.b64encode(x).decode("ascii")

_CHUNK = 8000      # base64 按固定长度切片成字符串数组
_parts = [payload[i:i+_CHUNK] for i in range(0, len(payload), _CHUNK)]
_enc_expr = json.dumps(_parts, ensure_ascii=True)
_key_expr = ",".join(str(b) for b in _KEY)
```

实测：1657KB 数据 → zlib 228KB → base64 305KB，**整页 3666KB → 323KB（-91%）**。

⚠️ **base64 分片只切不填充**：先整体 base64，再按固定长度切片，运行时 `join('')` 拼回。
不要在每片前面加 `"序号|"` 之类前缀（会破坏 base64 结构，`atob` 直接抛错），
也**不要**让每片各自 base64（`=` 填充会错位）。需要保序就用数组下标，数组本身就是有序的。

## 二、解密：只用原生 API，绝不逐字符拼字符串

**这是启动卡顿的头号元凶**。`out += String.fromCharCode(...)` 循环上百万次是 O(n²) 级开销，
数据一大就是几秒白屏。

```js
function __dec(){
  var s = atob(__E.join(''));                    // 原生 join + 原生 base64
  var n = s.length, K = __KEY, k = K.length;
  var buf = new Uint8Array(n);                   // 定型数组，非字符串拼接
  for (var i = 0; i < n; i++) buf[i] = s.charCodeAt(i) ^ K[i % k];
  var raw = pako.inflate(buf);                   // zlib 解压
  return new TextDecoder('utf-8').decode(raw);   // 原生 UTF-8 解码
}
```

- 解压库内联 `pako_inflate.min.js`（21KB，`pako@2.1.0/dist/pako_inflate.min.js`，UMD 会挂 `window.pako`）。
  它比 `DecompressionStream` 兼容性好、且是同步 API，省掉把初始化改成异步的麻烦。
- 别用「对 base64 字符本身异或」——base64 字符集与明文字节无关，异或出来不是原始字节。
  顺序必须是 **base64 解码 → 字节异或 → inflate**。

## 三、DOM 数量：能懒就懒

一次性渲染上千个可折叠面板，节点轻松上 3 万，手机直接卡死。

```js
// 渲染时只留空壳 + 一个取数据的 key
html += '<span class="btn" data-menu="'+r.id+'">菜单('+r.mn+')</span>'
      + '<div class="panel"></div>';

// 点开时才构建
if (opened && panel && !panel.dataset.done) {
  panel.innerHTML = buildPanel(DATA[mid] || []);
  panel.dataset.done = '1';
}
```

配套：数据侧把可折叠内容**从每条记录里提取到一张 `{id: content}` 表**，
记录里只留 `id` 和数量 —— 既支持懒渲染，也避免同一份数据在页面里出现两次。

实测：**28518 → 11162 节点（-61%）**，展开耗时 **~1300ms → 0.6ms**。

## 四、多视图切换：切显隐，别重建

有 Tab / 分区的页面，切一次重建一次 DOM 是常见的卡顿来源。

```html
<div id="pane0" class="pane on"></div>
<div id="pane1" class="pane"></div>
```
```css
.pane{display:none} .pane.on{display:block}
```
```js
/* 用状态签名判断能否复用，条件没变就不重建 */
var paneState = [null, null];
for (var j=0;j<panes.length;j++) panes[j].className = 'pane'+(j===i?' on':'');
var sig = q.value+'|'+only.checked+'|'+located;
if (paneState[i] && paneState[i].sig === sig) {
  cnt.textContent = paneState[i].cnt;          // 工具条状态也要一起缓存
  empty.style.display = paneState[i].hasEmpty ? 'block' : 'none';
} else {
  render(i);
}
```

**易漏**：切换时工具栏上的统计数字/空态提示也得跟着恢复，只切 `display` 会把上一个视图的数字留在屏幕上。

实测：**切换 240–1300ms → 0.1–5.5ms**。

## 五、首屏：分块渲染，别阻塞主线程

即使节点总量可控，一次性 `innerHTML` 上万节点仍会卡住主线程。

```js
/* 构建时按「城市/分组」切成块 */
chunks.push(cg);

function paint(head, chunks){
  pane.innerHTML = head + (chunks[0] || '');     // 首屏立刻可读可点
  var ci = 1;
  (function more(){
    var added = 0;
    do { pane.insertAdjacentHTML('beforeend', chunks[ci]); added += chunks[ci].length; ci++; }
    while (ci < chunks.length && added < 24000);  // 按字符数控每帧预算
    if (ci < chunks.length) setTimeout(more, 0);
  })();
}
```

- 用**字符数**而不是块数做每帧预算，块大小不均时更稳。
- 加一个 `seq` 序号：重新渲染时让旧的续插任务自行退出，避免两次渲染的内容混在一起。

实测：**首屏可交互 0.34s**。

## 六、滚动卡顿：两个高频真凶

前面五项解决的是「打开慢 / 切换卡」，**滚动卡是另一个独立问题**，多是这两处造成的：

**① `backdrop-filter: blur()` 挂在吸顶栏上**

```css
/* 坏：安卓 WebView 滚动时每帧都要对下方内容实时模糊，掉帧极严重 */
.bar{ position:sticky; top:0; backdrop-filter:blur(8px); background:rgba(244,241,236,.93) }
/* 好：用不透明实色替代，视觉损失很小，性能天差地别 */
.bar{ position:sticky; top:0; background:#f5f2ed }
```

模糊、`filter`、大范围 `box-shadow` 在移动端都是逐帧成本。桌面浏览器上很难复现，必须按移动端标准取舍。

**② 屏幕外元素仍在参与布局**

```css
.rest{ content-visibility:auto; contain-intrinsic-size:auto 215px }
```

`content-visibility:auto` 让浏览器**跳过屏幕外元素的渲染与布局**，长列表滚动开销能降一个量级。
`contain-intrinsic-size` 必须给，否则滚动条会抖；用 `auto <估计高度>` 的形式让浏览器记住实测值，
首次渲染后即自动校准。粒度选**单个卡片**（而不是整块分组）效果最好。

实测：201 段滚动到底的同步耗时 **1ms、零长任务**；卡片实测高 244px，配置里估 215px 也没出现跳动。

**验证方法**（别靠肉眼）：

```js
// 分步滚动 + 每步强制布局，同时收集 longtask
window.__lt=[];
new PerformanceObserver(l=>{for(const e of l.getEntries()) window.__lt.push(e.duration)})
  .observe({entryTypes:['longtask']});
const t0=performance.now();
for(let y=0;y<document.body.scrollHeight;y+=700){ window.scrollTo(0,y); document.body.offsetHeight; }
console.log(performance.now()-t0, window.__lt.length);
```

顺带：`setInterval` 里做 DOM 操作时加 `if(document.hidden) return`，后台标签别空转；
图片加 `decoding="async"`，避免滚动时主线程被解码阻塞。

## 七、坐标/聚合类数据的一个通用坑

**聚合层的坐标 ≠ 明细层的坐标。** 定位、距离排序这类功能，务必优先用**明细级坐标**。

真实案例：某平台站点的"城市"字段其实是**运营区域**（形如"华南一区""XX站段"），
而城市坐标表里只有真实城市名 —— 查不到 → 坐标全为 null → 定位功能对这批数据**完全静默失效**（不报错、只是没反应）。

规则：
1. 明细（站点/门店）自带经纬度时，**一律用明细坐标**算距离，别用分组中心。
2. 分组距离取**组内最近明细的距离**，而不是组内坐标的几何中心 ——
   否则一个大区域会被平均坐标拖远，排序结果与直觉不符。
3. 「离你最近」要**遍历全部明细取全局最小值**，不能取「第一个有坐标的分组里的第一个」。
4. 坐标缺失时要能降级（城市中心兜底），并在 UI 上说明精度差异。

## 八、其他易忽略的细节

- `window.scrollTo({behavior:'smooth'})` 在切换场景下会**放大卡顿感**，该瞬时就瞬时。
- 图片 `loading="lazy"` + `decoding="async"`，并确保 CSS 给了固定宽高（否则滚动时布局抖动）。
- 图床缩放因云而异：阿里云 OSS 是 `?x-oss-process=image/resize,m_fill,w_,h_`，
  腾讯云数据万象是 `?imageMogr2/thumbnail/WxH` —— **两者不通用**（用错会返回原图，HTTP 200 但体积不变）。
  已带处理参数的 URL 一律原样使用，避免参数冲突。
- 定位、筛选这类会改变排序/结果的操作，记得让所有视图的缓存失效。

## 验收清单

改完逐项量一遍，别只看单张截图：

- [ ] 页面体积（目标：几 MB → 几百 KB）
- [ ] DOM 节点数（目标：< 1.5 万）
- [ ] 首屏可交互时间
- [ ] 视图切换耗时（页面内 `performance.now()` 计时）
- [ ] 折叠面板展开耗时
- [ ] 搜索/筛选响应耗时
- [ ] 0 JS 错误、无横向溢出
- [ ] 防盗/加密后**明文仍搜不到**（`grep` 关键内容应为 0）
