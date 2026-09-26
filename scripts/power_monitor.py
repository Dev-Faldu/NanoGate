"""Log GPU power/util/temperature/clock + unified memory every second to var/power_log.csv.

Each line is fsync'ed so the last seconds before an abrupt power loss survive on disk.
Usage: python scripts/power_monitor.py [--interval 1]
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import pynvml

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=1.0)
    a = ap.parse_args()
    pynvml.nvmlInit()
    h = pynvml.nvmlDeviceGetHandleByIndex(0)
    out = ROOT / "var" / "power_log.csv"
    new = not out.exists()
    f = open(out, "a", buffering=1)
    if new:
        f.write("ts,iso,power_w,util_pct,temp_c,sm_clock_mhz,mem_used_gb\n")
    while True:
        try:
            p = pynvml.nvmlDeviceGetPowerUsage(h) / 1000
            u = pynvml.nvmlDeviceGetUtilizationRates(h).gpu
            t = pynvml.nvmlDeviceGetTemperature(h, 0)
            c = pynvml.nvmlDeviceGetClockInfo(h, 0)
            info = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
            mem = (info["MemTotal"] - info["MemAvailable"]) / 1024 ** 2
            now = time.time()
            f.write(f"{now:.1f},{time.strftime('%H:%M:%S')},{p:.2f},{u},{t},{c},{mem:.1f}\n")
            f.flush()
            os.fsync(f.fileno())
        except Exception as e:
            f.write(f"{time.time():.1f},error,{type(e).__name__}\n")
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
