"""Entry point of the web panel for gunicorn: `gunicorn instance_manager.wsgi:app` (started by `instance-manager serve`)."""

from instance_manager.web import create_app

app = create_app()
