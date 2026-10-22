"""Read-only computer inspection: processes, network, startup items, installed software, health scan."""
from __future__ import annotations

import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil

from . import tool

GROUP = "máy tính (chỉ đọc)"
MB = 1024 * 1024


def _gb(n: float) -> str:
    return f"{n / 1024**3:.1f} GB"


def _cpu_snapshot(procs: list[psutil.Process], wait: float = 0.4) -> None:
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(wait)


def _proc_rows():
    procs = list(psutil.process_iter(["pid", "name", "username", "exe", "memory_info"]))
    _cpu_snapshot(procs)
    rows = []
    ncpu = psutil.cpu_count() or 1
    for p in procs:
        try:
            info = p.info
            if info["pid"] == 0:  # "System Idle Process" is not a real process
                continue
            rows.append({
                "pid": info["pid"], "name": info["name"] or "?", "user": (info["username"] or "").split("\\")[-1],
                "exe": info["exe"] or "", "mem": (info["memory_info"].rss if info["memory_info"] else 0) / MB,
                "cpu": p.cpu_percent(None) / ncpu,
            })
        except psutil.Error:
            continue
    return rows


@tool("Tổng quan máy: hệ điều hành, thời gian bật máy, CPU, RAM, ổ đĩa, pin.", group=GROUP)
def system_overview() -> str:
    vm = psutil.virtual_memory()
    boot = datetime.fromtimestamp(psutil.boot_time())
    lines = [
        f"{platform.system()} {platform.release()} ({platform.version()}), {platform.machine()}",
        f"Máy: {platform.node()} · bật từ {boot:%Y-%m-%d %H:%M} ({(datetime.now() - boot).days} ngày)",
        f"CPU: {psutil.cpu_count(logical=False)} nhân / {psutil.cpu_count()} luồng, đang dùng {psutil.cpu_percent(0.5)}%",
        f"RAM: {_gb(vm.used)} / {_gb(vm.total)} ({vm.percent}%)",
    ]
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
            lines.append(f"Ổ {part.mountpoint}: trống {_gb(u.free)} / {_gb(u.total)} ({u.percent}% đã dùng)")
        except (PermissionError, OSError):
            continue
    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if bat:
        lines.append(f"Pin: {bat.percent:.0f}% {'(đang sạc)' if bat.power_plugged else ''}")
    return "\n".join(lines)


@tool("Liệt kê các phần mềm/tiến trình đang chạy (pid, tên, RAM, CPU, đường dẫn).",
      {"?sort_by": "str: memory|cpu|name, mặc định memory", "?limit": "int: mặc định 30",
       "?filter": "str: lọc theo tên"}, group=GROUP)
def list_processes(sort_by: str = "memory", limit: int = 30, filter: str | None = None) -> str:
    rows = _proc_rows()
    if filter:
        rows = [r for r in rows if filter.lower() in r["name"].lower() or filter.lower() in r["exe"].lower()]
    key = {"cpu": lambda r: -r["cpu"], "name": lambda r: r["name"].lower()}.get(sort_by, lambda r: -r["mem"])
    rows.sort(key=key)
    out = [f"{len(rows)} tiến trình. Top {min(int(limit), len(rows))}:", "PID     RAM(MB)  CPU%  TÊN  —  ĐƯỜNG DẪN"]
    for r in rows[: int(limit)]:
        out.append(f"{r['pid']:<7} {r['mem']:>7.0f} {r['cpu']:>5.1f}  {r['name']}  —  {r['exe'] or '(không truy cập được)'}")
    return "\n".join(out)


@tool("Chi tiết một tiến trình: lệnh khởi chạy, thư mục, tiến trình cha/con, kết nối mạng.",
      {"pid": "int"}, group=GROUP)
def process_details(pid: int) -> str:
    try:
        p = psutil.Process(int(pid))
        with p.oneshot():
            lines = [f"PID {p.pid}: {p.name()} · trạng thái {p.status()}",
                     f"Bắt đầu: {datetime.fromtimestamp(p.create_time()):%Y-%m-%d %H:%M:%S}"]
            for label, fn in (("Đường dẫn", p.exe), ("Lệnh", lambda: " ".join(p.cmdline())), ("Thư mục", p.cwd),
                              ("User", p.username)):
                try:
                    lines.append(f"{label}: {fn()}")
                except psutil.Error:
                    lines.append(f"{label}: (cần quyền admin)")
            parent = p.parent()
            lines.append(f"Cha: {parent.pid} {parent.name()}" if parent else "Cha: -")
            kids = p.children()
            if kids:
                lines.append("Con: " + ", ".join(f"{c.pid} {c.name()}" for c in kids[:15]))
            lines.append(f"RAM: {p.memory_info().rss / MB:.0f} MB · luồng: {p.num_threads()}")
            try:
                conns = p.net_connections() if hasattr(p, "net_connections") else p.connections()
                for c in conns[:15]:
                    raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "-"
                    lines.append(f"Mạng: {c.laddr.ip}:{c.laddr.port} → {raddr} ({c.status})")
            except psutil.Error:
                pass
        return "\n".join(lines)
    except psutil.NoSuchProcess:
        return f"Không có tiến trình {pid}."


@tool("Liệt kê kết nối mạng và cổng đang mở, kèm tên phần mềm sở hữu.",
      {"?status": "str: LISTEN|ESTABLISHED|all, mặc định all", "?limit": "int: mặc định 60"}, group=GROUP)
def network_connections(status: str = "all", limit: int = 60) -> str:
    try:
        conns = psutil.net_connections(kind="inet")
    except psutil.AccessDenied:
        return "Cần quyền admin để xem kết nối mạng."
    names: dict[int, str] = {}
    out = []
    for c in conns:
        if status.lower() != "all" and c.status != status.upper():
            continue
        if c.pid and c.pid not in names:
            try:
                names[c.pid] = psutil.Process(c.pid).name()
            except psutil.Error:
                names[c.pid] = "?"
        raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "-"
        out.append(f"{c.laddr.ip}:{c.laddr.port:<6} → {raddr:<22} {c.status:<12} {names.get(c.pid, '-')} ({c.pid})")
    out.sort()
    return f"{len(out)} kết nối:\n" + "\n".join(out[: int(limit)])


def _reg_values(root, path):
    import winreg
    items = []
    try:
        with winreg.OpenKey(root, path) as key:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    items.append((name, str(value)))
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return items


@tool("Liệt kê các chương trình tự khởi động cùng Windows (registry Run + thư mục Startup).", group=GROUP)
def startup_programs() -> str:
    if sys.platform != "win32":
        return "Chỉ hỗ trợ Windows."
    import winreg
    out = []
    for root, label in ((winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
        for path in (r"Software\Microsoft\Windows\CurrentVersion\Run",
                     r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
                     r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"):
            for name, value in _reg_values(root, path):
                out.append(f"[{label}\\{path.split('\\')[-1]}] {name}: {value}")
    for folder in (Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup",
                   Path(os.environ.get("PROGRAMDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\StartUp"):
        if folder.is_dir():
            out += [f"[Startup] {f.name}" for f in folder.iterdir() if f.name.lower() != "desktop.ini"]
    return "\n".join(out) or "Không có chương trình khởi động nào."


@tool("Liệt kê phần mềm đã cài trên máy (tên, phiên bản, nhà phát hành).",
      {"?filter": "str: lọc theo tên", "?limit": "int: mặc định 80"}, group=GROUP)
def installed_software(filter: str | None = None, limit: int = 80) -> str:
    if sys.platform != "win32":
        return "Chỉ hỗ trợ Windows."
    import winreg
    seen, out = set(), []
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base in (r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                     r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try:
                with winreg.OpenKey(root, base) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                                vals = {}
                                for field in ("DisplayName", "DisplayVersion", "Publisher", "InstallDate"):
                                    try:
                                        vals[field] = str(winreg.QueryValueEx(sub, field)[0])
                                    except OSError:
                                        vals[field] = ""
                        except OSError:
                            continue
                        name = vals["DisplayName"]
                        if not name or name in seen or (filter and filter.lower() not in name.lower()):
                            continue
                        seen.add(name)
                        out.append(f"{name} {vals['DisplayVersion']} — {vals['Publisher']}".strip(" —"))
            except OSError:
                continue
    out.sort(key=str.lower)
    return f"{len(out)} phần mềm:\n" + "\n".join(out[: int(limit)])


@tool("Rà soát nhanh sức khỏe & bảo mật máy: tài nguyên, tiến trình ngốn máy, tiến trình chạy từ thư mục "
      "bất thường (Temp/Downloads/AppData), cổng đang mở, chương trình khởi động. Chỉ đọc, không sửa gì.",
      group=GROUP)
def system_scan() -> str:
    rows = _proc_rows()
    vm = psutil.virtual_memory()
    report = ["== TÀI NGUYÊN ==", f"CPU {psutil.cpu_percent(0.3)}% · RAM {vm.percent}% ({_gb(vm.available)} trống)"]
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
            flag = "  ⚠️ gần đầy" if u.percent > 90 else ""
            report.append(f"Ổ {part.mountpoint} {u.percent}%{flag}")
        except OSError:
            continue
    by_app: dict[str, list] = {}
    for r in rows:
        agg = by_app.setdefault(r["name"], [0.0, 0.0, 0])
        agg[0] += r["mem"]
        agg[1] += r["cpu"]
        agg[2] += 1
    report.append("\n== NGỐN RAM NHẤT (gộp theo phần mềm) ==")
    report += [f"{n} ×{c}: {m:.0f} MB" for n, (m, _, c) in sorted(by_app.items(), key=lambda x: -x[1][0])[:8]]
    report.append("\n== NGỐN CPU NHẤT (gộp theo phần mềm) ==")
    report += [f"{n} ×{c}: {cpu:.1f}%" for n, (_, cpu, c) in sorted(by_app.items(), key=lambda x: -x[1][1])[:6]]
    odd_dirs = ("\\temp\\", "\\tmp\\", "\\downloads\\", "\\appdata\\local\\temp", "\\public\\", "\\recycle")
    odd = [r for r in rows if r["exe"] and any(d in r["exe"].lower() for d in odd_dirs)]
    report.append("\n== TIẾN TRÌNH CHẠY TỪ THƯ MỤC BẤT THƯỜNG ==")
    report += [f"{r['name']} ({r['pid']}): {r['exe']}" for r in odd[:15]] or ["(không có)"]
    try:
        listen = [c for c in psutil.net_connections(kind="inet") if c.status == "LISTEN"]
        ports = sorted({(c.laddr.port, c.pid) for c in listen})
        report.append(f"\n== CỔNG ĐANG MỞ ({len(ports)}) ==")
        for port, pid in ports[:25]:
            try:
                name = psutil.Process(pid).name() if pid else "?"
            except psutil.Error:
                name = "?"
            report.append(f":{port} — {name}")
    except psutil.AccessDenied:
        report.append("\n(Cần quyền admin để xem cổng mạng)")
    if sys.platform == "win32":
        report.append("\n== KHỞI ĐỘNG CÙNG MÁY ==")
        report.append(startup_programs())
    return "\n".join(report)
