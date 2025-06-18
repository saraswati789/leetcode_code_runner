# code_runner_environments/python_env.Dockerfile

# Use a minimal Python base image
# We choose a specific version for stability and security.
# Alpine is often used for smaller images, but for stability, a full Debian-based image is good.
FROM python:3.9-slim-buster

# Set the working directory inside the container
WORKDIR /usr/src/app

# Create a non-root user for security
# This user will run the submitted code
RUN useradd -m user
USER user

# Command to run (this is a placeholder; actual code will be mounted and run by the backend)
CMD ["python", "--version"]