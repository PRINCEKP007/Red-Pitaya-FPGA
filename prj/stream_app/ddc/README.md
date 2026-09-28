# Digital down-converter (DDC) for stream_app

The DDC turns each 125 MSPS ADC channel into complex baseband I/Q around a
programmable centre frequency `fc`:

| | Raw ADC | DDC output |
|---|---|---|
| Sample rate | 125 MSPS real | 6.25 MSPS complex |
| Bandwidth kept | 0–62.5 MHz | fc ± 2.5 MHz |
| Data rate / channel | 250 MB/s | **25 MB/s** |
| Both channels | 500 MB/s | **50 MB/s** (fits the 62.5 MB/s TCP limit) |

Plain decimation throws away samples, so everything above the new Nyquist
frequency folds into your band. The DDC instead shifts your band to 0 Hz and
filters away everything outside it *before* reducing the rate, so the 5 MHz
you care about arrives intact.

## Signal chain

```
ADC ─► calib/filter ─►┬─► × cos ─► CIC ↓10 ─► FIR ↓2 ─► I ─┐
      (existing)      │                                    ├─► I,Q,I,Q… ─► trigger/acquire/DMA (existing)
                      └─► ×−sin ─► CIC ↓10 ─► FIR ↓2 ─► Q ─┘
                            ▲
                           NCO (fc register)
```

| Block | File | What it does |
|---|---|---|
| NCO | `osc_ddc.v`, `osc_ddc_sin_rom.v` | A 32-bit phase counter adds `pinc` every clock; its top 10 bits address a sine table. `fc = pinc × 125 MHz / 2³²` (0.03 Hz steps). |
| Mixer | `osc_ddc.v` | `I = x·cos`, `Q = −x·sin`. Multiplying by `e^(−j2πfc·t)` moves `fc` to 0 Hz. Uses 2 DSP48 multipliers. |
| CIC ↓10 | `osc_ddc_cic.v` | 5 integrators → keep 1 of 10 → 5 differentiators. A cheap low-pass filter with no multipliers, 125 → 12.5 MSPS. Its passband sags by about 2.3 dB at 2.5 MHz ("droop"). |
| FIR ↓2 | `osc_ddc_fir.v`, `osc_ddc_fir_coef.v` | 40-tap filter: flat to 2.5 MHz (it also cancels the CIC droop), stops from 3.75 MHz. 12.5 → 6.25 MSPS. One multiplier per path does all 20 symmetric tap pairs, one per clock. |
| Packer | `osc_ddc.v` | Outputs I then Q as 16-bit words. Bit 0 is a tag (0 = I, 1 = Q) so the host can find the pairing anywhere in the stream. |

Measured performance (from the model): passband ripple 0.1 dB, alias
rejection ≥ 60 dB at the band edges (better toward the centre), NCO spurs about −60 dBc.

**Scaling:** a real tone of amplitude A at the ADC gives |I + jQ| = A/2. The
factor ½ is inherent to real→complex conversion and leaves 6 dB of headroom.
Values are 15-bit (full scale = 1.0 ↔ 16384).

## Registers (oscilloscope block, base 0x40000000)

| Offset | Name | Meaning |
|---|---|---|
| 0x88 | DDC_CTRL | bit0 = DDC on CH1, bit1 = DDC on CH2. When on, the DDC replaces the decimator output. |
| 0x8C | DDC_PINC_CH1 | `round(fc / 125e6 × 2³²)` |
| 0x90 | DDC_PINC_CH2 | same for CH2 |

On the board: `./ddc_setup.sh 20e6 20e6` (or `./ddc_setup.sh off`).

Notes when the DDC is enabled:
- The decimation register is ignored for that channel, and the stream rate is
  always 12.5 Mwords/s (6.25 MSPS I/Q). Use 16-bit mode, not the 8-bit mode.
- The level trigger sees I/Q words, not the waveform, so use continuous streaming.
- Tell your client software the rate is 6.25 MSPS complex, then decode with
  `python3 ddc_decode.py capture.bin --fc 20e6` → `capture.bin.iq.npy`.

## Files and verification flow

```
ddc/ddc_model.py        bit-exact Python model; designs the FIR, generates the two ROM .v files
ddc/ddc_decode.py       host-side decoder (int16 words -> complex numpy array)
ddc/ddc_setup.sh        register setup on the board (uses `monitor`)
tbn/ddc/osc_ddc_tb.v    testbench: feeds a test signal, records the output words
tbn/ddc/sim_ddc.sh      runs model + Icarus Verilog + compare -> PASS/FAIL
```

```sh
python3 ddc/ddc_model.py report        # filter response, alias rejection, tone test
tbn/ddc/sim_ddc.sh                     # RTL vs model, must print PASS
```

If you change a parameter such as the filter bands or tap count, edit `ddc_model.py`, run
`python3 ddc_model.py gen-rtl ../rtl/rtl/rp_oscilloscope`, then re-run
`sim_ddc.sh`. The model and RTL must stay bit-exact.

Resources per channel: 4 DSP48 slices, 1–2 block RAMs (sine table), and about
1k LUT/FF. Both channels fit easily in the Zynq-7010.
