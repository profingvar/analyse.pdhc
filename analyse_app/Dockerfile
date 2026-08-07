FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FLASK_APP=app

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app
RUN chmod +x /app/entrypoint.sh

EXPOSE 9110
# entrypoint runs `flask db upgrade` then execs gunicorn on 0.0.0.0:9110.
# The host only ever reaches it via compose's 127.0.0.1:9110->9110 mapping.
ENTRYPOINT ["/app/entrypoint.sh"]
