# Compressive measurement (osc_cs) for stream_app

Instead of sending every 125 MSPS sample, the FPGA sends one **linear
combination of N samples** per block:

```
y[m] = w[mN]·x[mN] + w[mN+1]·x[mN+1] + … + w[mN+N-1]·x[mN+N-1]
```

With N = 10 that is **10:1 compression**: 12.5 M measurements/s = 25 MB/s
per channel (50 MB/s for both). This fits the 62.5 MB/s streaming limit, and every ADC
sample at the full 125 MSPS contributes to a measurement.

## Measurement matrix Φ

The weights come from a table of L entries (L a multiple of N, up to 1024)
that repeats. One table period (L samples) gives P = L/N measurements:

```
        ┌ w0..w9                        ┐   x0..x9
y = Φx, │        w10..w19               │ · x10..x19          Φ is P × L,
        │                 ⋱             │   …                 block-diagonal
        └                   w(L-10)..   ┘   x(L-10)..x(L-1)
```

- **Default table:** ±1 pseudo-random (Bernoulli), the classic "random
  demodulator". It's built into the bitstream and needs no loading.
- **Your own Φ:** load any weights (Q1.15, 32767 ≈ +1.0) with `cs_setup.sh`.

Every output word carries a **frame flag** in bit 0, set on the measurement
that uses row 0 of Φ. The decoder uses it to line measurements up with Φ, even if
the streaming client drops a buffer.

## Hardware (osc_cs.v)

```
x ─► [ × w ] ─► [ accumulate N samples ] ─► [ round, >> shift, saturate ] ─► {y[14:0], frame}
       ▲
  weight table[pos]      pos = sample count mod L (osc_cs_tbl.v, 1024 × 16-bit block RAM)
```

| Stage | What happens |
|---|---|
| counters | `blk` counts 0..N-1 inside a block, `pos` counts 0..L-1 through the table. Both restart when the block is disabled. |
| 1 | read weight `w = table[pos]`, delay `x` one clock to line up |
| 2 | `prod = x · w` (one DSP48 multiplier) |
| 3 | `acc = prod` on the first sample of a block, `acc += prod` otherwise |
| 4 | after the last sample: `y = round(acc >> shift)`, saturate to 15 bits, output `{y, frame}` |

Resources per channel: 1 DSP48, 1 block RAM, and a small amount of logic.

**Scaling.** `acc` is Σ x·w with x and w in Q1.15, so `acc / 2³⁰` is the sum in
real units (full-scale input = 1.0, weight 32767 = 1.0). The output is
`y = acc >> shift`, which covers ±2^(shift−16) in real units:

| shift | range of Σ w·x | resolution |
|---|---|---|
| 20 (default) | ±16 | 1/1024 |
| 19 | ±8 | 1/2048 |
| 18 | ±4 | 1/4096 |

With ±1 weights and N = 10, |Σ w·x| ≤ 10, so shift 20 can never saturate.
Signals well below full scale can use a smaller shift for more resolution.
`cs_decode.py` reports how many measurements saturated.

## Registers (oscilloscope block, base 0x40000000)

| Offset | Name | Meaning |
|---|---|---|
| 0x88 | CS_CTRL | bit0 = CH1, bit1 = CH2. When on, the measurement replaces the decimator output. |
| 0x8C | CS_BLK_LEN | N, samples per measurement (default 10) |
| 0x90 | CS_TBL_LEN | L, table period, a multiple of N (default 1000) |
| 0x94 | CS_SHIFT | output shift (default 20) |
| 0x98 | CS_TBL_ADDR | table write pointer |
| 0xB0 | CS_TBL_DATA | writing stores a weight at the pointer and increments the pointer |

Both channels share N, L, shift and the table. The table can be written but
not read back.

## Using it

On the Red Pitaya (copy `cs_setup.sh` over):
```sh
./cs_setup.sh on                          # N=10, L=1000, shift=20, built-in ±1 table
./cs_setup.sh on 10 1000 19 phi.txt       # your own weights (one Q1.15 integer per line)
./cs_setup.sh off
```
Then stream as usual, in continuous mode with 16-bit samples. The decimation setting is
ignored for enabled channels, and the level trigger does not apply to measurements.
The client will label the data with its own sample rate. The real rate is 125 MSPS / N.

On the PC:
```sh
python3 cs_decode.py capture.bin -N 10 -L 1000 --shift 20 [--table phi.txt]
```
This writes `capture.bin.npz` with `Y` (one row of P measurements per table
period) and `Phi` (P × L), so that `Y[k] = Phi @ x_k`. Feed these to your
reconstruction. `cs_model.py report` runs a small OMP example that recovers
3 tones from 100 measurements of 1000 samples.

## Verification

```sh
python3 cs/cs_model.py report     # rates + sparse-recovery demo
tbn/cs/sim_cs.sh                  # Verilog vs bit-exact Python model, must print PASS twice
```

`sim_cs.sh` tests two configurations: the defaults, and N=7, L=994, shift=16
with table writes and saturation. If you change the default table, edit
`default_table()` in `cs_model.py` and run
`python3 cs_model.py gen-rtl ../rtl/rtl/rp_oscilloscope`.
