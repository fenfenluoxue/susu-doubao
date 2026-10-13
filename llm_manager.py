"""
LLM Manager - susu-doubao 插件核心管理器
支持：进程管理、流式输出、文本补全、显存检测、自动端口、预设管理、速度统计
      自动重启、函数调用、多模态图片分析
"""
import subprocess
import threading
import time
import json
import struct
import urllib.request
import urllib.error
import os
import sys
import re
import base64
import io

# ========== 全局状态 ==========
_running_servers = {}        # port -> {process, model_path, base_url, ...}
_server_configs = {}          # port -> 启动配置（用于自动重启）
_sessions = {}                # session_id -> [messages]
_presets = {}                 # 预设缓存
_stop_flags = {}              # request_id -> bool  用于停止生成
_lock = threading.RLock()  # 可重入锁，允许同一线程嵌套加锁（如list_running_servers调用get_server_info）
_monitor_running = False
_monitor_thread = None

# 默认预设
DEFAULT_PRESETS = {
    "创意写作": {"temperature": 1.0, "top_p": 0.95, "max_tokens": 1024, "system_prompt": "你是一个富有创造力的写作助手。"},
    "精确回答": {"temperature": 0.3, "top_p": 0.9, "max_tokens": 512, "system_prompt": "你是一个精确、简洁的助手，只回答事实。"},
    "代码助手": {"temperature": 0.2, "top_p": 0.95, "max_tokens": 2048, "system_prompt": "你是一个专业的程序员助手，输出高质量代码。"},
    "画图Prompt": {"temperature": 0.7, "top_p": 0.9, "max_tokens": 300, "system_prompt": "你是一个专业的AI绘画提示词工程师，将用户描述转化为英文提示词，只输出提示词。"},
}


# ========== 工具函数 ==========
def is_port_in_use(port):
    """检查端口是否被占用"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def find_free_port(start_port=18080, max_tries=50):
    """自动找一个空闲端口"""
    for i in range(max_tries):
        port = start_port + i
        if not is_port_in_use(port):
            return port
    return start_port + max_tries


def get_gpu_memory():
    """获取 GPU 显存使用情况，返回 (已用MB, 总计MB) 或 None"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            line = result.stdout.strip().split("\n")[0]
            used, total = line.split(",")
            return int(used.strip()), int(total.strip())
    except Exception:
        pass
    return None


def count_tokens(text):
    """粗略估算 token 数（中文约1.5字/token，英文约4字符/token）"""
    if not text:
        return 0
    chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
    other = len(text) - chinese
    return int(chinese / 1.5 + other / 4)


# ========== 预设管理 ==========
def get_presets_file():
    """获取预设文件路径"""
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(plugin_dir, "presets.json")


def load_presets():
    """加载预设，合并默认预设"""
    global _presets
    presets = dict(DEFAULT_PRESETS)
    try:
        fpath = get_presets_file()
        if os.path.isfile(fpath):
            with open(fpath, "r", encoding="utf-8") as f:
                user_presets = json.load(f)
                presets.update(user_presets)
    except Exception:
        pass
    _presets = presets
    return presets


def save_preset(name, params):
    """保存自定义预设"""
    fpath = get_presets_file()
    user_presets = {}
    try:
        if os.path.isfile(fpath):
            with open(fpath, "r", encoding="utf-8") as f:
                user_presets = json.load(f)
    except Exception:
        pass
    user_presets[name] = params
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(user_presets, f, ensure_ascii=False, indent=2)
    load_presets()
    return True


def delete_preset(name):
    """删除自定义预设"""
    if name in DEFAULT_PRESETS:
        return False, "不能删除默认预设"
    fpath = get_presets_file()
    try:
        if os.path.isfile(fpath):
            with open(fpath, "r", encoding="utf-8") as f:
                user_presets = json.load(f)
            if name in user_presets:
                del user_presets[name]
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump(user_presets, f, ensure_ascii=False, indent=2)
                load_presets()
                return True, "已删除"
    except Exception as e:
        return False, str(e)
    return False, "预设不存在"


# ========== 服务管理 ==========
def wait_for_server(base_url, timeout=60, api_key="", process=None):
    """等待 server 启动完成（先检测端口，再检测 API），过程中检查进程是否存活"""
    import socket
    try:
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        port = parsed.port
        host = parsed.hostname or "127.0.0.1"
    except Exception:
        host, port = "127.0.0.1", 18080

    start = time.time()
    # 第一步：检测端口
    while time.time() - start < timeout:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                if s.connect_ex((host, port)) == 0:
                    break
        except Exception:
            pass
        time.sleep(0.5)

    # 第二步：检测 API
    while time.time() - start < timeout:
        # 如果传入了 process，检查进程是否还活着（死了就不用等了）
        if process is not None and process.poll() is not None:
            return False
        try:
            req = urllib.request.Request(f"{base_url}/v1/models")
            if api_key and api_key.strip():
                req.add_header("Authorization", f"Bearer {api_key.strip()}")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    return True
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def start_server(llama_server_path, model_path, n_gpu_layers=99, n_ctx=4096,
                 port=None, extra_args="", api_key="", auto_port=True, auto_restart=True,
                 chat_template="自动检测", custom_template="", mmproj=""):
    """
    启动 llama-server 进程
    port=None 时自动找空闲端口
    auto_restart=True 时进程崩溃自动重启
    chat_template: 聊天模板，"自动检测"时不指定，让llama.cpp自动检测
    custom_template: 自定义Jinja2模板，仅当chat_template="自定义"时生效
    mmproj: 多模态投影模型路径（视觉模型用），为空时不加载
    返回: (success, message, base_url, port)
    """
    global _running_servers, _server_configs, _monitor_running, _monitor_thread

    template_tmp_path = None  # 自定义模板临时文件路径，用于停止服务时清理

    # 自动选端口
    if port is None or auto_port:
        target_port = port or 18080
        if is_port_in_use(target_port):
            target_port = find_free_port(target_port + 1)
        port = target_port

    base_url = f"http://127.0.0.1:{port}"

    with _lock:
        # 复用已运行的同端口服务
        if port in _running_servers:
            info = _running_servers[port]
            if info["process"].poll() is None:
                return True, f"复用已有服务 (端口 {port})", base_url, port
            else:
                del _running_servers[port]

        # 检查文件
        if not os.path.isfile(llama_server_path):
            return False, f"llama-server 不存在: {llama_server_path}", base_url, port
        if not os.path.isfile(model_path):
            return False, f"模型文件不存在: {model_path}", base_url, port

        # 构建命令
        cmd = [
            llama_server_path,
            "-m", model_path,
            "-ngl", str(n_gpu_layers),
            "-c", str(n_ctx),
            "--host", "127.0.0.1",
            "--port", str(port),
        ]
        if api_key and api_key.strip():
            cmd.extend(["--api-key", api_key.strip()])
        if extra_args and extra_args.strip():
            cmd.extend(extra_args.strip().split())

        # 多模态投影模型（视觉模型）
        if mmproj and mmproj.strip():
            if os.path.isfile(mmproj.strip()):
                cmd.extend(["--mmproj", mmproj.strip()])
            else:
                return False, f"mmproj文件不存在: {mmproj}", base_url, port

        # 聊天模板
        if chat_template and chat_template != "自动检测":
            if chat_template == "自定义" and custom_template and custom_template.strip():
                # 自定义模板写入临时文件
                import tempfile
                tmp_fd, tmp_path = tempfile.mkstemp(suffix='.jinja', text=True)
                with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                    f.write(custom_template.strip())
                template_tmp_path = tmp_path
                cmd.extend(["--chat-template-file", tmp_path])
            elif chat_template != "自定义":
                cmd.extend(["--chat-template", chat_template])

        # 启动进程
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        working_dir = os.path.dirname(os.path.abspath(llama_server_path))

        # 创建临时日志文件
        import tempfile
        log_fd, log_path = tempfile.mkstemp(suffix='.log', prefix='llama-server-')
        os.close(log_fd)
        log_file = open(log_path, 'w', encoding='utf-8', errors='replace')
        
        try:
            process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                cwd=working_dir,
            )
        except Exception as e:
            log_file.close()
            return False, f"启动失败: {str(e)}", base_url, port

        # 轮询检查进程是否立即退出（最多1.5秒，每0.15秒检查一次；正常启动的进程会在第一次检查时就存活）
        crashed = False
        for _ in range(10):
            time.sleep(0.15)
            if process.poll() is not None:
                crashed = True
                break
        if crashed:
            # 进程已退出，读取日志
            log_file.close()
            # 清理自定义模板临时文件
            if template_tmp_path and os.path.isfile(template_tmp_path):
                try:
                    os.unlink(template_tmp_path)
                except Exception:
                    pass
            try:
                with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                    log_content = f.read()[-800:]
            except Exception:
                log_content = "无法读取日志"
            return False, f"进程启动后立即退出(退出码:{process.returncode})\n日志: {log_content}", base_url, port
        
        # 进程存活，把日志文件路径保存到_running_servers
        _running_servers[port] = {
            "process": process,
            "model_path": model_path,
            "base_url": base_url,
            "llama_server_path": llama_server_path,
            "n_gpu_layers": n_gpu_layers,
            "n_ctx": n_ctx,
            "api_key": api_key,
            "chat_template": chat_template,
            "custom_template": custom_template,
            "mmproj": mmproj,
            "template_tmp_path": template_tmp_path,
            "log_path": log_path,
            "log_file": log_file,
            "start_time": time.time(),
        }

    # 等待就绪
    if wait_for_server(base_url, timeout=60, api_key=api_key, process=process):
        # 保存配置用于自动重启
        if auto_restart:
            _server_configs[port] = {
                "llama_server_path": llama_server_path,
                "model_path": model_path,
                "n_gpu_layers": n_gpu_layers,
                "n_ctx": n_ctx,
                "port": port,
                "extra_args": extra_args,
                "api_key": api_key,
                "chat_template": chat_template,
                "custom_template": custom_template,
                "mmproj": mmproj,
                "auto_restart": True,
                "restart_count": 0,
            }
            # 启动监控线程（如果还没启动）
            if not _monitor_running:
                _monitor_running = True
                _monitor_thread = threading.Thread(target=_monitor_servers, daemon=True)
                _monitor_thread.start()
        return True, f"服务已启动 (端口 {port})", base_url, port
    else:
        with _lock:
            if port in _running_servers:
                info = _running_servers[port]
                try:
                    info["process"].kill()
                except Exception:
                    pass
                # 关闭日志文件句柄
                try:
                    lf = info.get("log_file")
                    if lf and not lf.closed:
                        lf.close()
                except Exception:
                    pass
                # 删除自定义模板临时文件
                try:
                    ttp = info.get("template_tmp_path")
                    if ttp and os.path.isfile(ttp):
                        os.unlink(ttp)
                except Exception:
                    pass
                del _running_servers[port]
        
        # 启动失败，读取日志提取具体错误原因
        error_detail = ""
        try:
            if log_path and os.path.isfile(log_path):
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    log_content = f.read()
                # 搜索常见错误关键词
                import re
                error_lines = []
                for line in log_content.split('\n'):
                    line_lower = line.lower()
                    if any(kw in line_lower for kw in ['error', 'failed', 'mismatch', 'exception', 'fatal', 'cannot', 'unable']):
                        # 提取错误信息（去掉时间戳前缀）
                        clean_line = re.sub(r'^\d+\.\d+\s+[EWI]\s+\w+\s+', '', line).strip()
                        if clean_line and len(clean_line) > 5:
                            error_lines.append(clean_line)
                if error_lines:
                    # 取最后几条错误，避免太长
                    error_detail = " | ".join(error_lines[-3:])
        except Exception:
            pass
        
        if error_detail:
            return False, f"启动失败: {error_detail}", base_url, port
        return False, "服务启动超时（60秒）", base_url, port


def stop_server(port):
    """停止指定端口的服务"""
    global _running_servers, _server_configs
    with _lock:
        if port in _running_servers:
            info = _running_servers[port]
            try:
                info["process"].kill()
            except Exception:
                pass
            # 关闭日志文件句柄，防止文件描述符泄漏
            try:
                lf = info.get("log_file")
                if lf and not lf.closed:
                    lf.close()
            except Exception:
                pass
            # 删除自定义模板临时文件
            try:
                ttp = info.get("template_tmp_path")
                if ttp and os.path.isfile(ttp):
                    os.unlink(ttp)
            except Exception:
                pass
            del _running_servers[port]
            if port in _server_configs:
                del _server_configs[port]  # 同时删除自动重启配置
            return True, f"已停止端口 {port} 的服务"
    return False, f"端口 {port} 没有运行中的服务"


def stop_all_servers():
    """停止本插件管理的所有服务（仅停止_running_servers中记录的进程，不影响其他程序启动的llama-server）"""
    global _running_servers, _server_configs
    count = 0
    with _lock:
        for port, info in list(_running_servers.items()):
            try:
                info["process"].kill()
                count += 1
            except Exception:
                pass
            # 关闭日志文件句柄
            try:
                lf = info.get("log_file")
                if lf and not lf.closed:
                    lf.close()
            except Exception:
                pass
            # 删除自定义模板临时文件
            try:
                ttp = info.get("template_tmp_path")
                if ttp and os.path.isfile(ttp):
                    os.unlink(ttp)
            except Exception:
                pass
        _running_servers.clear()
        _server_configs.clear()  # 清空自动重启配置，防止监控线程自动重启

    return count


def get_server_info(port):
    """获取服务信息"""
    with _lock:
        if port in _running_servers:
            info = _running_servers[port]
            running = info["process"].poll() is None
            return {
                "running": running,
                "model": os.path.basename(info["model_path"]),
                "port": port,
                "base_url": info["base_url"],
                "api_key_set": bool(info.get("api_key")),
                "uptime": int(time.time() - info["start_time"]),
            }
    return None


def list_running_servers():
    """列出所有运行中的服务"""
    result = []
    with _lock:
        for port in list(_running_servers.keys()):
            info = get_server_info(port)
            if info and info["running"]:
                result.append(info)
    return result


def get_server_log(port, lines=150):
    """读取指定端口服务的最新日志"""
    with _lock:
        if port not in _running_servers:
            return None, "该端口没有运行中的服务"
        info = _running_servers[port]
        log_path = info.get("log_path")
        if not log_path or not os.path.isfile(log_path):
            return None, "日志文件不存在"
        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                all_lines = f.readlines()
                recent = all_lines[-lines:] if len(all_lines) > lines else all_lines

                # 添加启动时间和运行时长
                start_time = info.get("start_time")
                header = ""
                if start_time:
                    from datetime import datetime
                    start_dt = datetime.fromtimestamp(start_time)
                    elapsed = int(time.time() - start_time)
                    elapsed_str = f"{elapsed//3600}时{elapsed%3600//60}分{elapsed%60}秒"
                    header = f"📅 服务启动时间: {start_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    header += f"⏱  已运行: {elapsed_str}\n"
                    header += f"📝 以下为 llama-server 原始日志（时间为相对启动秒数）\n"
                    header += "=" * 60 + "\n\n"

                # 关键信息标记
                marked_lines = []
                for line in recent:
                    marked_line = line
                    if "print_timing" in line and "t/s" in line:
                        marked_line = "⚡ " + marked_line
                    elif "model loaded" in line:
                        marked_line = "✅ " + marked_line
                    elif "listening on" in line:
                        marked_line = "🌐 " + marked_line
                    elif line.strip().startswith("W ") or "warning" in line.lower():
                        marked_line = "⚠️  " + marked_line
                    elif "error" in line.lower() and "print_timing" not in line:
                        marked_line = "❌ " + marked_line
                    marked_lines.append(marked_line)

                content = header + "".join(marked_lines)
                return True, content
        except Exception as e:
            return None, f"读取日志失败: {str(e)}"


# ========== 生成停止控制 ==========
def request_stop(request_id):
    """请求停止生成"""
    _stop_flags[request_id] = True


def is_stopped(request_id):
    """检查是否已请求停止"""
    return _stop_flags.get(request_id, False)


def clear_stop_flag(request_id):
    """清除停止标志"""
    _stop_flags.pop(request_id, None)


# 全局停止标志（用于控制台"停止当前生成"操作，影响所有正在进行的流式生成）
_global_generation_stop = False


def stop_all_generations():
    """请求停止所有正在进行的生成（全局）"""
    global _global_generation_stop
    _global_generation_stop = True
    # 同时设置所有已知的按请求停止标志
    for rid in list(_stop_flags.keys()):
        _stop_flags[rid] = True
    return True


def is_global_stop():
    """检查是否已请求全局停止"""
    return _global_generation_stop


def reset_global_stop():
    """重置全局停止标志（每次新生成开始时调用）"""
    global _global_generation_stop
    _global_generation_stop = False


# ========== API 调用 ==========
def _build_headers(api_key=""):
    headers = {"Content-Type": "application/json"}
    if api_key and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    return headers


def chat_completion(base_url, messages, temperature=0.7, top_p=0.9,
                     max_tokens=512, top_k=40, model="local", api_key="", request_id=None,
                     repeat_penalty=1.1, seed=None, min_p=None):
    """
    非流式聊天补全
    返回: (success, content, error, stats)
    """
    url = f"{base_url}/v1/chat/completions"
    payload = {
        "model": model, "messages": messages,
        "temperature": temperature, "top_p": top_p,
        "max_tokens": max_tokens, "top_k": top_k, "stream": False,
        "repeat_penalty": repeat_penalty,
    }
    if seed is not None:
        payload["seed"] = seed
    if min_p is not None:
        payload["min_p"] = min_p
    start_time = time.time()
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=_build_headers(api_key), method="POST")
        with urllib.request.urlopen(req, timeout=600) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            elapsed = time.time() - start_time
            message = result["choices"][0]["message"]
            content = message.get("content", "") or ""
            reasoning = message.get("reasoning_content", "") or ""
            if not content.strip() and reasoning.strip():
                content = reasoning
            elif content.strip() and reasoning.strip():
                content = f"【思考过程】\n{reasoning}\n\n【回答】\n{content}"
            # 统计
            usage = result.get("usage", {})
            completion_tokens = usage.get("completion_tokens", count_tokens(content))
            stats = {
                "elapsed": round(elapsed, 2),
                "tokens": completion_tokens,
                "tokens_per_sec": round(completion_tokens / elapsed, 1) if elapsed > 0 else 0,
                "prompt_tokens": usage.get("prompt_tokens", 0),
            }
            return True, content, "", stats
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = str(e)
        return False, "", f"HTTP {e.code}: {err_body}", {}
    except Exception as e:
        return False, "", str(e), {}


def chat_completion_stream(base_url, messages, temperature=0.7, top_p=0.9,
                           max_tokens=512, top_k=40, model="local", api_key="", request_id=None,
                           repeat_penalty=1.1, seed=None, min_p=None):
    """
    流式聊天补全（生成器，yield 每个 chunk）
    yield: (type, data)
      type='delta' -> 增量文本
      type='done' -> 完成，附带 stats
      type='error' -> 错误
    """
    url = f"{base_url}/v1/chat/completions"
    payload = {
        "model": model, "messages": messages,
        "temperature": temperature, "top_p": top_p,
        "max_tokens": max_tokens, "top_k": top_k, "stream": True,
        "repeat_penalty": repeat_penalty,
    }
    if seed is not None:
        payload["seed"] = seed
    if min_p is not None:
        payload["min_p"] = min_p
    start_time = time.time()
    full_content = ""
    full_reasoning = ""
    token_count = 0

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=_build_headers(api_key), method="POST")
        with urllib.request.urlopen(req, timeout=600) as resp:
            for line in resp:
                # 检查停止请求
                if request_id and is_stopped(request_id):
                    yield "stopped", full_content
                    return
                line = line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    reasoning = delta.get("reasoning_content", "")
                    if content:
                        full_content += content
                        token_count += 1
                        yield "delta", content
                    if reasoning:
                        full_reasoning += reasoning
                except Exception:
                    continue
    except Exception as e:
        yield "error", str(e)
        return

    elapsed = time.time() - start_time
    # 思考模型兼容
    if not full_content.strip() and full_reasoning.strip():
        full_content = full_reasoning
    elif full_content.strip() and full_reasoning.strip():
        full_content = f"【思考过程】\n{full_reasoning}\n\n【回答】\n{full_content}"

    stats = {
        "elapsed": round(elapsed, 2),
        "tokens": token_count,
        "tokens_per_sec": round(token_count / elapsed, 1) if elapsed > 0 else 0,
    }
    yield "done", {"content": full_content, "stats": stats}


def text_completion(base_url, prompt, temperature=0.7, top_p=0.9,
                    max_tokens=512, top_k=40, model="local", api_key="",
                    repeat_penalty=1.1, seed=None, min_p=None):
    """
    文本补全模式（/v1/completions），适合续写和生成 prompt
    返回: (success, text, error, stats)
    """
    url = f"{base_url}/v1/completions"
    payload = {
        "model": model, "prompt": prompt,
        "temperature": temperature, "top_p": top_p,
        "max_tokens": max_tokens, "top_k": top_k, "stream": False,
        "repeat_penalty": repeat_penalty,
    }
    if seed is not None:
        payload["seed"] = seed
    if min_p is not None:
        payload["min_p"] = min_p
    start_time = time.time()
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=_build_headers(api_key), method="POST")
        with urllib.request.urlopen(req, timeout=600) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            elapsed = time.time() - start_time
            text = result["choices"][0].get("text", "")
            usage = result.get("usage", {})
            completion_tokens = usage.get("completion_tokens", count_tokens(text))
            stats = {
                "elapsed": round(elapsed, 2),
                "tokens": completion_tokens,
                "tokens_per_sec": round(completion_tokens / elapsed, 1) if elapsed > 0 else 0,
            }
            return True, text, "", stats
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = str(e)
        return False, "", f"HTTP {e.code}: {err_body}", {}
    except Exception as e:
        return False, "", str(e), {}


# ========== 会话管理 ==========
def get_session_history(session_id):
    if not session_id:
        return []
    return _sessions.get(session_id, [])


def append_to_session(session_id, role, content):
    if not session_id:
        return
    if session_id not in _sessions:
        _sessions[session_id] = []
    _sessions[session_id].append({"role": role, "content": content})


def clear_session(session_id):
    if session_id in _sessions:
        del _sessions[session_id]


def list_sessions():
    return list(_sessions.keys())


# ========== 自动重启监控 ==========
def _monitor_servers():
    """后台监控线程，检测崩溃的进程并自动重启
    三阶段锁设计：
      阶段1（锁内）：检测崩溃端口，快照配置，清理旧进程和资源
      阶段2（锁外）：Popen启动进程、创建临时文件、短轮询检测崩溃（耗时操作不持锁）
      阶段3（锁内）：竞态检查端口未被占用，存入_running_servers；被占用则清理自己的进程
    """
    global _monitor_running
    while _monitor_running:
        try:
            time.sleep(5)

            # ===== 阶段1：锁内检测崩溃端口并清理旧资源 =====
            restart_tasks = []  # [(port, config_shallow_copy), ...]
            with _lock:
                for port, config in list(_server_configs.items()):
                    if not config.get("auto_restart"):
                        continue
                    # 检查进程是否还在运行
                    server_info = _running_servers.get(port)
                    if server_info:
                        process = server_info.get("process")
                        if process and process.poll() is None:
                            continue  # 进程正常运行
                    # 进程崩溃了，需要重启
                    if config.get("restart_count", 0) < 5:  # 最多重启5次
                        config["restart_count"] = config.get("restart_count", 0) + 1
                        restart_tasks.append((port, dict(config)))  # 快照配置
                        # 清理旧进程和资源（在锁内操作_running_servers）
                        if port in _running_servers:
                            old_info = _running_servers[port]
                            try:
                                old_info["process"].kill()
                            except Exception:
                                pass
                            try:
                                lf = old_info.get("log_file")
                                if lf and not lf.closed:
                                    lf.close()
                            except Exception:
                                pass
                            old_template_tmp = old_info.get("template_tmp_path")
                            if old_template_tmp and os.path.isfile(old_template_tmp):
                                try:
                                    os.unlink(old_template_tmp)
                                except Exception:
                                    pass
                            del _running_servers[port]

            # ===== 阶段2：锁外执行耗时操作（Popen、文件创建、崩溃检测）=====
            for port, config in restart_tasks:
                template_tmp_path = None
                log_file = None
                log_path = None
                process = None
                ct = config.get("chat_template", "自动检测")
                try:
                    import tempfile
                    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                    working_dir = os.path.dirname(os.path.abspath(config["llama_server_path"]))
                    cmd = [
                        config["llama_server_path"],
                        "-m", config["model_path"],
                        "-ngl", str(config["n_gpu_layers"]),
                        "-c", str(config["n_ctx"]),
                        "--host", "127.0.0.1",
                        "--port", str(port),
                    ]
                    if config.get("api_key"):
                        cmd.extend(["--api-key", config["api_key"]])
                    if config.get("extra_args"):
                        cmd.extend(config["extra_args"].strip().split())

                    # 多模态投影模型（视觉模型）
                    if config.get("mmproj") and os.path.isfile(config["mmproj"]):
                        cmd.extend(["--mmproj", config["mmproj"]])

                    # 聊天模板
                    if ct and ct != "自动检测":
                        if ct == "自定义" and config.get("custom_template"):
                            tmp_fd, tmp_path = tempfile.mkstemp(suffix='.jinja', text=True)
                            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                                f.write(config["custom_template"].strip())
                            template_tmp_path = tmp_path
                            cmd.extend(["--chat-template-file", tmp_path])
                        elif ct != "自定义":
                            cmd.extend(["--chat-template", ct])

                    # 创建日志文件
                    log_fd, log_path = tempfile.mkstemp(suffix='.log', prefix='llama-server-')
                    os.close(log_fd)
                    log_file = open(log_path, 'w', encoding='utf-8', errors='replace')

                    process = subprocess.Popen(
                        cmd, stdout=log_file, stderr=subprocess.STDOUT,
                        creationflags=creationflags, cwd=working_dir,
                    )

                    # 短轮询检测进程是否立即崩溃（最多1.5秒，每0.15秒检查一次）
                    crashed = False
                    for _ in range(10):
                        time.sleep(0.15)
                        if process.poll() is not None:
                            crashed = True
                            break

                    if crashed:
                        # 进程立即崩溃，清理资源，不存入_running_servers
                        try:
                            log_file.close()
                        except Exception:
                            pass
                        if template_tmp_path and os.path.isfile(template_tmp_path):
                            try:
                                os.unlink(template_tmp_path)
                            except Exception:
                                pass
                        continue

                    # ===== 阶段3：锁内竞态检查并存入 =====
                    with _lock:
                        if port in _running_servers:
                            # 其他线程已先在该端口启动了服务，kill我们的进程并清理
                            try:
                                process.kill()
                            except Exception:
                                pass
                            try:
                                log_file.close()
                            except Exception:
                                pass
                            if template_tmp_path and os.path.isfile(template_tmp_path):
                                try:
                                    os.unlink(template_tmp_path)
                                except Exception:
                                    pass
                        else:
                            _running_servers[port] = {
                                "process": process,
                                "model_path": config["model_path"],
                                "base_url": f"http://127.0.0.1:{port}",
                                "llama_server_path": config["llama_server_path"],
                                "n_gpu_layers": config["n_gpu_layers"],
                                "n_ctx": config["n_ctx"],
                                "api_key": config.get("api_key", ""),
                                "chat_template": ct,
                                "custom_template": config.get("custom_template", ""),
                                "mmproj": config.get("mmproj", ""),
                                "template_tmp_path": template_tmp_path,
                                "log_path": log_path,
                                "log_file": log_file,
                                "start_time": time.time(),
                            }
                except Exception:
                    # 启动异常，清理所有资源
                    try:
                        if process:
                            process.kill()
                    except Exception:
                        pass
                    try:
                        if log_file:
                            log_file.close()
                    except Exception:
                        pass
                    if template_tmp_path and os.path.isfile(template_tmp_path):
                        try:
                            os.unlink(template_tmp_path)
                        except Exception:
                            pass
        except Exception:
            pass


def disable_auto_restart(port):
    """禁用指定端口的自动重启"""
    with _lock:
        if port in _server_configs:
            _server_configs[port]["auto_restart"] = False
            return True
    return False


def get_restart_count(port):
    """获取重启次数"""
    with _lock:
        if port in _server_configs:
            return _server_configs[port].get("restart_count", 0)
    return 0


# ========== 函数调用（工具调用）==========
def chat_completion_with_tools(base_url, messages, tools, temperature=0.7, top_p=0.9,
                                max_tokens=1024, top_k=40, model="local", api_key="", tool_choice="auto"):
    """
    支持函数调用的聊天补全
    tools: 工具定义列表（OpenAI 格式）
    tool_choice: "auto" / "none" / {"type": "function", "function": {"name": "xxx"}}
    返回: (success, content, tool_calls, error, stats)
      tool_calls: [{"id": "...", "name": "...", "arguments": {...}}]
    """
    url = f"{base_url}/v1/chat/completions"
    payload = {
        "model": model, "messages": messages,
        "temperature": temperature, "top_p": top_p,
        "max_tokens": max_tokens, "top_k": top_k, "stream": False,
        "tools": tools, "tool_choice": tool_choice,
    }
    start_time = time.time()
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=_build_headers(api_key), method="POST")
        with urllib.request.urlopen(req, timeout=600) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            elapsed = time.time() - start_time
            message = result["choices"][0]["message"]
            content = message.get("content", "") or ""
            # 解析工具调用
            tool_calls = []
            if "tool_calls" in message and message["tool_calls"]:
                for tc in message["tool_calls"]:
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except Exception:
                        args = tc["function"]["arguments"]
                    tool_calls.append({
                        "id": tc.get("id", ""),
                        "name": tc["function"]["name"],
                        "arguments": args,
                    })
            usage = result.get("usage", {})
            stats = {
                "elapsed": round(elapsed, 2),
                "completion_tokens": usage.get("completion_tokens", 0),
                "tokens_per_sec": round(usage.get("completion_tokens", 0) / elapsed, 1) if elapsed > 0 else 0,
            }
            return True, content, tool_calls, "", stats
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = str(e)
        return False, "", [], f"HTTP {e.code}: {err_body}", {}
    except Exception as e:
        return False, "", [], str(e), {}


# ========== 多模态（图片分析）==========
def encode_image_to_base64(image_input):
    """
    将图片编码为 base64
    image_input: 可以是 numpy array (ComfyUI IMAGE 格式)、文件路径、或 PIL Image
    返回: data URL 字符串 (data:image/png;base64,...)
    """
    try:
        import numpy as np
    except ImportError:
        np = None

    # numpy array (ComfyUI IMAGE 格式: [B, H, W, C], float32 0-1)
    if np is not None and isinstance(image_input, np.ndarray):
        try:
            from PIL import Image
            # 取第一张图
            img_array = image_input[0] if image_input.ndim == 4 else image_input
            img_array = (img_array * 255).astype(np.uint8)
            img = Image.fromarray(img_array)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            return f"data:image/png;base64,{b64}"
        except Exception as e:
            raise ValueError(f"numpy 图片编码失败: {e}")

    # 文件路径
    if isinstance(image_input, str) and os.path.isfile(image_input):
        with open(image_input, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        ext = os.path.splitext(image_input)[1].lower().lstrip(".")
        if ext == "jpg":
            ext = "jpeg"
        return f"data:image/{ext};base64,{b64}"

    # PIL Image
    try:
        from PIL import Image
        if isinstance(image_input, Image.Image):
            buf = io.BytesIO()
            image_input.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            return f"data:image/png;base64,{b64}"
    except Exception:
        pass

    raise ValueError("不支持的图片输入格式")


def chat_completion_with_image(base_url, messages, image, temperature=0.7, top_p=0.9,
                                max_tokens=1024, top_k=40, model="local", api_key=""):
    """
    多模态聊天（图片分析）
    image: 图片（numpy array / 文件路径 / PIL Image）
    messages: 消息列表（最后一条用户消息会被替换为带图片的格式）
    返回: (success, content, error, stats)
    """
    url = f"{base_url}/v1/chat/completions"

    # 编码图片
    try:
        image_data_url = encode_image_to_base64(image)
    except Exception as e:
        return False, "", f"图片编码失败: {e}", {}

    # 构建带图片的消息
    multimodal_messages = []
    for i, msg in enumerate(messages):
        if msg["role"] == "user" and i == len(messages) - 1:
            # 最后一条用户消息带上图片
            multimodal_messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": msg["content"]},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            })
        else:
            multimodal_messages.append(msg)

    payload = {
        "model": model, "messages": multimodal_messages,
        "temperature": temperature, "top_p": top_p,
        "max_tokens": max_tokens, "top_k": top_k, "stream": False,
    }
    start_time = time.time()
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=_build_headers(api_key), method="POST")
        with urllib.request.urlopen(req, timeout=600) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            elapsed = time.time() - start_time
            content = result["choices"][0]["message"].get("content", "") or ""
            usage = result.get("usage", {})
            stats = {
                "elapsed": round(elapsed, 2),
                "completion_tokens": usage.get("completion_tokens", 0),
                "tokens_per_sec": round(usage.get("completion_tokens", 0) / elapsed, 1) if elapsed > 0 else 0,
            }
            return True, content, "", stats
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = str(e)
        return False, "", f"HTTP {e.code}: {err_body}", {}
    except Exception as e:
        return False, "", str(e), {}


def read_gguf_chat_template(model_path):
    """
    从GGUF文件中读取内置的聊天模板（tokenizer.chat_template）
    GGUF metadata 部分为紧密打包，无字节对齐（padding），直接顺序读取即可。
    返回: (成功, 模板内容或错误信息)
    """
    import struct
    try:
        with open(model_path, 'rb') as f:
            magic = f.read(4)
            if magic != b'GGUF':
                return False, "不是有效的GGUF文件"

            version = struct.unpack('<I', f.read(4))[0]
            tensor_count = struct.unpack('<Q', f.read(8))[0]
            metadata_count = struct.unpack('<Q', f.read(8))[0]

            def read_string():
                """读取GGUF字符串：length(uint64) + data，无对齐"""
                length = struct.unpack('<Q', f.read(8))[0]
                if length > 50 * 1024 * 1024:
                    raise ValueError(f"字符串长度异常: {length}")
                return f.read(length).decode('utf-8', errors='replace')

            def skip_value(value_type):
                """快速跳过不需要的值（不解码字符串），用于遍历非目标entry"""
                if value_type == 9:  # ARRAY
                    elem_type = struct.unpack('<I', f.read(4))[0]
                    array_len = struct.unpack('<Q', f.read(8))[0]
                    for _ in range(array_len):
                        skip_value(elem_type)
                elif value_type == 8:  # STRING
                    length = struct.unpack('<Q', f.read(8))[0]
                    f.read(length)
                elif value_type in (0, 1, 7):  # uint8, int8, bool
                    f.read(1)
                elif value_type in (2, 3):  # uint16, int16
                    f.read(2)
                elif value_type in (4, 5, 6):  # uint32, int32, float32
                    f.read(4)
                elif value_type in (10, 11, 12):  # uint64, int64, float64
                    f.read(8)

            def read_value(value_type):
                """读取值并返回内容（仅用于目标entry）"""
                if value_type == 9:  # ARRAY
                    elem_type = struct.unpack('<I', f.read(4))[0]
                    array_len = struct.unpack('<Q', f.read(8))[0]
                    result = []
                    for _ in range(array_len):
                        result.append(read_value(elem_type))
                    return result
                if value_type == 8:  # STRING
                    return read_string()
                if value_type == 0: return struct.unpack('<B', f.read(1))[0]
                if value_type == 1: return struct.unpack('<b', f.read(1))[0]
                if value_type == 2: return struct.unpack('<H', f.read(2))[0]
                if value_type == 3: return struct.unpack('<h', f.read(2))[0]
                if value_type == 4: return struct.unpack('<I', f.read(4))[0]
                if value_type == 5: return struct.unpack('<i', f.read(4))[0]
                if value_type == 6: return struct.unpack('<f', f.read(4))[0]
                if value_type == 7: return struct.unpack('<B', f.read(1))[0] != 0
                if value_type == 10: return struct.unpack('<Q', f.read(8))[0]
                if value_type == 11: return struct.unpack('<q', f.read(8))[0]
                if value_type == 12: return struct.unpack('<d', f.read(8))[0]
                return None

            for _ in range(metadata_count):
                # key: string（无对齐，直接读）
                key = read_string()
                # value_type: uint32（无对齐，直接读）
                value_type = struct.unpack('<I', f.read(4))[0]

                if key == 'tokenizer.chat_template':
                    value = read_value(value_type)
                    if isinstance(value, list):
                        template = value[0] if value else ""
                    else:
                        template = str(value)
                    return True, template
                else:
                    # 非目标entry，快速跳过
                    skip_value(value_type)

            return False, "模型中没有找到内置聊天模板"

    except Exception as e:
        return False, f"读取GGUF文件失败: {e}"