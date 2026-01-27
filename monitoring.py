import os
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration


def init_sentry():
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return  # Sentry is optional; fail silently if not configured

    sentry_sdk.init(
        dsn=dsn,
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.1,   # low overhead
        send_default_pii=False,   # important for privacy
        environment=os.environ.get("ENV", "production"),
    )
