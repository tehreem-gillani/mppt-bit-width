"""
Three MPPT controllers. Each one is called once per control period with the
latest voltage and current measurement and returns the next voltage
reference for the DC-DC converter.
"""
import numpy as np


class PerturbObserve:
    name = "Perturb and observe"

    def __init__(self, v0, step=0.5):
        self.v_ref = v0
        self.step = step
        self.prev_p = None
        self.direction = 1.0

    def update(self, v, i):
        p = v * i
        if self.prev_p is not None and p < self.prev_p:
            self.direction = -self.direction
        self.prev_p = p
        self.v_ref += self.direction * self.step
        return self.v_ref


class IncrementalConductance:
    """At the MPP dP/dV = 0, which is the same as dI/dV = -I/V."""
    name = "Incremental conductance"

    def __init__(self, v0, step=0.5, tol=0.02):
        self.v_ref = v0
        self.step = step
        self.tol = tol
        self.prev_v = None
        self.prev_i = None

    def update(self, v, i):
        if self.prev_v is None:
            self.prev_v, self.prev_i = v, i
            self.v_ref += self.step
            return self.v_ref
        dv, di = v - self.prev_v, i - self.prev_i
        if abs(dv) < 1e-6:
            if abs(di) > 1e-6:
                self.v_ref += self.step if di > 0 else -self.step
        else:
            err = di / dv + i / max(v, 1e-6)
            if abs(err) > self.tol:
                self.v_ref += self.step if err > 0 else -self.step
        self.prev_v, self.prev_i = v, i
        return self.v_ref


class ScanThenPO:
    """
    Every `period` control steps, sweep the voltage across the whole range
    in coarse steps, remember where power was highest, jump there, then
    hand over to P&O for fine tracking. The sweep costs some energy, which
    the simulation counts.
    """
    name = "Periodic scan + P&O"

    def __init__(self, v0, v_max, step=0.5, scan_points=24, period=400):
        self.po = PerturbObserve(v0, step)
        self.v_max = v_max
        self.scan_v = np.linspace(0.08 * v_max, 0.95 * v_max, scan_points)
        self.period = period
        self.k = 0
        self.scan_idx = None
        self.best = (None, -1.0)
        self.v_ref = v0

    def update(self, v, i):
        p = v * i
        if self.scan_idx is not None:
            if p > self.best[1]:
                self.best = (v, p)
            self.scan_idx += 1
            if self.scan_idx < len(self.scan_v):
                self.v_ref = self.scan_v[self.scan_idx]
            else:
                self.scan_idx = None
                self.po = PerturbObserve(self.best[0], self.po.step)
                self.v_ref = self.best[0]
            return self.v_ref
        self.k += 1
        if self.k % self.period == 0:
            self.scan_idx = 0
            self.best = (None, -1.0)
            self.v_ref = self.scan_v[0]
            return self.v_ref
        self.v_ref = self.po.update(v, i)
        return self.v_ref


class EventScanPO(ScanThenPO):
    """
    Same as ScanThenPO, but a sweep is also started as soon as the measured
    power jumps by more than `trigger` (relative) between two steps, which
    is what a shadow or a cloud edge looks like from the controller's side.
    The periodic sweep is kept as a slow backstop.
    """
    name = "Event-triggered scan + P&O"

    def __init__(self, v0, v_max, step=0.5, scan_points=24, period=2000,
                 trigger=0.10):
        super().__init__(v0, v_max, step, scan_points, period)
        self.trigger = trigger
        self.last_p = None

    def update(self, v, i):
        p = v * i
        if self.scan_idx is None and self.last_p is not None and self.last_p > 1.0:
            if abs(p - self.last_p) / self.last_p > self.trigger:
                self.last_p = None
                self.k = 0
                self.scan_idx = 0
                self.best = (None, -1.0)
                self.v_ref = self.scan_v[0]
                return self.v_ref
        self.last_p = p if self.scan_idx is None else None
        return super().update(v, i)
