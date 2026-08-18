import smtplib
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage

from .config import settings


def _connect():
    """One authenticated SMTP connection. Callers are responsible for closing
    it -- use it as a context manager, or hand it to send_many() which reuses
    a single connection for a whole run."""
    server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT,
                          timeout=settings.SMTP_TIMEOUT)
    if settings.SMTP_USE_TLS:
        server.starttls()
    if settings.SMTP_USER:
        server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
    return server


def _build(to_email, to_name, subject, html_body, images=None):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = f"{to_name} <{to_email}>"
    msg.set_content("Your results are ready. Please view this email in an HTML-capable client.")
    msg.add_alternative(html_body, subtype="html")

    html_part = msg.get_payload()[-1]
    for cid, png_bytes in (images or {}).items():
        html_part.add_related(png_bytes, maintype="image", subtype="png", cid=f"<{cid}>")
    return msg


def send_result_email(to_email, to_name, subject, html_body, images=None):
    """
    images: dict of {content_id: png_bytes}, referenced in html_body as
            <img src="cid:CONTENT_ID">. Pass None/{} for a text-only mail
            such as the Post Survey 2 reminder.

    Does nothing (logs to stdout) if EMAIL_ENABLED is false or SMTP host
    is unset -- lets the app run in a dev/demo environment without a
    real mail server configured. Returns True only if a mail actually left.
    """
    if not settings.EMAIL_ENABLED or not settings.SMTP_HOST:
        print(f"[email disabled] would send '{subject}' to {to_name} <{to_email}>")
        return False

    msg = _build(to_email, to_name, subject, html_body, images)
    with _connect() as server:
        server.send_message(msg)
    return True


def send_many(label, items, on_sent=None):
    """
    Send a whole run of mail down ONE SMTP connection.

    items: list of (to_email, to_name, subject, html_body) tuples.
    on_sent: optional callback given the recipient address after each mail
             that actually leaves, so progress is recorded as it happens
             rather than only at the end.

    A run of 500 reminders opening 500 separate authenticated connections is
    what tips a mail provider into rate-limiting; one connection carrying 500
    messages does not. A single bad address is logged and skipped, and a
    dropped connection is re-established once before the run gives up.
    Returns (sent, failed).
    """
    sent = failed = 0
    if not settings.EMAIL_ENABLED or not settings.SMTP_HOST:
        for to_email, to_name, subject, _html in items:
            print(f"[email disabled] would send '{subject}' to {to_name} <{to_email}>")
        return 0, 0

    server = None
    try:
        for to_email, to_name, subject, html_body in items:
            try:
                if server is None:
                    server = _connect()
                server.send_message(_build(to_email, to_name, subject, html_body))
            except smtplib.SMTPServerDisconnected:
                # The provider dropped a long-running connection. Reconnect
                # once and retry this one message before counting it lost.
                try:
                    server = _connect()
                    server.send_message(_build(to_email, to_name, subject, html_body))
                except Exception:
                    print(f"[email] {label} failed for {to_email} after reconnect:")
                    traceback.print_exc()
                    server = None
                    failed += 1
                    continue
            except Exception:
                print(f"[email] {label} failed for {to_email}:")
                traceback.print_exc()
                failed += 1
                continue
            sent += 1
            if on_sent:
                try:
                    on_sent(to_email)
                except Exception:
                    print(f"[email] {label} could not record the send for {to_email}:")
                    traceback.print_exc()
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass
    print(f"[email] {label} finished: {sent} sent, {failed} failed")
    return sent, failed


# --- The background sender ------------------------------------------------
#
# Mail is built and sent off the request path, on a small pool of threads.
#
# Small on purpose. Rendering the PNG charts is CPU work holding the GIL, and
# the pool is shared with nothing else: if mail could fan out across the
# request threadpool, a cohort of 500 submitting at once would have hundreds
# of threads drawing charts while other students are still waiting for their
# result page. A handful of mail threads keeps a backlog of email from ever
# competing with the students still filling the form -- the queue drains a
# little later, which nobody notices, instead of the site slowing down, which
# everybody does.

_pool = None
_pool_lock = threading.Lock()


def _executor():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadPoolExecutor(
                    max_workers=settings.EMAIL_WORKERS, thread_name_prefix="email")
    return _pool


def queue_send(label, fn, *args, **kwargs):
    """
    Run a mail-sending function in the background and return immediately.

    The submission is safely stored by the time we get here, so neither a
    slow SMTP server nor a failing one should be able to hold up the
    student's result page or turn it into an error. Whatever goes wrong in
    there is logged and stays there.
    """
    def _run():
        try:
            fn(*args, **kwargs)
        except Exception:                            # SMTP down, bad address, etc.
            print(f"[email] {label} failed:")
            traceback.print_exc()

    try:
        _executor().submit(_run)
    except RuntimeError:                             # interpreter shutting down
        print(f"[email] {label} dropped -- the process is shutting down")
