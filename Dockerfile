# SnapDeploy (free) deploy for the FreeModels Chat API.
# Build with: a repo containing this Dockerfile + requirements.txt + app/.
# No secrets anywhere in the image: the upstream needs no key.
# The deploy host sets $PORT — the server must bind to it.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
