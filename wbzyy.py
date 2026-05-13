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
from pathlib import Path

# 基础配置
HOST = "0.0.0.0"
PORT = 30971
BALCON_PATH = Path("./balcon/balcon.exe").resolve()
TIMEOUT_MINUTES = 9999
timeout_timer = None
MIN_AUDIO_SIZE = 2048
SAMPLE_RATE = 44100
CHANNELS = 1

# 超时自动退出
def reset_timeout_timer():
    global timeout_timer
    if timeout_timer is not None:
        timeout_timer.cancel()
    timeout_timer = threading.Timer(TIMEOUT_MINUTES * 60, exit_server)
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
                # 过滤掉标题行：SAPI 5:、---、空行
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
        # 生成临时wav
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

        # 格式转换
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
        # 清理临时文件
        for file in [temp_wav, final_file]:
            if file and os.path.exists(file):
                try:
                    os.remove(file)
                except:
                    pass

# HTTP请求处理器
class RequestHandler(http.server.BaseHTTPRequestHandler):
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
            self.wfile.write(json.dumps(voices, ensure_ascii=False).encode("utf-8"))
            return

        # 2. /exit 退出程序
        if path == "/exit":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            def close_server():
                self.server.server_close()
                sys.exit(0)
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
            self.wfile.write(api_doc.encode("utf-8"))
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
                self.wfile.write(("无" if not err else err).encode("utf-8"))
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
                        self.wfile.write(b64_data.encode("utf-8"))
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
                        self.wfile.write(b64_data.encode("utf-8"))
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
                    self.wfile.write(b64_data.encode("utf-8"))
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
                        self.wfile.write(data)
                        return

        # 9. /?{内容} 使用第一个可用音色生成WAV（修复版）
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
                    self.wfile.write(data)
                    return
            # 如果没有可用音色返回错误
            self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("无可用音色".encode("utf-8"))
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
                    self.wfile.write(data)
                    return

        # 无匹配接口
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("接口不存在".encode("utf-8"))

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
    socketserver.TCPServer.allow_reuse_address = True
    try:
        with socketserver.TCPServer((HOST, PORT), RequestHandler) as server:
            reset_timeout_timer()
            server.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)

if __name__ == "__main__":
    main()