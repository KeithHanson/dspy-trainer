ARG BACKEND_BASE_IMAGE
FROM ${BACKEND_BASE_IMAGE}

COPY backend/deployer-requirements.txt /tmp/deployer-requirements.txt
RUN pip install --no-cache-dir -r /tmp/deployer-requirements.txt

CMD ["python", "backend/deployer.py"]
