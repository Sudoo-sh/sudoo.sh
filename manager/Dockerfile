# The panel image: Alpine with the Docker CLI and Compose plugin (to manage instances on the host's Docker),
# Python, Flask, gunicorn, aiohttp (gateway), cryptography (vault), Markdown (documentation pages) and git.
FROM docker:29-cli

LABEL org.opencontainers.image.title="Odoo Instance Manager" \
      org.opencontainers.image.authors="Yousef M. Y. Al Sabbah" \
      org.opencontainers.image.vendor="Yousef M. Y. Al Sabbah"

RUN apk add --no-cache python3 py3-flask py3-gunicorn py3-aiohttp py3-cryptography py3-markdown \
        git openssh-client openssh-keygen \
 && git config --system --add safe.directory '*'

WORKDIR /app
COPY wsgi.py manage.py ./
COPY instance_manager ./instance_manager
COPY documentation ./documentation

# One worker process: jobs, monitor and gateway share its memory. Listens on 127.0.0.1 only; the panel's
# Cloudflare connector (and an SSH tunnel, for administrators) are the only ways in.
CMD ["gunicorn", "--workers", "1", "--threads", "32", "--timeout", "120", "--bind", "127.0.0.1:9000", "wsgi:app"]
