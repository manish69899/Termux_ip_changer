#!/usr/bin/env python3
import os
import re
import time
import json
import random
import requests
import threading
import subprocess
from datetime import datetime, timedelta
from urllib.parse import urlparse

# ===== CONFIGURATION =====
PROXY_API_URL = "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc"
LOG_FILE = "termux_proxy.log"
ROTATION_INTERVAL = 300  # 5 minutes default
IP_CHECK_URL = "http://icanhazip.com"

# ===== TERMUX PROXY ENGINE =====
class TermuxProxyMaster:
    def __init__(self):
        self.proxies = []
        self.current_proxy = None
        self.session_proxies = {}
        self.rotation_active = False
        self.rotation_thread = None
        self.setup_directories()
        
    def setup_directories(self):
        """Ensure required directories exist"""
        os.makedirs("proxy_cache", exist_ok=True)
        
    def fetch_live_proxies(self):
        """Get fresh proxies from Geonode API"""
        try:
            print("🌐 Fetching proxies from Geonode API...")
            response = requests.get(PROXY_API_URL, timeout=30)
            data = response.json()
            
            if 'data' not in data:
                print("⚠️ API format changed! Check documentation")
                return False
                
            self.proxies = [
                {
                    'host': proxy['ip'],
                    'port': proxy['port'],
                    'protocol': proxy['protocols'][0].lower(),
                    'country': proxy['country'],
                    'latency': proxy['latency']
                }
                for proxy in data['data']
                if proxy['latency'] < 2000  # Filter slow proxies
            ]
            
            print(f"✅ Loaded {len(self.proxies)} fresh proxies")
            self.log(f"Fetched {len(self.proxies)} proxies from API")
            return True
            
        except Exception as e:
            self.log(f"Proxy fetch failed: {str(e)}")
            print(f"❌ Proxy fetch error: {str(e)}")
            return False

    def test_proxy(self, proxy):
        """Test proxy connection with 3-second timeout"""
        test_url = f"{proxy['protocol']}://{proxy['host']}:{proxy['port']}"
        proxies = {
            'http': test_url,
            'https': test_url
        }
        
        try:
            start = time.time()
            response = requests.get(
                IP_CHECK_URL,
                proxies=proxies,
                timeout=3,
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            latency = int((time.time() - start) * 1000)
            
            if response.status_code == 200:
                return {
                    'working': True,
                    'ip': response.text.strip(),
                    'latency': latency
                }
        except:
            pass
        return {'working': False}

    def find_working_proxy(self, max_attempts=10):
        """Find a working proxy from the pool"""
        random.shuffle(self.proxies)
        
        for i, proxy in enumerate(self.proxies[:max_attempts]):
            print(f"🔎 Testing {proxy['host']}:{proxy['port']} ({i+1}/{max_attempts})")
            result = self.test_proxy(proxy)
            
            if result['working']:
                print(f"✅ Found working proxy: {result['ip']} | Latency: {result['latency']}ms")
                return {**proxy, **result}
        
        print("❌ No working proxies found in batch")
        return None

    def set_termux_proxy(self, proxy):
        """Set proxy for Termux environment"""
        if not proxy:
            return False
            
        try:
            # Set environment variables
            os.environ['HTTP_PROXY'] = f"{proxy['protocol']}://{proxy['host']}:{proxy['port']}"
            os.environ['HTTPS_PROXY'] = f"{proxy['protocol']}://{proxy['host']}:{proxy['port']}"
            
            # For curl/wget support
            with open(os.path.expanduser('~/.curlrc'), 'w') as f:
                f.write(f"proxy = {proxy['protocol']}://{proxy['host']}:{proxy['port']}\n")
                
            # Save current proxy
            self.current_proxy = proxy
            self.log(f"Proxy set: {proxy['host']}:{proxy['port']} | IP: {proxy['ip']}")
            return True
        except Exception as e:
            self.log(f"Proxy set failed: {str(e)}")
            return False

    def rotate_proxy(self):
        """Rotate to a new working proxy"""
        print("\n🔄 Rotating IP address...")
        new_proxy = self.find_working_proxy()
        if new_proxy and self.set_termux_proxy(new_proxy):
            return new_proxy
        return None

    def start_rotation(self, interval_min, duration_hr):
        """Start automatic proxy rotation"""
        self.rotation_active = True
        end_time = datetime.now() + timedelta(hours=duration_hr)
        
        def rotation_loop():
            while self.rotation_active and datetime.now() < end_time:
                proxy_info = self.rotate_proxy()
                if proxy_info:
                    print(f"⏱ Next rotation in {interval_min} minutes")
                    self.show_wifi_instructions(proxy_info)
                time.sleep(interval_min * 60)
            self.rotation_active = False
            print("\n⏹ Rotation schedule completed")
            
        self.rotation_thread = threading.Thread(target=rotation_loop)
        self.rotation_thread.daemon = True
        self.rotation_thread.start()

    def stop_rotation(self):
        """Stop automatic rotation"""
        if self.rotation_active:
            self.rotation_active = False
            if self.rotation_thread and self.rotation_thread.is_alive():
                self.rotation_thread.join(timeout=2)
            print("\n⏹ Proxy rotation stopped")

    def show_wifi_instructions(self, proxy):
        """Display Android Wi-Fi proxy setup instructions"""
        print("\n" + "="*50)
        print("📱 ANDROID WI-FI PROXY SETUP INSTRUCTIONS")
        print("="*50)
        print(f"Proxy Host: {proxy['host']}")
        print(f"Proxy Port: {proxy['port']}")
        print("\n1. Go to Settings > Network & Internet > Wi-Fi")
        print("2. Long-press your connected network")
        print("3. Select 'Modify network'")
        print("4. Tap 'Advanced options'")
        print("5. Set Proxy to 'Manual'")
        print(f"6. Enter above Host and Port")
        print("7. Save configuration")
        print("\n💡 Your device will now route traffic through this proxy")
        print("="*50 + "\n")

    def log(self, message):
        """Log to file with timestamp"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, 'a') as f:
            f.write(f"[{timestamp}] {message}\n")

# ===== MAIN APPLICATION =====
def main():
    print("\n" + "="*60)
    print("TERMUX PROXY MASTER".center(60))
    print("="*60)
    print("Features:")
    print("- Fetch live proxies from Geonode API")
    print("- Auto-rotate IP addresses")
    print("- Termux environment configuration")
    print("- Android Wi-Fi setup instructions")
    print("="*60)
    
    proxy_master = TermuxProxyMaster()
    
    while True:
        print("\nMain Menu:")
        print("1. Fetch new proxies")
        print("2. Set random proxy (single)")
        print("3. Start scheduled rotation")
        print("4. Stop rotation")
        print("5. Show current proxy")
        print("6. Show Wi-Fi setup instructions")
        print("7. Exit")
        
        choice = input("\nSelect option: ").strip()
        
        if choice == '1':
            if proxy_master.fetch_live_proxies():
                print(f"🌟 {len(proxy_master.proxies)} proxies available")
        
        elif choice == '2':
            if not proxy_master.proxies:
                print("⚠️ No proxies! Fetch first")
                continue
                
            proxy = proxy_master.rotate_proxy()
            if proxy:
                proxy_master.show_wifi_instructions(proxy)
        
        elif choice == '3':
            if not proxy_master.proxies:
                print("⚠️ No proxies! Fetch first")
                continue
                
            interval = int(input("Rotation interval (minutes): ") or ROTATION_INTERVAL/60)
            duration = int(input("Duration (hours): ") or 1)
            proxy_master.start_rotation(interval, duration)
            print(f"\n🔄 Rotation started: {interval} min intervals for {duration} hours")
        
        elif choice == '4':
            proxy_master.stop_rotation()
        
        elif choice == '5':
            if proxy_master.current_proxy:
                p = proxy_master.current_proxy
                print(f"\nCurrent Proxy: {p['host']}:{p['port']}")
                print(f"Protocol: {p['protocol'].upper()}")
                print(f"Location: {p.get('country', 'N/A')}")
                print(f"Your IP: {p.get('ip', 'N/A')}")
            else:
                print("\n❌ No active proxy")
        
        elif choice == '6':
            if proxy_master.current_proxy:
                proxy_master.show_wifi_instructions(proxy_master.current_proxy)
            else:
                print("⚠️ Set a proxy first")
        
        elif choice == '7':
            proxy_master.stop_rotation()
            print("\n🔌 Exiting Termux Proxy Master")
            break
        
        else:
            print("⚠️ Invalid selection")

# ===== RUN APPLICATION =====
if __name__ == "__main__":
    main()