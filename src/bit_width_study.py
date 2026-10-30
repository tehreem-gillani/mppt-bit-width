"""
How many ADC bits does an MPPT controller actually need?

The controller from the pv-mppt-shading project is run again, but every
voltage and current it sees first goes through an N-bit ADC model. The
power it compares is then the product of two integer codes, exactly as a
small fixed-point microcontroller or a dedicated control block on a chip
would compute it.

Usage:  python src/bit_width_study.py
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pv_model import PVModule, PVString, Curve
from mppt import PerturbObserve, EventScanPO

HERE = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(HERE, "..", "figures")
os.makedirs(FIG, exist_ok=True)

V_FS = 120.0   # ADC full scale for the string voltage, V
I_FS = 10.0    # ADC full scale for the string current, A

SCENARIO = [
    ([1000, 1000, 1000], 1000),
    ([1000, 600, 250],   1500),
    ([800, 800, 300],    1500),
    ([900, 900, 900],    1000),
]


class ADC:
    def __init__(self, bits, full_scale):
        self.levels = 2 ** bits - 1
        self.lsb = full_scale / self.levels

    def code(self, x):
        return int(np.clip(np.round(x / self.lsb), 0, self.levels))


def run(bits, step, noise, factory, curves, seed=7):
    rng = np.random.default_rng(seed)
    adc_v, adc_i = ADC(bits, V_FS), ADC(bits, I_FS)
    v_oc = curves[0][0].v_oc
    ctrl = factory(0.8 * v_oc, v_oc, step)
    v_ref = ctrl.v_ref
    got = avail = 0.0
    for curve, n in curves:
        for _ in range(n):
            v = float(np.clip(v_ref, 0.0, curve.v_oc))
            i = curve.current(v)
            got += v * i
            avail += curve.p_mpp
            vm = v + (rng.normal(0, noise[0]) if noise else 0.0)
            im = i + (rng.normal(0, noise[1]) if noise else 0.0)
            cv, ci = adc_v.code(vm), adc_i.code(im)
            # Controller works on the reconstructed values; the product
            # cv*ci is an exact integer, so this is equivalent to comparing
            # integer power codes in hardware.
            v_ref = ctrl.update(cv * adc_v.lsb, ci * adc_i.lsb)
    return got / avail


def main():
    string = PVString(3, PVModule())
    curves = [(Curve(*string.iv_curve(g)), n) for g, n in SCENARIO]

    po = lambda v0, voc, s: PerturbObserve(v0, step=s)
    ev = lambda v0, voc, s: EventScanPO(v0, voc, step=s, scan_points=24,
                                         period=2000, trigger=0.10)
    bits_list = list(range(4, 15))
    noise = (0.10, 0.01)

    print("Event-triggered scan + P&O, 0.5 V step")
    print(f"{'bits':>5} {'V LSB (V)':>10} {'I LSB (mA)':>11} {'no noise':>10} {'with noise':>11}")
    clean, noisy = [], []
    for b in bits_list:
        e0 = run(b, 0.5, None, ev, curves)
        e1 = run(b, 0.5, noise, ev, curves)
        clean.append(e0); noisy.append(e1)
        print(f"{b:5d} {V_FS/(2**b-1):10.3f} {1000*I_FS/(2**b-1):11.2f} "
              f"{e0*100:9.2f}% {e1*100:10.2f}%")

    fig, ax = plt.subplots(figsize=(6.5, 3.8))
    ax.plot(bits_list, np.array(clean) * 100, "o-", label="Ideal sensors")
    ax.plot(bits_list, np.array(noisy) * 100, "s-", label="Sensor noise added")
    ax.set_xlabel("ADC resolution (bits)")
    ax.set_ylabel("Energy collected (% of available)")
    ax.set_title("Event-triggered scan + P&O, 0.5 V step")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "efficiency_vs_bits.png"), dpi=150)

    # Bits vs step size, plain P&O, clear sky only (the steady-state case).
    clear = [curves[0][:1] + (3000,)]
    steps = [0.1, 0.25, 0.5, 1.0, 2.0]
    grid = np.zeros((len(steps), len(bits_list)))
    for a, s in enumerate(steps):
        for b_i, b in enumerate(bits_list):
            grid[a, b_i] = run(b, s, None, po, clear) * 100
    fig, ax = plt.subplots(figsize=(7, 3.6))
    im = ax.imshow(grid, aspect="auto", origin="lower", cmap="viridis",
                   vmin=max(grid.min(), 80), vmax=100)
    ax.set_xticks(range(len(bits_list)), bits_list)
    ax.set_yticks(range(len(steps)), steps)
    ax.set_xlabel("ADC resolution (bits)")
    ax.set_ylabel("P&O step (V)")
    ax.set_title("Clear-sky tracking efficiency (%), ideal sensors")
    for a in range(len(steps)):
        for b_i in range(len(bits_list)):
            ax.text(b_i, a, f"{grid[a, b_i]:.0f}", ha="center", va="center",
                    fontsize=7, color="w" if grid[a, b_i] < 95 else "k")
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "bits_vs_step.png"), dpi=150)
    print("\nPlain P&O, clear sky, ideal sensors (rows: step V, cols: bits)")
    print("      " + "".join(f"{b:6d}" for b in bits_list))
    for s, row in zip(steps, grid):
        print(f"{s:5.2f} " + "".join(f"{x:6.1f}" for x in row))


def low_light(curves_unused=None):
    """Where resolution really matters: at low irradiance the current is a
    small fraction of the ADC full scale."""
    string = PVString(3, PVModule())
    po = lambda v0, voc, s: PerturbObserve(v0, step=s)
    levels = [1000, 400, 200, 100, 50]
    bits_list = [6, 8, 10, 12]
    table = np.zeros((len(levels), len(bits_list)))
    for a, g in enumerate(levels):
        c = Curve(*string.iv_curve([g, g, g]))
        for b_i, b in enumerate(bits_list):
            table[a, b_i] = run(b, 0.5, (0.10, 0.01), po, [(c, 3000)]) * 100
    print("\nPlain P&O, uniform irradiance, sensor noise on (rows: W/m^2, cols: bits)")
    print("       " + "".join(f"{b:7d}" for b in bits_list))
    for g, row in zip(levels, table):
        print(f"{g:6d} " + "".join(f"{x:7.1f}" for x in row))
    fig, ax = plt.subplots(figsize=(6.5, 3.8))
    for b_i, b in enumerate(bits_list):
        ax.plot(levels, table[:, b_i], "o-", label=f"{b}-bit ADC")
    ax.set_xscale("log")
    ax.set_xlabel("Irradiance (W/m$^2$)")
    ax.set_ylabel("Tracking efficiency (%)")
    ax.set_title("Low light is where ADC resolution matters")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "low_light.png"), dpi=150)


if __name__ == "__main__":
    main()
    low_light()
