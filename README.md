# Task_itms

A simple task management application with a Flask backend and a static frontend.

## Features
- User authentication
- Task and item management
- Database-backed persistence
- Upload support for files

## Project Structure
- `server.py` - main application entry point
- `auth.py` - authentication logic
- `database.py` - database setup and helpers
- `models.py` - data models
- `schemas.py` - request/response schemas
- `migrate.py` / `upgrade_db.py` - database migration helpers
- `static/` - frontend assets
- `uploads/` - uploaded files

## Requirements
Install dependencies with:

```bash
pip install -r requirements.txt
```

## Running the Application
Start the server with:

```bash
python server.py
```

Or on Windows:

```bat
run_server.bat
```

## Database Setup
If needed, run the migration script:

```bash
python migrate.py
```

## Notes
- The app uses local files and a database for persistence.
- Uploaded files are stored under the `uploads/` directory.
- Backup artifacts are stored under `backups/`.
