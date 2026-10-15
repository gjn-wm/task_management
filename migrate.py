import sqlite3
from datetime import datetime
from sqlalchemy import create_engine, MetaData, text
from database import Base, DATABASE_URL
import models  # Required to register classes with Base.metadata

def run_migration():
    print("Connecting to source SQLite database...")
    sqlite_conn = sqlite3.connect("database.db")
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cursor = sqlite_conn.cursor()

    print("Connecting to target PostgreSQL database...")
    pg_engine = create_engine(DATABASE_URL)

    print("Creating tables in PostgreSQL if they don't exist...")
    Base.metadata.create_all(bind=pg_engine)
 
    tables_in_order = ["users", "labels", "tasks", "task_labels", "comments", "attachments", "activity_logs"]

    try:
        meta = MetaData()
        meta.reflect(bind=pg_engine)

        for table_name in tables_in_order:
            print(f"Migrating table '{table_name}'...")
            
            # Fetch data from SQLite
            sqlite_cursor.execute(f"SELECT * FROM {table_name}")
            rows = sqlite_cursor.fetchall()
            
            if not rows:
                print(f"  No records found in SQLite for '{table_name}'. Skipping.")
                continue
                
            print(f"  Found {len(rows)} records in SQLite.")
            
            pg_table = meta.tables[table_name]
            
            # Prepare data
            data_list = []
            for row in rows:
                data = dict(row)
                for col in pg_table.columns:
                    if col.name in data and data[col.name] is not None:
                        # Check if column is a DateTime column
                        from sqlalchemy import DateTime
                        if isinstance(col.type, DateTime):
                            val = data[col.name]
                            if isinstance(val, str):
                                try:
                                    val_clean = val.replace(" ", "T")
                                    if val_clean.endswith("Z"):
                                        val_clean = val_clean[:-1] + "+00:00"
                                    data[col.name] = datetime.fromisoformat(val_clean)
                                except ValueError:
                                    pass
                data_list.append(data)
            
            # Perform bulk insert in Postgres
            with pg_engine.begin() as pg_conn:
                # Clear any existing rows to prevent duplicates in case of retries
                pg_conn.execute(pg_table.delete())
                pg_conn.execute(pg_table.insert(), data_list)
                
            print(f"  Successfully migrated {len(data_list)} rows to Postgres table '{table_name}'.")
            
            # Reset the sequence for auto-incrementing ID column if applicable
            if table_name != "task_labels":
                with pg_engine.begin() as pg_conn:
                    # In PostgreSQL, we can use setval on pg_get_serial_sequence to update identity columns sequence
                    seq_query = text(f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), COALESCE(MAX(id), 1)) FROM {table_name};")
                    pg_conn.execute(seq_query)
                print(f"  Reset primary key sequence for table '{table_name}'.")
                
        print("\nMigration completed successfully!")
    except Exception as e:
        print(f"\nMigration failed with error: {e}")
    finally:
        sqlite_conn.close()

if __name__ == "__main__":
    run_migration()
