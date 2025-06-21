# Dockerfile
FROM python:3.10-slim-buster

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your application code
COPY . .

# Expose the port Uvicorn will run on
EXPOSE 8000

# Default command for the web service (overridden for worker)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]