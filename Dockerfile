FROM python:3.10-slim

# Install system deps commonly required by OpenCV, PyTorch wheels, and ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    gcc \
    ffmpeg \
    libsm6 \
    libxrender1 \
    libxext6 \
    libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency list and install. Use requirements-full.txt for full ML stack.
COPY requirements-full.txt ./requirements-full.txt
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements-full.txt

# Copy project files (models should be added to repo or mounted at /app/models)
COPY . /app

ENV PYTHONUNBUFFERED=1
EXPOSE 5000

CMD ["python", "app.py"]
