#!/bin/sh
# WED 诊断：证明 mt7915e 不支持运行时热重载
#
# 用途：当你想"先热启用 WED 试试、不行再退"时，跑一遍这个脚本就知道行不通。
#       结论已经确定：rmmod 返回 0 但内核抛 WARNING，且 modprobe 传的参数不生效。
#       这个脚本的价值是把证据留在日志里，省掉下一次重复试探的时间。
#
# 用法：sh wed_diag.sh && cat /tmp/wed_diag.log
# 注意：会短暂 wifi down/up，期间断网约 25 秒。远程只能靠 Wi-Fi 管理时慎用。

exec >/tmp/wed_diag.log 2>&1
echo "=== diag start ==="

echo "--- 现状 ---"
lsmod | grep mt7915e
echo "wed_enable=$(cat /sys/module/mt7915e/parameters/wed_enable)"

echo "--- wifi down ---"
wifi down
sleep 4
lsmod | grep mt7915e

# 看这里：rmmod 的返回码是 0（"成功"），但 dmesg 里会有一条 WARNING，
# 调用栈里带 mt7915e+0x...。驱动没有实现正确的模块卸载路径。
rmmod mt7915e 2>&1
echo "rmmod rc=$?"
lsmod | grep mt7915e || echo "已卸载"
sleep 1

# 再看这里：即使卸载后带参数重新 modprobe，参数也不会生效。
modprobe mt7915e wed_enable=1 2>&1
echo "modprobe rc=$?"
sleep 3
lsmod | grep mt7915e
echo "wed_enable=$(cat /sys/module/mt7915e/parameters/wed_enable)"

echo "--- modinfo parm（确认驱动确实声明了这个参数）---"
modinfo mt7915e 2>&1 | grep -i parm

echo "--- 配置来源 ---"
ls /etc/modprobe.d/ 2>&1
echo "[modules.conf]"; cat /etc/modules.conf 2>&1
echo "[modules.d/mt7915e]"; cat /etc/modules.d/mt7915e 2>&1

echo "--- dmesg 尾部 ---"
dmesg 2>/dev/null | tail -12

echo "--- wifi up ---"
wifi up
sleep 18
iw dev | grep -E "Interface|ssid|channel"
echo "=== diag done ==="
