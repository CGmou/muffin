"""Small system-information helpers shared by the worker agent and the GUIs."""

import platform


def cpu_name() -> str:
    """Marketing CPU name like Task Manager shows (e.g. 'AMD Ryzen 9 5950X
    16-Core Processor'), not the raw architecture string."""
    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            if value and value.strip():
                return value.strip()
        except OSError:
            pass
    elif platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()
