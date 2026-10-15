"""Apply schema upgrades for projects, subtasks, and task.project_id."""
from sqlalchemy import inspect, text
from database import engine, Base
import models  # noqa: F401 — register models with Base.metadata


def column_exists(inspector, table: str, column: str) -> bool:
    return any(col["name"] == column for col in inspector.get_columns(table))


def table_exists(inspector, table: str) -> bool:
    return table in inspector.get_table_names()


def set_fk_on_delete_null(conn, table: str, column: str, ref_table: str):
    """Make table.column nullable with ON DELETE SET NULL. Idempotent."""
    row = conn.execute(text("""
        SELECT con.conname, con.confdeltype
        FROM pg_constraint con
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ANY(con.conkey)
        WHERE con.contype = 'f' AND con.conrelid = CAST(:table AS regclass) AND a.attname = :column
    """), {"table": table, "column": column}).first()
    if row is not None and row.confdeltype == "n":
        return
    print(f"  {table}.{column}: ON DELETE SET NULL...")
    conn.execute(text(f'ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL'))
    if row is not None:
        conn.execute(text(f'ALTER TABLE {table} DROP CONSTRAINT "{row.conname}"'))
    conn.execute(text(f'ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_fkey '
                      f'FOREIGN KEY ({column}) REFERENCES {ref_table}(id) ON DELETE SET NULL'))


def run_upgrade():
    print("Running database upgrade...")
    inspector = inspect(engine)

    with engine.begin() as conn:
        if not table_exists(inspector, "projects"):
            print("  Creating projects table...")
            conn.execute(text("""
                CREATE TABLE projects (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    description VARCHAR DEFAULT '',
                    status VARCHAR DEFAULT 'Active',
                    lead_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP WITHOUT TIME ZONE,
                    updated_at TIMESTAMP WITHOUT TIME ZONE
                )
            """))

        if not table_exists(inspector, "subtasks"):
            print("  Creating subtasks table...")
            conn.execute(text("""
                CREATE TABLE subtasks (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    title VARCHAR NOT NULL,
                    is_completed BOOLEAN DEFAULT FALSE,
                    sort_order INTEGER DEFAULT 0,
                    created_at TIMESTAMP WITHOUT TIME ZONE
                )
            """))

        if table_exists(inspector, "tasks") and not column_exists(inspector, "tasks", "project_id"):
            print("  Adding project_id to tasks...")
            conn.execute(text("""
                ALTER TABLE tasks
                ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL
            """))

        if table_exists(inspector, "users") and not column_exists(inspector, "users", "skills"):
            print("  Adding skills to users...")
            conn.execute(text("ALTER TABLE users ADD COLUMN skills VARCHAR DEFAULT ''"))

        if not table_exists(inspector, "sprints"):
            print("  Creating sprints table...")
            conn.execute(text("""
                CREATE TABLE sprints (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
                    start_date VARCHAR NOT NULL,
                    end_date VARCHAR NOT NULL,
                    goal VARCHAR DEFAULT '',
                    status VARCHAR DEFAULT 'Planned',
                    created_at TIMESTAMP WITHOUT TIME ZONE
                )
            """))

        if not table_exists(inspector, "notifications"):
            print("  Creating notifications table...")
            conn.execute(text("""
                CREATE TABLE notifications (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
                    type VARCHAR NOT NULL,
                    title VARCHAR NOT NULL,
                    message VARCHAR NOT NULL,
                    is_read BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP WITHOUT TIME ZONE
                )
            """))

        if table_exists(inspector, "tasks") and not column_exists(inspector, "tasks", "sprint_id"):
            print("  Adding sprint_id to tasks...")
            conn.execute(text("""
                ALTER TABLE tasks
                ADD COLUMN sprint_id INTEGER REFERENCES sprints(id) ON DELETE SET NULL
            """))

        if table_exists(inspector, "tasks") and not column_exists(inspector, "tasks", "completed_at"):
            print("  Adding completed_at to tasks...")
            conn.execute(text("ALTER TABLE tasks ADD COLUMN completed_at TIMESTAMP WITHOUT TIME ZONE"))

        if table_exists(inspector, "users") and not column_exists(inspector, "users", "token_version"):
            print("  Adding token_version to users...")
            conn.execute(text("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0"))

        # Keep history when a user or task is deleted: these foreign keys used to
        # CASCADE, silently erasing comments, uploads and the activity log.
        for table, column, ref in (("comments", "user_id", "users"), ("attachments", "user_id", "users"),
                                   ("activity_logs", "user_id", "users"), ("activity_logs", "task_id", "tasks")):
            if table_exists(inspector, table):
                set_fk_on_delete_null(conn, table, column, ref)

        if table_exists(inspector, "notifications"):
            print("  Removing duplicate notifications...")
            conn.execute(text("""
                DELETE FROM notifications a
                USING notifications b
                WHERE a.id > b.id
                  AND a.user_id = b.user_id
                  AND a.task_id IS NOT DISTINCT FROM b.task_id
                  AND a.type = b.type
            """))

    Base.metadata.create_all(bind=engine)
    print("Database upgrade completed.")


if __name__ == "__main__":
    run_upgrade()
