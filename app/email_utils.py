import smtplib
from email.message import EmailMessage

from .config import settings


def send_result_email(to_email, to_name, subject, html_body, images=None):
    """
    images: dict of {content_id: png_bytes}, referenced in html_body as
            <img src="cid:CONTENT_ID">. Pass None/{} for a text-only mail
            such as the week-4 reminder.

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

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        if settings.SMTP_USE_TLS:
            server.starttls()
        if settings.SMTP_USER:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(msg)
    return True
