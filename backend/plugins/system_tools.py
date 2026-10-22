"""Plugin: clipboard và mở ứng dụng trên máy chủ nhân."""
import subprocess
import sys

from cazung.tools import tool

GROUP = "máy tính"


@tool("Đọc nội dung đang có trong clipboard của chủ nhân.", group=GROUP)
def clipboard_read() -> str:
    if sys.platform == "win32":
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"],
                             capture_output=True, timeout=10)
        return out.stdout.decode("utf-8", "replace") or "(clipboard trống)"
    return "Chỉ hỗ trợ Windows."


@tool("Chép một đoạn văn bản vào clipboard của chủ nhân.", {"text": "str: nội dung"}, dangerous=True, group=GROUP)
def clipboard_write(text: str) -> str:
    if sys.platform == "win32":
        subprocess.run(["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
                       input=text.encode("utf-8"), timeout=10)
        return "Đã chép vào clipboard."
    return "Chỉ hỗ trợ Windows."


@tool("Mở một ứng dụng hoặc file trên máy (vd 'notepad', 'calc', 'explorer', đường dẫn file).",
      {"target": "str: tên ứng dụng hoặc đường dẫn"}, dangerous=True, group=GROUP)
def open_app(target: str) -> str:
    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
    else:
        subprocess.Popen(["xdg-open", target])
    return f"Đã mở {target}"
