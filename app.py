from flask import Flask, request, jsonify
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from azure.core.exceptions import ResourceExistsError
import os
import json
import time

app = Flask(__name__)

# Azure Storage Account details
connection_string = os.getenv('AZURE_STORAGE_CONNECTION_STRING_1')
container_name = "task-box-storage"

# Initialize BlobServiceClient
blob_service_client = BlobServiceClient.from_connection_string(connection_string)
container_client = blob_service_client.get_container_client(container_name)

# Ensure the container exists
try:
    container_client.create_container()
except ResourceExistsError:
    pass

@app.route('/check_blob', methods=['GET'])
def check_blob():
    """
    Checks if the user's blob exists.
    """
    username = request.args.get("username")
    if not username:
        return jsonify({"message": "Username is required"}), 400

    user_blob_name = f"{username}.json"
    blob_client = container_client.get_blob_client(user_blob_name)

    if blob_client.exists():
        return jsonify({"exists": True}), 200
    return jsonify({"exists": False}), 200

@app.route('/create_blob', methods=['POST'])
def create_blob():
    """
    Creates a blob for the user if it does not exist.
    """
    data = request.json
    username = data.get("username")
    if not username:
        return jsonify({"message": "Username is required"}), 400

    user_blob_name = f"{username}.json"
    blob_client = container_client.get_blob_client(user_blob_name)

    try:
        # Initialize the blob with an empty tasks list and stats
        initial_data = {
            "tasks": [],
            "stats": {
                "total_completed": 0,
                "created_at": int(time.time())
            }
        }
        blob_client.upload_blob(json.dumps(initial_data), overwrite=False)
        return jsonify({"message": "Blob created successfully"}), 201
    except ResourceExistsError:
        return jsonify({"message": "Blob already exists"}), 400

@app.route('/add_task', methods=['POST'])
def add_task():
    """
    Adds a new task for the user.
    """
    task_data = request.json
    username = task_data.get("username")
    if not username:
        return jsonify({"message": "Username is required"}), 400

    user_blob_name = f"{username}.json"
    blob_client = container_client.get_blob_client(user_blob_name)

    # Check if the user's blob exists
    try:
        user_data = json.loads(blob_client.download_blob().readall().decode('utf-8'))
        if "tasks" not in user_data:  # Handle older format
            user_data = {"tasks": user_data, "stats": {"total_completed": 0, "created_at": int(time.time())}}
    except Exception:
        # Blob does not exist, create a new one
        user_data = {"tasks": [], "stats": {"total_completed": 0, "created_at": int(time.time())}}

    # Generate a unique ID using timestamp to avoid ID conflicts
    task_id = int(time.time() * 1000)
    
    # Add new task
    new_task = {
        "id": task_id,
        "text": task_data["text"],
        "file_url": None,
        "completed": False,
        "created_at": int(time.time())
    }

    # Handle file upload (if exists)
    if 'file' in request.files:
        file = request.files['file']
        file_name = file.filename
        file_blob_client = container_client.get_blob_client(f"{username}/{task_id}/{file_name}")
        
        try:
            file_blob_client.upload_blob(file, overwrite=True)
            new_task['file_url'] = file_blob_client.url  # File URL from Azure Blob Storage
        except ResourceExistsError:
            return jsonify({"message": "File already exists"}), 400

    user_data["tasks"].append(new_task)
    
    # Update the user's blob with the new task
    blob_client.upload_blob(json.dumps(user_data), overwrite=True)
    
    return jsonify({
        "message": "Task added successfully", 
        "task": new_task,
        "stats": user_data["stats"]
    }), 201

@app.route('/list_tasks', methods=['GET'])
def list_tasks():
    """
    Lists all tasks for the user.
    """
    username = request.args.get("username")
    if not username:
        return jsonify({"message": "Username is required"}), 400

    user_blob_name = f"{username}.json"
    blob_client = container_client.get_blob_client(user_blob_name)
    
    try:
        user_data = json.loads(blob_client.download_blob().readall().decode('utf-8'))
        # Handle older format
        if not isinstance(user_data, dict) or "tasks" not in user_data:
            tasks = user_data
            stats = {"total_completed": sum(1 for t in tasks if t.get("completed", False)), "created_at": int(time.time())}
            user_data = {"tasks": tasks, "stats": stats}
            # Update blob with new format
            blob_client.upload_blob(json.dumps(user_data), overwrite=True)
            
        return jsonify({
            "tasks": user_data["tasks"],
            "stats": user_data["stats"]
        })
    except Exception as e:
        return jsonify({"message": f"No tasks found for this user: {str(e)}"}), 404

@app.route('/mark_task_completed', methods=['POST'])
def mark_task_completed():
    """
    Marks a task as completed for the user.
    """
    task_data = request.json
    username = task_data.get("username")
    task_id = task_data.get("task_id")

    if not username or task_id is None:
        return jsonify({"message": "Username and task ID are required"}), 400

    user_blob_name = f"{username}.json"
    blob_client = container_client.get_blob_client(user_blob_name)
    
    try:
        user_data = json.loads(blob_client.download_blob().readall().decode('utf-8'))
        
        # Handle older format
        if not isinstance(user_data, dict) or "tasks" not in user_data:
            tasks = user_data
            stats = {"total_completed": sum(1 for t in tasks if t.get("completed", False)), "created_at": int(time.time())}
            user_data = {"tasks": tasks, "stats": stats}
            
        task = next((t for t in user_data["tasks"] if t['id'] == task_id), None)
        if task:
            # Only increment counter if task wasn't already completed
            if not task['completed']:
                user_data["stats"]["total_completed"] += 1
                
            task['completed'] = True
            blob_client.upload_blob(json.dumps(user_data), overwrite=True)
            return jsonify({
                "message": "Task marked as completed", 
                "task": task,
                "stats": user_data["stats"]
            })
        else:
            return jsonify({"message": "Task not found"}), 404
    except Exception as e:
        return jsonify({"message": f"Error processing task: {str(e)}"}), 404

@app.route('/delete_task', methods=['DELETE'])
def delete_task():
    """
    Deletes a task for the user (for left swipe action).
    """
    username = request.args.get("username")
    task_id = request.args.get("task_id")
    
    if not username or not task_id:
        return jsonify({"message": "Username and task ID are required"}), 400
    
    try:
        task_id = int(task_id)
    except ValueError:
        return jsonify({"message": "Task ID must be a number"}), 400

    user_blob_name = f"{username}.json"
    blob_client = container_client.get_blob_client(user_blob_name)
    
    try:
        user_data = json.loads(blob_client.download_blob().readall().decode('utf-8'))
        
        # Handle older format
        if not isinstance(user_data, dict) or "tasks" not in user_data:
            tasks = user_data
            stats = {"total_completed": sum(1 for t in tasks if t.get("completed", False)), "created_at": int(time.time())}
            user_data = {"tasks": tasks, "stats": stats}
            
        # Find the task index
        task_index = next((i for i, t in enumerate(user_data["tasks"]) if t['id'] == task_id), None)
        
        if task_index is not None:
            # Get the task before removing it
            task = user_data["tasks"][task_index]
            
            # If the task was completed, decrement the counter
            if task.get('completed', False):
                user_data["stats"]["total_completed"] = max(0, user_data["stats"]["total_completed"] - 1)
                
            # Remove the task
            user_data["tasks"].pop(task_index)
            
            # Save updated data
            blob_client.upload_blob(json.dumps(user_data), overwrite=True)
            
            return jsonify({
                "message": "Task deleted successfully",
                "stats": user_data["stats"]
            })
        else:
            return jsonify({"message": "Task not found"}), 404
    except Exception as e:
        return jsonify({"message": f"Error deleting task: {str(e)}"}), 404

@app.route('/edit_task', methods=['PUT'])
def edit_task():
    """
    Edits an existing task for the user.
    """
    task_data = request.json
    username = task_data.get("username")
    task_id = task_data.get("task_id")
    new_text = task_data.get("text")

    if not username or task_id is None or new_text is None:
        return jsonify({"message": "Username, task ID, and task text are required"}), 400

    user_blob_name = f"{username}.json"
    blob_client = container_client.get_blob_client(user_blob_name)
    
    try:
        user_data = json.loads(blob_client.download_blob().readall().decode('utf-8'))
        
        # Handle older format
        if not isinstance(user_data, dict) or "tasks" not in user_data:
            tasks = user_data
            stats = {"total_completed": sum(1 for t in tasks if t.get("completed", False)), "created_at": int(time.time())}
            user_data = {"tasks": tasks, "stats": stats}
            
        task = next((t for t in user_data["tasks"] if t['id'] == task_id), None)
        
        if task:
            # Update the task text
            task['text'] = new_text
            # Add last_edited timestamp
            task['last_edited'] = int(time.time())
            
            # Save updated data
            blob_client.upload_blob(json.dumps(user_data), overwrite=True)
            
            return jsonify({
                "message": "Task updated successfully",
                "task": task,
                "stats": user_data["stats"]
            })
        else:
            return jsonify({"message": "Task not found"}), 404
    except Exception as e:
        return jsonify({"message": f"Error updating task: {str(e)}"}), 404

@app.route('/get_stats', methods=['GET'])
def get_stats():
    """
    Get task statistics for the user.
    """
    username = request.args.get("username")
    if not username:
        return jsonify({"message": "Username is required"}), 400

    user_blob_name = f"{username}.json"
    blob_client = container_client.get_blob_client(user_blob_name)
    
    try:
        user_data = json.loads(blob_client.download_blob().readall().decode('utf-8'))
        
        # Handle older format
        if not isinstance(user_data, dict) or "tasks" not in user_data:
            tasks = user_data
            active_tasks = sum(1 for t in tasks if not t.get("completed", False))
            completed_tasks = sum(1 for t in tasks if t.get("completed", False))
            stats = {
                "total_completed": completed_tasks,
                "active_tasks": active_tasks,
                "created_at": int(time.time())
            }
        else:
            active_tasks = sum(1 for t in user_data["tasks"] if not t.get("completed", False))
            completed_tasks = sum(1 for t in user_data["tasks"] if t.get("completed", False))
            stats = user_data.get("stats", {})
            stats["active_tasks"] = active_tasks
            
            # Ensure the stats are up to date
            if stats.get("total_completed", 0) != completed_tasks:
                stats["total_completed"] = completed_tasks
                user_data["stats"] = stats
                blob_client.upload_blob(json.dumps(user_data), overwrite=True)
        
        return jsonify({"stats": stats})
    except Exception as e:
        return jsonify({"message": f"Error retrieving stats: {str(e)}"}), 404

if __name__ == '__main__':
    app.run(debug=True, threaded=True)
