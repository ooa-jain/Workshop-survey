import os
from dotenv import load_dotenv

load_dotenv()



def _bool(val, default=False):
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # Mongo
    MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    MONGO_DB = os.environ.get("MONGO_DB", "job_intelligence_survey")

    # SMTP (for result emails)
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_USE_TLS = _bool(os.environ.get("SMTP_USE_TLS"), default=True)
    # Seconds to wait on the SMTP server before giving up. Without this a
    # hung mail server holds a socket open indefinitely.
    SMTP_TIMEOUT = int(os.environ.get("SMTP_TIMEOUT", "20"))
    EMAIL_FROM = os.environ.get("EMAIL_FROM", "Job Intelligence Workshop <noreply@juooa.cloud>")
    EMAIL_ENABLED = _bool(os.environ.get("EMAIL_ENABLED"), default=True)

    # Admin dashboard
    ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me")

    # App
    APP_NAME = os.environ.get("APP_NAME", "Job Search to Job Intelligence")
    BASE_URL = os.environ.get("BASE_URL", "https://job-intelligence.juooa.cloud")
    SESSION_SECRET = os.environ.get("SESSION_SECRET", "change-me-too")

    # Workshop metadata -- shown on forms, used to tag submissions by cohort/batch
    WORKSHOP_BATCH = os.environ.get("WORKSHOP_BATCH", "2026-final-year")

    # --- Stage gating -------------------------------------------------------
    # Post Survey 1 survey opens the moment that student's Pre lands, and closes
    # this many hours later. 14h covers a 9:30am Pre -> 11:30pm same night.
    SAMEDAY_WINDOW_HOURS = int(os.environ.get("SAMEDAY_WINDOW_HOURS", "14"))
    # Post Survey 2 survey stays locked this many days after that student's Pre.
    WEEK4_UNLOCK_DAYS = int(os.environ.get("WEEK4_UNLOCK_DAYS", "30"))
    # ...and then stays open this many days before expiring.
    WEEK4_OPEN_DAYS = int(os.environ.get("WEEK4_OPEN_DAYS", "14"))
    # Emergency override: set true to open every stage for everyone, e.g. if
    # you need to demo all three surveys in one sitting.
    GATING_DISABLED = _bool(os.environ.get("GATING_DISABLED"), default=False)


settings = Settings()
