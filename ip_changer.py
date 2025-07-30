
import time
import requests
from stem import Signal
from stem.control import Controller

# Function to renew the Tor circuit, effectively changing the IP address
def renew_tor_connection():
    """
    Renews the Tor circuit by sending a NEWNYM signal to the Tor controller.
    This action requests a new identity, which typically results in a new exit node
    and thus a new public IP address.
    """
    try:
        with Controller.from_port(port=9051) as controller:
            # Authenticate with the Tor controller. No password needed if CookieAuthentication is enabled.
            controller.authenticate()
            # Send the NEWNYM signal to request a new Tor circuit.
            controller.signal(Signal.NEWNYM)
        print("Tor connection renewed. New IP address should be active.")
    except Exception as e:
        print(f"Error renewing Tor connection: {e}")

# Function to get the current public IP address using httpbin.org
def get_current_ip():
    """
    Fetches the current public IP address by making a request through the Tor SOCKS5 proxy.
    Returns the IP address as a string if successful, otherwise returns None.
    """
    try:
        # Define proxies for HTTP and HTTPS traffic through Tor.
        proxies = {
            'http': 'socks5h://127.0.0.1:9050',
            'https': 'socks5h://127.0.0.1:9050'
        }
        # Make a GET request to httpbin.org/ip through the Tor proxy.
        response = requests.get("http://httpbin.org/ip", proxies=proxies, timeout=10)
        # Parse the JSON response and extract the 'origin' field, which contains the IP.
        return response.json().get("origin")
    except requests.exceptions.RequestException as e:
        print(f"Error getting IP: {e}")
        return None

# Main function to run the IP changing script
def main():
    """
    Main function to handle user input, schedule IP changes, and manage the script's lifecycle.
    It prompts the user for an IP change interval and a total duration for the script to run.
    """
    print("Welcome to the IP Changer for Termux!")
    print("This script uses Tor to change your IP address.")

    # Loop to get valid user input for interval and duration
    while True:
        try:
            interval_str = input("\nEnter the interval in seconds for IP change (e.g., 300 for 5 minutes): ")
            interval = int(interval_str)
            if interval <= 0:
                print("Interval must be a positive number.")
                continue

            duration_str = input("Enter the total duration in minutes for the script to run (e.g., 60 for 1 hour, 0 for indefinite): ")
            duration = int(duration_str)
            if duration < 0:
                print("Duration cannot be negative.")
                continue

            break # Exit loop if inputs are valid
        except ValueError:
            print("Invalid input. Please enter a number.")

    # Calculate the end time for the script's execution
    start_time = time.time()
    end_time = start_time + (duration * 60) if duration > 0 else float("inf") # float("inf") for indefinite run

    print(f"\nStarting IP change every {interval} seconds.")
    if duration > 0:
        print(f"Script will run for {duration} minutes.")
    else:
        print("Script will run indefinitely.")

    # Main loop for IP rotation
    while time.time() < end_time:
        print(f"\n[{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())}] Requesting new IP...")
        renew_tor_connection()
        current_ip = get_current_ip()
        if current_ip:
            print(f"New IP address: {current_ip}")
        else:
            print("Could not verify new IP address.")

        # Calculate remaining time for the current interval to ensure accurate waiting
        time_to_wait = interval - ((time.time() - start_time) % interval)
        
        # Check if remaining duration is less than the next interval, and if duration is not indefinite
        if duration > 0 and (time.time() + time_to_wait) > end_time:
            print("Remaining time is less than the interval. Exiting soon.")
            break # Exit loop if not enough time for another full interval

        print(f"Waiting for {interval} seconds before next IP change...")
        time.sleep(interval)

    print("\nScript finished running.")

# Entry point of the script
if __name__ == "__main__":
    main()


