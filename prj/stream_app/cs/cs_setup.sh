#!/bin/sh
# Run on the Red Pitaya to configure the compressive-measurement block.
#
#   ./cs_setup.sh on  [N] [L] [SHIFT] [weights.txt]   defaults: 10 1000 20 (built-in +/-1 table)
#   ./cs_setup.sh off
#
# weights.txt: one integer weight per line, Q1.15 (32767 = +1.0, -32768 = -1.0),
# at least L lines. Loading 1000 weights takes a few seconds.
# Registers (oscilloscope block at 0x40000000): 0x88 enable (bit0 CH1, bit1 CH2),
# 0x8C N, 0x90 L, 0x94 shift, 0x98 table pointer, 0xB0 table data.
set -e
BASE=0x40000000
reg() { printf "0x%08x" $(( BASE + $1 )); }

case "$1" in
  off)
    monitor $(reg 0x88) 0
    echo "compressive measurement disabled"
    exit 0 ;;
  on) ;;
  *) echo "usage: $0 on [N] [L] [SHIFT] [weights.txt] | off"; exit 1 ;;
esac

N=${2:-10}
L=${3:-1000}
SHIFT=${4:-20}
TABLE=$5

[ $((L % N)) -eq 0 ] || { echo "L=$L must be a multiple of N=$N"; exit 1; }
[ "$L" -le 1024 ] || { echo "L must be <= 1024"; exit 1; }

monitor $(reg 0x88) 0                   # disable while reconfiguring
monitor $(reg 0x8C) $N
monitor $(reg 0x90) $L
monitor $(reg 0x94) $SHIFT

if [ -n "$TABLE" ]; then
  monitor $(reg 0x98) 0
  head -n "$L" "$TABLE" | while read -r W; do
    monitor $(reg 0xB0) $(( W & 0xFFFF ))
  done
  echo "loaded $L weights from $TABLE"
fi

monitor $(reg 0x88) 3                   # enable CH1 and CH2
echo "compressive measurement on: N=$N ($N:1), L=$L ($((L / N)) rows), shift=$SHIFT"
