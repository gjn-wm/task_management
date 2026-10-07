#!/usr/bin/env python3
"""
zk_tool.py — ZKTeco fingerprint attendance machine toolkit (pyzk wrapper)

Single-file tool that works two ways:

  1) CLI              python zk_tool.py                     -> interactive menu
                       python zk_tool.py attendance --days 3 -> scriptable subcommand
  2) API server        python zk_tool.py serve               -> FastAPI on :8000 for HRMS integration

Requires:
    pip install pyzk fastapi uvicorn

Connection settings resolve in this order: CLI flags > environment vars > defaults.
    ZK_HOST      (default 192.168.29.150)
    ZK_PORT      (default 4370)
    ZK_PASSWORD  (default 0)
    ZK_TIMEOUT   (default 10)
"""

import os
import sys
import json
import base64
import argparse
from datetime import datetime, timedelta

try:
    from zk import ZK, const
except ImportError:
    ZK = None
    const = None


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

DEFAULTS = {
    "host": os.environ.get("ZK_HOST", "192.168.29.150"),
    "port": int(os.environ.get("ZK_PORT", 4370)),
    "password": int(os.environ.get("ZK_PASSWORD", 0)),
    "timeout": int(os.environ.get("ZK_TIMEOUT", 10)),
}


def require_pyzk():
    if ZK is None:
        die("pyzk is not installed. Run: pip install pyzk")


def die(msg, code=1):
    print(json.dumps({"error": msg}))
    sys.exit(code)


def get_conn(host=None, port=None, password=None, timeout=None,
             force_udp=False, ommit_ping=False):
    require_pyzk()
    zk = ZK(
        host or DEFAULTS["host"],
        port=port or DEFAULTS["port"],
        timeout=timeout or DEFAULTS["timeout"],
        password=password if password is not None else DEFAULTS["password"],
        force_udp=force_udp,
        ommit_ping=ommit_ping,
    )
    return zk.connect()


class Managed:
    """Context manager: connect, optionally disable device, always disconnect."""

    def __init__(self, disable=False, **conn_kwargs):
        self.disable = disable
        self.conn_kwargs = conn_kwargs
        self.conn = None

    def __enter__(self):
        self.conn = get_conn(**self.conn_kwargs)
        if self.disable:
            self.conn.disable_device()
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            try:
                if self.disable:
                    self.conn.enable_device()
            finally:
                self.conn.disconnect()


# --------------------------------------------------------------------------
# Core operations — each returns plain JSON-serializable data
# --------------------------------------------------------------------------

def op_device_info(conn):
    return {
        "firmware_version": conn.get_firmware_version(),
        "serial_number": conn.get_serialnumber(),
        "mac": conn.get_mac() if hasattr(conn, "get_mac") else None,
        "device_name": conn.get_device_name() if hasattr(conn, "get_device_name") else None,
        "platform": conn.get_platform() if hasattr(conn, "get_platform") else None,
        "pin_width": conn.get_pin_width() if hasattr(conn, "get_pin_width") else None,
        "face_version": conn.get_face_version() if hasattr(conn, "get_face_version") else None,
        "fp_version": conn.get_fp_version() if hasattr(conn, "get_fp_version") else None,
        "extra_fp_version": conn.get_extra_fp_version() if hasattr(conn, "get_extra_fp_version") else None,
        "network_params": conn.get_network_params() if hasattr(conn, "get_network_params") else None,
        "device_time": str(conn.get_time()),
        "sizes": conn.read_sizes().__dict__ if hasattr(conn.read_sizes(), "__dict__") else str(conn.read_sizes()),
    }


def op_get_attendance(conn, days=None, start_date=None, end_date=None):
    records = conn.get_attendance()

    if start_date or end_date or days is not None:
        today = datetime.now().date()
        if days is not None:
            start = today - timedelta(days=days)
            end = today
        else:
            start = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else today
            end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else today
        records = [r for r in records if start <= r.timestamp.date() <= end]

    return [
        {
            "uid": r.uid,
            "user_id": r.user_id,
            "timestamp": str(r.timestamp),
            "status": r.status,
            "punch": r.punch,
        }
        for r in records
    ]


def op_get_users(conn):
    return [
        {
            "uid": u.uid,
            "name": u.name,
            "privilege": u.privilege,
            "password": u.password,
            "group_id": u.group_id,
            "user_id": u.user_id,
            "card": u.card,
        }
        for u in conn.get_users()
    ]


def op_set_user(conn, uid, name, privilege=0, password="", group_id="", user_id="", card=0):
    conn.set_user(
        uid=uid, name=name, privilege=privilege,
        password=password, group_id=group_id, user_id=user_id, card=card,
    )
    return {"ok": True, "uid": uid, "name": name}


def op_delete_user(conn, uid):
    conn.delete_user(uid=uid)
    return {"ok": True, "deleted_uid": uid}


def op_get_templates(conn):
    templates = conn.get_templates()
    return [
        {
            "uid": t.uid,
            "fid": t.fid,
            "valid": t.valid,
            "template_b64": base64.b64encode(t.template).decode("ascii"),
            "size": t.size,
        }
        for t in templates
    ]


def op_get_user_template(conn, uid, temp_id):
    t = conn.get_user_template(uid=uid, temp_id=temp_id)
    if t is None:
        return None
    return {
        "uid": t.uid,
        "fid": t.fid,
        "valid": t.valid,
        "template_b64": base64.b64encode(t.template).decode("ascii"),
        "size": t.size,
    }


def op_backup(conn, filepath):
    payload = {
        "exported_at": str(datetime.now()),
        "device": op_device_info(conn),
        "users": op_get_users(conn),
        "templates": op_get_templates(conn),
    }
    with open(filepath, "w") as f:
        json.dump(payload, f, indent=2)
    return {"ok": True, "file": filepath, "users": len(payload["users"]), "templates": len(payload["templates"])}


def op_restore(conn, filepath):
    require_pyzk()
    from zk.finger import Finger
    from zk.user import User

    with open(filepath) as f:
        payload = json.load(f)

    restored_users, restored_templates, errors = 0, 0, []

    for u in payload.get("users", []):
        try:
            conn.set_user(
                uid=u["uid"], name=u["name"], privilege=u["privilege"],
                password=u.get("password", ""), group_id=u.get("group_id", ""),
                user_id=u.get("user_id", ""), card=u.get("card", 0),
            )
            restored_users += 1
        except Exception as e:
            errors.append(f"user uid={u.get('uid')}: {e}")

    # Group templates by uid and restore per-user (device/firmware dependent — best effort)
    by_uid = {}
    for t in payload.get("templates", []):
        by_uid.setdefault(t["uid"], []).append(t)

    for uid, temps in by_uid.items():
        try:
            fingers = [
                Finger(uid=t["uid"], fid=t["fid"], valid=t["valid"],
                       template=base64.b64decode(t["template_b64"]))
                for t in temps
            ]
            user = next((u for u in payload["users"] if u["uid"] == uid), None)
            user_obj = User(
                uid=uid, name=user["name"] if user else "", privilege=user["privilege"] if user else 0,
                password=user.get("password", "") if user else "",
                group_id=user.get("group_id", "") if user else "",
                user_id=user.get("user_id", "") if user else "", card=user.get("card", 0) if user else 0,
            )
            conn.save_user_template(user_obj, fingers)
            restored_templates += len(fingers)
        except Exception as e:
            errors.append(f"templates uid={uid}: {e}")

    return {
        "ok": len(errors) == 0,
        "restored_users": restored_users,
        "restored_templates": restored_templates,
        "errors": errors,
    }


def op_set_time(conn, dt=None):
    conn.set_time(dt or datetime.now())
    return {"ok": True, "device_time": str(conn.get_time())}


def op_clear_attendance(conn):
    conn.clear_attendance()
    return {"ok": True, "message": "Attendance log cleared"}


def op_clear_data(conn):
    conn.clear_data()
    return {"ok": True, "message": "ALL data (users, fingerprints, attendance) cleared"}


def op_restart(conn):
    conn.restart()
    return {"ok": True, "message": "Restart command sent"}


def op_poweroff(conn):
    conn.poweroff()
    return {"ok": True, "message": "Poweroff command sent"}


def op_test_voice(conn, index=0):
    conn.test_voice(index=index)
    return {"ok": True, "message": f"Voice test {index} played"}


# --------------------------------------------------------------------------
# CLI — argparse subcommands (scriptable) + interactive menu (bare run)
# --------------------------------------------------------------------------

def add_conn_args(p):
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--password", type=int, default=None)
    p.add_argument("--timeout", type=int, default=None)


def build_parser():
    parser = argparse.ArgumentParser(prog="zk_tool", description="ZKTeco device toolkit (CLI + API server)")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("device-info", help="Show device/firmware/capacity info")
    add_conn_args(p)

    p = sub.add_parser("attendance", help="Fetch attendance records")
    add_conn_args(p)
    p.add_argument("--days", type=int, default=None, help="Last N days (default: all records)")
    p.add_argument("--start", default=None, help="YYYY-MM-DD")
    p.add_argument("--end", default=None, help="YYYY-MM-DD")

    p = sub.add_parser("users", help="List enrolled users")
    add_conn_args(p)

    p = sub.add_parser("add-user", help="Add/update a user")
    add_conn_args(p)
    p.add_argument("--uid", type=int, required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--privilege", type=int, default=0)
    p.add_argument("--user-password", dest="user_password", default="")
    p.add_argument("--group-id", default="")
    p.add_argument("--user-id", default="")
    p.add_argument("--card", type=int, default=0)

    p = sub.add_parser("delete-user", help="Delete a user by uid")
    add_conn_args(p)
    p.add_argument("--uid", type=int, required=True)

    p = sub.add_parser("templates", help="Dump fingerprint templates (base64)")
    add_conn_args(p)

    p = sub.add_parser("backup", help="Backup users + templates to a JSON file")
    add_conn_args(p)
    p.add_argument("--file", required=True)

    p = sub.add_parser("restore", help="Restore users + templates from a JSON file")
    add_conn_args(p)
    p.add_argument("--file", required=True)

    p = sub.add_parser("set-time", help="Sync device clock to now")
    add_conn_args(p)

    p = sub.add_parser("clear-attendance", help="Wipe attendance log only")
    add_conn_args(p)
    p.add_argument("--yes", action="store_true", help="Skip confirmation")

    p = sub.add_parser("clear-all", help="DANGER: wipe users, fingerprints, attendance")
    add_conn_args(p)
    p.add_argument("--yes", action="store_true", help="Skip confirmation")

    p = sub.add_parser("restart", help="Restart the device")
    add_conn_args(p)
    p.add_argument("--yes", action="store_true")

    p = sub.add_parser("poweroff", help="Power off the device")
    add_conn_args(p)
    p.add_argument("--yes", action="store_true")

    p = sub.add_parser("live-capture", help="Stream punches as they happen (Ctrl+C to stop)")
    add_conn_args(p)
    p.add_argument("--seconds", type=int, default=None, help="Stop after N seconds (default: run until Ctrl+C)")

    p = sub.add_parser("test-voice", help="Play a voice prompt on the device by index")
    add_conn_args(p)
    p.add_argument("--index", type=int, default=0, help="Voice index (0-13)")

    p = sub.add_parser("serve", help="Start FastAPI server for HRMS integration")
    add_conn_args(p)
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--api-port", type=int, default=8000)

    return parser


def confirm(prompt):
    return input(f"{prompt} Type 'yes' to continue: ").strip().lower() == "yes"


def run_cli_command(args):
    conn_kwargs = dict(host=args.host, port=args.port, password=args.password, timeout=args.timeout)

    if args.command == "device-info":
        with Managed(**conn_kwargs) as conn:
            print(json.dumps(op_device_info(conn), indent=2))

    elif args.command == "attendance":
        with Managed(**conn_kwargs) as conn:
            print(json.dumps(op_get_attendance(conn, days=args.days, start_date=args.start, end_date=args.end)))

    elif args.command == "users":
        with Managed(**conn_kwargs) as conn:
            print(json.dumps(op_get_users(conn), indent=2))

    elif args.command == "add-user":
        with Managed(disable=True, **conn_kwargs) as conn:
            print(json.dumps(op_set_user(
                conn, args.uid, args.name, args.privilege, args.user_password,
                args.group_id, args.user_id, args.card,
            )))

    elif args.command == "delete-user":
        with Managed(disable=True, **conn_kwargs) as conn:
            print(json.dumps(op_delete_user(conn, args.uid)))

    elif args.command == "templates":
        with Managed(**conn_kwargs) as conn:
            print(json.dumps(op_get_templates(conn)))

    elif args.command == "backup":
        with Managed(**conn_kwargs) as conn:
            print(json.dumps(op_backup(conn, args.file)))

    elif args.command == "restore":
        if not args.__dict__.get("yes_bypass") and not confirm(f"This will write users/templates from {args.file} onto the device."):
            die("Aborted by user")
        with Managed(disable=True, **conn_kwargs) as conn:
            print(json.dumps(op_restore(conn, args.file)))

    elif args.command == "set-time":
        with Managed(**conn_kwargs) as conn:
            print(json.dumps(op_set_time(conn)))

    elif args.command == "clear-attendance":
        if not args.yes and not confirm("This will erase the attendance log."):
            die("Aborted by user")
        with Managed(**conn_kwargs) as conn:
            print(json.dumps(op_clear_attendance(conn)))

    elif args.command == "clear-all":
        if not args.yes and not confirm("DANGER: this erases users, fingerprints AND attendance."):
            die("Aborted by user")
        with Managed(**conn_kwargs) as conn:
            print(json.dumps(op_clear_data(conn)))

    elif args.command == "restart":
        if not args.yes and not confirm("This will restart the device."):
            die("Aborted by user")
        with Managed(**conn_kwargs) as conn:
            print(json.dumps(op_restart(conn)))

    elif args.command == "poweroff":
        if not args.yes and not confirm("This will power off the device."):
            die("Aborted by user")
        with Managed(**conn_kwargs) as conn:
            print(json.dumps(op_poweroff(conn)))

    elif args.command == "test-voice":
        with Managed(**conn_kwargs) as conn:
            print(json.dumps(op_test_voice(conn, args.index)))

    elif args.command == "live-capture":
        conn = get_conn(**conn_kwargs)
        start = datetime.now()
        try:
            for att in conn.live_capture():
                if att:
                    print(json.dumps({
                        "user_id": att.user_id, "timestamp": str(att.timestamp),
                        "status": att.status, "punch": att.punch,
                    }))
                if args.seconds and (datetime.now() - start).total_seconds() > args.seconds:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            conn.disconnect()

    elif args.command == "serve":
        run_server(host=args.host, port=args.port, password=args.password,
                    timeout=args.timeout, bind=args.bind, api_port=args.api_port)


MENU = """
=========================================
 ZKTeco Device Toolkit
=========================================
 1) Device info
 2) Attendance (last N days)
 3) List users
 4) Add / update user
 5) Delete user
 6) Dump fingerprint templates
 7) Backup users + templates to file
 8) Restore users + templates from file
 9) Sync device time
10) Clear attendance log
11) Clear ALL data (danger)
12) Restart device
13) Power off device
14) Live capture (real-time punches)
15) Start API server for HRMS integration
16) Test voice prompt by ID
 0) Exit
=========================================
"""


def interactive_menu():
    print(f"Connecting to {DEFAULTS['host']}:{DEFAULTS['port']} by default "
          f"(override with env vars ZK_HOST / ZK_PORT, or answer prompts below).")
    while True:
        print(MENU)
        choice = input("Choose an option: ").strip()

        if choice == "0":
            break

        elif choice == "1":
            with Managed() as conn:
                print(json.dumps(op_device_info(conn), indent=2))

        elif choice == "2":
            days = input("How many days back? [3]: ").strip() or "3"
            with Managed() as conn:
                print(json.dumps(op_get_attendance(conn, days=int(days)), indent=2))

        elif choice == "3":
            with Managed() as conn:
                print(json.dumps(op_get_users(conn), indent=2))

        elif choice == "4":
            uid = int(input("uid (numeric): ").strip())
            name = input("name: ").strip()
            privilege = input("privilege [0=user,14=admin] (default 0): ").strip() or "0"
            user_id = input("user_id (external/employee id, optional): ").strip()
            with Managed(disable=True) as conn:
                print(json.dumps(op_set_user(conn, uid, name, int(privilege), "", "", user_id, 0)))

        elif choice == "5":
            uid = int(input("uid to delete: ").strip())
            with Managed(disable=True) as conn:
                print(json.dumps(op_delete_user(conn, uid)))

        elif choice == "6":
            with Managed() as conn:
                print(json.dumps(op_get_templates(conn), indent=2))

        elif choice == "7":
            path = input("Backup file path [backup.json]: ").strip() or "backup.json"
            with Managed() as conn:
                print(json.dumps(op_backup(conn, path)))

        elif choice == "8":
            path = input("Restore file path: ").strip()
            if confirm("This overwrites matching users/templates on the device."):
                with Managed(disable=True) as conn:
                    print(json.dumps(op_restore(conn, path)))

        elif choice == "9":
            with Managed() as conn:
                print(json.dumps(op_set_time(conn)))

        elif choice == "10":
            if confirm("Erase attendance log?"):
                with Managed() as conn:
                    print(json.dumps(op_clear_attendance(conn)))

        elif choice == "11":
            if confirm("DANGER: erase users, fingerprints AND attendance?"):
                with Managed() as conn:
                    print(json.dumps(op_clear_data(conn)))

        elif choice == "12":
            if confirm("Restart device?"):
                with Managed() as conn:
                    print(json.dumps(op_restart(conn)))

        elif choice == "13":
            if confirm("Power off device?"):
                with Managed() as conn:
                    print(json.dumps(op_poweroff(conn)))

        elif choice == "14":
            seconds = input("Stop after how many seconds? [blank = until Ctrl+C]: ").strip()
            conn = get_conn()
            start = datetime.now()
            print("Listening for punches... (Ctrl+C to stop)")
            try:
                for att in conn.live_capture():
                    if att:
                        print(json.dumps({
                            "user_id": att.user_id, "timestamp": str(att.timestamp),
                            "status": att.status, "punch": att.punch,
                        }))
                    if seconds and (datetime.now() - start).total_seconds() > int(seconds):
                        break
            except KeyboardInterrupt:
                print("\nStopped.")
            finally:
                conn.disconnect()

        elif choice == "15":
            bind = input("Bind address [0.0.0.0]: ").strip() or "0.0.0.0"
            api_port = input("API port [8000]: ").strip() or "8000"
            run_server(bind=bind, api_port=int(api_port))
            break  # serve blocks until killed; menu resumes only after server stops

        elif choice == "16":
            index = input("Voice index (0-13) [0]: ").strip() or "0"
            with Managed() as conn:
                print(json.dumps(op_test_voice(conn, int(index))))

        else:
            print("Unknown option.")


# --------------------------------------------------------------------------
# FastAPI server — for HRMS integration
# --------------------------------------------------------------------------

def run_server(host=None, port=None, password=None, timeout=None, bind="0.0.0.0", api_port=8000):
    try:
        from fastapi import FastAPI, HTTPException, Query
        from fastapi.responses import StreamingResponse
        from pydantic import BaseModel
        import uvicorn
    except ImportError:
        die("fastapi and uvicorn are required for serve mode. Run: pip install fastapi uvicorn")

    app = FastAPI(
        title="ZKTeco Device API",
        description="HRMS-facing API in front of a ZKTeco fingerprint attendance machine (via pyzk).",
        version="1.0.0",
    )

    default_conn = dict(host=host, port=port, password=password, timeout=timeout)

    def conn_params(zk_host=None, zk_port=None, zk_password=None, zk_timeout=None):
        """Allow per-request override of the target device, falling back to server defaults."""
        return dict(
            host=zk_host or default_conn["host"],
            port=zk_port or default_conn["port"],
            password=zk_password if zk_password is not None else default_conn["password"],
            timeout=zk_timeout or default_conn["timeout"],
        )

    class UserIn(BaseModel):
        uid: int
        name: str
        privilege: int = 0
        password: str = ""
        group_id: str = ""
        user_id: str = ""
        card: int = 0

    class ConfirmIn(BaseModel):
        confirm: bool = False

    class SetTimeIn(BaseModel):
        iso_datetime: str | None = None  # e.g. "2026-07-06T10:00:00"

    class VoiceIn(BaseModel):
        index: int = 0

    def handle(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Device error: {e}")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/device/info")
    def device_info(zk_host: str = None, zk_port: int = None):
        with Managed(**conn_params(zk_host, zk_port)) as conn:
            return handle(op_device_info, conn)

    @app.get("/attendance")
    def attendance(days: int = None, start: str = None, end: str = None,
                   zk_host: str = None, zk_port: int = None):
        with Managed(**conn_params(zk_host, zk_port)) as conn:
            return handle(op_get_attendance, conn, days=days, start_date=start, end_date=end)

    @app.get("/users")
    def list_users(zk_host: str = None, zk_port: int = None):
        with Managed(**conn_params(zk_host, zk_port)) as conn:
            return handle(op_get_users, conn)

    @app.post("/users")
    def upsert_user(user: UserIn, zk_host: str = None, zk_port: int = None):
        with Managed(disable=True, **conn_params(zk_host, zk_port)) as conn:
            return handle(op_set_user, conn, user.uid, user.name, user.privilege,
                          user.password, user.group_id, user.user_id, user.card)

    @app.delete("/users/{uid}")
    def remove_user(uid: int, zk_host: str = None, zk_port: int = None):
        with Managed(disable=True, **conn_params(zk_host, zk_port)) as conn:
            return handle(op_delete_user, conn, uid)

    @app.get("/templates")
    def templates(zk_host: str = None, zk_port: int = None):
        with Managed(**conn_params(zk_host, zk_port)) as conn:
            return handle(op_get_templates, conn)

    @app.get("/templates/{uid}/{temp_id}")
    def one_template(uid: int, temp_id: int, zk_host: str = None, zk_port: int = None):
        with Managed(**conn_params(zk_host, zk_port)) as conn:
            result = handle(op_get_user_template, conn, uid, temp_id)
            if result is None:
                raise HTTPException(status_code=404, detail="Template not found")
            return result

    @app.post("/backup")
    def backup(file: str = Query(...), zk_host: str = None, zk_port: int = None):
        with Managed(**conn_params(zk_host, zk_port)) as conn:
            return handle(op_backup, conn, file)

    @app.post("/restore")
    def restore(file: str = Query(...), body: ConfirmIn = ConfirmIn(), zk_host: str = None, zk_port: int = None):
        if not body.confirm:
            raise HTTPException(status_code=400, detail="Set confirm=true in body to proceed")
        with Managed(disable=True, **conn_params(zk_host, zk_port)) as conn:
            return handle(op_restore, conn, file)

    @app.post("/device/set-time")
    def set_time(body: SetTimeIn = SetTimeIn(), zk_host: str = None, zk_port: int = None):
        dt = datetime.fromisoformat(body.iso_datetime) if body.iso_datetime else None
        with Managed(**conn_params(zk_host, zk_port)) as conn:
            return handle(op_set_time, conn, dt)

    @app.post("/attendance/clear")
    def clear_attendance(body: ConfirmIn, zk_host: str = None, zk_port: int = None):
        if not body.confirm:
            raise HTTPException(status_code=400, detail="Set confirm=true in body to proceed")
        with Managed(**conn_params(zk_host, zk_port)) as conn:
            return handle(op_clear_attendance, conn)

    @app.post("/device/clear-all")
    def clear_all(body: ConfirmIn, zk_host: str = None, zk_port: int = None):
        if not body.confirm:
            raise HTTPException(status_code=400, detail="Set confirm=true in body to proceed")
        with Managed(**conn_params(zk_host, zk_port)) as conn:
            return handle(op_clear_data, conn)

    @app.post("/device/restart")
    def restart(body: ConfirmIn, zk_host: str = None, zk_port: int = None):
        if not body.confirm:
            raise HTTPException(status_code=400, detail="Set confirm=true in body to proceed")
        with Managed(**conn_params(zk_host, zk_port)) as conn:
            return handle(op_restart, conn)

    @app.post("/device/poweroff")
    def poweroff(body: ConfirmIn, zk_host: str = None, zk_port: int = None):
        if not body.confirm:
            raise HTTPException(status_code=400, detail="Set confirm=true in body to proceed")
        with Managed(**conn_params(zk_host, zk_port)) as conn:
            return handle(op_poweroff, conn)

    @app.post("/device/test-voice")
    def test_voice(body: VoiceIn = VoiceIn(), zk_host: str = None, zk_port: int = None):
        with Managed(**conn_params(zk_host, zk_port)) as conn:
            return handle(op_test_voice, conn, body.index)

    @app.get("/attendance/stream")
    def attendance_stream(seconds: int = 60, zk_host: str = None, zk_port: int = None):
        """
        Server-Sent-Events style stream of live punches, useful for pushing
        real-time check-ins into an HRMS without polling.
        Each line is one JSON object. Connection auto-closes after `seconds`.
        """
        params = conn_params(zk_host, zk_port)

        def gen():
            conn = get_conn(**params)
            start = datetime.now()
            try:
                for att in conn.live_capture():
                    if att:
                        yield json.dumps({
                            "user_id": att.user_id, "timestamp": str(att.timestamp),
                            "status": att.status, "punch": att.punch,
                        }) + "\n"
                    if (datetime.now() - start).total_seconds() > seconds:
                        break
            finally:
                conn.disconnect()

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    print(f"Starting API server on http://{bind}:{api_port}  (docs at /docs)")
    print(f"Default device target: {default_conn['host']}:{default_conn['port']} "
          f"(override per-request with ?zk_host=&zk_port=)")
    uvicorn.run(app, host=bind, port=api_port)


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------

def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        interactive_menu()
    else:
        run_cli_command(args)


if __name__ == "__main__":
    main()