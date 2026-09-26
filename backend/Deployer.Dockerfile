FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/requirements.txt /tmp/backend-requirements.txt
COPY backend/deployer-requirements.txt /tmp/deployer-requirements.txt
RUN pip install --no-cache-dir -r /tmp/backend-requirements.txt -r /tmp/deployer-requirements.txt

COPY backend /app/backend

CMD ["python", "backend/deployer.py"]
