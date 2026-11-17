FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends openssl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml /app/
COPY future_intelligence /app/future_intelligence
COPY deploy/bootstrap.py /app/deploy/bootstrap.py
RUN pip install --no-cache-dir '.[postgres,web]'
USER 10001:10001
CMD ["python","-m","future_intelligence.runtime","serve","--container-bind"]
