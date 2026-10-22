"""Email: read the inbox (IMAP, read-only) and send mail (SMTP, always asks first). Configure [email]."""
from __future__ import annotations

import email
import imaplib
import smtplib
import ssl
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, parsedate_to_datetime

from ..utils import truncate_middle
from . import ToolContext, tool

GROUP = "email"


def _cfg(ctx: ToolContext):
    e = getattr(ctx.cfg, "email", None)
    if not e or not e.enabled or not e.username or not e.password:
        raise RuntimeError("Email chưa được cấu hình. Điền mục [email] trong config.toml (Gmail: dùng App Password).")
    return e


def _dec(value) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:  # noqa: BLE001
        return str(value or "")


def _body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition")):
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                from bs4 import BeautifulSoup
                html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                return BeautifulSoup(html, "html.parser").get_text("\n")
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", "replace")


@tool("Đọc email trong hộp thư (không đánh dấu đã đọc).",
      {"?limit": "int: số thư, mặc định 10", "?unread_only": "bool: chỉ thư chưa đọc, mặc định true",
       "?search": "str: lọc theo người gửi/tiêu đề", "?folder": "str: mặc định INBOX",
       "?full": "bool: kèm nội dung đầy đủ"}, group=GROUP)
def read_emails(limit: int = 10, unread_only: bool = True, search: str | None = None, folder: str = "INBOX",
                full: bool = False, ctx: ToolContext = None) -> str:
    e = _cfg(ctx)
    unread = str(unread_only).lower() not in ("false", "0", "no")
    criteria = ["UNSEEN"] if unread else ["ALL"]
    if search:
        criteria = ["OR", "FROM", f'"{search}"', "SUBJECT", f'"{search}"'] + (["UNSEEN"] if unread else [])
    with imaplib.IMAP4_SSL(e.imap_host, int(e.imap_port)) as box:
        box.login(e.username, e.password)
        box.select(folder, readonly=True)
        _, data = box.search("UTF-8" if search else None, *criteria)
        ids = data[0].split()[-int(limit):][::-1]
        out = []
        for mid in ids:
            _, msg_data = box.fetch(mid, "(BODY.PEEK[])")
            msg = email.message_from_bytes(msg_data[0][1])
            try:
                date = parsedate_to_datetime(msg["Date"]).strftime("%Y-%m-%d %H:%M")
            except Exception:  # noqa: BLE001
                date = msg["Date"] or ""
            body = " ".join(_body(msg).split())
            out.append(f"#{mid.decode()} [{date}] Từ: {_dec(msg['From'])}\n   Tiêu đề: {_dec(msg['Subject'])}\n   "
                       + (truncate_middle(body, 3000) if str(full).lower() in ("true", "1") else body[:200]))
    return "\n".join(out) or "Không có thư nào."


@tool("Gửi email thay chủ nhân (luôn hiện bản xem trước để chủ nhân duyệt).",
      {"to": "str: địa chỉ người nhận, nhiều địa chỉ cách nhau bằng dấu phẩy", "subject": "str",
       "body": "str: nội dung", "?cc": "str"}, dangerous=True, group=GROUP)
def send_email(to: str, subject: str, body: str, cc: str | None = None, ctx: ToolContext = None) -> str:
    e = _cfg(ctx)
    msg = EmailMessage()
    msg["From"] = formataddr((e.from_name, e.username)) if e.from_name else e.username
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg.set_content(body)
    port = int(e.smtp_port)
    if port == 465:
        with smtplib.SMTP_SSL(e.smtp_host, port, context=ssl.create_default_context(), timeout=30) as s:
            s.login(e.username, e.password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(e.smtp_host, port, timeout=30) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(e.username, e.password)
            s.send_message(msg)
    return f"Đã gửi email tới {to}: «{subject}»"
