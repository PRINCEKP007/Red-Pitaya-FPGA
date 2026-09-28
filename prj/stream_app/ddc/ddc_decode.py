#!/usr/bin/env python3
"""Decode DDC I/Q words captured by the streaming client into complex baseband.

    python3 ddc_decode.py capture.bin              # raw little-endian int16
    python3 ddc_decode.py capture.wav --channel 0  # WAV from the streaming client

Writes <input>.iq.npy (complex64, full scale = 1.0, 6.25 MSPS) and prints the
strongest spectral component as a quick sanity check.
"""
import argparse
import numpy as np
from ddc_model import unpack, FS_OUT


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
    ap.add_argument("--channel", type=int, default=0, help="WAV channel index")
    ap.add_argument("--fc", type=float, default=0.0, help="NCO frequency [Hz], only for printing")
    args = ap.parse_args()

    z = unpack(load(args.file, args.channel)).astype(np.complex64)
    out = args.file + ".iq.npy"
    np.save(out, z)

    n = min(len(z), 1 << 16)
    win = np.blackman(n)
    spec = np.fft.fftshift(np.fft.fft(z[:n] * win)) / win.sum()
    freqs = np.fft.fftshift(np.fft.fftfreq(n, 1 / FS_OUT))
    pk = int(np.argmax(np.abs(spec)))
    print("%d complex samples (%.3f ms) -> %s" % (len(z), len(z) / FS_OUT * 1e3, out))
    print("strongest component: %+.4f MHz offset (%.4f MHz absolute), %.1f dBFS"
          % (freqs[pk] / 1e6, (args.fc + freqs[pk]) / 1e6, 20 * np.log10(np.abs(spec[pk]) + 1e-12)))


if __name__ == "__main__":
    main()
