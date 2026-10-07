import time
import datetime
import secrets
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from typing import Optional
from zk_tool import Managed, op_set_time, op_test_voice

app = FastAPI(title="ZKTeco Testing API")
security = HTTPBasic()

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    # Simple hardcoded check for admin. You can change the password 'admin' to something else.
    is_admin = secrets.compare_digest(credentials.username, "admin")
    is_pass = secrets.compare_digest(credentials.password, "admin")
    if not (is_admin and is_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


class TimeTestRequest(BaseModel):
    use_today: bool
    target_date: Optional[str] = None
    target_time: str
    wait_time_sec: int

class VoiceTestRequest(BaseModel):
    index: int


def perform_time_test(target_dt: datetime.datetime, wait_time_sec: int):
    print(f"\n[Background Task] Connecting to device...")
    try:
        with Managed() as conn:
            original_device_time = conn.get_time()
            print(f"[Background Task] Original device time before change: {original_device_time}")
            print(f"[Background Task] Setting device time to: {target_dt}")
            op_set_time(conn, target_dt)
            print("[Background Task] Time set successfully on the machine.")
    except Exception as e:
        print(f"[Background Task] Error: Failed to connect or set time: {e}")
        return

    print(f"[Background Task] Waiting for {wait_time_sec} seconds to let you test...")
    time.sleep(wait_time_sec)
    
    restore_time = original_device_time + datetime.timedelta(seconds=wait_time_sec)
    
    print(f"[Background Task] Restoring device time to: {restore_time}")
    try:
        with Managed() as conn:
            op_set_time(conn, restore_time)
            print("[Background Task] Machine time restored successfully.")
    except Exception as e:
        print(f"[Background Task] Error: Failed to restore time: {e}")


@app.post("/test-time")
def run_time_test(req: TimeTestRequest, background_tasks: BackgroundTasks, username: str = Depends(verify_admin)):
    if req.use_today:
        target_date_str = datetime.date.today().strftime("%Y-%m-%d")
    else:
        if not req.target_date:
            raise HTTPException(status_code=400, detail="target_date is required when use_today is false")
        target_date_str = req.target_date
        
    try:
        target_dt = datetime.datetime.strptime(f"{target_date_str} {req.target_time}", "%Y-%m-%d %H:%M")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date or time format. Details: {e}")

    background_tasks.add_task(perform_time_test, target_dt, req.wait_time_sec)

    return {
        "status": "success",
        "message": f"Time test initiated. Setting time to {target_dt}, waiting {req.wait_time_sec}s, then restoring."
    }

@app.post("/test-voice")
def run_voice_test(req: VoiceTestRequest, username: str = Depends(verify_admin)):
    try:
        with Managed() as conn:
            result = op_test_voice(conn, req.index)
            return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to play voice: {e}")

@app.get("/", response_class=HTMLResponse)
def get_test_page(username: str = Depends(verify_admin)):
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>ZKTeco Device Tester</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg-grad: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%);
                --glass-bg: rgba(30, 41, 59, 0.7);
                --glass-border: rgba(255, 255, 255, 0.1);
                --primary: #8b5cf6;
                --primary-hover: #7c3aed;
                --text: #f8fafc;
                --text-muted: #94a3b8;
            }
            body {
                background: var(--bg-grad);
                color: var(--text);
                font-family: 'Outfit', sans-serif;
                margin: 0;
                padding: 40px 20px;
                display: flex;
                flex-direction: column;
                align-items: center;
                min-height: 100vh;
                box-sizing: border-box;
            }
            h2 { margin-top: 0; margin-bottom: 25px; font-weight: 600; color: #fff; text-shadow: 0 2px 4px rgba(0,0,0,0.5); }
            
            .container {
                background: var(--glass-bg);
                backdrop-filter: blur(16px);
                -webkit-backdrop-filter: blur(16px);
                padding: 35px;
                border-radius: 20px;
                box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5);
                max-width: 450px;
                width: 100%;
                border: 1px solid var(--glass-border);
                margin-bottom: 30px;
                transition: transform 0.3s ease;
            }
            .container:hover { transform: translateY(-5px); }

            .form-group { margin-bottom: 20px; }
            label { display: block; margin-bottom: 8px; color: var(--text-muted); font-size: 14px; font-weight: 400;}
            
            input[type="text"], input[type="number"], input[type="date"], input[type="time"] {
                width: 100%; padding: 12px 15px; border-radius: 10px;
                border: 1px solid var(--glass-border); background: rgba(0,0,0,0.2); color: var(--text);
                box-sizing: border-box; font-family: inherit; font-size: 15px;
                transition: border-color 0.3s, box-shadow 0.3s;
            }
            input:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.3); }

            /* Custom Checkbox */
            .checkbox-group { display: flex; align-items: center; gap: 12px; cursor: pointer; }
            input[type="checkbox"] { width: 20px; height: 20px; accent-color: var(--primary); cursor: pointer; }
            
            button {
                background: linear-gradient(135deg, var(--primary), var(--primary-hover));
                color: white; border: none; padding: 14px 20px; border-radius: 10px; cursor: pointer;
                font-weight: 600; width: 100%; font-size: 16px; font-family: inherit;
                transition: transform 0.2s, box-shadow 0.2s;
                box-shadow: 0 4px 15px rgba(139, 92, 246, 0.4);
            }
            button:hover { transform: translateY(-2px); box-shadow: 0 6px 20px rgba(139, 92, 246, 0.6); }
            button:active { transform: translateY(1px); }

            /* Voice Grid */
            .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; }
            .grid button { 
                background: rgba(255,255,255,0.05); border: 1px solid var(--glass-border);
                box-shadow: none; font-size: 18px; padding: 20px 0; border-radius: 14px;
            }
            .grid button:hover { 
                background: rgba(255,255,255,0.1); border-color: var(--primary);
                box-shadow: 0 0 15px rgba(139, 92, 246, 0.3);
            }
            
            /* Message Toast */
            #message {
                position: fixed; bottom: 30px; padding: 15px 25px; border-radius: 50px;
                font-weight: 400; display: none; animation: slideUp 0.3s ease-out; z-index: 1000;
                backdrop-filter: blur(10px); box-shadow: 0 10px 25px rgba(0,0,0,0.3);
            }
            .success { background: rgba(34, 197, 94, 0.15); color: #4ade80; border: 1px solid #22c55e; }
            .error { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid #ef4444; }

            @keyframes slideUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>⏱️ Time Test Setup</h2>
            <div class="form-group checkbox-group" onclick="document.getElementById('useToday').click()">
                <input type="checkbox" id="useToday" checked onchange="toggleDate(event)">
                <label style="margin:0; cursor:pointer; color: #fff;">Use today's date</label>
            </div>
            <div class="form-group" id="dateGroup" style="display:none; animation: slideUp 0.3s;">
                <label>Target Date</label>
                <input type="date" id="targetDate">
            </div>
            <div class="form-group">
                <label>Target Time</label>
                <input type="time" id="targetTime" required>
            </div>
            <div class="form-group">
                <label>Wait Time (seconds)</label>
                <input type="number" id="waitTime" value="30" required>
            </div>
            <button onclick="submitTimeTest()">Initialize Time Test</button>
        </div>

        <div class="container">
            <h2>🔊 Voice Prompt Test</h2>
            <p style="color:var(--text-muted); font-size:14px; margin-top:-15px; margin-bottom: 25px;">
                Test the ZKTeco device speaker by triggering internal voice prompts.
            </p>
            <div class="grid">
                <button onclick="testVoice(0)">0</button>
                <button onclick="testVoice(1)">1</button>
                <button onclick="testVoice(2)">2</button>
                <button onclick="testVoice(3)">3</button>
                <button onclick="testVoice(4)">4</button>
                <button onclick="testVoice(5)">5</button>
                <button onclick="testVoice(6)">6</button>
                <button onclick="testVoice(7)">7</button>
                <button onclick="testVoice(8)">8</button>
            </div>
        </div>

        <div id="message"></div>

        <script>
            function toggleDate(e) {
                if (e) e.stopPropagation();
                document.getElementById('dateGroup').style.display = 
                    document.getElementById('useToday').checked ? 'none' : 'block';
            }
            
            function showMsg(msg, isError) {
                const el = document.getElementById('message');
                el.textContent = msg;
                el.className = isError ? 'error' : 'success';
                el.style.display = 'block';
                setTimeout(() => el.style.display = 'none', 4000);
            }

            async function submitTimeTest() {
                const useToday = document.getElementById('useToday').checked;
                const targetDate = document.getElementById('targetDate').value;
                const targetTime = document.getElementById('targetTime').value;
                const waitTime = document.getElementById('waitTime').value;

                if (!useToday && !targetDate) return showMsg("Please select a date", true);
                if (!targetTime) return showMsg("Please select a time", true);

                try {
                    const res = await fetch('/test-time', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            use_today: useToday,
                            target_date: targetDate || null,
                            target_time: targetTime,
                            wait_time_sec: parseInt(waitTime)
                        })
                    });
                    const data = await res.json();
                    if(res.ok) showMsg(data.message, false);
                    else showMsg(data.detail, true);
                } catch(e) {
                    showMsg("Network error connecting to API", true);
                }
            }

            async function testVoice(index) {
                try {
                    const res = await fetch('/test-voice', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ index })
                    });
                    const data = await res.json();
                    if(res.ok) showMsg("🔊 Playing voice " + index, false);
                    else showMsg(data.detail || "Device Error", true);
                } catch(e) {
                    showMsg("Network error connecting to API", true);
                }
            }
        </script>
    </body>
    </html>
    """
    return html_content


if __name__ == '__main__':
    import uvicorn
    # Run the server on port 8001 to avoid conflicting with zk_tool.py serve (which uses 8000)
    uvicorn.run("test_time_api:app", host="0.0.0.0", port=8001, reload=True)
