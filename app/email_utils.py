import smtplib
import traceback
from email.message import EmailMessage

from starlette.background import BackgroundTask

from .config import settings


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

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = f"{to_name} <{to_email}>"
    msg.set_content("Your results are ready. Please view this email in an HTML-capable client.")
    msg.add_alternative(html_body, subtype="html")

    html_part = msg.get_payload()[-1]
    for cid, png_bytes in (images or {}).items():
        html_part.add_related(png_bytes, maintype="image", subtype="png", cid=f"<{cid}>")

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT,
                      timeout=settings.SMTP_TIMEOUT) as server:
        if settings.SMTP_USE_TLS:
            server.starttls()
        if settings.SMTP_USER:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(msg)
    return True


def background_send(label, fn, *args, **kwargs):
    """
    A background task that builds and sends one result email *after* the
    student's result page has already gone out.

    The submission is safely stored by the time we get here, so neither a
    slow SMTP server nor a failing one should be able to hold that page up or
    turn it into an error. Starlette runs this on a worker thread, so the
    chart rendering and the SMTP round-trip also stay off the event loop,
    where they would otherwise stall every other student mid-submit.
    Whatever goes wrong in there is logged and stays there.
    """
    def _run():
        try:
            fn(*args, **kwargs)
        except Exception:                            # SMTP down, bad address, etc.
            print(f"[email] {label} failed:")
            traceback.print_exc()

    return BackgroundTask(_run)
