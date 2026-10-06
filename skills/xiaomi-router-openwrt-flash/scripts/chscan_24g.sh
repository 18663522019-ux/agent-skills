#!/bin/sh
# 2.4G 信道占用率实测（survey 差值法）
#
# 为什么必须用差值法：
#   `iw dev <if> survey dump` 输出的 channel active/busy time 是**上电以来的累计值**，
#   直接读没有任何意义（跑了一天的机器，busy 数字永远很大）。
#   正确做法是隔一段时间采两次，用增量算忙率：
#       忙率 = (busy2 - busy1) * 100 / (active2 - active1)
#
# 为什么不能只用 `iw dev <if> survey dump` 看 "noise"：
#   噪声底在 20MHz 信道之间的差异只有几 dB，区分度不够；忙率是直接可比的。
#
# 用法：sh chscan_24g.sh
#   SSID 环境变量指定 2.4G 的 SSID（接口名会随驱动重载在 phy0-ap0 / phy6-ap0 之间变，
#   所以脚本用 SSID 反查接口名，不要写死 phy0-ap0）。
#   CAT 环境变量指定要对比的信道，默认 "1 6 11"。
#
# 实测参考（居民区，AX3000T + mt7976，HE20）：
#   ch1 ≈ 74%、ch6 ≈ 66%、ch11 ≈ 58%  -> 选 ch11

exec >/tmp/chscan.log 2>&1
SSID="${SSID:-XIAOMIUP-2G}"
CAT="${CAT:-1 6 11}"

# 采样函数：输出三列 "频率 active_time busy_time"
dump() {
  iw dev "$1" survey dump 2>/dev/null | awk '
    /frequency/          { c = $2 }
    /channel active time/{ a[c] = $4 }
    /channel busy time/  { b[c] = $4 }
    END { for (k in a) if (a[k] > 0) printf "%s %s %s\n", k, a[k], b[k] }
  '
}

for ch in $CAT; do
  echo "=== ch$ch ==="
  uci set wireless.radio0.channel=$ch
  uci commit wireless
  wifi reload >/dev/null 2>&1
  sleep 22

  IF=$(iw dev 2>/dev/null | awk -v s="$SSID" '/Interface/{i=$2} $0 ~ ("ssid " s){print i}')
  echo "  2.4G iface = $IF"
  if [ -z "$IF" ]; then echo "  (接口未找到，跳过)"; continue; fi

  dump "$IF" > /tmp/a.txt
  sleep 15
  dump "$IF" > /tmp/b.txt

  awk -v CH="$ch" '
    NR == FNR { aa[$1] = $2; bb[$1] = $3; next }
    {
      da = $2 - aa[$1]; db = $3 - bb[$1]
      if (da > 1000) printf "  ==> ch%s (%s MHz): 忙率 %d%% (active+%dms busy+%dms)\n", CH, $1, db * 100 / da, da, db
    }
  ' /tmp/a.txt /tmp/b.txt

  iw dev | grep -E "Interface|ssid|channel" | head -6
done
echo "=== done ==="
