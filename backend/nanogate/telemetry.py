"""Hardware/runtime telemetry with per-field capability detection.

Every field is {status: live|unavailable|demo, value, unit, source, reason}. Unavailable fields
carry value=None and an explicit reason; nothing is ever substituted. DevelopmentTelemetryProvider
exists only behind TELEMETRY_MODE=demo and every field it returns is marked status='demo'.
"""
from __future__ import annotations

import os
import platform
import random
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import psutil


def F(value: Any, unit: str | None, source: str, status: str = "live", reason: str | None = None) -> dict:
    return {"status": status, "value": value, "unit": unit, "source": source, "reason": reason}


def U(reason: str, unit: str | None = None, source: str | None = None) -> dict:
    return {"status": "unavailable", "value": None, "unit": unit, "source": source, "reason": reason}


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except Exception:
        return None


class RealZGXTelemetryProvider:
    mode = "real"

    def __init__(self):
        self._nvml = None
        self._handle = None
        self.nvml_error: str | None = None
        self._lock = threading.Lock()
        try:
            import pynvml
            pynvml.nvmlInit()
            self._nvml = pynvml
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception as e:
            self.nvml_error = f"NVML unavailable: {type(e).__name__}: {e}"[:200]
        psutil.cpu_percent(interval=None)  # prime

    def _nv(self, fn: str, *args):
        if not self._nvml:
            raise RuntimeError(self.nvml_error or "NVML not initialised")
        with self._lock:
            return getattr(self._nvml, fn)(self._handle, *args) if self._handle is not None else getattr(self._nvml, fn)(*args)

    # --- identity --------------------------------------------------------------------------
    def get_system_identity(self) -> dict:
        product = _read("/sys/class/dmi/id/product_name")
        vendor = _read("/sys/class/dmi/id/sys_vendor")
        cpu_models: list[str] = []
        try:
            out = subprocess.run(["lscpu"], capture_output=True, text=True, timeout=3).stdout
            cpu_models = [l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("Model name")]
        except Exception:
            pass
        osr = {}
        try:
            for l in Path("/etc/os-release").read_text().splitlines():
                if "=" in l:
                    k, v = l.split("=", 1)
                    osr[k] = v.strip('"')
        except Exception:
            pass
        return {
            "hostname": F(socket.gethostname(), None, "socket.gethostname"),
            "product": F(product, None, "/sys/class/dmi/id/product_name") if product else U("DMI product name not readable"),
            "vendor": F(vendor, None, "/sys/class/dmi/id/sys_vendor") if vendor else U("DMI vendor not readable"),
            "architecture": F(platform.machine(), None, "platform.machine"),
            "cpu_model": F(" + ".join(cpu_models), None, "lscpu") if cpu_models else U("lscpu unavailable"),
            "cpu_cores": F(psutil.cpu_count(logical=True), "cores", "psutil"),
            "os": F(osr.get("PRETTY_NAME"), None, "/etc/os-release") if osr else U("os-release unreadable"),
            "kernel": F(platform.release(), None, "platform.release"),
        }

    # --- GPU -------------------------------------------------------------------------------
    def get_gpu(self) -> dict:
        out: dict[str, Any] = {}
        src = "NVML"
        for key, fn, unit, conv in [
            ("name", "nvmlDeviceGetName", None, lambda v: v.decode() if isinstance(v, bytes) else v),
            ("utilization", "nvmlDeviceGetUtilizationRates", "%", lambda v: v.gpu),
            ("sm_clock", "nvmlDeviceGetClockInfo", "MHz", None),
        ]:
            try:
                v = self._nv(fn, 0) if fn == "nvmlDeviceGetClockInfo" else self._nv(fn)
                out[key] = F(conv(v) if conv else v, unit, src)
            except Exception as e:
                out[key] = U(f"{fn}: {type(e).__name__}: {e}"[:160], unit, src)
        try:
            out["driver_version"] = F(self._nvml.nvmlSystemGetDriverVersion(), None, src) if self._nvml else U(self.nvml_error or "")
            cv = self._nvml.nvmlSystemGetCudaDriverVersion() if self._nvml else None
            out["cuda_version"] = F(f"{cv // 1000}.{(cv % 1000) // 10}", None, src) if cv else U("CUDA driver version unavailable")
        except Exception as e:
            out["driver_version"] = U(str(e)[:160])
            out["cuda_version"] = U(str(e)[:160])
        try:
            cc = self._nv("nvmlDeviceGetCudaComputeCapability")
            out["compute_capability"] = F(f"{cc[0]}.{cc[1]}", None, src)
        except Exception as e:
            out["compute_capability"] = U(str(e)[:160])
        try:
            self._nv("nvmlDeviceGetMemoryInfo")
            out["dedicated_memory"] = U("unexpected")
        except Exception:
            out["dedicated_memory"] = U("GB10 uses unified CPU/GPU memory; NVML reports GPU memory as Not Supported. "
                                        "See memory.unified.", "bytes", src)
        return out

    def get_power(self) -> dict:
        out = {}
        try:
            out["gpu_power"] = F(round(self._nv("nvmlDeviceGetPowerUsage") / 1000.0, 2), "W", "NVML nvmlDeviceGetPowerUsage")
        except Exception as e:
            out["gpu_power"] = U(f"{type(e).__name__}: {e}"[:160], "W", "NVML")
        try:
            out["gpu_energy_total"] = F(round(self._nv("nvmlDeviceGetTotalEnergyConsumption") / 1000.0, 1), "J",
                                        "NVML nvmlDeviceGetTotalEnergyConsumption")
        except Exception as e:
            out["gpu_energy_total"] = U(f"{type(e).__name__}: {e}"[:160], "J", "NVML")
        out["system_power"] = U("Whole-system power is not exposed to unprivileged software on this device; only GPU power via NVML.", "W")
        return out

    def energy_mj(self) -> float | None:
        try:
            return float(self._nv("nvmlDeviceGetTotalEnergyConsumption"))
        except Exception:
            return None

    def get_temperature(self) -> dict:
        out = {}
        try:
            out["gpu"] = F(self._nv("nvmlDeviceGetTemperature", 0), "°C", "NVML")
        except Exception as e:
            out["gpu"] = U(f"{type(e).__name__}: {e}"[:160], "°C", "NVML")
        zones = []
        for z in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
            t = _read(str(z / "temp"))
            if t and t.lstrip("-").isdigit():
                zones.append(int(t) / 1000.0)
        out["soc_max"] = F(max(zones), "°C", f"/sys/class/thermal ({len(zones)} zones)") if zones else U("no readable thermal zones", "°C")
        return out

    def get_memory(self) -> dict:
        try:
            info = {l.split(":")[0]: int(l.split()[1]) * 1024 for l in open("/proc/meminfo")}
            total, avail = info["MemTotal"], info["MemAvailable"]
            return {"unified_total": F(total, "bytes", "/proc/meminfo MemTotal"),
                    "unified_available": F(avail, "bytes", "/proc/meminfo MemAvailable"),
                    "unified_used": F(total - avail, "bytes", "/proc/meminfo"),
                    "pressure": F(round((total - avail) / total, 4), "ratio", "/proc/meminfo")}
        except Exception as e:
            return {"unified_total": U(str(e)[:160], "bytes")}

    def get_cpu(self) -> dict:
        util = F(psutil.cpu_percent(interval=None), "%", "psutil.cpu_percent")
        if not hasattr(os, "getloadavg"):   # Windows: no load average
            return {"utilization": util, "load_avg_1m": U("os.getloadavg not available on this OS", None)}
        return {"utilization": util, "load_avg_1m": F(round(os.getloadavg()[0], 2), None, "os.getloadavg")}

    def get_storage(self, path: str = "/") -> dict:
        try:
            du = shutil.disk_usage(path)
            return {"path": F(path, None, "shutil"), "total": F(du.total, "bytes", "shutil.disk_usage"),
                    "used": F(du.used, "bytes", "shutil.disk_usage"), "free": F(du.free, "bytes", "shutil.disk_usage")}
        except Exception as e:
            return {"total": U(str(e)[:160], "bytes")}

    def get_network(self) -> dict:
        ifs = []
        try:
            for name, st in psutil.net_if_stats().items():
                if name == "lo":
                    continue
                ifs.append({"name": name, "up": st.isup, "speed_mbps": st.speed or None})
        except Exception:
            pass
        default_route = False
        try:
            for l in Path("/proc/net/route").read_text().splitlines()[1:]:
                parts = l.split()
                if len(parts) > 2 and parts[1] == "00000000":
                    default_route = True
        except Exception:
            pass
        io = psutil.net_io_counters()
        return {"interfaces": F(ifs, None, "psutil.net_if_stats"),
                "default_route": F(default_route, None, "/proc/net/route"),
                "bytes_sent_total": F(io.bytes_sent, "bytes", "psutil.net_io_counters (host-wide)"),
                "bytes_recv_total": F(io.bytes_recv, "bytes", "psutil.net_io_counters (host-wide)")}

    def snapshot(self) -> dict:
        return {"mode": self.mode, "label": "Live device telemetry", "ts": time.time(),
                "system": self.get_system_identity(), "gpu": self.get_gpu(), "power": self.get_power(),
                "temperature": self.get_temperature(), "memory": self.get_memory(), "cpu": self.get_cpu(),
                "storage": self.get_storage(), "network": self.get_network()}

    def sample(self) -> dict:
        """Cheap subset for periodic sampling."""
        g, p, t, m, c = self.get_gpu(), self.get_power(), self.get_temperature(), self.get_memory(), self.get_cpu()
        return {"ts": time.time(), "gpu_util": g["utilization"]["value"], "gpu_clock_mhz": g["sm_clock"]["value"],
                "gpu_power_w": p["gpu_power"]["value"], "gpu_temp_c": t["gpu"]["value"],
                "mem_used_bytes": m.get("unified_used", {}).get("value"), "mem_total_bytes": m.get("unified_total", {}).get("value"),
                "cpu_util": c["utilization"]["value"]}


class DevelopmentTelemetryProvider(RealZGXTelemetryProvider):
    """ONLY active with TELEMETRY_MODE=demo. Every value is marked status='demo' and labelled in the UI."""
    mode = "demo"

    def snapshot(self) -> dict:
        s = super().snapshot()
        s["label"] = "Demo telemetry"
        for section in ("gpu", "power", "temperature", "memory", "cpu"):
            for k, v in s[section].items():
                if isinstance(v, dict):
                    v["status"], v["reason"] = "demo", "TELEMETRY_MODE=demo"
        return s

    def sample(self) -> dict:
        d = super().sample()
        d["gpu_util"] = d["gpu_util"] if d["gpu_util"] is not None else random.uniform(5, 30)
        d["demo"] = True
        return d


def provider(mode: str):
    return DevelopmentTelemetryProvider() if mode == "demo" else RealZGXTelemetryProvider()
