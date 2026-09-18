FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Non-root user for security
RUN useradd --create-home appuser
USER appuser

# Expose the documented port
EXPOSE 8000

# Bind to 0.0.0.0 — no baked-in secrets
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
