FROM python:3.10-slim

WORKDIR /app

# Install system build dependencies if needed (e.g. for cryptography)
RUN apt-get update && apt-get install -y \
    build-essential \
    libssl-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sync.py .

CMD ["python", "sync.py"]
