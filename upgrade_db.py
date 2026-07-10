"""Apply schema upgrades for projects, subtasks, and task.project_id."""
from sqlalchemy import inspect, text
from database import engine, Base
import models  # noqa: F401 — register models with Base.metadata


def column_exists(inspector, table: str, column: str) -> bool:
    return any(col["name"] == column for col in inspector.get_columns(table))


def table_exists(inspector, table: str) -> bool:
    return table in inspector.get_table_names()


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

    Base.metadata.create_all(bind=engine)
    print("Database upgrade completed.")


if __name__ == "__main__":
    run_upgrade()
