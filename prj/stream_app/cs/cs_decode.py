#!/usr/bin/env python3
"""Turn captured measurement words into (Y, Phi) for your reconstruction code.

    python3 cs_decode.py capture.bin                         # defaults N=10 L=1000 shift=20
    python3 cs_decode.py capture.bin -N 10 -L 1000 --shift 20 --table weights.txt
    python3 cs_decode.py capture.wav --channel 0

Writes <input>.npz with
    Y    (K x P)  one row per table period, P = L/N measurements each
    Phi  (P x L)  measurement matrix, so that Y[k] = Phi @ x_k
where x_k are the L consecutive 125 MSPS samples (full scale = 1.0) of period k.
Use the same N, L, shift and table that cs_setup.sh loaded.
"""
import argparse
import numpy as np
from cs_model import decode, phi, default_table, DEF_N, DEF_L, DEF_SHIFT


def load(path, channel):
    if path.lower().endswith(".wav"):
        from scipy.io import wavfile
        _, data = wavfile.read(path)
        if data.ndim > 1:
            data = data[:, channel]
        return data.astype(np.int16)
    return np.fromfile(path, dtype="<i2")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("-N", type=int, default=DEF_N, help="samples per measurement")
    ap.add_argument("-L", type=int, default=DEF_L, help="weight table period")
    ap.add_argument("--shift", type=int, default=DEF_SHIFT)
    ap.add_argument("--table", help="weights file loaded with cs_setup.sh (default: built-in table)")
    ap.add_argument("--channel", type=int, default=0, help="WAV channel index")
    args = ap.parse_args()

    tbl = np.loadtxt(args.table, dtype=np.int64) if args.table else default_table()
    y, start = decode(load(args.file, args.channel), args.N, args.L, args.shift)
    m = phi(tbl, args.N, args.L)
    out = args.file + ".npz"
    np.savez(out, Y=y, Phi=m, N=args.N, L=args.L, shift=args.shift)

    sat = np.mean(np.abs(y) >= (2**14 - 1) * 2.0**(args.shift - 30))
    print("skipped %d words to the first frame flag" % start)
    print("Y: %d periods x %d measurements, Phi: %dx%d -> %s" % (y.shape + m.shape + (out,)))
    print("saturated measurements: %.3f%%%s" % (100 * sat, "  (increase --shift)" if sat > 1e-3 else ""))


if __name__ == "__main__":
    main()
