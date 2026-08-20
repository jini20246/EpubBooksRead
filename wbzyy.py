import http.server
import socketserver
import urllib.parse
import subprocess
import tempfile
import os
import threading
import sys
import json
import base64
import signal
import shutil
from pathlib import Path
import socket

# ===================== 端口占用处理配置项 =====================
# PORT_CONFLICT_MODE 可选值：
#  1 = 弹窗提示并退出 (Windows弹出消息框，非Windows仅控制台提示退出)
#  2 = 控制台交互，手动输入新端口
#  3 = 自动寻找随机可用端口
PORT_CONFLICT_MODE = 3
# ============================================================

# ---------------------- Windows控制台防选中冻结 ----------------------
if sys.platform == "win32":
    import ctypes
    kernel32 = ctypes.windll.kernel32
    STD_OUTPUT_HANDLE = -11
    handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
    if handle != ctypes.c_void_p(-1).value:
        mode = ctypes.c_uint()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        new_mode = mode.value & ~0x0040
        kernel32.SetConsoleMode(handle, new_mode)

# 基础配置
HOST = "0.0.0.0"
PORT = 30971
BALCON_PATH = Path("./balcon/balcon.exe").resolve()
TIMEOUT_MINUTES = 9999
timeout_timer = None
MIN_AUDIO_SIZE = 2048
SAMPLE_RATE = 44100
CHANNELS = 1
SHUTDOWN_WAIT_SEC = 3  # Ctrl+C优雅关闭最大等待秒数，超时强制退出

server_instance = None


def check_dependencies():
    """启动前校验依赖：balcon.exe 和 ffmpeg"""
    ok = True
    # 校验 balcon.exe
    if not BALCON_PATH.is_file():
        print(f"[错误] 未找到balcon.exe，路径：{BALCON_PATH}")
        ok = False
    # 校验 ffmpeg 在PATH中可用
    if shutil.which("ffmpeg") is None:
        print("[错误] 系统PATH未找到ffmpeg，请安装ffmpeg并加入环境变量")
        ok = False
    else:
        try:
            subprocess.run(["ffmpeg", "-version"],
                           capture_output=True, timeout=3)
        except Exception:
            print("[错误] ffmpeg执行校验失败")
            ok = False
    if not ok:
        print("[错误] 依赖校验失败，程序退出")
        sys.exit(1)
    print("[信息] 依赖校验通过：balcon.exe、ffmpeg")


def is_port_in_use(host, port):
    """检测端口是否被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def get_random_free_port():
    """获取系统分配的空闲端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def show_windows_msgbox(title, text):
    """Windows弹窗提示，非Windows直接跳过"""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)


def resolve_port_conflict():
    """处理端口冲突，返回最终可用端口"""
    global PORT
    if not is_port_in_use(HOST, PORT):
        return PORT

    print("[错误] 端口 {} 已被占用!".format(PORT))

    if PORT_CONFLICT_MODE == 1:
        msg = "端口 {} 已被占用，程序即将退出".format(PORT)
        show_windows_msgbox("TTS服务端口占用", msg)
        sys.exit(1)

    elif PORT_CONFLICT_MODE == 2:
        while True:
            inp = input("[信息] 请输入新端口号(1‑65535): ").strip()
            if not inp.isdigit():
                print("[错误] 端口必须是数字")
                continue
            new_p = int(inp)
            if not (1 <= new_p <= 65535):
                print("[错误] 端口范围1‑65535")
                continue
            if is_port_in_use(HOST, new_p):
                print("[错误] 端口 {} 仍然被占用，请更换".format(new_p))
                continue
            PORT = new_p
            return PORT

    elif PORT_CONFLICT_MODE == 3:
        new_p = get_random_free_port()
        PORT = new_p
        print("[信息] 自动选用随机空闲端口: {}".format(PORT))
        return PORT
    return PORT


def sigint_handler(signum, frame):
    """Ctrl+C信号处理器，解决关闭卡顿"""
    global server_instance
    print("\n[信息] 收到Ctrl+C，正在关闭服务...")
    if timeout_timer is not None:
        timeout_timer.cancel()
    if server_instance is not None:
        def stop_server():
            try:
                server_instance.shutdown()
            except Exception:
                pass
        t = threading.Thread(target=stop_server, daemon=True)
        t.start()
        t.join(timeout=SHUTDOWN_WAIT_SEC)
    print("[信息] 服务已退出")
    sys.exit(0)


# 超时自动退出
def reset_timeout_timer():
    global timeout_timer
    if timeout_timer is not None:
        timeout_timer.cancel()
    timeout_sec = TIMEOUT_MINUTES * 60
    MAX_ALLOW_TIMER_SEC = 24 * 3600 * 30
    if timeout_sec > MAX_ALLOW_TIMER_SEC:
        return
    timeout_timer = threading.Timer(timeout_sec, exit_server)
    timeout_timer.daemon = True  # 定时器线程设为守护，不阻止进程退出
    timeout_timer.start()


def exit_server():
    sys.exit(0)


# 获取文件大小
def get_file_size(file_path):
    try:
        return os.path.getsize(file_path)
    except:
        return 0


# 获取可用音色列表，纯数组返回（已过滤 SAPI 5: 等无关内容）
def get_available_voices():
    try:
        result = subprocess.run(
            [str(BALCON_PATH), "-l"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore'
        )
        voices = []
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                if line and not line.startswith("---") and not line.startswith("SAPI"):
                    voices.append(line)
        return voices
    except:
        return []


# 生成音频
def generate_audio(text, voice_name, output_format="wav"):
    temp_wav = None
    final_file = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_wav = f.name
        subprocess.run(
            [str(BALCON_PATH), "-n", voice_name, "-t", text, "-w", temp_wav, "-r", str(SAMPLE_RATE)],
            check=True,
            capture_output=True,
            timeout=10
        )
        if get_file_size(temp_wav) < MIN_AUDIO_SIZE:
            return None, "音频生成失败"
        final_file = tempfile.mktemp(suffix=f".{output_format}")
        cmd = [
            "ffmpeg", "-i", temp_wav,
            "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
            "-y", "-loglevel", "error", final_file
        ]
        if output_format == "mp3":
            cmd += ["-acodec", "libmp3lame"]
        subprocess.run(cmd, check=True, capture_output=True, timeout=10)
        with open(final_file, "rb") as f:
            return f.read(), None
    except subprocess.CalledProcessError:
        return None, "音色无效或生成失败"
    except Exception as e:
        return None, str(e)
    finally:
        for file in [temp_wav, final_file]:
            if file and os.path.exists(file):
                try:
                    os.remove(file)
                except:
                    pass


# HTTP请求处理器
class RequestHandler(http.server.BaseHTTPRequestHandler):
    def safe_write(self, data):
        try:
            self.wfile.write(data)
        except (OSError, BrokenPipeError):
            pass

    def do_GET(self):
        reset_timeout_timer()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = parsed.query
        # 1. /ky 返回纯JSON音色数组
        if path == "/ky":
            voices = get_available_voices()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.safe_write(json.dumps(voices, ensure_ascii=False).encode("utf-8"))
            return
        # 2. /exit 退出程序
        if path == "/exit":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            def close_server():
                global server_instance
                if server_instance:
                    try:
                        server_instance.shutdown()
                    except Exception:
                        pass

            threading.Thread(target=close_server, daemon=True).start()
            return
        # 3. /教程 /help /jc 返回API说明
        if path in ["/教程", "/help", "/jc"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            api_doc = """API说明
/ky                  获取可用音色列表(JSON数组)
/{音色名}?{内容}      生成WAV音频
/mp3/{音色名}?{内容}  生成MP3音频
/?{内容}              使用第一个音色生成WAV
/findq/{音色名}?{文本} 检查音色是否可用
/base64/{音色名}?{内容}  使用指定音色生成指定文本的WAV音频并返回base64编码
/base64/mp3/{音色名}?{内容}  使用指定音色生成指定文本的MP3音频并返回base64编码
/base64?{内容}        使用第一个可用音色生成指定文本的WAV音频并返回base64编码
/exit                退出服务
"""
            self.safe_write(api_doc.encode("utf-8"))
            return
        # 4. /findq/{音色名}?{测试文本} 检查音色
        if path.startswith("/findq/"):
            parts = path.strip("/").split("/", 1)
            if len(parts) >= 2:
                voice = urllib.parse.unquote(parts[1])
                text = urllib.parse.unquote(query) if query else "测试"
                _, err = generate_audio(text, voice)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.safe_write(("无" if not err else err).encode("utf-8"))
                return
        # 5. /base64/mp3/{音色名}?{内容} 返回MP3音频base64编码
        if path.startswith("/base64/mp3/"):
            parts = path.strip("/").split("/", 2)
            if len(parts) >= 3:
                voice = urllib.parse.unquote(parts[2])
                text = urllib.parse.unquote(query)
                if voice and text:
                    data, err = generate_audio(text, voice, "mp3")
                    if data:
                        b64_data = base64.b64encode(data).decode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.safe_write(b64_data.encode("utf-8"))
                        return
        # 6. /base64/{音色名}?{内容} 返回WAV音频base64编码
        if path.startswith("/base64/") and not path.startswith("/base64/mp3/"):
            parts = path.strip("/").split("/", 1)
            if len(parts) >= 2:
                voice = urllib.parse.unquote(parts[1])
                text = urllib.parse.unquote(query)
                if voice and text:
                    data, err = generate_audio(text, voice, "wav")
                    if data:
                        b64_data = base64.b64encode(data).decode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.safe_write(b64_data.encode("utf-8"))
                        return
        # 7. /base64?{内容} 使用第一个音色生成WAV并返回base64编码
        if path == "/base64" and query:
            voices = get_available_voices()
            if voices:
                text = urllib.parse.unquote(query)
                data, err = generate_audio(text, voices[0], "wav")
                if data:
                    b64_data = base64.b64encode(data).decode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.safe_write(b64_data.encode("utf-8"))
                    return
        # 8. /mp3/{音色名}?{内容} 返回MP3音频
        if path.startswith("/mp3/"):
            parts = path.strip("/").split("/", 1)
            if len(parts) >= 2:
                voice = urllib.parse.unquote(parts[1])
                text = urllib.parse.unquote(query)
                if text:
                    data, err = generate_audio(text, voice, "mp3")
                    if data:
                        self.send_response(200)
                        self.send_header("Content-Type", "audio/mpeg")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.safe_write(data)
                        return
        # 9. /?{内容} 使用第一个可用音色生成WAV
        if path == "/" and query:
            voices = get_available_voices()
            if voices:
                text = urllib.parse.unquote(query)
                data, err = generate_audio(text, voices[0], "wav")
                if data:
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.safe_write(data)
                    return
            self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.safe_write("无可用音色".encode("utf-8"))
            return
        # 10. /{音色名}?{内容} 返回WAV音频
        if path.count("/") == 1 and path != "/":
            voice = urllib.parse.unquote(path.strip("/"))
            text = urllib.parse.unquote(query)
            if voice and text:
                data, err = generate_audio(text, voice, "wav")
                if data:
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.safe_write(data)
                    return
        # 无匹配接口
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.safe_write("接口不存在".encode("utf-8"))

    # 跨域支持
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # 关闭默认日志
    def log_message(self, format, *args):
        return


# 启动服务
def main():
    global server_instance
    socketserver.TCPServer.allow_reuse_address = True
    # 注册Ctrl+C信号处理器
    signal.signal(signal.SIGINT, sigint_handler)
    check_dependencies()
    final_port = resolve_port_conflict()
    print("[信息] Balcon TTS服务正在启动，监听地址：{}:{}".format(HOST, final_port))
    server_instance = socketserver.TCPServer((HOST, final_port), RequestHandler)
    reset_timeout_timer()
    print("[信息] TTS服务已成功运行，Ctrl+C 停止服务")
    try:
        server_instance.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if timeout_timer is not None:
            timeout_timer.cancel()
        if server_instance:
            server_instance.server_close()
    print("[信息] 服务已退出")


if __name__ == "__main__":
    main()