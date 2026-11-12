"""Entry point of the web panel for gunicorn: `gunicorn sudoo.wsgi:app` (started by `sudoo.sh serve`)."""

from sudoo.web import create_app

app = create_app()
