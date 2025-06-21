# celery_worker.py
import subprocess
import tempfile
import os
import shutil
import time
from celery import Celery
from typing import Dict, Any, Optional, List
import logging
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)

# Celery configuration
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6377/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6377/0')

celery_app = Celery(
    'code_runner',
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND
)

celery_app.conf.update(
    task_track_started=True,
    task_time_limit=30, # Increased timeout for more complex code/multiple test cases
    task_soft_time_limit=25,
    broker_connection_retry_on_startup=True,
)

# Language-specific Docker image and command configurations
# For Python, the command needs to be the actual interpreter call inside the container.
# We will use 'bash -c "echo ... | python main.py"' to handle input.
LANGUAGE_CONFIGS = {
    "python": {
        "image": "python:3.9-slim-buster",
        # The command for running the script within the container
        # We will dynamically build the full docker run command later
        "executable": ["python", "/app/main.py"], # <-- Changed from 'command' to 'executable'
        "filename": "main.py"
    },
    # "java": {
    #     "image": "openjdk:17-jdk-slim",
    #     "executable": ["java", "Main"],
    #     "filename": "Main.java"
    # },
    # "cpp": {
    #     "image": "gcc:latest",
    #     "executable": ["bash", "-c", "g++ main.cpp -o main && ./main"], # Example for C++ build/run
    #     "filename": "main.cpp"
    # }
}

# --- Re-defining the Pydantic models needed by the worker ---
class TestCase(BaseModel):
    input: str
    expected_output: str

class TestCaseResult(BaseModel):
    test_case_number: int
    input: str
    expected_output: str
    actual_output: str
    passed: bool
    error: Optional[str] = None

class CodeExecutionResult(BaseModel):
    status: str # "success", "compilation_error", "runtime_error", "timeout", "worker_error"
    overall_passed: Optional[bool] = None
    output: Optional[str] = None
    error: Optional[str] = None
    execution_time: Optional[float] = None
    test_results: Optional[List[TestCaseResult]] = None


@celery_app.task(bind=True, default_retry_delay=5, max_retries=3)
def run_code_in_docker(self, language: str, code: str, test_cases: Optional[List[Dict[str, str]]] = None):
    """
    Executes user code in a Docker container with provided test cases.
    Returns a CodeExecutionResult object.
    """
    config = LANGUAGE_CONFIGS.get(language)
    if not config:
        return CodeExecutionResult(
            status="worker_error",
            error=f"Unsupported language: {language}",
            overall_passed=False
        ).dict()

    temp_dir = None
    start_time = time.time()
    all_test_results: List[TestCaseResult] = []
    overall_status = "success"
    overall_passed_flag = True
    overall_error_message = None

    try:
        temp_dir = tempfile.mkdtemp()
        file_path = os.path.join(temp_dir, config["filename"])
        with open(file_path, "w") as f:
            f.write(code)

        docker_image = config["image"]

        # Base Docker command prefix for all runs
        base_docker_run_prefix = [
            "docker", "run", "--rm",
            "-v", f"{temp_dir}:/app", # Mount the temp directory
            "-w", "/app",             # Set working directory inside container
            docker_image
        ]

        # If no specific test cases provided, run once without stdin, capture stdout/stderr
        if not test_cases:
            try:
                logging.info(f"Running code without specific test cases for {language}...")
                process = subprocess.run(
                    base_docker_run_prefix + config["executable"], # Use 'executable' here
                    capture_output=True,
                    text=True, # Keep text=True for this branch as no stdin is used via `input` arg
                    check=False,
                    timeout=celery_app.conf.task_time_limit
                )
                actual_output = process.stdout.strip()
                error_output = process.stderr.strip()

                if process.returncode != 0:
                    overall_status = "runtime_error"
                    overall_error_message = error_output if error_output else f"Process exited with non-zero code {process.returncode}."
                    overall_passed_flag = False
                elif error_output:
                    overall_error_message = error_output

                execution_time = time.time() - start_time
                return CodeExecutionResult(
                    status=overall_status,
                    overall_passed=overall_passed_flag,
                    output=actual_output,
                    error=overall_error_message,
                    execution_time=execution_time,
                    test_results=[]
                ).dict()

            except subprocess.TimeoutExpired:
                overall_status = "timeout"
                overall_error_message = "Code execution timed out."
                overall_passed_flag = False
                if process.poll() is None:
                    process.kill()
                execution_time = time.time() - start_time
                return CodeExecutionResult(
                    status=overall_status,
                    overall_passed=overall_passed_flag,
                    output="",
                    error=overall_error_message,
                    execution_time=execution_time
                ).dict()
            except Exception as e:
                overall_status = "worker_error"
                overall_error_message = f"An unexpected error occurred during execution: {str(e)}"
                overall_passed_flag = False
                execution_time = time.time() - start_time
                return CodeExecutionResult(
                    status=overall_status,
                    overall_passed=overall_passed_flag,
                    output="",
                    error=overall_error_message,
                    execution_time=execution_time
                ).dict()


        # --- Execute with Test Cases ---
        logging.info(f"Running code with {len(test_cases)} test cases for {language}...")
        for i, test_case_dict in enumerate(test_cases):
            test_case = TestCase(**test_case_dict)
            test_case_number = i + 1
            passed = False
            test_case_error = None

            # Construct the command that pipes input using 'echo -e'
            # 'echo -e' interprets backslash escapes like '\n'
            # We need to properly escape the input string to be safe within the shell command
            escaped_input = test_case.input.replace('\\', '\\\\').replace('"', '\\"') # Escape backslashes and double quotes
            # Ensure a newline is always at the end of the piped input
            full_shell_command = f"echo -e \"{escaped_input}\\n\" | {' '.join(config['executable'])}"

            # The docker command now runs 'bash -c' with the full shell command
            docker_command_with_input = base_docker_run_prefix + ["bash", "-c", full_shell_command]

            try:
                logging.info(f"Executing test case {test_case_number}: {' '.join(docker_command_with_input)}")
                process = subprocess.run(
                    docker_command_with_input,
                    capture_output=True,
                    text=True, # text=True is fine here because input is handled by echo -e, not subprocess's input arg
                    check=False,
                    timeout=celery_app.conf.task_time_limit
                )

                actual_output = process.stdout.strip()
                error_output = process.stderr.strip()

                if process.returncode != 0:
                    test_case_error = error_output if error_output else f"Runtime error: exited with code {process.returncode}"
                    overall_status = "runtime_error"
                    overall_passed_flag = False
                elif error_output:
                    test_case_error = error_output
                    overall_status = "runtime_error"
                    overall_passed_flag = False

                # Compare output, ignoring leading/trailing whitespace and ensuring consistent line endings
                if not test_case_error and actual_output.replace('\r\n', '\n') == test_case.expected_output.strip().replace('\r\n', '\n'):
                    passed = True
                else:
                    passed = False
                    overall_passed_flag = False

                all_test_results.append(
                    TestCaseResult(
                        test_case_number=test_case_number,
                        input=test_case.input, # Store original input without added newline
                        expected_output=test_case.expected_output,
                        actual_output=actual_output,
                        passed=passed,
                        error=test_case_error
                    )
                )

            except subprocess.TimeoutExpired:
                test_case_error = "Code execution timed out for this test case."
                all_test_results.append(
                    TestCaseResult(
                        test_case_number=test_case_number,
                        input=test_case.input,
                        expected_output=test_case.expected_output,
                        actual_output="",
                        passed=False,
                        error=test_case_error
                    )
                )
                overall_status = "timeout"
                overall_passed_flag = False
                logging.warning(f"Timeout on test case {test_case_number} for task {self.request.id}")
            except Exception as e:
                test_case_error = f"Worker error during test case {test_case_number}: {str(e)}"
                all_test_results.append(
                    TestCaseResult(
                        test_case_number=test_case_number,
                        input=test_case.input,
                        expected_output=test_case.expected_output,
                        actual_output="",
                        passed=False,
                        error=test_case_error
                    )
                )
                overall_status = "worker_error"
                overall_passed_flag = False
                logging.exception(f"Unhandled error on test case {test_case_number} for task {self.request.id}")

        execution_time = time.time() - start_time

        if overall_status == "success" and not overall_passed_flag:
            overall_status = "failure"

        return CodeExecutionResult(
            status=overall_status,
            overall_passed=overall_passed_flag,
            output="\n".join([r.actual_output for r in all_test_results]) if all_test_results else "",
            error=overall_error_message,
            execution_time=execution_time,
            test_results=all_test_results
        ).dict()

    except subprocess.CalledProcessError as e:
        overall_status = "compilation_error"
        overall_error_message = e.stderr or str(e)
        overall_passed_flag = False
        execution_time = time.time() - start_time
        logging.error(f"Docker process error for task {self.request.id}: {overall_error_message}")
        return CodeExecutionResult(
            status=overall_status,
            overall_passed=overall_passed_flag,
            output="",
            error=overall_error_message,
            execution_time=execution_time
        ).dict()
    except FileNotFoundError:
        overall_status = "worker_error"
        overall_error_message = "Docker or necessary command not found on worker."
        overall_passed_flag = False
        execution_time = time.time() - start_time
        self.retry(exc=FileNotFoundError("Docker CLI not found."), countdown=5)
        logging.error(f"Docker CLI not found for task {self.request.id}")
        return CodeExecutionResult(
            status=overall_status,
            overall_passed=overall_passed_flag,
            output="",
            error=overall_error_message,
            execution_time=execution_time
        ).dict()
    except Exception as e:
        overall_status = "worker_error"
        overall_error_message = f"An unhandled error occurred in worker: {str(e)}"
        overall_passed_flag = False
        execution_time = time.time() - start_time
        logging.exception(f"Unhandled exception in worker for task {self.request.id}")
        return CodeExecutionResult(
            status=overall_status,
            overall_passed=overall_passed_flag,
            output="",
            error=overall_error_message,
            execution_time=execution_time
        ).dict()
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            logging.info(f"Cleaned up temporary directory: {temp_dir}")

# This ensures the Celery app is available for the worker to discover tasks
if __name__ == '__main__':
    celery_app.worker_main(['worker', '-l', 'info', '--concurrency=1'])