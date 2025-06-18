import os
import tempfile
import shutil
import time
import subprocess
import logging

from celery import Celery

# Configure logging (can be adjusted for less verbosity if needed later)
logging.basicConfig(level=logging.INFO) # Changed to INFO for less clutter during normal operation
logging.getLogger('celery').setLevel(logging.INFO)

# Initialize Celery app
# Using Redis as both broker and backend
# RENAMED from 'app' to 'celery_app' to match import in main.py
celery_app = Celery('celery_worker',
             broker='redis://localhost:6377/0',
             backend='redis://localhost:6377/0')

celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True, # Acknowledge task only after it's completed
    worker_prefetch_multiplier=1, # Process one task at a time
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    timezone='Asia/Kolkata', # Set your timezone
    enable_utc=True,
)

# --- Docker execution configuration ---
# Define configurations for different languages.
# Make sure these images are available locally or on Docker Hub.
# The `command` assumes the code file will be mounted at /usr/src/app/main.<extension>
language_configs = {
    "python": {
        "image": "python:3.9-slim-buster", # Or your custom Python image
        "file_extension": "py",
        "command": "python /usr/src/app/main.py", # Full path inside container
        "timeout": 30 # seconds
    },
    "java": {
        "image": "openjdk:17-jdk-slim", # Or your custom Java image
        "file_extension": "java",
        # Compile and then run. Assumes main class is `main`
        "command": "javac /usr/src/app/main.java && java -cp /usr/src/app main",
        "timeout": 30 # seconds
    },
    "javascript": {
        "image": "node:18-slim", # Or your custom Node.js image
        "file_extension": "js",
        "command": "node /usr/src/app/main.js",
        "timeout": 30 # seconds
    },
    # Add more languages as needed
}

# --- Celery Task for Code Execution ---
@celery_app.task(bind=True) # Use celery_app.task decorator
def run_code_in_docker(self, language: str, code: str):
    self.request.max_retries = 3 # Max retries for the task
    self.request.countdown = 2   # Initial delay before retrying

    if language not in language_configs:
        return {"status": "error", "output": "", "error": "Unsupported language.", "execution_time": None}

    language_config = language_configs[language]
    temp_dir = None # Initialize temp_dir to ensure it's always defined for finally block

    try:
        # Create a temporary directory to store the code file
        # This directory will be mounted into the Docker container.
        temp_dir = tempfile.mkdtemp(prefix="code_runner_")
        temp_file_name = f"main.{language_config['file_extension']}"
        temp_file_path_in_host = os.path.join(temp_dir, temp_file_name)

        # Write the user's code to the temporary file
        with open(temp_file_path_in_host, 'w') as f:
            f.write(code)

        # Construct the `docker run` command as a list of arguments
        docker_command = [
            "docker", "run",
            "--rm", # Automatically remove the container when it exits
            "-v", f"{temp_dir}:/usr/src/app:ro", # Mount the temporary directory read-only to /usr/src/app inside container
            language_config['image'], # The Docker image to use (e.g., python:3.9-slim-buster)
            "sh", "-c", language_config['command'] # Execute the specified command inside the container via sh
        ]

        logging.info(f"Executing Docker command: {' '.join(docker_command)}")

        start_time = time.time()
        # Execute the command using subprocess.run
        result = subprocess.run(
            docker_command,
            capture_output=True, # Capture stdout and stderr
            text=True,           # Decode stdout/stderr as text
            timeout=language_config['timeout'], # Set a timeout for the Docker process
        )
        end_time = time.time()
        execution_time = end_time - start_time

        output = result.stdout.strip()
        error = result.stderr.strip() if result.stderr else None

        # Determine overall status based on Docker command's exit code
        status = "success"
        if result.returncode != 0:
            status = "error"
            if not error: # If no stderr, but non-zero exit, use a generic error message
                error = f"Process exited with non-zero status code: {result.returncode}. Output: {output}"

        logging.info(f"Code execution finished. Status: {status}, Time: {execution_time:.2f}s")
        if output:
            logging.info(f"Output:\n{output}")
        if error:
            logging.error(f"Error:\n{error}")

        return {
            "status": status,
            "output": output,
            "error": error,
            "execution_time": execution_time
        }

    except subprocess.TimeoutExpired:
        # Handle cases where the Docker command times out
        logging.error(f"Execution timed out for task {self.request.id} after {language_config['timeout']} seconds.")
        raise self.retry(
            exc=subprocess.TimeoutExpired(cmd=docker_command, timeout=language_config['timeout']),
            countdown=self.request.countdown * 2 # Exponential backoff for retries
        )
    except FileNotFoundError:
        # This typically means the 'docker' command itself was not found in PATH
        logging.critical("Docker command not found. Is Docker installed and in your PATH?")
        return {
            "status": "error",
            "output": "",
            "error": "Docker CLI not found. Please ensure Docker is installed and accessible in the system's PATH.",
            "execution_time": None
        }
    except Exception as e:
        # Catch any other unexpected errors during the process
        logging.error(f"An unexpected error occurred for task {self.request.id}: {str(e)}")
        raise self.retry(
            exc=e,
            countdown=self.request.countdown * 2
        )
    finally:
        # Ensure the temporary directory is cleaned up
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logging.info(f"Cleaned up temporary directory: {temp_dir}")
            except Exception as e:
                logging.error(f"Error cleaning up temporary directory {temp_dir}: {e}")

# This ensures the Celery app is available for the worker to discover tasks
if __name__ == '__main__':
    celery_app.worker_main(['worker', '-l', 'info', '--concurrency=1']) # Use celery_app here