import os
import sys
import json
import http.server
import socketserver
import urllib.request
import urllib.parse
import urllib.error
import concurrent.futures
import time
import shutil
import threading
import ssl

# Create SSL context to bypass certificate verification for sites with SSL issues
ssl_context = ssl._create_unverified_context()


PORT = 12345
SCRATCH_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(SCRATCH_DIR, "temp_segments")

# Global state for tracking download
download_state = {
    "status": "idle",
    "total_segments": 0,
    "completed_segments": 0,
    "speed": 0.0,
    "eta": 0.0,
    "error_message": "",
    "output_file": "",
    "log": []
}

state_lock = threading.Lock()
cancel_flag = threading.Event()
active_executor = None

def add_log(message):
    with state_lock:
        timestamp = time.strftime("[%H:%M:%S]")
        log_line = f"{timestamp} {message}"
        download_state["log"].append(log_line)
        # Keep logs at a reasonable length
        if len(download_state["log"]) > 100:
            download_state["log"].pop(0)
        print(log_line)

def parse_m3u8(playlist_url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    req = urllib.request.Request(playlist_url, headers=headers)
    with urllib.request.urlopen(req, context=ssl_context) as response:
        content = response.read().decode('utf-8')
    
    parsed_playlist = urllib.parse.urlparse(playlist_url)
    base_dir_url = playlist_url.rsplit('/', 1)[0] + '/'
    base_domain_url = f"{parsed_playlist.scheme}://{parsed_playlist.netloc}"
    
    segments = []
    lines = content.splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        
        # Resolve segment URL
        if line.startswith("http://") or line.startswith("https://"):
            segment_url = line
        elif line.startswith("/"):
            segment_url = base_domain_url + line
        else:
            segment_url = base_dir_url + line
            
        segments.append(segment_url)
    return segments

def download_segment(args):
    if cancel_flag.is_set():
        return None, False
        
    url, index, total_segments = args
    temp_path = os.path.join(TEMP_DIR, f"segment_{index:05d}.ts")
    
    if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
        return temp_path, True
        
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    max_retries = 8
    
    for attempt in range(1, max_retries + 1):
        if cancel_flag.is_set():
            return None, False
            
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30, context=ssl_context) as response:
                data = response.read()
                with open(temp_path, "wb") as f:
                    f.write(data)
            return temp_path, True
        except urllib.error.HTTPError as e:
            if attempt == max_retries:
                add_log(f"Lỗi tải đoạn {index} (HTTP {e.code}): {e.reason}")
                return temp_path, False
            if e.code in [429, 503, 504]:
                # Server too busy or rate limited, back off longer
                time.sleep(attempt * 4)
            else:
                time.sleep(attempt * 2)
        except Exception as e:
            if attempt == max_retries:
                add_log(f"Lỗi tải đoạn {index}: {e}")
                return temp_path, False
            # Backoff retry delay
            time.sleep(attempt * 2)
            
    return temp_path, False

def run_download_thread(url, output_file):
    global active_executor
    
    with state_lock:
        download_state["status"] = "fetching"
        download_state["total_segments"] = 0
        download_state["completed_segments"] = 0
        download_state["speed"] = 0.0
        download_state["eta"] = 0.0
        download_state["error_message"] = ""
        download_state["output_file"] = output_file
        download_state["log"] = []
    
    add_log(f"Bắt đầu phân tích danh sách phát: {url}")
    
    try:
        segments = parse_m3u8(url)
    except Exception as e:
        with state_lock:
            download_state["status"] = "failed"
            download_state["error_message"] = f"Không thể lấy file M3U8: {e}"
        add_log(f"Thất bại: Không thể lấy file M3U8. {e}")
        return
        
    total = len(segments)
    with state_lock:
        download_state["total_segments"] = total
        download_state["status"] = "downloading"
    
    add_log(f"Tìm thấy {total} phân đoạn để tải xuống.")
    
    # Create temp dir
    if not os.path.exists(TEMP_DIR):
        os.makedirs(TEMP_DIR, exist_ok=True)
    else:
        # Clean existing segments in temp
        for f in os.listdir(TEMP_DIR):
            if f.startswith("segment_"):
                try:
                    os.remove(os.path.join(TEMP_DIR, f))
                except:
                    pass

    download_args = [(seg_url, i, total) for i, seg_url in enumerate(segments)]
    
    completed = 0
    failed_indices = []
    start_time = time.time()
    
    max_workers = 4
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        active_executor = executor
        futures = {executor.submit(download_segment, arg): arg for arg in download_args}
        
        for future in concurrent.futures.as_completed(futures):
            if cancel_flag.is_set():
                break
                
            arg = futures[future]
            index = arg[1]
            try:
                path, success = future.result()
                if success:
                    completed += 1
                else:
                    failed_indices.append(index)
            except Exception as exc:
                add_log(f"Lỗi tải đoạn {index}: {exc}")
                failed_indices.append(index)
                
            # Update speed and ETA
            elapsed = time.time() - start_time
            speed = completed / elapsed if elapsed > 0 else 0
            eta = (total - completed) / speed if speed > 0 else 0
            
            with state_lock:
                download_state["completed_segments"] = completed
                download_state["speed"] = round(speed, 2)
                download_state["eta"] = int(eta)
                
            if completed % 25 == 0 or completed == total:
                add_log(f"Đã tải {completed}/{total} phân đoạn ({completed/total*100:.1f}%) | Tốc độ: {speed:.1f} seg/s")

    if cancel_flag.is_set():
        with state_lock:
            download_state["status"] = "cancelled"
        add_log("Tải xuống đã bị hủy bởi người dùng.")
        cleanup_temp()
        return

    # Retry failed segments
    if failed_indices:
        add_log(f"Đang tải lại {len(failed_indices)} phân đoạn bị lỗi...")
        retry_args = [download_args[i] for i in failed_indices]
        failed_indices = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            futures = {executor.submit(download_segment, arg): arg for arg in retry_args}
            for future in concurrent.futures.as_completed(futures):
                if cancel_flag.is_set():
                    break
                arg = futures[future]
                index = arg[1]
                try:
                    path, success = future.result()
                    if success:
                        completed += 1
                        with state_lock:
                            download_state["completed_segments"] = completed
                    else:
                        failed_indices.append(index)
                except Exception as exc:
                    failed_indices.append(index)
                    
        if cancel_flag.is_set():
            with state_lock:
                download_state["status"] = "cancelled"
            add_log("Tải xuống đã bị hủy bởi người dùng.")
            cleanup_temp()
            return
            
    if failed_indices:
        add_log(f"Cảnh báo: Có {len(failed_indices)} phân đoạn không tải được. Tiến hành ghép file mặc dù có thể bị gián đoạn.")

    with state_lock:
        download_state["status"] = "merging"
    add_log("Đang tiến hành ghép các phân đoạn thành video hoàn chỉnh...")
    
    try:
        # Make sure output directory exists
        out_dir = os.path.dirname(output_file)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
            
        segment_files = [f for f in os.listdir(TEMP_DIR) if f.startswith("segment_") and f.endswith(".ts")]
        segment_files.sort()
        
        with open(output_file, "wb") as outfile:
            for filename in segment_files:
                file_path = os.path.join(TEMP_DIR, filename)
                with open(file_path, "rb") as infile:
                    shutil.copyfileobj(infile, outfile)
                    
        total_size_mb = os.path.getsize(output_file) / (1024 * 1024)
        add_log(f"Hoàn thành ghép file: {output_file} ({total_size_mb:.2f} MB)")
        
        with state_lock:
            download_state["status"] = "completed"
    except Exception as e:
        with state_lock:
            download_state["status"] = "failed"
            download_state["error_message"] = f"Lỗi ghép file: {e}"
        add_log(f"Lỗi ghép file: {e}")
        
    cleanup_temp()

def cleanup_temp():
    if os.path.exists(TEMP_DIR):
        add_log("Đang dọn dẹp thư mục tạm...")
        try:
            shutil.rmtree(TEMP_DIR)
            add_log("Dọn dẹp hoàn tất.")
        except Exception as e:
            add_log(f"Lỗi dọn dẹp thư mục tạm: {e}")

class DownloaderHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress command line logging to keep terminal clean
        pass
        
    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        
        if path == "/":
            self.serve_file(os.path.join(SCRATCH_DIR, "templates", "index.html"), "text/html")
        elif path == "/static/styles.css":
            self.serve_file(os.path.join(SCRATCH_DIR, "static", "styles.css"), "text/css")
        elif path == "/static/app.js":
            self.serve_file(os.path.join(SCRATCH_DIR, "static", "app.js"), "application/javascript")
        elif path == "/api/progress":
            self.send_json_response(download_state)
        else:
            self.send_error(404, "Not Found")
            
    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        
        if path == "/api/download":
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                url = data.get("url")
                output_path = data.get("outputPath")
                
                if not url or not output_path:
                    self.send_json_response({"success": False, "error": "Thiếu URL hoặc đường dẫn xuất file"}, 400)
                    return
                
                with state_lock:
                    if download_state["status"] in ["fetching", "downloading", "merging"]:
                        self.send_json_response({"success": False, "error": "Đang có tiến trình tải xuống khác đang chạy"}, 400)
                        return
                
                # Start background download thread
                cancel_flag.clear()
                t = threading.Thread(target=run_download_thread, args=(url, output_path))
                t.daemon = True
                t.start()
                
                self.send_json_response({"success": True})
            except Exception as e:
                self.send_json_response({"success": False, "error": str(e)}, 500)
                
        elif path == "/api/cancel":
            cancel_flag.set()
            add_log("Yêu cầu hủy từ người dùng...")
            self.send_json_response({"success": True})
        else:
            self.send_error(404, "Not Found")
            
    def serve_file(self, file_path, content_type):
        if not os.path.exists(file_path):
            self.send_error(404, "File Not Found")
            return
            
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        
        with open(file_path, "rb") as f:
            self.wfile.write(f.read())
            
    def send_json_response(self, data, status_code=200):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

def main():
    # Ensure static and templates folder exist
    os.makedirs(os.path.join(SCRATCH_DIR, "templates"), exist_ok=True)
    os.makedirs(os.path.join(SCRATCH_DIR, "static"), exist_ok=True)
    
    server_address = ('', PORT)
    try:
        with socketserver.ThreadingTCPServer(server_address, DownloaderHandler) as httpd:
            print(f"==================================================")
            print(f"  M3U8 Downloader GUI is running on:")
            print(f"  --> http://localhost:{PORT}")
            print(f"==================================================")
            print("Press Ctrl+C to stop the server.")
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    except Exception as e:
        print(f"Error starting server: {e}")

if __name__ == "__main__":
    main()
