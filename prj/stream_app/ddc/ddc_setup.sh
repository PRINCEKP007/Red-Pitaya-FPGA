#!/bin/sh
# Run on the Red Pitaya to enable the DDC.
#   ./ddc_setup.sh <fc_hz_ch1> [fc_hz_ch2]     e.g. ./ddc_setup.sh 20e6 20e6
#   ./ddc_setup.sh off
# Registers (oscilloscope block at 0x40000000):
#   0x88 DDC control (bit0 = CH1, bit1 = CH2), 0x8C / 0x90 NCO phase increment CH1 / CH2
set -e
BASE=0x40000000

reg() { printf "0x%08x" $(( BASE + $1 )); }

if [ "$1" = "off" ]; then
  monitor $(reg 0x88) 0
  echo "DDC disabled"
  exit 0
fi

[ -n "$1" ] || { echo "usage: $0 <fc_hz_ch1> [fc_hz_ch2] | off"; exit 1; }

pinc() { awk -v f="$1" 'BEGIN { printf "0x%08x", int(f / 125e6 * 4294967296 + 0.5) % 4294967296 }'; }

EN=1
monitor $(reg 0x8C) $(pinc "$1")
if [ -n "$2" ]; then
  monitor $(reg 0x90) $(pinc "$2")
  EN=3
fi
monitor $(reg 0x88) $EN
echo "DDC on: CH1 fc=$1 ${2:+CH2 fc=$2} -> 6.25 MSPS complex, 25 MB/s per channel"
