#!/usr/bin/env python3
"""Bit-exact reference model of the osc_ddc digital down-converter.

The FPGA block turns the real 125 MSPS ADC stream into complex baseband
(I/Q) at 6.25 MSPS, centred on a programmable frequency fc, covering
fc +/- 2.5 MHz:

    x[n] --> x cos / x -sin (NCO) --> CIC decimate by 10 --> FIR decimate by 2 --> I/Q words

Every integer operation here mirrors the Verilog exactly, so the RTL
testbench output must match this model word for word.

Usage:
    python3 ddc_model.py report            # filter design + spectral checks
    python3 ddc_model.py gen-rtl <dir>     # regenerate sine ROM + FIR coefficient ROM
    python3 ddc_model.py vectors <dir>     # write simulation input/expected files
    python3 ddc_model.py compare <dir>     # compare RTL output with expected
"""
import sys
import os
import numpy as np

FS         = 125e6      # ADC sample rate
PHASE_BITS = 32         # NCO phase accumulator width
LUT_BITS   = 10         # NCO sine table address bits (1024 entries)
LUT_AMP    = 32767      # NCO sine amplitude (Q1.15)
MIX_SHIFT  = 13         # mixer product 16x16 -> 18 bit
CIC_N      = 5          # CIC stages
CIC_R      = 10         # CIC decimation
CIC_W      = 35         # CIC register width: 18 + ceil(N*log2(R))
CIC_SHIFT  = 17         # CIC output: 35 -> 18 bit
FIR_TAPS   = 40         # symmetric FIR, decimates by 2
COEF_BITS  = 18         # FIR coefficient width (Q1.17)
FIR_SHIFT  = 20         # FIR accumulator -> 15 bit output

FS_CIC = FS / CIC_R             # 12.5 MSPS
FS_OUT = FS_CIC / 2             # 6.25 MSPS
BAND   = 2.5e6                  # usable half-bandwidth (fc +/- 2.5 MHz)


def pinc_for(fc):
    """NCO phase increment register value for centre frequency fc [Hz]."""
    return int(round(fc / FS * 2**PHASE_BITS)) % 2**PHASE_BITS


def sin_lut():
    n = np.arange(2**LUT_BITS)
    return np.round(LUT_AMP * np.sin(2 * np.pi * n / 2**LUT_BITS)).astype(np.int64)


def cic_gain():
    """CIC DC gain after the output shift (R^N / 2^CIC_SHIFT)."""
    return CIC_R**CIC_N / 2**CIC_SHIFT


def cic_response(f):
    """CIC magnitude response normalised to 1 at DC; f in Hz at the 125 MSPS rate."""
    f = np.asarray(f, dtype=float)
    x = np.pi * f / FS
    with np.errstate(invalid='ignore', divide='ignore'):
        h = np.sin(CIC_R * x) / (CIC_R * np.sin(x))
    h = np.where(np.abs(np.sin(x)) < 1e-12, 1.0, h)
    return np.abs(h) ** CIC_N


def design_fir():
    """Weighted least-squares design of the symmetric 40-tap decimate-by-2 FIR.

    Passband (0..2.5 MHz) inverts the CIC droop and the CIC gain, so the
    overall DC gain from a real input tone to the complex output is 1/2.
    Stopband starts at 3.75 MHz: anything above that would alias into the
    +/-2.5 MHz band after decimating to 6.25 MSPS.
    """
    half = FIR_TAPS // 2
    k = np.arange(half) + 0.5                       # type II symmetric filter
    fp = np.linspace(0, BAND, 400)
    fs_ = np.linspace(FS_OUT - BAND, FS_CIC / 2, 600)
    f = np.concatenate([fp, fs_])
    w = 2 * np.pi * f / FS_CIC
    desired = np.concatenate([1 / (cic_gain() * cic_response(fp)), np.zeros_like(fs_)])
    weight = np.concatenate([np.ones_like(fp), 30 * np.ones_like(fs_)])
    a = 2 * np.cos(np.outer(w, k))                  # H(w) = sum 2 h_k cos(w (k+0.5))
    b, *_ = np.linalg.lstsq(a * weight[:, None], desired * weight, rcond=None)
    h = np.concatenate([b[::-1], b])                # taps 0..39, symmetric
    return h


def fir_coefs():
    """Quantised coefficients; only the first half is stored (the rest mirrors it)."""
    h = design_fir()
    c = np.round(h * 2**(COEF_BITS - 1)).astype(np.int64)
    assert np.all(np.abs(c) < 2**(COEF_BITS - 1)), "coefficient overflow"
    return c


def wrap(v, bits):
    """Two's-complement wrap-around, like a Verilog register of width `bits`."""
    m = 1 << bits
    return ((v + (m >> 1)) % m) - (m >> 1)


def ddc(x, pinc):
    """Run the fixed-point DDC. x: int16 samples. Returns (i15, q15) integer arrays."""
    lut = sin_lut()
    x = np.asarray(x, dtype=np.int64)
    n = np.arange(len(x), dtype=np.int64)
    phase = (n * pinc) % 2**PHASE_BITS
    addr = phase >> (PHASE_BITS - LUT_BITS)
    cos = lut[(addr + 2**(LUT_BITS - 2)) % 2**LUT_BITS]
    sin = lut[addr]
    rnd = 1 << (MIX_SHIFT - 1)
    mix_i = (x * cos + rnd) >> MIX_SHIFT
    mix_q = -((x * sin + rnd) >> MIX_SHIFT)

    cic_i = cic(mix_i)
    cic_q = cic(mix_q)
    return fir(cic_i), fir(cic_q)


def cic(x):
    """CIC decimator, mirrors the register-level behaviour of osc_ddc_cic."""
    integ = [0] * CIC_N
    samples = []
    cnt = 0
    for v in x:
        if cnt == CIC_R - 1:
            samples.append(integ[-1])      # old value of the last integrator
            cnt = 0
        else:
            cnt += 1
        new = [wrap(integ[0] + int(v), CIC_W)]
        for s in range(1, CIC_N):
            new.append(wrap(integ[s] + integ[s - 1], CIC_W))
        integ = new
    comb = [0] * CIC_N
    dly = [0] * CIC_N
    out = []
    for s_in in samples:
        prev = [s_in] + comb[:-1]           # inputs are the old register values
        comb = [wrap(prev[s] - dly[s], CIC_W) for s in range(CIC_N)]
        dly = prev
        out.append(comb[-1] >> CIC_SHIFT)
    return np.array(out, dtype=np.int64)


def fir(x):
    """Symmetric decimate-by-2 FIR, mirrors osc_ddc_fir. Output is 15 bit signed."""
    c = fir_coefs()
    half = FIR_TAPS // 2
    xp = np.concatenate([np.zeros(FIR_TAPS, dtype=np.int64), np.asarray(x, dtype=np.int64)])
    out = []
    for m in range(1, len(x), 2):           # after every second input sample
        base = m + FIR_TAPS
        acc = 0
        for k in range(half):
            acc += int(c[k]) * int(xp[base - k] + xp[base - (FIR_TAPS - 1) + k])
        y = (acc + (1 << (FIR_SHIFT - 1))) >> FIR_SHIFT
        out.append(min(max(y, -2**14), 2**14 - 1))
    return np.array(out, dtype=np.int64)


def pack(i15, q15):
    """Interleave to the 16-bit DMA words: I has LSB=0, Q has LSB=1."""
    w = np.empty(2 * len(i15), dtype=np.int64)
    w[0::2] = (i15 << 1)
    w[1::2] = (q15 << 1) | 1
    return w.astype(np.int16)


def unpack(words):
    """Host-side decoder: int16 DMA words -> complex baseband (full scale = 1.0).

    The LSB tag finds the I/Q alignment, so decoding works from any start
    point in the stream (e.g. after a dropped DMA buffer).
    """
    w = np.asarray(words, dtype=np.int16)
    tag = w & 1
    start = int(np.argmax((tag[:-1] == 0) & (tag[1:] == 1)))
    w = w[start:]
    w = w[: len(w) // 2 * 2]
    i = (w[0::2].astype(np.int32) >> 1) / 2**14
    q = (w[1::2].astype(np.int32) >> 1) / 2**14
    return i + 1j * q


# ---------------------------------------------------------------------------
# RTL generation
# ---------------------------------------------------------------------------

def gen_rtl(outdir):
    lut = sin_lut()
    lines = ["`timescale 1ns / 1ps",
             "// Generated by prj/stream_app/ddc/ddc_model.py - do not edit.",
             "// NCO sine table, %d x 16 bit (cos = sin addressed 90 degrees ahead)." % len(lut),
             "module osc_ddc_sin_rom (",
             "  input  wire                 clk,",
             "  input  wire [%d-1:0]        addr_a,   // sin" % LUT_BITS,
             "  input  wire [%d-1:0]        addr_b,   // cos" % LUT_BITS,
             "  output reg  signed [16-1:0] dat_a,",
             "  output reg  signed [16-1:0] dat_b",
             ");",
             "",
             "reg signed [16-1:0] rom [0:%d];" % (len(lut) - 1),
             "",
             "initial begin"]
    for a, v in enumerate(lut):
        lines.append("  rom[%4d] = %s16'sd%d;" % (a, "-" if v < 0 else " ", abs(v)))
    lines += ["end",
              "",
              "always @(posedge clk) begin",
              "  dat_a <= rom[addr_a];",
              "  dat_b <= rom[addr_b];",
              "end",
              "",
              "endmodule",
              ""]
    with open(os.path.join(outdir, "osc_ddc_sin_rom.v"), "w") as f:
        f.write("\n".join(lines))

    c = fir_coefs()[: FIR_TAPS // 2]
    lines = ["`timescale 1ns / 1ps",
             "// Generated by prj/stream_app/ddc/ddc_model.py - do not edit.",
             "// First half of the symmetric %d-tap FIR (Q1.17): passband 0..2.5 MHz" % FIR_TAPS,
             "// with CIC droop compensation, stopband from 3.75 MHz at 12.5 MSPS.",
             "module osc_ddc_fir_coef (",
             "  input  wire                 clk,",
             "  input  wire [5-1:0]         addr,",
             "  output reg  signed [%d-1:0] dat" % COEF_BITS,
             ");",
             "",
             "always @(posedge clk) begin",
             "  case (addr)"]
    for a, v in enumerate(c):
        lines.append("    5'd%-2d : dat <= %s%d'sd%d;" % (a, "-" if v < 0 else " ", COEF_BITS, abs(v)))
    lines += ["    default: dat <= %d'sd0;" % COEF_BITS,
              "  endcase",
              "end",
              "",
              "endmodule",
              ""]
    with open(os.path.join(outdir, "osc_ddc_fir_coef.v"), "w") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def db(v):
    return 20 * np.log10(np.maximum(np.abs(v), 1e-12))


def fir_response(h, f):
    n = np.arange(len(h))
    return np.exp(-2j * np.pi * np.outer(f, n) / FS_CIC) @ h


def tone_test(fc, f_off, amp=0.9, n=200000, noise=True):
    """Run a real tone at fc+f_off through the model; return the output spectrum peak info."""
    rng = np.random.default_rng(1)
    t = np.arange(n)
    x = amp * np.cos(2 * np.pi * (fc + f_off) / FS * t + 0.3)
    if noise:
        x += rng.normal(0, 2 / 2**13, n)      # about 2 LSB of 14-bit ADC noise
    x16 = np.clip(np.round(x * 2**15), -2**15, 2**15 - 1).astype(np.int64)
    x16 &= ~0x3                                # 14-bit ADC left-aligned in 16 bits
    i, q = ddc(x16, pinc_for(fc))
    z = unpack(pack(i, q))[200:]              # skip filter start-up
    win = np.blackman(len(z))
    spec = np.fft.fftshift(np.fft.fft(z * win)) / win.sum()
    freqs = np.fft.fftshift(np.fft.fftfreq(len(z), 1 / FS_OUT))
    return freqs, spec


def report():
    c = fir_coefs()
    h = c / 2**(COEF_BITS - 1)
    print("FIR taps (Q1.17, first half):", list(c[:FIR_TAPS // 2]))
    print("max |coef| = %d  (limit %d)" % (np.abs(c).max(), 2**(COEF_BITS - 1) - 1))

    fp = np.linspace(0, BAND, 500)
    total_p = cic_gain() * cic_response(fp) * np.abs(fir_response(h, fp))
    print("passband 0..2.5 MHz: gain ripple %.3f dB (min %.3f / max %.3f)"
          % (db(total_p).max() - db(total_p).min(), db(total_p).min(), db(total_p).max()))

    # worst alias: every input frequency that folds onto the +/-2.5 MHz output band
    worst = -1e9
    for fo in np.linspace(-BAND, BAND, 201):
        for k in range(1, 20):
            for sgn in (1, -1):
                fa = abs(fo + sgn * k * FS_OUT)
                if fa > FS / 2:
                    continue
                g = cic_gain() * cic_response(fa)
                if fa < FS_CIC / 2 or True:
                    fa_c = ((fa + FS_CIC / 2) % FS_CIC) - FS_CIC / 2
                    g = g * np.abs(fir_response(h, [fa_c]))[0]
                worst = max(worst, db(g)[()] if np.ndim(db(g)) else db(g))
    print("worst-case alias into band: %.1f dB below passband" % -worst)

    fc = 20e6
    for f_off in (1.0e6, -2.0e6):
        freqs, spec = tone_test(fc, f_off)
        pk = np.argmax(np.abs(spec))
        img = np.argmin(np.abs(freqs + f_off))
        print("tone fc%+.1f MHz -> peak at %+.3f MHz, amplitude %.3f, image %.1f dBc"
              % (f_off / 1e6, freqs[pk] / 1e6, np.abs(spec[pk]),
                 db(spec[img])[()] - db(spec[pk])[()]))
    rate = FS_OUT * 2 * 2 / 1e6
    print("output: %.2f MSPS complex = %.1f MB/s per channel" % (FS_OUT / 1e6, rate))


# ---------------------------------------------------------------------------
# Simulation vectors
# ---------------------------------------------------------------------------

TEST_FC = 20e6


def test_input(n=40000):
    rng = np.random.default_rng(7)
    t = np.arange(n)
    x = (0.45 * np.cos(2 * np.pi * (TEST_FC + 1.1e6) / FS * t)
         + 0.3 * np.cos(2 * np.pi * (TEST_FC - 1.7e6) / FS * t + 1.0)
         + 0.2 * np.cos(2 * np.pi * 31e6 / FS * t)        # out of band, must be removed
         + rng.normal(0, 0.02, n))
    x16 = np.clip(np.round(x * 2**15), -2**15, 2**15 - 1).astype(np.int64)
    x16[1000:1010] = 32767                                  # full-scale burst
    x16[1010:1020] = -32768
    return x16


def vectors(outdir):
    os.makedirs(outdir, exist_ok=True)
    x = test_input()
    pinc = pinc_for(TEST_FC)
    with open(os.path.join(outdir, "ddc_in.hex"), "w") as f:
        for v in x:
            f.write("%04x\n" % (int(v) & 0xFFFF))
    with open(os.path.join(outdir, "ddc_pinc.hex"), "w") as f:
        f.write("%08x\n" % pinc)
    i, q = ddc(x, pinc)
    np.savetxt(os.path.join(outdir, "ddc_expected.txt"), pack(i, q), fmt="%d")
    print("wrote %d input samples, %d expected words (pinc=0x%08x)" % (len(x), 2 * len(i), pinc))


def compare(outdir):
    exp = np.loadtxt(os.path.join(outdir, "ddc_expected.txt"), dtype=np.int64)
    got = np.loadtxt(os.path.join(outdir, "ddc_rtl_out.txt"), dtype=np.int64)
    n = min(len(exp), len(got))
    if n < len(exp) - 4:
        print("FAIL: RTL produced %d words, expected %d" % (len(got), len(exp)))
        return 1
    bad = np.nonzero(exp[:n] != got[:n])[0]
    if len(bad):
        print("FAIL: %d/%d words differ, first at %d: rtl=%d model=%d"
              % (len(bad), n, bad[0], got[bad[0]], exp[bad[0]]))
        return 1
    print("PASS: %d RTL output words match the model bit-exactly" % n)
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "report":
        report()
    elif cmd == "gen-rtl":
        gen_rtl(sys.argv[2])
    elif cmd == "vectors":
        vectors(sys.argv[2])
    elif cmd == "compare":
        sys.exit(compare(sys.argv[2]))
    else:
        print(__doc__)
        sys.exit(2)
