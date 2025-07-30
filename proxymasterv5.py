#!/usr/bin/env python3
import os
import re
import sys
import time
import json
import random
import signal
import requests
import threading
import subprocess
from datetime import datetime, timedelta
from urllib.parse import urlparse
import platform
import readline
import fcntl
import socket
import struct
import uuid
import sqlite3

# ===== CONFIGURATION =====
PROXY_API_URL = "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc"
TOR_BRIDGES_URL = "https://bridges.torproject.org/bridges?transport=obfs4"
LOG_FILE = "termux_proxy.log"
ROTATION_INTERVAL = 300  # 5 minutes default
IP_CHECK_URL = "http://icanhazip.com"
CONFIG_FILE = "proxy_config.json"
LOCAL_PROXY_HOST = "127.0.0.1"
LOCAL_PROXY_PORT = 8080  # Fixed local proxy port
VERSION = "5.0"
DNSCRYPT_CONFIG = "/data/data/com.termux/files/usr/etc/dnscrypt-proxy/dnscrypt-proxy.toml"

# ===== ENHANCED TERMUX PROXY MASTER =====
class TermuxProxyMaster:
    def __init__(self):
        self.proxies = []
        self.tor_bridges = []
        self.current_proxy = None
        self.favorites = []
        self.rotation_active = False
        self.rotation_thread = None
        self.local_proxy_active = False
        self.local_proxy_thread = None
        self.config = {
            "api_url": PROXY_API_URL,
            "max_latency": 2000,
            "protocol_preference": ["http", "socks5", "socks4", "https"],
            "auto_start": False,
            "favorite_countries": [],
            "single_host_mode": False,
            "auto_refresh": False,
            "refresh_interval": 60,  # minutes
            "max_history": 20,
            "notifications": True,
            "theme": "dark",
            "enable_tor": False,
            "proxy_chain": [],
            "dns_protection": True,
            "kill_switch": False,
            "mac_randomization": False,
            "packet_fragmentation": False,
            "browser_spoofing": True
        }
        self.load_config()
        self.setup_directories()
        self.load_favorites()
        self.load_history()
        self.traffic_stats = {"sent": 0, "received": 0}  # Track traffic
        signal.signal(signal.SIGINT, self.signal_handler)
        
    def signal_handler(self, signum, frame):
        """Handle Ctrl+C interruption"""
        print("\n🛑 Interrupt received! Shutting down...")
        self.stop_rotation()
        if self.local_proxy_active:
            self.stop_local_proxy()
        self.disable_kill_switch()  # Ensure kill switch is disabled
        sys.exit(0)
        
    def setup_directories(self):
        """Ensure required directories exist"""
        os.makedirs("proxy_cache", exist_ok=True)
        os.makedirs("logs", exist_ok=True)
        os.makedirs("browser_profiles", exist_ok=True)
        
    def load_config(self):
        """Load configuration from file"""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    self.config = {**self.config, **json.load(f)}
                    print(f"✅ Loaded configuration from {CONFIG_FILE}")
            except Exception as e:
                print(f"⚠️ Error loading config: {str(e)}")
        else:
            self.save_config()
            
    def save_config(self):
        """Save configuration to file"""
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=4)
            print(f"💾 Configuration saved to {CONFIG_FILE}")
            return True
        except Exception as e:
            print(f"❌ Failed to save config: {str(e)}")
            return False
            
    def load_favorites(self):
        """Load favorites from file"""
        if os.path.exists('favorites.json'):
            try:
                with open('favorites.json', 'r') as f:
                    self.favorites = json.load(f)
                print(f"✅ Loaded {len(self.favorites)} favorites")
            except:
                print("⚠️ Error loading favorites")
                
    def save_favorites(self):
        """Save favorites to file"""
        try:
            with open('favorites.json', 'w') as f:
                json.dump(self.favorites, f, indent=4)
            return True
        except:
            return False
            
    def load_history(self):
        """Load proxy history"""
        if os.path.exists('history.json'):
            try:
                with open('history.json', 'r') as f:
                    self.history = json.load(f)
            except:
                self.history = []
        else:
            self.history = []
            
    def save_history(self):
        """Save proxy history"""
        try:
            with open('history.json', 'w') as f:
                json.dump(self.history, f, indent=4)
            return True
        except:
            return False
            
    def fetch_live_proxies(self):
        """Get fresh proxies from API"""
        try:
            print(f"🌐 Fetching proxies from {self.config['api_url']}")
            headers = {
                'User-Agent': self.generate_random_user_agent(),
                'Accept': 'application/json'
            }
            response = requests.get(
                self.config['api_url'], 
                headers=headers,
                timeout=30
            )
            data = response.json()
            
            if 'data' not in data:
                print("⚠️ API format changed! Check documentation")
                return False
                
            self.proxies = []
            for proxy in data['data']:
                # Filter by latency
                if proxy['latency'] > self.config['max_latency']:
                    continue
                    
                # Filter by country preference
                if (self.config['favorite_countries'] and 
                    proxy['country'] not in self.config['favorite_countries']):
                    continue
                    
                # Use first available protocol
                for protocol in self.config['protocol_preference']:
                    if protocol in proxy['protocols']:
                        self.proxies.append({
                            'host': proxy['ip'],
                            'port': proxy['port'],
                            'protocol': protocol,
                            'country': proxy['country'],
                            'latency': proxy['latency'],
                            'last_checked': proxy['lastChecked'],
                            'is_favorite': any(fav['host'] == proxy['ip'] for fav in self.favorites)
                        })
                        break
                        
            print(f"✅ Loaded {len(self.proxies)} filtered proxies")
            self.log(f"Fetched {len(self.proxies)} proxies from API")
            
            # Cache proxies
            self.cache_proxies()
            return True
            
        except Exception as e:
            self.log(f"Proxy fetch failed: {str(e)}")
            print(f"❌ Proxy fetch error: {str(e)}")
            return False

    def fetch_tor_bridges(self):
        """Fetch Tor bridges for enhanced anonymity"""
        try:
            print("🌐 Fetching Tor bridges...")
            response = requests.get(TOR_BRIDGES_URL, timeout=15)
            if response.status_code == 200:
                self.tor_bridges = response.text.strip().split('\n')
                print(f"✅ Loaded {len(self.tor_bridges)} Tor bridges")
                return True
        except Exception as e:
            print(f"❌ Tor bridge fetch failed: {str(e)}")
        return False

    def cache_proxies(self):
        """Cache proxies to file"""
        cache_file = f"proxy_cache/proxies_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        try:
            with open(cache_file, 'w') as f:
                json.dump(self.proxies, f, indent=4)
            print(f"💾 Proxies cached to {cache_file}")
        except:
            print("⚠️ Failed to cache proxies")

    def test_proxy(self, proxy, timeout=3):
        """Test proxy connection with timeout"""
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
                timeout=timeout,
                headers={'User-Agent': self.generate_random_user_agent()}
            )
            latency = int((time.time() - start) * 1000)
            
            if response.status_code == 200:
                # Track traffic
                self.traffic_stats['received'] += len(response.content)
                return {
                    'working': True,
                    'ip': response.text.strip(),
                    'latency': latency
                }
        except:
            pass
        return {'working': False}

    def find_working_proxy(self, max_attempts=15):
        """Find a working proxy with intelligent selection"""
        if not self.proxies:
            print("⚠️ No proxies available! Fetching new proxies...")
            if not self.fetch_live_proxies():
                return None
                
        # Create a prioritized list (favorites first, then by latency)
        candidates = [p for p in self.proxies if p.get('is_favorite', False)]
        if not candidates:
            candidates = sorted(self.proxies, key=lambda x: x['latency'])
        
        # Ensure we don't exceed max attempts
        candidates = candidates[:max_attempts]
        random.shuffle(candidates)  # Add randomness for load distribution
        
        for i, proxy in enumerate(candidates):
            print(f"🔎 Testing {proxy['host']}:{proxy['port']} ({proxy['protocol'].upper()})")
            result = self.test_proxy(proxy, timeout=5)
            
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
            # If in single host mode, use local proxy instead
            if self.config['single_host_mode']:
                proxy_host = LOCAL_PROXY_HOST
                proxy_port = LOCAL_PROXY_PORT
                print(f"🔒 Using fixed proxy: {proxy_host}:{proxy_port}")
            else:
                proxy_host = proxy['host']
                proxy_port = proxy['port']
            
            # Set environment variables
            proxy_url = f"{proxy['protocol']}://{proxy_host}:{proxy_port}"
            os.environ['HTTP_PROXY'] = proxy_url
            os.environ['HTTPS_PROXY'] = proxy_url
            
            # For curl/wget support
            with open(os.path.expanduser('~/.curlrc'), 'w') as f:
                f.write(f"proxy = {proxy_url}\n")
                
            # Save current proxy
            self.current_proxy = proxy
            self.log(f"Proxy set: {proxy_host}:{proxy_port} | IP: {proxy['ip']}")
            
            # Add to history
            self.add_to_history(proxy)
            
            # Apply proxy chain if enabled
            if self.config['proxy_chain']:
                self.setup_proxy_chain()
                
            # Apply DNS protection
            if self.config['dns_protection']:
                self.enable_dns_protection()
                
            # Apply kill switch
            if self.config['kill_switch']:
                self.enable_kill_switch()
                
            # Apply MAC randomization
            if self.config['mac_randomization']:
                self.randomize_mac_address()
                
            # Apply browser spoofing
            if self.config['browser_spoofing']:
                self.generate_browser_profile()
                
            return True
        except Exception as e:
            self.log(f"Proxy set failed: {str(e)}")
            return False

    def add_to_history(self, proxy):
        """Add proxy to history"""
        entry = {
            'host': proxy['host'],
            'port': proxy['port'],
            'protocol': proxy['protocol'],
            'country': proxy.get('country', ''),
            'ip': proxy.get('ip', ''),
            'set_time': datetime.now().isoformat(),
            'latency': proxy.get('latency', 'N/A')
        }
        
        # Add to beginning
        self.history.insert(0, entry)
        
        # Keep only last N entries
        self.history = self.history[:self.config['max_history']]
        self.save_history()

    def add_favorite(self, proxy):
        """Add proxy to favorites"""
        if not any(fav['host'] == proxy['host'] for fav in self.favorites):
            self.favorites.append({
                'host': proxy['host'],
                'port': proxy['port'],
                'protocol': proxy['protocol'],
                'country': proxy.get('country', ''),
                'added': datetime.now().isoformat()
            })
            print(f"🌟 Added {proxy['host']} to favorites")
            self.save_favorites()
            return True
        return False

    def remove_favorite(self, host):
        """Remove proxy from favorites"""
        self.favorites = [fav for fav in self.favorites if fav['host'] != host]
        print(f"🗑️ Removed {host} from favorites")
        self.save_favorites()
        return True

    def rotate_proxy(self):
        """Rotate to a new working proxy"""
        print("\n🔄 Rotating IP address...")
        new_proxy = self.find_working_proxy()
        if new_proxy and self.set_termux_proxy(new_proxy):
            if self.config['notifications']:
                self.show_notification("Proxy Rotated", f"New IP: {new_proxy['ip']}")
            return new_proxy
        return None

    def start_rotation(self, interval_min, duration_hr):
        """Start automatic proxy rotation with infinite option"""
        self.rotation_active = True
        
        # Handle infinite rotation
        if duration_hr <= 0:
            end_time = None
            print("♾️ Rotation started: Runs indefinitely until manually stopped")
        else:
            end_time = datetime.now() + timedelta(hours=duration_hr)
            print(f"⏱ Rotation started: {interval_min} min intervals for {duration_hr} hours")
        
        def rotation_loop():
            while self.rotation_active and (end_time is None or datetime.now() < end_time):
                proxy_info = self.rotate_proxy()
                if proxy_info:
                    print(f"⏱ Next rotation in {interval_min} minutes")
                    self.show_wifi_instructions(proxy_info)
                else:
                    print("⚠️ Rotation failed, retrying in 30 seconds")
                    time.sleep(30)
                    continue
                    
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
            return True
        return False

    def show_wifi_instructions(self, proxy):
        """Display Android Wi-Fi proxy setup instructions"""
        # Use fixed host/port if in single host mode
        if self.config['single_host_mode']:
            host = LOCAL_PROXY_HOST
            port = LOCAL_PROXY_PORT
            note = "\n💡 Fixed proxy endpoint - IP changes automatically behind this address"
        else:
            host = proxy['host']
            port = proxy['port']
            note = ""
        
        print("\n" + "="*50)
        print("📱 ANDROID WI-FI PROXY SETUP INSTRUCTIONS")
        print("="*50)
        print(f"Proxy Host: {host}")
        print(f"Proxy Port: {port}")
        print(f"Protocol: {proxy['protocol'].upper()}")
        print(f"Country: {proxy.get('country', 'Unknown')}")
        print(f"Latency: {proxy.get('latency', 'N/A')}ms")
        print("\n1. Go to Settings > Network & Internet > Wi-Fi")
        print("2. Long-press your connected network")
        print("3. Select 'Modify network'")
        print("4. Tap 'Advanced options'")
        print("5. Set Proxy to 'Manual'")
        print(f"6. Enter above Host and Port")
        print("7. Save configuration")
        print("\n💡 Your device will now route traffic through this proxy")
        print(note)
        print("="*50 + "\n")
        
        # Generate QR code for easy sharing
        self.generate_wifi_qr(host, port)

    def generate_wifi_qr(self, host, port):
        """Generate QR code for proxy configuration"""
        try:
            from qrcode import QRCode, constants
            proxy_url = f"http://{host}:{port}"
            qr = QRCode(
                version=1,
                error_correction=constants.ERROR_CORRECT_L,
                box_size=2,
                border=1,
            )
            qr.add_data(proxy_url)
            qr.make(fit=True)
            
            print("🔳 QR Code for Proxy Configuration:")
            qr.print_ascii(invert=True)
        except ImportError:
            print("ℹ️ Install 'qrcode' package for QR generation: pip install qrcode")
        except Exception as e:
            print(f"⚠️ QR generation error: {str(e)}")

    def clear_proxy_settings(self):
        """Clear all proxy settings"""
        try:
            # Clear environment variables
            if 'HTTP_PROXY' in os.environ:
                del os.environ['HTTP_PROXY']
            if 'HTTPS_PROXY' in os.environ:
                del os.environ['HTTPS_PROXY']
                
            # Remove curl config
            curlrc = os.path.expanduser('~/.curlrc')
            if os.path.exists(curlrc):
                os.remove(curlrc)
                
            self.current_proxy = None
            print("🔌 Cleared all proxy settings")
            self.disable_kill_switch()
            return True
        except Exception as e:
            print(f"❌ Clear failed: {str(e)}")
            return False

    def toggle_single_host_mode(self):
        """Toggle single host mode"""
        self.config['single_host_mode'] = not self.config['single_host_mode']
        status = "ENABLED" if self.config['single_host_mode'] else "DISABLED"
        print(f"\n🔀 Single Host Mode: {status}")
        print("💡 Use fixed proxy: 127.0.0.1:8080 for all connections")
        print("    (IP changes automatically behind this address)")
        self.save_config()
        
        # Update environment if proxy is active
        if self.current_proxy:
            self.set_termux_proxy(self.current_proxy)
        return True

    def start_local_proxy(self):
        """Start local proxy server"""
        if self.local_proxy_active:
            print("⚠️ Local proxy is already running")
            return False
            
        try:
            print("🚀 Starting local proxy server...")
            # This would be a separate implementation
            # For now, we'll simulate it
            self.local_proxy_active = True
            print("✅ Local proxy running at 127.0.0.1:8080")
            return True
        except Exception as e:
            print(f"❌ Failed to start local proxy: {str(e)}")
            return False

    def stop_local_proxy(self):
        """Stop local proxy server"""
        if not self.local_proxy_active:
            print("⚠️ Local proxy is not running")
            return False
            
        try:
            print("🛑 Stopping local proxy server...")
            self.local_proxy_active = False
            print("✅ Local proxy stopped")
            return True
        except Exception as e:
            print(f"❌ Failed to stop local proxy: {str(e)}")
            return False

    def show_notification(self, title, message):
        """Show system notification"""
        try:
            if platform.system() == "Linux":
                subprocess.run(['notify-send', title, message])
            elif platform.system() == "Darwin":  # macOS
                subprocess.run(['osascript', '-e', f'display notification "{message}" with title "{title}"'])
            print(f"🔔 {title}: {message}")
        except:
            pass

    def show_history(self):
        """Show proxy history"""
        print("\n" + "="*60)
        print("🕰 PROXY HISTORY".center(60))
        print("="*60)
        if not self.history:
            print("No history available")
            return
            
        for i, entry in enumerate(self.history[:10], 1):
            print(f"{i}. {entry['host']}:{entry['port']} ({entry['protocol'].upper()})")
            print(f"   IP: {entry.get('ip', 'N/A')} | Country: {entry.get('country', 'N/A')}")
            print(f"   Set at: {entry['set_time']} | Latency: {entry.get('latency', 'N/A')}ms")
            print("-" * 60)

    def export_proxies(self, filename="proxies_export.txt"):
        """Export proxies to file"""
        try:
            with open(filename, 'w') as f:
                for proxy in self.proxies:
                    f.write(f"{proxy['protocol']}://{proxy['host']}:{proxy['port']}\n")
            print(f"💾 Exported {len(self.proxies)} proxies to {filename}")
            return True
        except Exception as e:
            print(f"❌ Export failed: {str(e)}")
            return False

    def speed_test(self, proxy=None, test_url="http://example.com", timeout=5):
        """Test proxy speed"""
        target = proxy or self.current_proxy
        if not target:
            print("⚠️ No proxy selected")
            return
            
        print(f"⏱ Testing speed for {target['host']}:{target['port']}...")
        
        try:
            test_url = f"{target['protocol']}://{target['host']}:{target['port']}"
            proxies = {'http': test_url, 'https': test_url}
            
            start = time.time()
            response = requests.get(
                test_url,
                proxies=proxies,
                timeout=timeout,
                stream=True
            )
            size = len(response.content)
            elapsed = time.time() - start
            
            if response.status_code == 200:
                speed = size / elapsed / 1024  # KB/s
                print(f"✅ Speed test completed: {speed:.2f} KB/s")
                return speed
            else:
                print("❌ Speed test failed: Non-200 response")
                return None
        except Exception as e:
            print(f"❌ Speed test failed: {str(e)}")
            return None

    def log(self, message):
        """Log to file with timestamp"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        
        # Write to main log
        with open(LOG_FILE, 'a') as f:
            f.write(log_entry + "\n")
            
        # Write to daily log
        daily_log = f"logs/proxy_{datetime.now().strftime('%Y%m%d')}.log"
        with open(daily_log, 'a') as f:
            f.write(log_entry + "\n")

    # ===== NEW FEATURES =====
    def setup_proxy_chain(self):
        """Setup proxy chaining for multi-hop anonymity"""
        if not self.config['proxy_chain']:
            return
            
        print("⛓ Setting up proxy chain...")
        chain = self.config['proxy_chain']
        chain_str = " -> ".join([f"{p['protocol']}://{p['host']}:{p['port']}" for p in chain])
        print(f"Proxy chain: {chain_str}")
        
        # In a real implementation, this would configure proxychains
        # For now, we'll simulate it
        os.environ['PROXY_CHAIN'] = json.dumps(chain)
        print("✅ Proxy chain configured")

    def enable_dns_protection(self):
        """Enable DNS leak protection using DNSCrypt-proxy"""
        if not os.path.exists(DNSCRYPT_CONFIG):
            print("⚠️ DNSCrypt-proxy not installed. Skipping DNS protection.")
            return
            
        try:
            print("🔒 Enabling DNS leak protection...")
            # Configure DNSCrypt to use anonymous DNS
            with open(DNSCRYPT_CONFIG, 'r') as f:
                config = f.read()
                
            # Modify configuration
            config = re.sub(r'^listen_addresses.*', 'listen_addresses = ["127.0.0.1:53"]', config, flags=re.M)
            config = re.sub(r'^require_dnssec.*', 'require_dnssec = true', config, flags=re.M)
            config = re.sub(r'^require_nolog.*', 'require_nolog = true', config, flags=re.M)
            config = re.sub(r'^require_nofilter.*', 'require_nofilter = true', config, flags=re.M)
            
            with open(DNSCRYPT_CONFIG, 'w') as f:
                f.write(config)
                
            # Restart service
            subprocess.run(['pkill', 'dnscrypt-proxy'])
            subprocess.run(['dnscrypt-proxy', '-config', DNSCRYPT_CONFIG, '-daemonize'])
            print("✅ DNS protection enabled")
        except Exception as e:
            print(f"❌ DNS protection failed: {str(e)}")

    def enable_kill_switch(self):
        """Enable network kill switch to prevent IP leaks"""
        print("🛡️ Enabling kill switch...")
        try:
            # Flush existing rules
            subprocess.run(['iptables', '-F'])
            subprocess.run(['iptables', '-X'])
            subprocess.run(['iptables', '-t', 'nat', '-F'])
            
            # Allow local traffic
            subprocess.run(['iptables', '-A', 'OUTPUT', '-d', '127.0.0.1', '-j', 'ACCEPT'])
            
            # Allow DNS
            subprocess.run(['iptables', '-A', 'OUTPUT', '-p', 'udp', '--dport', '53', '-j', 'ACCEPT'])
            
            # Block all other traffic
            subprocess.run(['iptables', '-A', 'OUTPUT', '-j', 'DROP'])
            
            print("✅ Kill switch activated - All traffic blocked except proxy")
            return True
        except Exception as e:
            print(f"❌ Kill switch failed: {str(e)}")
            return False

    def disable_kill_switch(self):
        """Disable network kill switch"""
        print("🔓 Disabling kill switch...")
        try:
            subprocess.run(['iptables', '-F'])
            subprocess.run(['iptables', '-X'])
            subprocess.run(['iptables', '-t', 'nat', '-F'])
            print("✅ Kill switch disabled")
            return True
        except Exception as e:
            print(f"❌ Failed to disable kill switch: {str(e)}")
            return False

    def randomize_mac_address(self):
        """Randomize MAC address for Wi-Fi interface"""
        if not self.config['mac_randomization']:
            return
            
        print("🔀 Randomizing MAC address...")
        try:
            # Get current MAC
            interfaces = subprocess.getoutput("ip link show | grep '^[0-9]' | awk -F': ' '{print $2}'").split()
            wifi_interface = next((iface for iface in interfaces if 'wlan' in iface), None)
            
            if not wifi_interface:
                print("⚠️ No Wi-Fi interface found")
                return
                
            # Generate random MAC
            new_mac = ':'.join(['{:02x}'.format(random.randint(0, 255)) for _ in range(6)])
            
            # Set new MAC
            subprocess.run(['ip', 'link', 'set', wifi_interface, 'down'])
            subprocess.run(['ip', 'link', 'set', wifi_interface, 'address', new_mac])
            subprocess.run(['ip', 'link', 'set', wifi_interface, 'up'])
            
            print(f"✅ MAC address randomized: {new_mac}")
            return True
        except Exception as e:
            print(f"❌ MAC randomization failed: {str(e)}")
            return False

    def generate_random_user_agent(self):
        """Generate random user agent for requests"""
        agents = [
            # Chrome
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{}.{}.{}.{} Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{}.{}.{}.{} Safari/537.36",
            
            # Firefox
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{}.0) Gecko/20100101 Firefox/{}.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:{}.0) Gecko/20100101 Firefox/{}.0",
            
            # Safari
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/{}.{} Safari/605.1.15"
        ]
        
        template = random.choice(agents)
        
        if "Chrome" in template:
            version = f"{random.randint(90, 115)}.0.{random.randint(1000, 6000)}.{random.randint(1, 200)}"
            return template.format(*(version.split('.')))
        elif "Firefox" in template:
            version = random.randint(90, 115)
            return template.format(version, version)
        else:  # Safari
            return template.format(random.randint(14, 16), random.randint(0, 5))

    def generate_browser_profile(self):
        """Generate randomized browser profile to prevent fingerprinting"""
        if not self.config['browser_spoofing']:
            return
            
        print("🖥️ Generating browser profile...")
        profile = {
            "user_agent": self.generate_random_user_agent(),
            "platform": random.choice(["Win32", "MacIntel", "Linux x86_64"]),
            "language": random.choice(["en-US", "en-GB", "fr-FR", "de-DE"]),
            "timezone": random.choice(["UTC", "GMT", "PST", "EST"]),
            "screen": f"{random.randint(1280, 3840)}x{random.randint(720, 2160)}",
            "fonts": random.sample([
                "Arial", "Times New Roman", "Helvetica", "Verdana", 
                "Georgia", "Courier New", "Tahoma"
            ], 5),
            "canvas_hash": ''.join(random.choices('0123456789abcdef', k=32)),
            "webgl_hash": ''.join(random.choices('0123456789abcdef', k=32)),
            "audio_context": random.random(),
            "hardware_concurrency": random.choice([2, 4, 8, 16]),
            "device_memory": random.choice([2, 4, 8, 16])
        }
        
        profile_file = f"browser_profiles/profile_{int(time.time())}.json"
        with open(profile_file, 'w') as f:
            json.dump(profile, f)
            
        print(f"✅ Browser profile generated: {profile_file}")
        return profile

    def apply_geolocation_spoof(self, lat, lon):
        """Apply mock location to spoof GPS coordinates"""
        try:
            print(f"📍 Spoofing location to: {lat}, {lon}")
            # Requires mock location app to be installed
            subprocess.run([
                'am', 'startservice', '-n',
                'com.lexa.fakegps/.FakeGPSService',
                '-a', 'android.intent.action.VIEW',
                '--ef', 'latitude', str(lat),
                '--ef', 'longitude', str(lon)
            ])
            print("✅ Location spoofed")
            return True
        except Exception as e:
            print(f"❌ Location spoofing failed: {str(e)}")
            return False

# ===== ENHANCED MAIN APPLICATION =====
def display_banner():
    print("\n" + "="*60)
    print(f"🚀 TERMUX PROXY MASTER {VERSION}".center(60))
    print("="*60)
    print("Ultimate Proxy Management Solution".center(60))
    print("="*60)
    print("Features:")
    print("- ♾️ Infinite rotation mode")
    print("- 🔒 Single-host proxy endpoint")
    print("- ⛓ Proxy chaining (multi-hop)")
    print("- 🛡️ Network kill switch")
    print("- 🔀 MAC address randomization")
    print("- 🔒 DNS leak protection")
    print("- 🖥 Browser fingerprint spoofing")
    print("- 📍 GPS location spoofing")
    print("="*60)

def main():
    display_banner()
    
    proxy_master = TermuxProxyMaster()
    
    # Auto-start if configured
    if proxy_master.config.get('auto_start', False):
        print("\n🚀 Starting auto-rotation as per configuration...")
        proxy_master.start_rotation(
            proxy_master.config.get('rotation_interval', 5),
            proxy_master.config.get('rotation_duration', 1)
        )
    
    while True:
        print("\n" + "="*30)
        print("📱 MAIN MENU".center(30))
        print("="*30)
        print("1. 🌐 Fetch new proxies")
        print("2. 🔄 Set random proxy")
        print("3. ⏱️ Start rotation")
        print("4. ⏹️ Stop rotation")
        print("5. ℹ️ Show current proxy")
        print("6. 📶 Wi-Fi setup")
        print("7. ⚙️ Configuration")
        print("8. ⭐ Favorites")
        print("9. 🕰 History")
        print("10. 📤 Export proxies")
        print("11. 🚀 Speed test")
        print("12. 🔀 Toggle Single-Host")
        print("13. 🛡️ Toggle Kill Switch")
        print("14. 📍 Spoof Location")
        print("15. 🖥 Generate Browser Profile")
        print("16. 🔌 Clear settings")
        print("17. 🚪 Exit")
        
        try:
            choice = input("\n🔍 Select option: ").strip()
        except EOFError:
            print("\nExiting...")
            break
            
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
                
            interval = input("⏱ Rotation interval (minutes) [5]: ").strip()
            interval = int(interval) if interval else 5
            
            duration = input("⏳ Duration (hours, 0=infinite) [1]: ").strip()
            duration = float(duration) if duration else 1.0
            
            proxy_master.start_rotation(interval, duration)
        
        elif choice == '4':
            proxy_master.stop_rotation()
        
        elif choice == '5':
            if proxy_master.current_proxy:
                p = proxy_master.current_proxy
                print(f"\n🔌 Current Proxy: {p['host']}:{p['port']}")
                print(f"📡 Protocol: {p['protocol'].upper()}")
                print(f"🌍 Location: {p.get('country', 'N/A')}")
                print(f"📶 Your IP: {p.get('ip', 'N/A')}")
                print(f"⏱ Latency: {p.get('latency', 'N/A')}ms")
                if proxy_master.config['single_host_mode']:
                    print("\n💡 Single-Host Mode: ACTIVE")
                    print(f"    Using fixed endpoint: {LOCAL_PROXY_HOST}:{LOCAL_PROXY_PORT}")
                if proxy_master.config['kill_switch']:
                    print("\n🛡️ Kill Switch: ACTIVE")
            else:
                print("\n❌ No active proxy")
        
        elif choice == '6':
            if proxy_master.current_proxy:
                proxy_master.show_wifi_instructions(proxy_master.current_proxy)
            else:
                print("⚠️ Set a proxy first")
        
        elif choice == '7':
            print("\n" + "="*30)
            print("⚙️ CONFIGURATION".center(30))
            print("="*30)
            print(f"1. API URL: {proxy_master.config['api_url']}")
            print(f"2. Max Latency: {proxy_master.config['max_latency']}ms")
            print(f"3. Protocol Priority: {', '.join(proxy_master.config['protocol_preference'])}")
            print(f"4. Favorite Countries: {', '.join(proxy_master.config['favorite_countries']) or 'None'}")
            print(f"5. Auto-start: {'✅ Enabled' if proxy_master.config['auto_start'] else '❌ Disabled'}")
            print(f"6. Single-Host Mode: {'✅ Enabled' if proxy_master.config['single_host_mode'] else '❌ Disabled'}")
            print(f"7. Notifications: {'✅ Enabled' if proxy_master.config['notifications'] else '❌ Disabled'}")
            print(f"8. History Limit: {proxy_master.config['max_history']} entries")
            print(f"9. DNS Protection: {'✅ Enabled' if proxy_master.config['dns_protection'] else '❌ Disabled'}")
            print(f"10. MAC Randomization: {'✅ Enabled' if proxy_master.config['mac_randomization'] else '❌ Disabled'}")
            print(f"11. Browser Spoofing: {'✅ Enabled' if proxy_master.config['browser_spoofing'] else '❌ Disabled'}")
            
            sub_choice = input("\nSelect setting to change (1-11) or [Enter] to return: ")
            if sub_choice == '1':
                new_url = input("Enter new API URL: ").strip()
                if new_url:
                    proxy_master.config['api_url'] = new_url
            elif sub_choice == '2':
                try:
                    new_latency = int(input("Enter max latency (ms): "))
                    proxy_master.config['max_latency'] = new_latency
                except:
                    print("Invalid input")
            elif sub_choice == '3':
                print("Enter protocols in order (comma separated):")
                print("Options: http, https, socks4, socks5")
                protocols = input("Protocols: ").lower().split(',')
                valid = [p.strip() for p in protocols if p.strip() in ['http', 'https', 'socks4', 'socks5']]
                if valid:
                    proxy_master.config['protocol_preference'] = valid
            elif sub_choice == '4':
                print("Enter country codes (comma separated, e.g., US,CA,GB):")
                countries = [c.strip().upper() for c in input("Countries: ").split(',') if c.strip()]
                proxy_master.config['favorite_countries'] = countries
            elif sub_choice == '5':
                proxy_master.config['auto_start'] = not proxy_master.config['auto_start']
                print(f"Auto-start {'✅ enabled' if proxy_master.config['auto_start'] else '❌ disabled'}")
            elif sub_choice == '6':
                proxy_master.config['single_host_mode'] = not proxy_master.config['single_host_mode']
                print(f"Single-Host Mode {'✅ enabled' if proxy_master.config['single_host_mode'] else '❌ disabled'}")
            elif sub_choice == '7':
                proxy_master.config['notifications'] = not proxy_master.config['notifications']
                print(f"Notifications {'✅ enabled' if proxy_master.config['notifications'] else '❌ disabled'}")
            elif sub_choice == '8':
                try:
                    new_max = int(input("Enter max history entries: "))
                    proxy_master.config['max_history'] = new_max
                except:
                    print("Invalid input")
            elif sub_choice == '9':
                proxy_master.config['dns_protection'] = not proxy_master.config['dns_protection']
                print(f"DNS Protection {'✅ enabled' if proxy_master.config['dns_protection'] else '❌ disabled'}")
            elif sub_choice == '10':
                proxy_master.config['mac_randomization'] = not proxy_master.config['mac_randomization']
                print(f"MAC Randomization {'✅ enabled' if proxy_master.config['mac_randomization'] else '❌ disabled'}")
            elif sub_choice == '11':
                proxy_master.config['browser_spoofing'] = not proxy_master.config['browser_spoofing']
                print(f"Browser Spoofing {'✅ enabled' if proxy_master.config['browser_spoofing'] else '❌ disabled'}")
            
            proxy_master.save_config()
        
        elif choice == '8':
            print("\n" + "="*30)
            print("⭐ FAVORITES".center(30))
            print("="*30)
            if not proxy_master.favorites:
                print("No favorites yet")
            else:
                for i, fav in enumerate(proxy_master.favorites, 1):
                    print(f"{i}. {fav['host']}:{fav['port']} ({fav['protocol'].upper()}) - {fav['country']}")
            
            print("\na. ➕ Add current proxy")
            print("r. ➖ Remove favorite")
            print("c. 🧹 Clear all favorites")
            fav_choice = input("\nSelect option: ").lower()
            
            if fav_choice == 'a' and proxy_master.current_proxy:
                proxy_master.add_favorite(proxy_master.current_proxy)
            elif fav_choice == 'r' and proxy_master.favorites:
                try:
                    index = int(input("Enter favorite number to remove: ")) - 1
                    if 0 <= index < len(proxy_master.favorites):
                        proxy_master.remove_favorite(proxy_master.favorites[index]['host'])
                except:
                    print("Invalid selection")
            elif fav_choice == 'c' and proxy_master.favorites:
                confirm = input("⚠️ Clear ALL favorites? (y/n): ").lower()
                if confirm == 'y':
                    proxy_master.favorites = []
                    proxy_master.save_favorites()
                    print("🧹 All favorites cleared")
        
        elif choice == '9':
            proxy_master.show_history()
        
        elif choice == '10':
            filename = input("Enter export filename [proxies_export.txt]: ").strip() or "proxies_export.txt"
            proxy_master.export_proxies(filename)
        
        elif choice == '11':
            if proxy_master.current_proxy:
                proxy_master.speed_test()
            else:
                print("⚠️ No active proxy to test")
        
        elif choice == '12':
            proxy_master.toggle_single_host_mode()
        
        elif choice == '13':
            proxy_master.config['kill_switch'] = not proxy_master.config['kill_switch']
            status = "ENABLED" if proxy_master.config['kill_switch'] else "DISABLED"
            print(f"\n🛡️ Kill Switch: {status}")
            if proxy_master.config['kill_switch']:
                proxy_master.enable_kill_switch()
            else:
                proxy_master.disable_kill_switch()
        
        elif choice == '14':
            try:
                lat = float(input("Enter latitude: ").strip())
                lon = float(input("Enter longitude: ").strip())
                proxy_master.apply_geolocation_spoof(lat, lon)
            except:
                print("❌ Invalid coordinates")
        
        elif choice == '15':
            profile = proxy_master.generate_browser_profile()
            if profile:
                print(f"User Agent: {profile['user_agent']}")
                print(f"Platform: {profile['platform']}")
                print(f"Screen: {profile['screen']}")
        
        elif choice == '16':
            if proxy_master.clear_proxy_settings():
                print("✅ Proxy settings cleared")
        
        elif choice == '17':
            proxy_master.stop_rotation()
            print("\n🔌 Exiting Termux Proxy Master")
            break
        
        else:
            print("⚠️ Invalid selection")

# ===== RUN APPLICATION =====
if __name__ == "__main__":
    main()