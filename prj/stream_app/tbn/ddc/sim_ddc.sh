#!/bin/sh
# Bit-exact check of osc_ddc against the Python model (needs iverilog, numpy).
#   cd prj/stream_app/tbn/ddc && ./sim_ddc.sh
set -e
cd "$(dirname "$0")"
RTL=../../rtl/rtl/rp_oscilloscope
MODEL=../../ddc/ddc_model.py
OUT=build
mkdir -p $OUT

python3 $MODEL vectors $OUT
iverilog -g2005 -Wall -o $OUT/osc_ddc_tb.vvp osc_ddc_tb.v \
  $RTL/osc_ddc.v $RTL/osc_ddc_cic.v $RTL/osc_ddc_fir.v \
  $RTL/osc_ddc_fir_coef.v $RTL/osc_ddc_sin_rom.v
(cd $OUT && vvp -n osc_ddc_tb.vvp)
python3 $MODEL compare $OUT
