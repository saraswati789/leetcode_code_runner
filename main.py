# main.py
from fastapi import FastAPI, HTTPException, status
from celery.result import AsyncResult
from pydantic import BaseModel
from typing import Optional, Dict, Any, List # <-- Added List here

# Import your Celery app and task
from celery_worker import celery_app, run_code_in_docker

app = FastAPI(
    title="Code Runner Backend",
    description="Backend for running user-submitted code in a sandboxed environment.",
    version="0.1.0"
)

# --- NEW: Pydantic models for Test Cases ---
class TestCase(BaseModel):
    input: str
    expected_output: str

class TestCaseResult(BaseModel):
    test_case_number: int
    input: str
    expected_output: str
    actual_output: str
    passed: bool
    error: Optional[str] = None # For compilation/runtime errors specific to this test case

class CodeExecutionResult(BaseModel):
    status: str # "success", "compilation_error", "runtime_error", "timeout", "worker_error"
    overall_passed: Optional[bool] = None # True if all test cases passed
    output: Optional[str] = None # Overall output (e.g., for simple prints without test cases) or aggregated errors
    error: Optional[str] = None # Overall error (e.g., compilation error, general worker error)
    execution_time: Optional[float] = None
    test_results: Optional[List[TestCaseResult]] = None # List of results for each test case


# --- Updated CodeRequest ---
class CodeRequest(BaseModel):
    language: str
    code: str
    # NEW: Add test cases
    test_cases: Optional[List[TestCase]] = None # Make it optional for now, can be required later


# --- Updated TaskStatusResponse ---
class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    # Change result to match the new CodeExecutionResult structure
    result: Optional[CodeExecutionResult] = None # Using the new Pydantic model
    info: Optional[str] = None

@app.get("/")
async def read_root():
    return {"message": "Welcome to the Code Runner API"}

@app.get("/health")
async def health_check():
    try:
        celery_app.control.ping()
        redis_status = "connected"
    except Exception as e:
        redis_status = f"error: {e}"
    return {"status": "ok", "redis": redis_status}

@app.post("/run", response_model=TaskStatusResponse) # Using TaskStatusResponse as return model
async def submit_code(request: CodeRequest): # Renamed 'submission' to 'request' for consistency
    print(f"Code submitted for language: {request.language}, with {len(request.test_cases) if request.test_cases else 0} test cases.")
    # Pass the test_cases to the Celery task AFTER converting them to dictionaries
    task = run_code_in_docker.delay(
        request.language,
        request.code,
        [tc.dict() for tc in request.test_cases] if request.test_cases else None
    )
    # Return PENDING status immediately for new tasks
    return {"task_id": task.id, "status": "PENDING", "message": "Code submission received. Processing in background."}

@app.get("/status/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    print(f"Requested status for task '{task_id}'.")
    task_result = AsyncResult(task_id, app=celery_app)

    response_data = {
        "task_id": task_id,
        "status": task_result.status,
        "result": None,
        "info": None
    }

    if task_result.ready():
        try:
            # .get() will retrieve the CodeExecutionResult dictionary from the worker
            result_data = task_result.get()

            if task_result.status == "SUCCESS":
                # Validate the returned result_data against the CodeExecutionResult model
                response_data["result"] = CodeExecutionResult(**result_data)
            elif task_result.status == "FAILURE":
                response_data["info"] = f"Task failed: {str(result_data)}"
                if task_result.traceback:
                    response_data["info"] += f"\nTraceback:\n{task_result.traceback}"
                # If a task fails, its result might not strictly conform to CodeExecutionResult,
                # but we'll try to represent it for consistency.
                response_data["result"] = CodeExecutionResult(
                    status="worker_error", # Or "runtime_error", etc. based on actual failure cause
                    output="",
                    error=response_data["info"],
                    overall_passed=False,
                    execution_time=None,
                    test_results=[]
                )
            else: # Handle other terminal states like REVOKED
                response_data["info"] = f"Task state: {task_result.status}. Details: {str(result_data)}"

        except Exception as e:
            response_data["status"] = "ERROR_RETRIEVING_RESULT"
            response_data["info"] = f"Error retrieving task result: {str(e)}"
            if task_result.traceback:
                 response_data["info"] += f"\nOriginal Task Traceback:\n{task_result.traceback}"

    elif task_result.status == "RETRY":
        response_data["info"] = f"Task retrying due to: {str(task_result.info)}"
        if "TimeoutExpired" in str(task_result.info):
            response_data["info"] = "Code execution timed out and is being retried. Please wait."
        elif "FileNotFoundError" in str(task_result.info):
            response_data["info"] = "Docker CLI not found or accessible on worker. Retrying."

    return TaskStatusResponse(**response_data)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)