# main.py
from fastapi import FastAPI, HTTPException, status # Added 'status' for HTTP status codes
from celery.result import AsyncResult
from pydantic import BaseModel
from typing import Optional, Dict, Any # <-- Crucial: Added these imports!

# Import your Celery app and task
from celery_worker import celery_app, run_code_in_docker

app = FastAPI(
    title="Code Runner Backend",
    description="Backend for running user-submitted code in a sandboxed environment.",
    version="0.1.0"
)

class CodeRequest(BaseModel): # Renamed from CodeSubmission for consistency with prior discussions
    language: str
    code: str

class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: Optional[Dict[str, Any]] = None # Uses Optional and Dict from typing
    info: Optional[str] = None # Uses Optional from typing

@app.get("/")
async def read_root():
    return {"message": "Welcome to the Code Runner API"}

@app.get("/health")
async def health_check():
    try:
        celery_app.control.ping() # Check connection to Redis broker/backend
        redis_status = "connected"
    except Exception as e:
        redis_status = f"error: {e}"
    return {"status": "ok", "redis": redis_status}

@app.post("/run", response_model=TaskStatusResponse) # Using TaskStatusResponse as return model
async def submit_code(request: CodeRequest): # Renamed 'submission' to 'request' for consistency
    print(f"Code submitted.")
    task = run_code_in_docker.delay(request.language, request.code)
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

    # Check if the task has completed (SUCCESS, FAILURE, REVOKED)
    if task_result.ready():
        try:
            # .get() will retrieve the result if SUCCESS, or re-raise the exception if FAILURE
            result_data = task_result.get()

            if task_result.status == "SUCCESS":
                # The result_data is the dictionary returned by run_code_in_docker
                response_data["result"] = result_data
            elif task_result.status == "FAILURE":
                # result_data will be the exception that was raised
                response_data["info"] = f"Task failed: {str(result_data)}"
                if task_result.traceback:
                    response_data["info"] += f"\nTraceback:\n{task_result.traceback}"
                # Ensure the 'result' field is a dictionary matching CodeExecutionResult structure
                # This ensures consistency for the front-end even on failure.
                response_data["result"] = {"status": "error", "output": "", "error": response_data["info"], "execution_time": None}
            else: # Handle other terminal states like REVOKED
                response_data["info"] = f"Task state: {task_result.status}. Details: {str(result_data)}"

        except Exception as e:
            # This handles cases where .get() itself raises an unexpected error
            response_data["status"] = "ERROR_RETRIEVING_RESULT"
            response_data["info"] = f"Error retrieving task result: {str(e)}"
            if task_result.traceback: # If original task had a traceback, include it
                 response_data["info"] += f"\nOriginal Task Traceback:\n{task_result.traceback}"

    # If the task is not yet ready, but is in a RETRY state, provide more info
    elif task_result.status == "RETRY":
        response_data["info"] = f"Task retrying due to: {str(task_result.info)}"
        # Add user-friendly messages for common retry reasons
        if "TimeoutExpired" in str(task_result.info):
            response_data["info"] = "Code execution timed out and is being retried. Please wait."
        elif "FileNotFoundError" in str(task_result.info):
            response_data["info"] = "Docker CLI not found or accessible on worker. Retrying."

    # For PENDING and STARTED, result and info will remain None, which is fine as they are not final states.

    return TaskStatusResponse(**response_data)

# This part is useful for running the app directly, but Uvicorn command is preferred
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)