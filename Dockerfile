FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.in requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.in

COPY . .

EXPOSE 5000 8768

CMD ["python", "apps/elara_core/main.py"]
