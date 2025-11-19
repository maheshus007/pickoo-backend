FROM python:3.12-slim

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

EXPOSE 8000

# Run gunicorn with uvicorn workers for ASGI support
CMD ["gunicorn", "application:application", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "-w", "4", "--timeout", "120"]
