import sys
import os
import shutil
import subprocess
import traceback
from tkinter import Tk, messagebox

# ========== 可修改的配置变量 ==========
# 目标目录路径（相对于程序所在目录）
TARGET_DIR_RELATIVE = r"resources"
# 目标文件名
TARGET_FILENAME = "index.ajmf"
# 要启动的可执行文件路径（相对于程序所在目录）
EXECUTABLE_PATH_RELATIVE = r".\index.exe"
# 是否强制要求启动参数（True: 要求，False: 不要求）
REQUIRE_STARTUP_ARG = False
# 是否允许目标程序多开（True: 允许，False: 不允许）
ALLOW_MULTIPLE_INSTANCES = False
# ===================================

def get_absolute_path(relative_path):
    """将相对路径转换为绝对路径（基于程序所在目录）"""
    # 获取程序所在目录
    if getattr(sys, 'frozen', False):
        # 如果程序被打包成exe
        base_dir = os.path.dirname(sys.executable)
    else:
        # 如果程序是脚本
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 构建绝对路径
    absolute_path = os.path.join(base_dir, relative_path)
    
    # 规范化路径（清理..和.等）
    return os.path.normpath(absolute_path)

def show_error_message(title, message):
    """显示错误消息框"""
    try:
        # 创建隐藏的根窗口
        root = Tk()
        root.withdraw()  # 隐藏主窗口
        messagebox.showerror(title, message)
        root.destroy()  # 销毁窗口
    except Exception as e:
        # 如果tkinter不可用，则输出到控制台
        print(f"错误对话框显示失败: {e}")
        print(f"{title}: {message}")

def show_info_message(title, message):
    """显示信息消息框"""
    try:
        root = Tk()
        root.withdraw()
        messagebox.showinfo(title, message)
        root.destroy()
    except Exception:
        print(f"{title}: {message}")

def is_process_running(process_name_or_path):
    """检查指定进程是否正在运行"""
    try:
        # 逻辑倒置修正：禁止多开时才检查进程
        if not ALLOW_MULTIPLE_INSTANCES:
            if os.name == 'nt':  # Windows系统
                process_name = os.path.basename(process_name_or_path)
                # 避免shell=True的安全风险，改用subprocess.Popen
                result = subprocess.run(
                    ['tasklist', '/FI', f'IMAGENAME eq {process_name}'],
                    capture_output=True, text=True, encoding='gbk',  # 适配Windows中文编码
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                return process_name in result.stdout
            else:
                print('非Windows系统，无法检查进程')
                return False
        else:
            print('允许多开，跳过进程检查')
            return False
    except subprocess.CalledProcessError as e:
        print(f"进程检查命令执行失败: {e}")
        return False
    except Exception as e:
        print(f"检查进程状态时出错: {e}")
        return False
def copy_file_with_retry(source_file, target_file, retries=3):
    """复制文件并尝试多次重试"""
    for attempt in range(retries):
        try:
            # 确保目标目录存在
            target_dir = os.path.dirname(target_file)
            os.makedirs(target_dir, exist_ok=True)
            print(f"目标目录已创建/确认: {target_dir}")
            
            # 检查源文件是否存在
            if not os.path.exists(source_file):
                print(f"源文件不存在: {source_file}")
                return False
            
            # 先尝试删除目标文件（如果存在）
            if os.path.exists(target_file):
                try:
                    os.remove(target_file)
                    print(f"已删除旧文件: {target_file}")
                except PermissionError:
                    print(f"无法删除文件，可能被占用: {target_file}")
                    # 尝试重命名后删除
                    try:
                        temp_name = target_file + ".old"
                        os.rename(target_file, temp_name)
                        os.remove(temp_name)
                        print(f"已通过重命名删除旧文件")
                    except Exception as e:
                        print(f"重命名删除也失败: {e}")
                        # 继续尝试覆盖
                except Exception as e:
                    print(f"删除旧文件失败: {e}")
            
            # 复制文件
            shutil.copy2(source_file, target_file)
            print(f"文件复制成功: {source_file} -> {target_file}")
            
            # 验证文件是否复制成功
            if os.path.exists(target_file):
                # 检查文件大小
                source_size = os.path.getsize(source_file)
                target_size = os.path.getsize(target_file)
                
                if source_size == target_size:
                    print(f"文件复制验证通过 ({source_size} 字节)")
                    return True
                else:
                    print(f"文件大小不匹配: 源文件={source_size} 字节, 目标文件={target_size} 字节")
                    # 继续重试
            else:
                print(f"目标文件不存在: {target_file}")
                # 继续重试
            
        except Exception as e:
            print(f"复制文件失败 (尝试 {attempt + 1}/{retries}): {e}")
            import traceback
            print(traceback.format_exc())
            
            # 如果不是最后一次尝试，等待一下再重试
            if attempt < retries - 1:
                import time
                time.sleep(0.5)  # 等待0.5秒再重试
    
    return False

def main():
    try:
        # 获取基于程序所在目录的绝对路径
        target_dir = get_absolute_path(TARGET_DIR_RELATIVE)
        executable_path = get_absolute_path(EXECUTABLE_PATH_RELATIVE)
        
        # 调试信息
        print("=" * 50)
        print(f"程序路径: {sys.executable if getattr(sys, 'frozen', False) else __file__}")
        print(f"当前工作目录: {os.getcwd()}")
        print(f"程序所在目录: {get_absolute_path('.')}")
        print(f"目标目录: {target_dir}")
        print(f"可执行文件路径: {executable_path}")
        print(f"启动参数: {sys.argv[1:] if len(sys.argv) > 1 else '无'}")
        print("=" * 50)
        
        # 确定是否需要处理文件复制
        need_copy_file = False
        source_file = None
        
        if len(sys.argv) >= 2:
            # 有启动参数
            source_file = sys.argv[1]
            need_copy_file = True
            print(f"检测到启动参数，将处理文件复制: {source_file}")
        elif REQUIRE_STARTUP_ARG:
            # 需要启动参数但没有提供
            show_error_message("错误", "请提供一个文件路径作为启动参数（拖放文件到程序上）")
            sys.exit(1)
        else:
            # 不需要启动参数且没有提供
            print("未提供启动参数，直接启动程序")
        
        # 如果需要复制文件，检查源文件是否存在
        if need_copy_file:
            if not os.path.exists(source_file):
                show_error_message("错误", f"文件不存在: {source_file}")
                sys.exit(1)
            
            # 构建目标文件完整路径
            target_file = os.path.join(target_dir, TARGET_FILENAME)
            print(f"源文件: {source_file}")
            print(f"目标文件: {target_file}")
            
            # 检查目标文件是否已存在
            if os.path.exists(target_file):
                try:
                    target_stats = os.stat(target_file)
                    print(f"目标文件已存在: 大小={target_stats.st_size} 字节")
                except:
                    print(f"目标文件已存在，但无法获取详细信息")
            
            # 复制文件到目标位置
            if not copy_file_with_retry(source_file, target_file):
                show_error_message("错误", f"复制文件失败: {source_file} -> {target_file}")
                sys.exit(1)
            
            print(f"已成功复制文件到: {target_file}")
        else:
            print("跳过文件复制步骤")
        
        # 检查是否允许多开
        if not ALLOW_MULTIPLE_INSTANCES:
            # 获取目标程序名（包含完整路径）
            target_process_path = executable_path
            
            # 检查目标程序是否已在运行
            if is_process_running(target_process_path):
                print(f"目标程序已在运行: {target_process_path}")
                
                if need_copy_file:
                    # 已经复制了文件，可以退出
                    show_info_message("完成", f"文件已复制到: {target_file}\n程序已在运行中")
                    sys.exit(0)
                else:
                    # 没有复制文件，只是启动程序，但程序已在运行
                    show_info_message("提示", "程序已在运行中")
                    sys.exit(0)
        
        # 检查可执行文件是否存在
        if not os.path.exists(executable_path):
            show_error_message("错误", f"可执行文件不存在: {executable_path}")
            sys.exit(1)
        
        # 启动可执行文件
        print(f"启动程序: {executable_path}")
        try:
            # 根据是否需要复制文件决定启动方式
            if need_copy_file:
                # 文件已复制，启动程序
                subprocess.Popen([executable_path])
                print(f"已启动: {executable_path}")
            else:
                # 直接启动程序
                subprocess.Popen([executable_path])
                print(f"已启动: {executable_path}")
        except Exception as e:
            show_error_message("错误", f"启动程序失败: {e}")
            sys.exit(1)
        
    except Exception as e:
        # 捕获所有异常并显示错误信息
        error_msg = f"程序执行出错: {str(e)}\n\n详细错误信息:\n{traceback.format_exc()}"
        show_error_message("程序错误", error_msg)
        sys.exit(1)

if __name__ == "__main__":
    main()