"""
Single-diode model of a PV module and a series string with bypass diodes.

The module equation (implicit in I):
    I = Iph - I0 * (exp((V + I*Rs) / (n*Ns*Vt)) - 1) - (V + I*Rs) / Rsh

Parameters are chosen to resemble a common 60-cell, ~250 W crystalline
silicon module (Isc about 8.6 A, Voc about 37.5 V at STC). They are not
fitted to any specific datasheet.
"""
import numpy as np
from scipy.optimize import brentq

K_B = 1.380649e-23      # Boltzmann constant, J/K
Q = 1.602176634e-19     # electron charge, C
EG_SI = 1.12            # silicon bandgap, eV


class PVModule:
    def __init__(self, isc=8.6, voc=37.5, ns=60, n=1.3, rs=0.30, rsh=300.0,
                 alpha_isc=0.0005, bypass_drop=0.7):
        self.isc_stc = isc
        self.voc_stc = voc
        self.ns = ns
        self.n = n
        self.rs = rs
        self.rsh = rsh
        self.alpha = alpha_isc          # relative Isc change per kelvin
        self.vbp = bypass_drop          # forward drop of the bypass diode, V
        t_stc = 298.15
        vt = K_B * t_stc / Q
        # Saturation current at STC from the open-circuit condition (I = 0).
        self.i0_stc = (isc - voc / rsh) / (np.exp(voc / (n * ns * vt)) - 1)

    def params(self, g, t_c):
        """Photocurrent, saturation current and thermal voltage at
        irradiance g (W/m^2) and cell temperature t_c (deg C)."""
        t = t_c + 273.15
        t_stc = 298.15
        vt = K_B * t / Q
        iph = self.isc_stc * (g / 1000.0) * (1 + self.alpha * (t - t_stc))
        # Standard temperature scaling of the diode saturation current.
        i0 = self.i0_stc * (t / t_stc) ** 3 * np.exp(
            Q * EG_SI / (self.n * K_B) * (1 / t_stc - 1 / t))
        return iph, i0, vt

    def current(self, v, g, t_c=25.0):
        """Module current for a terminal voltage v (scalar)."""
        iph, i0, vt = self.params(g, t_c)
        a = self.n * self.ns * vt

        def f(i):
            return iph - i0 * np.expm1((v + i * self.rs) / a) \
                - (v + i * self.rs) / self.rsh - i
        return brentq(f, -50.0, iph + 5.0)

    def voltage(self, i, g, t_c=25.0):
        """Module voltage for a forced current i. If the string pushes more
        current than the module can make, its bypass diode turns on."""
        iph, i0, vt = self.params(g, t_c)
        a = self.n * self.ns * vt

        def f(v):
            return iph - i0 * np.expm1((v + i * self.rs) / a) \
                - (v + i * self.rs) / self.rsh - i
        v_oc_guess = a * np.log(iph / i0 + 1) if iph > 0 else 0.0
        # Voltage the module would need at this current, allowed negative.
        try:
            v = brentq(f, -60.0, v_oc_guess + 5.0)
        except ValueError:
            v = -60.0
        return max(v, -self.vbp)


class PVString:
    """Modules in series, each with its own bypass diode."""

    def __init__(self, n_modules=3, module=None):
        self.m = module or PVModule()
        self.n_modules = n_modules

    def iv_curve(self, irradiances, t_c=25.0, points=400):
        g = np.asarray(irradiances, dtype=float)
        i_max = max(self.m.params(gk, t_c)[0] for gk in g)
        currents = np.linspace(0.0, i_max * 1.0, points)
        volts = np.array([sum(self.m.voltage(i, gk, t_c) for gk in g)
                          for i in currents])
        # Sort by voltage so we can interpolate P(V) later.
        order = np.argsort(volts)
        v, i = volts[order], currents[order]
        keep = v >= 0
        return v[keep], i[keep]


class Curve:
    """Fast lookup of string current at any voltage (what the converter
    would see), built once per irradiance condition."""

    def __init__(self, v, i):
        self.v = v
        self.i = i
        self.p = v * i
        k = int(np.argmax(self.p))
        self.v_mpp, self.p_mpp = self.v[k], self.p[k]
        self.v_oc = self.v[-1]

    def current(self, v):
        return float(np.interp(v, self.v, self.i, left=self.i[0], right=0.0))

    def power(self, v):
        return v * self.current(v)

    def local_maxima(self):
        p = self.p
        idx = [k for k in range(1, len(p) - 1) if p[k] >= p[k-1] and p[k] > p[k+1]]
        return [(self.v[k], p[k]) for k in idx]
