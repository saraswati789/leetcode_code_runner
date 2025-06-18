# main.py
from fastapi import FastAPI, HTTPException, BackgroundTasks # BackgroundTasks is for future WebSockets, not strictly needed for basic polling
from pydantic import BaseModel, Field
import uvicorn
# import docker # No longer needed directly in main.py for run_code logic
# import os, tempfile, shutil, time # No longer needed directly in main.py

# Import your Celery app and task
from celery_worker import celery_app, run_code_in_docker

# Define Pydantic models for request and response
class CodeSubmission(BaseModel):
    language: str = Field(..., description="Programming language (e.g., 'python')")
    code: str = Field(..., description="The user's code to be executed")

# Response for when a task is submitted (contains task ID)
class CodeSubmissionResponse(BaseModel):
    task_id: str = Field(..., description="The ID of the Celery task for polling status")
    message: str = "Code submission received. Processing in background."

# Response for polling task status (from Celery result backend)
class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: dict | None = None # Use dict to hold CodeExecutionResult, or define it here too

# Initialize the FastAPI app
app = FastAPI(
    title="Code Runner Backend",
    description="Backend for running user-submitted code in a sandboxed environment.",
    version="0.1.0"
)

# --- Existing Endpoints ---
@app.get("/")
async def read_root():
    return {"message": "Welcome to the Code Runner Backend! API is running."}

@app.get("/health")
async def health_check():
    # You could try to ping Redis here for a more comprehensive health check
    # For now, we'll just check if the app is responsive
    return {"status": "ok", "message": "FastAPI app is running."}

# --- Modified Code Submission Endpoint ---
@app.post("/run", response_model=CodeSubmissionResponse)
async def submit_code(submission: CodeSubmission):
    # Enqueue the task to Celery
    task = run_code_in_docker.delay(submission.language, submission.code)
    return CodeSubmissionResponse(task_id=task.id)

# --- New Endpoint to Check Task Status ---
@app.get("/status/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    task = celery_app.AsyncResult(task_id)

    if task.state == 'PENDING':
        response = {"task_id": task_id, "status": "pending"}
    elif task.state == 'STARTED':
        response = {"task_id": task_id, "status": "started", "message": "Execution has begun."}
    elif task.state == 'PROGRESS':
        response = {"task_id": task_id, "status": "in_progress", "message": task.info.get('message', 'Processing...') if task.info else 'Processing...'}
    elif task.state == 'SUCCESS':
        response = {"task_id": task_id, "status": "success", "result": task.result}
    elif task.state == 'FAILURE':
        response = {
            "task_id": task_id,
            "status": "failure",
            "result": {
                "status": "worker_error",
                "output": "",
                "error": str(task.info), # Task info will contain the exception details
                "execution_time": None
            }
        }
    else:
        # PENDING, RECEIVED, STARTED, SUCCESS, FAILURE, RETRY, REVOKED
        response = {"task_id": task_id, "status": task.state.lower()}

    return TaskStatusResponse(**response)

# This part is useful for running the app directly, but Uvicorn command is preferred
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)