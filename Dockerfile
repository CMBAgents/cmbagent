# Use an official Python image as base
FROM python:3.13-slim

# Set environment variables to avoid interactive prompts during package installs
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /cmbagent

# Copy all the app code to the docker
COPY . .

# Install
RUN pip install .

# This informs Docker that the container will listen on port 8501 at runtime.
EXPOSE 8501

# Command to run the app
CMD ["cmbagent", "run"]