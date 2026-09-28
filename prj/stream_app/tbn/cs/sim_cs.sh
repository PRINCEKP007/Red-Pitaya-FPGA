#!/bin/sh
# Bit-exact check of osc_cs against the Python model (needs iverilog, numpy).
#   cd prj/stream_app/tbn/cs && ./sim_cs.sh
set -e
cd "$(dirname "$0")"
RTL=../../rtl/rtl/rp_oscilloscope
MODEL=../../cs/cs_model.py
OUT=build
mkdir -p $OUT

iverilog -g2005 -Wall -o $OUT/osc_cs_tb.vvp osc_cs_tb.v $RTL/osc_cs.v $RTL/osc_cs_tbl.v
for CASE in 0 1; do
  python3 $MODEL vectors $OUT $CASE
  (cd $OUT && vvp -n osc_cs_tb.vvp)
  python3 $MODEL compare $OUT
done
