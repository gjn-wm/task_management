import time
import datetime
from zk_tool import Managed, op_set_time

def main():
    print("--- ZKTeco Time Testing Tool ---")
    use_today = input("Use today's date? (y/n): ").strip().lower()
    
    if use_today == 'y':
        target_date_str = datetime.date.today().strftime("%Y-%m-%d")
        print(f"Using today's date: {target_date_str}")
    else:
        target_date_str = input("Enter date (YYYY-MM-DD): ").strip()
        
    target_time_str = input("What Time? (in 24:00 format, e.g., 14:30): ").strip()
    
    wait_time_sec_str = input("For how much time (in sec): ").strip()
    try:
        wait_time_sec = int(wait_time_sec_str)
    except ValueError:
        print("Error: Please enter a valid integer for seconds.")
        return

    try:
        target_dt = datetime.datetime.strptime(f"{target_date_str} {target_time_str}", "%Y-%m-%d %H:%M")
    except ValueError as e:
        print(f"Error: Invalid date or time format. Details: {e}")
        return

    print("\nConnecting to device...")
    try:
        with Managed() as conn:
            original_device_time = conn.get_time()
            print(f"Original device time before change: {original_device_time}")
            
            print(f"Setting device time to: {target_dt}")
            op_set_time(conn, target_dt)
            print("Time set successfully on the machine.")
    except Exception as e:
        print(f"Failed to connect or set time: {e}")
        return

    print(f"\nWaiting for {wait_time_sec} seconds to let you test...")
    time.sleep(wait_time_sec)
    
    # Calculate time to restore: original time + the time we waited
    restore_time = original_device_time + datetime.timedelta(seconds=wait_time_sec)
    
    print(f"\nRestoring device time to: {restore_time}")
    try:
        with Managed() as conn:
            op_set_time(conn, restore_time)
            print("Machine time restored successfully.")
    except Exception as e:
        print(f"Failed to restore time: {e}")

if __name__ == '__main__':
    main()
