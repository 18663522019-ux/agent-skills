#!/bin/sh
# WED 开机自检回滚守护（放在路由器上，挂在 /etc/rc.local）
#
# 背景：MT7981 的 WED（Wireless Ethernet Dispatch，无线硬件卸载）能带来最大收益，
#       但启用方式只有"写 /etc/modules.conf + 重启"这一条路。重启有风险——
#       如果 WED 起不来，AP 接口不会出现，此时若只能靠 Wi-Fi 管理路由器，就彻底失联了。
#
# 本脚本做的事：开机后轮询 180 秒，只要 AP 接口数 >= 2 就认定启动正常：
#   - 正常 -> 把自己从 /etc/rc.local 里删掉（只在"变更后第一次启动"跑一次，不做常驻开销）
#   - 失败 -> 抹掉 modules.conf 里的 wed 参数并自动重启，回到上一版可用配置
#
# 安装：
#   cp wed_guard.sh /root/wed_guard.sh && chmod +x /root/wed_guard.sh
#   在 /etc/rc.local 的 exit 0 之前加一行：
#     start-stop-daemon -S -b -x /root/wed_guard.sh
#
# ⚠️ 必须用 start-stop-daemon -S -b 拉起，不能用 `nohup ... &` 或裸 `&`：
#    SSH 会话一断，后台子进程会被回收，守护跑不完就没了。

LOG=/root/wed_guard.log
exec >>$LOG 2>&1
echo "=== guard $(date) ==="
echo "wed_enable=$(cat /sys/module/mt7915e/parameters/wed_enable 2>/dev/null)"

ok=0
i=0
while [ $i -lt 36 ]; do
  i=$((i+1)); sleep 5
  n=$(iw dev 2>/dev/null | grep -c "ap0")
  if [ "$n" -ge 2 ]; then ok=1; echo "AP_OK at $((i*5))s (ap_ifaces=$n)"; break; fi
done

if [ "$ok" = "1" ]; then
  echo "keep WED"
  sed -i "/wed_guard/d" /etc/rc.local
  echo "guard 已注销"
else
  echo "AP_FAIL -> 回滚 WED 并重启"
  sed -i "/options mt7915e/d" /etc/modules.conf
  sed -i "s|^mt7915e.*|mt7915e|" /etc/modules.d/mt7915e
  sync
  sleep 2
  reboot
fi
