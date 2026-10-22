"""Files & source code: explore any folder read-only; writing outside the workspace needs approval."""
from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from ..utils import truncate_middle
from . import ToolContext, tool

GROUP = "code & file"
IGNORED_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".idea",
                ".vscode", "target", "bin", "obj", ".npm-cache", ".electron-cache", ".mypy_cache", ".pytest_cache"}
MAX_FILE_BYTES = 2_000_000


def _ws(ctx: ToolContext) -> Path:
    p = Path(ctx.cfg.agent.workspace_path).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def resolve(ctx: ToolContext, path: str) -> Path:
    """Absolute paths are used as-is; relative paths are inside the workspace."""
    p = Path(os.path.expandvars(os.path.expanduser(str(path or ".").strip().strip('"'))))
    return (p if p.is_absolute() else _ws(ctx) / p).resolve()


def outside_workspace(args: dict, ctx: ToolContext) -> bool:
    target = resolve(ctx, args.get("path", "."))
    ws = _ws(ctx)
    return target != ws and ws not in target.parents


def _is_text(p: Path) -> bool:
    try:
        if p.stat().st_size > MAX_FILE_BYTES:
            return False
        with p.open("rb") as f:
            return b"\0" not in f.read(4096)
    except OSError:
        return False


def _walk(root: Path, glob: str | None = None):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".cache"))
        for name in sorted(filenames):
            if glob and not any(fnmatch.fnmatch(name, g.strip()) for g in glob.split(",")):
                continue
            yield Path(dirpath) / name


@tool("Liệt kê nội dung một thư mục (đường dẫn tuyệt đối, hoặc tương đối = trong workspace).",
      {"?path": "str: thư mục, mặc định workspace"}, group=GROUP)
def list_files(path: str = ".", ctx: ToolContext = None) -> str:
    p = resolve(ctx, path)
    if not p.is_dir():
        return f"Không phải thư mục: {p}"
    lines = [f"{p}:"]
    for item in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:300]:
        try:
            size = "" if item.is_dir() else f"  ({item.stat().st_size:,} B)"
        except OSError:
            size = ""
        lines.append(f"{'📁' if item.is_dir() else '📄'} {item.name}{size}")
    return "\n".join(lines)


@tool("Xem cây thư mục của một dự án (bỏ qua node_modules, .git, .venv...).",
      {"path": "str: thư mục gốc", "?depth": "int: độ sâu, mặc định 3"}, group=GROUP)
def tree(path: str, depth: int = 3, ctx: ToolContext = None) -> str:
    root = resolve(ctx, path)
    if not root.is_dir():
        return f"Không phải thư mục: {root}"
    depth = max(1, min(int(depth), 8))
    lines, count = [str(root)], 0

    def walk(d: Path, prefix: str, level: int):
        nonlocal count
        try:
            items = sorted((x for x in d.iterdir() if x.name not in IGNORED_DIRS),
                           key=lambda x: (not x.is_dir(), x.name.lower()))
        except OSError:
            return
        for i, item in enumerate(items):
            if count >= 400:
                lines.append(prefix + "…")
                return
            count += 1
            last = i == len(items) - 1
            lines.append(f"{prefix}{'└── ' if last else '├── '}{item.name}{'/' if item.is_dir() else ''}")
            if item.is_dir() and level < depth:
                walk(item, prefix + ("    " if last else "│   "), level + 1)

    walk(root, "", 1)
    return "\n".join(lines)


@tool("Đọc file văn bản/mã nguồn. Có thể chỉ đọc một khoảng dòng (kèm số dòng).",
      {"path": "str: đường dẫn file", "?start_line": "int", "?end_line": "int", "?max_chars": "int"}, group=GROUP)
def read_file(path: str, start_line: int | None = None, end_line: int | None = None,
              max_chars: int = 15000, ctx: ToolContext = None) -> str:
    p = resolve(ctx, path)
    if not p.is_file():
        return f"Không thấy file: {p}"
    if not _is_text(p):
        return f"{p} là file nhị phân hoặc quá lớn (>2MB)."
    text = p.read_text(encoding="utf-8", errors="replace")
    if start_line or end_line:
        lines = text.splitlines()
        a = max(1, int(start_line or 1))
        b = min(len(lines), int(end_line or len(lines)))
        text = "\n".join(f"{n:>5} | {lines[n - 1]}" for n in range(a, b + 1))
        text = f"{p} (dòng {a}-{b} / {len(lines)})\n" + text
    return truncate_middle(text, int(max_chars))


@tool("Tìm kiếm trong mã nguồn bằng regex (như grep), trả về file:dòng: nội dung.",
      {"pattern": "str: regex hoặc từ khóa", "?path": "str: thư mục gốc", "?glob": "str: lọc file, vd '*.py,*.js'",
       "?ignore_case": "bool", "?max_results": "int: mặc định 60"}, group=GROUP)
def search_code(pattern: str, path: str = ".", glob: str | None = None, ignore_case: bool = True,
                max_results: int = 60, ctx: ToolContext = None) -> str:
    root = resolve(ctx, path)
    flags = re.I if str(ignore_case).lower() not in ("false", "0", "no") else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error:
        rx = re.compile(re.escape(pattern), flags)
    files = [root] if root.is_file() else _walk(root, glob)
    hits, scanned = [], 0
    for f in files:
        if not _is_text(f):
            continue
        scanned += 1
        try:
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    rel = f.relative_to(root) if root.is_dir() else f.name
                    hits.append(f"{rel}:{n}: {line.strip()[:200]}")
                    if len(hits) >= int(max_results):
                        return "\n".join(hits) + f"\n… (dừng ở {max_results} kết quả)"
        except OSError:
            continue
    return "\n".join(hits) if hits else f"Không thấy '{pattern}' trong {scanned} file."


_OUTLINE = [
    re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+\w+"),                                           # python
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\*?|class|interface|type|enum)\s+\w+"),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>"),   # arrow fn
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?\w+|^\s*type\s+\w+\s+(?:struct|interface)"),                # go
    re.compile(r"^\s*(?:pub\s+)?(?:fn|struct|enum|trait|impl)\b"),                                         # rust
    re.compile(r"^\s*(?:public|private|protected|internal|static|abstract|final|sealed|override|virtual|\s)*"
               r"(?:class|interface|struct|enum|record|[\w<>\[\],]+\s+\w+\s*\([^;]*\)\s*(?:\{|$))"),       # java/c#
]


@tool("Xem dàn ý một file mã nguồn: các class/hàm cùng số dòng (để đào sâu code nhanh).",
      {"path": "str: đường dẫn file"}, group=GROUP)
def code_outline(path: str, ctx: ToolContext = None) -> str:
    p = resolve(ctx, path)
    if not p.is_file() or not _is_text(p):
        return f"Không đọc được: {p}"
    out = []
    for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if any(rx.match(line) for rx in _OUTLINE) and not re.match(r"^\s*(if|for|while|switch|catch|return)\b", line):
            out.append(f"{n:>5}: {line.rstrip()[:160]}")
    return f"{p}\n" + ("\n".join(out[:300]) if out else "(không nhận diện được class/hàm)")


@tool("Tạo/ghi đè (hoặc nối thêm) file. Dùng để viết code.",
      {"path": "str: đường dẫn (tương đối = trong workspace)", "content": "str: nội dung",
       "?append": "bool: nối thêm thay vì ghi đè"}, confirm_if=outside_workspace, group=GROUP)
def write_file(path: str, content: str, append: bool = False, ctx: ToolContext = None) -> str:
    p = resolve(ctx, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    append = str(append).lower() in ("1", "true", "yes")
    with p.open("a" if append else "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return f"Đã {'nối thêm' if append else 'ghi'} {len(content)} ký tự vào {p}"


@tool("Sửa file bằng cách thay một đoạn văn bản chính xác bằng đoạn mới (đọc file trước khi sửa).",
      {"path": "str", "old": "str: đoạn cũ, phải khớp chính xác", "new": "str: đoạn mới",
       "?replace_all": "bool"}, confirm_if=outside_workspace, group=GROUP)
def edit_file(path: str, old: str, new: str, replace_all: bool = False, ctx: ToolContext = None) -> str:
    p = resolve(ctx, path)
    text = p.read_text(encoding="utf-8")
    n = text.count(old)
    if n == 0:
        return "Không tìm thấy đoạn cũ trong file (hãy read_file để lấy đúng nội dung)."
    every = str(replace_all).lower() in ("1", "true", "yes")
    if n > 1 and not every:
        return f"Đoạn cũ xuất hiện {n} lần; thêm ngữ cảnh cho duy nhất, hoặc đặt replace_all=true."
    p.write_text(text.replace(old, new) if every else text.replace(old, new, 1), encoding="utf-8", newline="")
    return f"Đã sửa {n if every else 1} chỗ trong {p}"


@tool("Xóa một file.", {"path": "str"}, dangerous=True, group=GROUP)
def delete_file(path: str, ctx: ToolContext = None) -> str:
    p = resolve(ctx, path)
    if not p.is_file():
        return f"Không phải file: {p}"
    p.unlink()
    return f"Đã xóa {p}"
