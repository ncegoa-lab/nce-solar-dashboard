FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV SOLAR_OUTPUT_DIR=/app/reports
ENV SOLAR_AUTO_REFRESH_ON_OPEN=false

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends curl ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY requirements_web.txt .
RUN pip install --no-cache-dir -r requirements_web.txt

COPY . .

EXPOSE 8765

CMD ["sh", "-c", "python solar_live_app.py --host 0.0.0.0 --port ${PORT:-8765} --no-browser"]
