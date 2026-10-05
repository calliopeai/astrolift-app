"""One configured Django SMTP notice, with durable admission before DATA."""

import importlib
import re
import smtplib
import socket
import threading
import time
from contextlib import contextmanager

from django.core.mail.backends.smtp import EmailBackend

from core.permissions import PermissionDenied


class AlertMailUnavailable(ValueError):
    """Only fixed, content-free reasons cross this boundary."""


@contextmanager
def _private_transport():
    try:
        suppress = importlib.import_module("opentelemetry.instrumentation.utils").suppress_instrumentation
    except ImportError:
        yield
    else:
        with suppress():
            yield


def send_notice(message, *, source, checkpoint, before_data):
    """The final SMTP DATA acknowledgement is retained before public re-admission."""
    deadline = time.monotonic() + 15
    timed_out = threading.Event()
    active = []

    def remaining(connection=None):
        if timed_out.is_set() or time.monotonic() >= deadline:
            raise AlertMailUnavailable("ALERT_MAIL_DEADLINE_EXCEEDED")
        if connection is not None and getattr(connection, "sock", None) is not None:
            connection.sock.settimeout(min(5, max(0.01, deadline - time.monotonic())))

    def gate(connection=None):
        remaining(connection)
        checkpoint()
        remaining(connection)

    class GuardedSMTP:
        def __init__(self, *args, **kwargs):
            self.final_data_reply = False
            self.accepted = False
            active.append(self)
            gate()
            super().__init__(*args, **kwargs)

        def putcmd(self, cmd, args=""):
            gate(self)
            return super().putcmd(cmd, args)

        def getreply(self):
            # Reading the original SENT acknowledgement grants no subsequent command.
            if self.final_data_reply:
                remaining(self)
            else:
                gate(self)
            # smtplib bounds each line, but not multiline reply allocation.
            # Preserve its reply framing with a finite total/line-count ceiling.
            if self.file is None:
                self.file = self.sock.makefile("rb")
            lines, total = [], 0
            for _ in range(64):
                remaining(self)
                line = self.file.readline(smtplib._MAXLINE + 1)
                total += len(line)
                if len(line) > smtplib._MAXLINE or total > 32768:
                    raise AlertMailUnavailable("ALERT_MAIL_RESPONSE_LIMIT")
                if not line:
                    raise smtplib.SMTPServerDisconnected("SMTP reply unavailable")
                lines.append(line[4:].strip(b" \t\r\n"))
                try:
                    code = int(line[:3])
                except ValueError:
                    code = -1
                    break
                if line[3:4] != b"-":
                    break
            else:
                raise AlertMailUnavailable("ALERT_MAIL_RESPONSE_LIMIT")
            reply = code, b"\n".join(lines)
            if self.final_data_reply:
                self.accepted = reply[0] == 250
            else:
                gate(self)
            return reply

        def send(self, data):
            gate(self)
            return super().send(data)

        def data(self, msg):
            self.putcmd("data")
            code, reply = self.getreply()
            if code != 354:
                raise smtplib.SMTPDataError(code, reply)
            before_data()
            if isinstance(msg, str):
                msg = smtplib._fix_eols(msg).encode("ascii")
            payload = smtplib._quote_periods(msg)
            if not payload.endswith(b"\r\n"):
                payload += b"\r\n"
            self.send(payload + b".\r\n")
            self.final_data_reply = True
            try:
                return self.getreply()
            finally:
                self.final_data_reply = False

    class BoundSMTP(GuardedSMTP, smtplib.SMTP):
        pass

    class BoundSMTPSSL(GuardedSMTP, smtplib.SMTP_SSL):
        pass

    class BoundBackend(EmailBackend):
        @property
        def connection_class(self):
            return BoundSMTPSSL if self.use_ssl else BoundSMTP

        def close(self):
            # No new SMTP command after an acknowledgement or withdrawn authority.
            if self.connection is not None:
                self.connection.close()
                self.connection = None

    gate()
    backend = BoundBackend(**source.backend_kwargs(), fail_silently=False, timeout=5)

    def expire():
        timed_out.set()
        connection = active[-1] if active else None
        sock = getattr(connection, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    timer = threading.Timer(max(0.01, deadline - time.monotonic()), expire)
    timer.daemon = True
    try:
        with _private_transport():
            timer.start()
            backend.open()
            # The ordinary Django SMTP backend performs the same envelope/MIME submission.
            accepted = backend._send(message)
            if not accepted or not backend.connection.accepted:
                raise AlertMailUnavailable("ALERT_MAIL_NOT_ACCEPTED")
            return True
    except (AlertMailUnavailable, PermissionDenied):
        raise
    except smtplib.SMTPAuthenticationError:
        raise AlertMailUnavailable("ALERT_MAIL_AUTHENTICATION_FAILED") from None
    except smtplib.SMTPRecipientsRefused:
        raise AlertMailUnavailable("ALERT_MAIL_RECIPIENT_REJECTED") from None
    except smtplib.SMTPDataError:
        raise AlertMailUnavailable("ALERT_MAIL_DATA_REJECTED") from None
    except Exception:
        raise AlertMailUnavailable("ALERT_MAIL_TRANSPORT_UNCONFIRMED") from None
    finally:
        timer.cancel()
        if timer.ident is not None:
            timer.join(timeout=1)
        backend.close()
        # Constructor failures occur before EmailBackend can retain connection.
        for connection in active:
            if getattr(connection, "sock", None) is not None or getattr(connection, "file", None) is not None:
                connection.close()


def mailbox(value):
    return (
        isinstance(value, str)
        and len(value) <= 254
        and re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", value) is not None
        and len(value.split("@", 1)[0]) <= 64
        and ".." not in value
    )
