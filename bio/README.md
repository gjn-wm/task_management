# ZKTeco Device Toolkit (`zk_tool.py`)

A single-file Python toolkit for interacting with ZKTeco fingerprint attendance machines. It serves as a wrapper around `pyzk` and can be used in three distinct ways:

1. **Interactive CLI Menu**: A user-friendly menu to execute commands manually.
2. **Scriptable CLI**: Direct command-line arguments for automation and scripting.
3. **API Server**: A FastAPI-based HTTP server for HRMS integration.

## Requirements

Install the necessary dependencies using pip:

```bash
pip install pyzk fastapi uvicorn
```

## Configuration

Connection settings resolve in the following order: **CLI flags** > **Environment variables** > **Defaults**.

Available environment variables:
- `ZK_HOST` (default: `192.168.29.150`)
- `ZK_PORT` (default: `4370`)
- `ZK_PASSWORD` (default: `0`)
- `ZK_TIMEOUT` (default: `10`)

---

## 1. Interactive Menu Mode

Run the script without any arguments to launch the interactive menu:

```bash
python zk_tool.py
```
This will display a list of all available operations, allowing you to simply type a number to choose the action you want.

---

## 2. Scriptable CLI Mode

You can run specific commands directly from your terminal. You can also override connection parameters per command using `--host`, `--port`, `--password`, and `--timeout`.

### Examples for all Scenarios:

**Get Device Information**
```bash
python zk_tool.py device-info
```

**Fetch Attendance Records**
```bash
# Get all records
python zk_tool.py attendance

# Get records for the last 3 days
python zk_tool.py attendance --days 3

# Get records for a specific date range
python zk_tool.py attendance --start 2023-10-01 --end 2023-10-15
```

**Manage Users**
```bash
# List all enrolled users
python zk_tool.py users

# Add or update a user (uid and name are required)
python zk_tool.py add-user --uid 1 --name "John Doe" --privilege 0 --user-id "EMP001"

# Delete a user by UID
python zk_tool.py delete-user --uid 1
```

**Manage Fingerprint Templates**
```bash
# Dump fingerprint templates (base64 encoded)
python zk_tool.py templates
```

**Backup and Restore Data**
```bash
# Backup users and templates to a JSON file
python zk_tool.py backup --file backup.json

# Restore users and templates from a JSON file (requires interactive confirmation)
# Add --yes (if script allows bypass) or expect prompt
python zk_tool.py restore --file backup.json
```

**Device Operations**
```bash
# Sync device clock to the current system time
python zk_tool.py set-time

# Wipe attendance log only (use --yes to skip confirmation)
python zk_tool.py clear-attendance --yes

# DANGER: Wipe users, fingerprints, and attendance (use --yes to skip confirmation)
python zk_tool.py clear-all --yes

# Restart the device
python zk_tool.py restart --yes

# Power off the device
python zk_tool.py poweroff --yes
```

**Live Punch Capture**
```bash
# Stream punches as they happen (Ctrl+C to stop)
python zk_tool.py live-capture

# Stream punches for 60 seconds then stop
python zk_tool.py live-capture --seconds 60
```

---

## 3. API Server Mode (HRMS Integration)

Start a FastAPI server to interact with the device via HTTP requests. This is ideal for integrating the attendance machine into an HRMS or custom web application.

```bash
python zk_tool.py serve --bind 0.0.0.0 --api-port 8000
```

Once running, interactive API documentation is available at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

### Example API Usage

*Note: You can override the target device per-request by adding `?zk_host=192.168.X.X&zk_port=4370` to any URL.*

**Check Server Health:**
```bash
curl http://localhost:8000/health
```

**Get Device Info:**
```bash
curl http://localhost:8000/device/info
```

**Get Attendance (last 3 days):**
```bash
curl "http://localhost:8000/attendance?days=3"
```

**Add/Update a User (POST):**
```bash
curl -X POST http://localhost:8000/users \
  -H "Content-Type: application/json" \
  -d '{"uid": 2, "name": "Jane Smith", "privilege": 0}'
```

**Clear Attendance Data (Requires Confirmation):**
```bash
curl -X POST http://localhost:8000/attendance/clear \
  -H "Content-Type: application/json" \
  -d '{"confirm": true}'
```

**Stream Live Punches (Server-Sent Events style stream for 60 seconds):**
```bash
curl http://localhost:8000/attendance/stream?seconds=60
```
