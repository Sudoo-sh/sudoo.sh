"""Entry point of the web panel for gunicorn: `gunicorn wsgi:app`."""
from instance_manager.web import create_app

app = create_app()
