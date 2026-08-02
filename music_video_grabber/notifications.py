from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from .config import Settings

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, subject: str, body: str) -> bool:
        if not self.settings.mailrise_host:
            logger.info("Notification disabled", extra={"event": "notification_skipped"})
            return False
        message = EmailMessage()
        message["From"] = self.settings.notification_from
        message["To"] = self.settings.notification_to
        message["Subject"] = subject
        message.set_content(body)
        try:
            with smtplib.SMTP(
                self.settings.mailrise_host, self.settings.mailrise_port, timeout=15
            ) as smtp:
                smtp.send_message(message)
            logger.info("Notification accepted by Mailrise", extra={"event": "notification_sent"})
            return True
        except Exception:
            logger.exception("Mailrise notification failed", extra={"event": "notification_failed"})
            return False
