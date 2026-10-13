"""
susu-doubao - ComfyUI LLM 插件
整合 llama.cpp 本地模型加载、聊天、管理功能
节点：
- LLM 模型控制台: 启动/停止模型服务、模板设置、预设和会话管理
- LLM 聊天: 统一聊天/补全/多模态/工具调用节点
"""

import os
import sys
import json
import folder_paths
from server import PromptServer
from aiohttp import web

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    import urllib.request
    import urllib.error

# 全局参数：控制台设置的参数，聊天节点自动继承
_global_llm_params = {
    "temperature": 0.7,
    "top_p": 0.9,
    "top_k": 40,
    "max_tokens": 1024,
    "system_prompt": "",
}

# 全局变量：记录当前已启动的模型，避免重复启动
_current_running_model = None

from .llm_manager import (
    start_server, stop_server, stop_all_servers,
    chat_completion, chat_completion_stream, text_completion,
    chat_completion_with_tools, chat_completion_with_image,
    get_gpu_memory, list_running_servers, get_server_info, get_server_log,
    load_presets, save_preset, delete_preset,
    request_stop, is_stopped, clear_stop_flag,
    stop_all_generations, is_global_stop, reset_global_stop,
    get_restart_count, disable_auto_restart,
    read_gguf_chat_template,
)

# 插件目录
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LLAMA_SERVER = os.path.join(_PLUGIN_DIR, "bin", "llama-server.exe")
# 额外的模型扫描目录（可自行修改为你的模型存放路径，也可以留空只扫描 ComfyUI/models/LLM）
DEFAULT_MODEL_DIR = os.path.join(os.path.expanduser("~"), "llama.cpp", "models")

# 声明 web 目录，ComfyUI 会自动加载里面的 JavaScript 文件
WEB_DIRECTORY = "./web"

# 注册 ComfyUI 模型目录
if not hasattr(folder_paths, "folder_names_and_paths"):
    folder_paths.folder_names_and_paths = {}
if "LLM" not in folder_paths.folder_names_and_paths:
    llm_dir = os.path.join(folder_paths.models_dir, "LLM")
    os.makedirs(llm_dir, exist_ok=True)
    folder_paths.folder_names_and_paths["LLM"] = ([llm_dir, DEFAULT_MODEL_DIR], [".gguf"])


# 模型列表（插件加载时扫描一次，之后不重复扫描）
_model_list = None

def scan_gguf_models():
    """扫描所有 GGUF 模型，返回路径字符串列表"""
    """扫描所有 GGUF 模型，返回 (显示名, 完整路径) 元组列表（带缓存）"""
    global _model_list
    # 已经扫描过，直接返回
    if _model_list is not None:
        return _model_list
    
    models = set()
    search_dirs = [DEFAULT_MODEL_DIR]
    if "LLM" in folder_paths.folder_names_and_paths:
        search_dirs.extend(folder_paths.folder_names_and_paths["LLM"][0])
    search_dirs = list(dict.fromkeys(search_dirs))

    for base_dir in search_dirs:
        if not os.path.isdir(base_dir):
            continue
        for root, dirs, files in os.walk(base_dir):
            for f in files:
                if f.lower().endswith(".gguf"):
                    full_path = os.path.join(root, f)
                    models.add(full_path)
    sorted_models = sorted(models, key=lambda x: os.path.basename(x).lower())
    # 返回字符串列表（完整路径），ComfyUI下拉框兼容
    _model_list = sorted_models
    return _model_list
# ============================================================
# 节点1: LLM 模型控制台（合并加载器 + 管理器）
# ============================================================
def _console_ret(api_url, result):
    """统一控制台节点返回格式：result仅API地址作为输出端口，ui同时包含API地址和结果文本供前端显示"""
    return {"result": (api_url,), "ui": {"0": [api_url], "text": [result]}}


# ============================================================
# LLM 模型控制台节点
# ============================================================
class LLMModelConsole:
    """LLM 模型控制台：启动/停止模型、模板设置、预设和会话管理"""
    
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        models = scan_gguf_models()
        model_list = models if models else ["未找到模型，请检查模型目录"]
        
        return {
            "required": {
                "操作": ([
                    "🚀 加载并启动模型",
                    "⏹ 停止指定端口",
                    "⏹⏹ 停止全部服务",
                    "⏹ 停止当前生成",
                    "📋 列出运行中服务",
                    "📊 查看运行日志",
                    "🔄 刷新模型列表",
                    "📄 读取内置模板",
                    "💾 预设: 列出所有",
                    "📂 预设: 加载",
                    "💾 预设: 保存",
                    "🗑 预设: 删除",
                ],),
            },
            "optional": {
                "模型路径": (model_list, {"tooltip": "选择本地GGUF模型文件（'加载并启动模型'和'读取内置模板'需要，其他操作可留空）"}),
                "mmproj路径": ("STRING", {"default": "", "multiline": False, "placeholder": "留空自动检测 | 填文件名/路径加载 | 填无/none禁用", "tooltip": "多模态投影模型（mmproj）路径，用于支持图片输入的视觉模型。留空=自动检测模型同目录下的mmproj文件；填文件名或完整路径=加载指定mmproj；填'无'/'none'/'禁用'=不加载视觉模型也不自动检测"}),
                "操作参数": ("STRING", {"default": "", "placeholder": "停止服务填端口号 | 保存/加载/删除预设填名称", "tooltip": "根据操作填写：停止端口填端口号，保存/加载/删除预设填名称，其他操作留空"}),
                "GPU层数": ("INT", {"default": 99, "min": 0, "max": 1000, "step": 1, "tooltip": "99=全部卸载到GPU"}),
                "上下文长度": ("INT", {"default": 8192, "min": 512, "max": 131072, "step": 512}),
                "端口": ("INT", {"default": 8080, "min": 0, "max": 65535, "tooltip": "0=自动选择空闲端口，>0=指定端口"}),
                "API密钥": ("STRING", {"default": "", "multiline": False, "placeholder": "留空=无密钥"}),
                "预设温度": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.05, "tooltip": "控制随机性：越低越确定/保守，越高越创意/随机。建议0.5-1.0"}),
                "预设top_p": ("FLOAT", {"default": 0.9, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip": "核采样：只从累计概率达到此值的token中选择。越低越保守，建议0.8-0.95"}),
                "预设top_k": ("INT", {"default": 40, "min": 0, "max": 1000, "step": 1, "tooltip": "只从概率最高的前K个token中选择。0=禁用，建议20-50"}),
                "预设最大长度": ("INT", {"default": 2048, "min": 16, "max": 8192, "step": 16, "tooltip": "模型单次回复的最大token数。越大回复越长，但越占显存和时间"}),
                "最小P采样": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01, "tooltip": "最小概率采样：过滤掉概率低于(最高概率×此值)的token。0=禁用，建议0.05-0.1"}),
                "重复惩罚": ("FLOAT", {"default": 1.1, "min": 0.5, "max": 2.0, "step": 0.05, "tooltip": "防止模型重复输出，1.0=禁用"}),
                "随机种子": ("INT", {"default": -1, "min": -1, "max": 2147483647, "tooltip": "-1=随机，固定值=可复现结果"}),
                "思考预算": ("STRING", {"default": "", "placeholder": "如：用中文思考，不超过500字", "tooltip": "深度思考模型的思考引导，留空=不设置"}),
                "强行关闭思考": ("BOOLEAN", {"default": False, "tooltip": "开启=强制模型禁止输出思考过程，直接回答。适合Qwen3.5等默认思考的模型，能大幅加快速度"}),
                "上下文溢出处理": (["截断中间", "保留最新", "不处理"], {"default": "截断中间", "tooltip": "消息太长时的处理策略"}),
                "启动加速": ([
                    "无",
                    "🚀 --flash-attn on (Flash Attention加速)",
                    "🔒 --mlock (锁定内存)",
                    "🚀🔒 --flash-attn on --mlock",
                    "📝 --no-mmap (不使用内存映射)",
                    "🧵 --threads 8",
                    "🧵 --threads 16",
                    "自定义",
                ], {"default": "无"}),
                "自定义启动参数": ("STRING", {"default": "", "multiline": True, "placeholder": "选择'自定义'时在此填写，如 --flash-attn --threads 8"}),
                "CPU线程数": ("INT", {"default": 0, "min": 0, "max": 64, "tooltip": "0=自动，建议设为CPU核心数"}),
                "批处理大小": ("INT", {"default": 512, "min": 32, "max": 4096, "step": 32, "tooltip": "提示批处理大小，越大越快但越占显存"}),
                "KV缓存GPU": ("BOOLEAN", {"default": True, "tooltip": "开启=KV缓存放GPU，更快；关闭=放内存，省显存"}),
                "崩溃自动重启": ("BOOLEAN", {"default": True}),
                "启动前停止旧服务": ("BOOLEAN", {"default": True}),
                "模板模式": ([
                    "使用内置模板",
                    "读取并显示内置模板",
                    "使用自定义模板"
                ], {"default": "使用内置模板"}),
                "自定义模板内容": ("STRING", {"default": "", "multiline": True, "placeholder": "仅当模板模式选择'使用自定义模板'时生效"}),
                "llama_server路径": ("STRING", {"default": DEFAULT_LLAMA_SERVER}),
            },
        }
    
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("API地址",)
    FUNCTION = "console"
    CATEGORY = "susu-doubao/LLM"
    OUTPUT_NODE = True
    
    def console(self, 操作, 模型路径="",
                mmproj路径="",
                操作参数="",
                GPU层数=99, 上下文长度=8192,
                预设温度=0.7, 预设top_p=0.9, 预设top_k=40, 最小P采样=0.0,
                预设最大长度=2048,
                端口=8080, API密钥="",
                重复惩罚=1.1, 随机种子=-1, 思考预算="", 强行关闭思考=False,
                上下文溢出处理="截断中间",
                启动加速="无", 自定义启动参数="",
                CPU线程数=0, 批处理大小=512, KV缓存GPU=True,
                崩溃自动重启=True, 启动前停止旧服务=True,
                模板模式="使用内置模板", 自定义模板内容="",
                llama_server路径=DEFAULT_LLAMA_SERVER):
        
        # 全局变量声明（必须在函数开头）
        global _global_llm_params
        global _current_running_model
        global _model_list
        
        model_obj = None
        api_url = ""
        status = ""
        builtin_template = ""
        op_result = ""
        mmproj_to_use = ""
        mmproj_disabled = False
        
        # ===== 加载并启动模型 =====
        if 操作 == "🚀 加载并启动模型":
            if not 模型路径 or "未找到模型" in 模型路径:
                return _console_ret("", "❌ 错误: 请选择一个模型")
            if not os.path.isfile(模型路径):
                return _console_ret("", f"❌ 错误: 模型文件不存在: {模型路径}")
            
            # "读取并显示内置模板"是只读操作，优先处理，不受模型是否运行影响
            if 模板模式 == "读取并显示内置模板":
                success, result = read_gguf_chat_template(模型路径)
                if success:
                    return _console_ret("", f"📄 模型内置聊天模板:\n\n{result}")
                else:
                    return _console_ret("", f"❌ 读取内置模板失败: {result}")
            
            # 处理mmproj：支持完整路径、文件名（自动在模型同目录查找）、留空（自动检测）、填"无/none/禁用"（禁用）
            mmproj_raw = mmproj路径.strip() if mmproj路径 else ""
            mmproj_disabled = mmproj_raw.lower() in ("无", "none", "禁用", "disable", "off", "false", "0")
            mmproj_to_use = ""
            model_dir = os.path.dirname(模型路径)
            if mmproj_disabled:
                # 用户明确禁用mmproj，不加载也不自动检测
                mmproj_to_use = ""
            elif mmproj_raw:
                # 用户填了值，检查是否是完整路径
                if not os.path.isfile(mmproj_raw):
                    # 不是完整路径，尝试在模型同目录下查找
                    candidate = os.path.join(model_dir, mmproj_raw)
                    if os.path.isfile(candidate):
                        mmproj_to_use = candidate
                    else:
                        # 也尝试只匹配文件名前缀
                        try:
                            for f in os.listdir(model_dir):
                                if f.lower() == mmproj_raw.lower() or f.lower().startswith(mmproj_raw.lower()):
                                    mmproj_to_use = os.path.join(model_dir, f)
                                    break
                        except Exception:
                            pass
                else:
                    mmproj_to_use = mmproj_raw
            else:
                # 用户没填，自动检测模型同目录下的mmproj文件
                try:
                    for f in os.listdir(model_dir):
                        if f.lower().startswith("mmproj") and f.lower().endswith(".gguf"):
                            mmproj_to_use = os.path.join(model_dir, f)
                            break
                except Exception:
                    pass
            
            # 检查模型是否已经启动，如果是则直接返回，避免重复启动
            if _current_running_model is not None:
                same_model = _current_running_model.get("model_path") == 模型路径
                same_port = (端口 == 0) or (_current_running_model.get("port") == 端口)
                same_mmproj = _current_running_model.get("mmproj", "") == mmproj_to_use
                if same_model and same_port and same_mmproj:
                    # 验证服务是否真的在运行（进程可能已崩溃）
                    servers = list_running_servers()
                    cached_port = _current_running_model.get("port")
                    is_alive = any(s["port"] == cached_port for s in servers)
                    if is_alive:
                        # 模型已经启动，直接返回
                        model_obj = _current_running_model.get("model_obj")
                        api_url = f"{model_obj['base_url']}/v1"
                        gpu_info = get_gpu_memory()
                        status = f"✅ 模型已在运行（复用） | 端口: {model_obj['port']} | {gpu_info}"
                        op_result = f"服务运行在: {model_obj['base_url']}"
                        # 仍然更新全局参数（只调用update方法，不需要global声明）
                        _global_llm_params.update({
                            "temperature": 预设温度, "top_p": 预设top_p, "top_k": 预设top_k,
                            "max_tokens": 预设最大长度, "system_prompt": "",
                            "repeat_penalty": 重复惩罚, "seed": 随机种子, "min_p": 最小P采样,
                            "context_overflow": 上下文溢出处理, "context_length": 上下文长度,
                        })
                        return _console_ret(api_url, status)
                    else:
                        # 服务已死，清除缓存记录，继续启动新服务
                        _current_running_model = None
            
            # 处理聊天模板
            chat_template_arg = "自动检测"
            custom_template_arg = ""
            builtin_template = "使用内置模板"
            
            if 模板模式 == "使用自定义模板":
                if 自定义模板内容 and 自定义模板内容.strip():
                    custom_template_arg = 自定义模板内容.strip()
                    chat_template_arg = "自定义"
                else:
                    return _console_ret("", "❌ 错误: 选择了'使用自定义模板'但内容为空")
            
            port_arg = None if 端口 == 0 else 端口
            
            # 启动前停止旧服务
            if 启动前停止旧服务:
                try:
                    stop_all_servers()
                    import time
                    # 等待进程退出和端口释放（本插件只杀自己管理的进程，kill后通常立即退出）
                    time.sleep(0.5)
                except Exception:
                    pass
            
            # 根据启动加速选择设置额外启动参数
            extra_args_map = {
                "无": "",
                "🚀 --flash-attn on (Flash Attention加速)": "--flash-attn on",
                "🔒 --mlock (锁定内存)": "--mlock",
                "🚀🔒 --flash-attn on --mlock": "--flash-attn on --mlock",
                "📝 --no-mmap (不使用内存映射)": "--no-mmap",
                "🧵 --threads 8": "--threads 8",
                "🧵 --threads 16": "--threads 16",
                "自定义": 自定义启动参数.strip(),
            }
            额外启动参数 = extra_args_map.get(启动加速, "")

            # 启动服务
            model_obj = None
            api_url = ""
            try:
                # 构建额外启动参数
                extra_parts = [额外启动参数] if 额外启动参数 else []
                if CPU线程数 > 0:
                    extra_parts.append(f"--threads {CPU线程数}")
                if 批处理大小 != 512:
                    extra_parts.append(f"--batch-size {批处理大小}")
                if not KV缓存GPU:
                    extra_parts.append("--kv-cache-type f16")
                if 强行关闭思考:
                    extra_parts.append('--reasoning off')
                final_extra_args = " ".join(extra_parts)

                # mmproj文件存在性检查
                if mmproj_to_use and not os.path.isfile(mmproj_to_use):
                    return _console_ret("", f"❌ mmproj路径文件不存在: {mmproj_to_use}\n请填写完整路径，或留空自动检测模型同目录下的mmproj文件")

                success, message, base_url, actual_port = start_server(
                    llama_server_path=llama_server路径,
                    model_path=模型路径,
                    n_gpu_layers=GPU层数,
                    n_ctx=上下文长度,
                    port=port_arg,
                    extra_args=final_extra_args,
                    api_key=API密钥,
                    auto_port=True,
                    auto_restart=崩溃自动重启,
                    chat_template=chat_template_arg,
                    custom_template=custom_template_arg,
                    mmproj=mmproj_to_use,
                )
                
                if success:
                    gpu_info = get_gpu_memory()
                    # mmproj状态
                    mmproj_text = ""
                    if mmproj_to_use:
                        mmproj_text = f" | 视觉模型: {os.path.basename(mmproj_to_use)}"
                    elif mmproj_disabled:
                        mmproj_text = " | 视觉模型: 未使用"
                    model_obj = {
                        "base_url": base_url, "port": actual_port,
                        "model_path": 模型路径, "model_name": os.path.basename(模型路径),
                        "api_key": API密钥, "mmproj": mmproj_to_use,
                    }
                    # 记录当前已启动的模型，避免重复启动
                    _current_running_model = {
                        "model_path": 模型路径,
                        "port": actual_port,
                        "model_obj": model_obj,
                        "mmproj": mmproj_to_use,
                    }
                    api_url = f"{base_url}/v1"
                    # 计算预估显存（更合理的估算）
                    try:
                        model_size_mb = os.path.getsize(模型路径) / (1024*1024)
                        # 模型权重：GPU层数>=24时全部放GPU，否则按比例（假设小模型约24层）
                        model_vram_mb = model_size_mb * min(1.0, GPU层数 / 24.0)
                        # KV缓存：按模型大小的15%估算，随上下文长度线性变化
                        kv_cache_mb = model_size_mb * 0.15 * (上下文长度 / 4096.0)
                        # 总显存 = 模型权重 + KV缓存 + 200MB开销
                        est_vram = model_vram_mb + kv_cache_mb + 200
                        vram_text = f" | 预估显存: {est_vram/1024:.2f}GB"
                    except:
                        vram_text = ""
                    status = f"✅ 模型已启动 | 端口: {actual_port}{vram_text}{mmproj_text} | {gpu_info}"
                    op_result = f"服务运行在: {base_url}{mmproj_text}"
                else:
                    # 启动失败，友好提示
                    msg_lower = str(message).lower()
                    if "address already in use" in msg_lower or "port" in msg_lower and ("in use" in msg_lower or "bind" in msg_lower or "occupied" in msg_lower):
                        friendly = f"❌ 端口{actual_port if actual_port else ''}已被占用，请更换端口或先停止该端口服务（可用'停止全部服务'操作）"
                    elif "no such file" in msg_lower or "not found" in msg_lower:
                        friendly = f"❌ 未找到llama_server或模型文件，请检查路径"
                    elif "permission" in msg_lower or "access denied" in msg_lower:
                        friendly = f"❌ 权限不足，无法启动服务"
                    elif "out of memory" in msg_lower or "cuda" in msg_lower and "memory" in msg_lower:
                        friendly = f"❌ 显存不足，建议降低GPU层数或上下文长度"
                    else:
                        friendly = f"❌ 启动失败：{message}"
                    status = friendly
                    op_result = message
            except Exception as e:
                import traceback
                err_str = str(e).lower()
                if "address already in use" in err_str or ("port" in err_str and "in use" in err_str):
                    status = "❌ 端口被占用，请更换端口或先停止旧服务"
                elif "no such file" in err_str or "not found" in err_str:
                    status = "❌ 未找到llama_server或模型文件，请检查路径"
                elif "out of memory" in err_str or ("cuda" in err_str and "memory" in err_str):
                    status = "❌ 显存不足，建议降低GPU层数或上下文长度"
                else:
                    status = f"❌ 启动异常：{e}"
                op_result = traceback.format_exc()
            
            # 保存参数到全局变量，聊天节点自动继承
            # 处理思考预算
            final_sys_prompt = ""
            if 思考预算 and 思考预算.strip():
                if final_sys_prompt:
                    final_sys_prompt = f"{final_sys_prompt}\n\n思考要求：{思考预算}"
                else:
                    final_sys_prompt = f"思考要求：{思考预算}"

            _global_llm_params = {
                "temperature": 预设温度, "top_p": 预设top_p,
                "top_k": 预设top_k, "max_tokens": 预设最大长度,
                "system_prompt": final_sys_prompt,
                "repeat_penalty": 重复惩罚,
                "seed": 随机种子,
                "min_p": 最小P采样,
                "context_overflow": 上下文溢出处理,
                "context_length": 上下文长度,
            }
            return _console_ret(api_url, status)
        
        # ===== 停止指定端口 =====
        elif 操作 == "⏹ 停止指定端口":
            try:
                if not 操作参数 or not 操作参数.strip():
                    op_result = "⚠️ 请在'操作参数'中填写要停止的端口号（如 8080）"
                else:
                    try:
                        port = int(操作参数.strip())
                        stop_server(port)
                        # 如果停止的是当前运行的模型，清空记录
                        if _current_running_model and _current_running_model.get("port") == port:
                            _current_running_model = None
                        op_result = f"✅ 已停止端口 {port} 的服务"
                    except ValueError:
                        op_result = "❌ 操作参数格式错误，请填写数字端口号（如 8080）"
            except Exception as e:
                op_result = f"❌ 停止失败: {e}"
            return _console_ret("", op_result)
        
        # ===== 停止全部服务 =====
        elif 操作 == "⏹⏹ 停止全部服务":
            try:
                count = stop_all_servers()
                # 清空当前运行模型记录
                _current_running_model = None
                op_result = f"已停止所有服务"
            except Exception as e:
                op_result = f"❌ 停止失败: {e}"
            return _console_ret("", op_result)
        
        # ===== 停止当前生成 =====
        elif 操作 == "⏹ 停止当前生成":
            try:
                stop_all_generations()
                op_result = "✅ 已发送停止信号，当前流式生成将在完成当前token后停止"
            except Exception as e:
                op_result = f"❌ 停止失败: {e}"
            return _console_ret("", op_result)
        
        # ===== 列出运行中服务 =====
        elif 操作 == "📋 列出运行中服务":
            try:
                servers = list_running_servers()
                if servers:
                    lines = []
                    for s in servers:
                        lines.append(f"端口 {s['port']}: {os.path.basename(s['model_path'])}")
                    op_result = "\n".join(lines)
                else:
                    op_result = "没有运行中的服务"
            except Exception as e:
                op_result = f"❌ 查询失败: {e}"
            return _console_ret("", op_result)
        

        # ===== 查看运行日志 =====
        elif 操作 == "📊 查看运行日志":
            try:
                servers = list_running_servers()
                if not servers:
                    op_result = "没有运行中的服务"
                else:
                    target_port = None
                    if 操作参数 and 操作参数.strip():
                        try:
                            target_port = int(操作参数.strip())
                        except ValueError:
                            target_port = servers[0]["port"]
                    else:
                        target_port = servers[0]["port"]
                    success, log_content = get_server_log(target_port, lines=80)
                    if success:
                        op_result = f"📊 端口 {target_port} 运行日志（最近80行）:\n\n{log_content}"
                    else:
                        op_result = f"❌ 读取日志失败: {log_content}"
            except Exception as e:
                op_result = f"❌ 查询失败: {e}"
            return _console_ret("", op_result)

        # ===== 读取内置模板 =====
        elif 操作 == "📄 读取内置模板":
            try:
                if not 模型路径 or "未找到模型" in 模型路径:
                    op_result = "❌ 请先选择一个模型文件"
                elif not os.path.isfile(模型路径):
                    op_result = f"❌ 模型文件不存在: {模型路径}"
                else:
                    success, result = read_gguf_chat_template(模型路径)
                    if success:
                        model_name = os.path.basename(模型路径)
                        op_result = f"📄 模型「{model_name}」内置聊天模板:\n\n{result}\n\n💡 可将此模板复制到'自定义模板内容'中使用"
                    else:
                        op_result = f"❌ 读取内置模板失败: {result}"
            except Exception as e:
                op_result = f"❌ 读取异常: {e}"
            return _console_ret("", op_result)

        # ===== 刷新模型列表 =====
        elif 操作 == "🔄 刷新模型列表":
            try:
                _model_list = None
                models = scan_gguf_models()
                if models:
                    op_result = f"✅ 已刷新模型列表，共找到 {len(models)} 个模型：\n" + "\n".join(os.path.basename(m) for m in models[:20])
                    if len(models) > 20:
                        op_result += f"\n... 等共 {len(models)} 个"
                else:
                    op_result = "⚠️ 未找到任何 GGUF 模型，请检查模型目录"
            except Exception as e:
                op_result = f"❌ 刷新失败: {e}"
            return _console_ret("", op_result)

        # ===== 预设: 列出所有 =====
        elif 操作 == "💾 预设: 列出所有":
            try:
                presets = load_presets()
                if presets:
                    lines = [f"【{name}】" for name in presets.keys()]
                    op_result = "\n".join(lines)
                else:
                    op_result = "没有保存的预设"
            except Exception as e:
                op_result = f"❌ 查询失败: {e}"
            return _console_ret("", op_result)
        
        # ===== 预设: 加载 =====
        elif 操作 == "📂 预设: 加载":
            try:
                if not 操作参数 or not 操作参数.strip():
                    op_result = "⚠️ 请在'操作参数'中填写要加载的预设名称（先用'预设: 列出所有'查看有哪些预设）"
                else:
                    name = 操作参数.strip()
                    presets = load_presets()
                    if name not in presets:
                        op_result = f"❌ 预设 '{name}' 不存在\n\n可用预设：\n" + "\n".join(f"  • {n}" for n in presets.keys())
                    else:
                        p = presets[name]
                        lines = [f"📂 预设 '{name}' 内容：", ""]
                        if "temperature" in p: lines.append(f"  温度: {p['temperature']}")
                        if "top_p" in p: lines.append(f"  top_p: {p['top_p']}")
                        if "top_k" in p: lines.append(f"  top_k: {p['top_k']}")
                        if "max_tokens" in p: lines.append(f"  最大长度: {p['max_tokens']}")
                        if "system_prompt" in p and p["system_prompt"]: lines.append(f"  系统提示词: {p['system_prompt'][:100]}{'...' if len(p['system_prompt']) > 100 else ''}")
                        for k, v in p.items():
                            if k not in ("temperature", "top_p", "top_k", "max_tokens", "system_prompt"):
                                lines.append(f"  {k}: {v}")
                        lines.append("")
                        lines.append("💡 请手动将以上参数填入节点对应位置后启动模型")
                        op_result = "\n".join(lines)
            except Exception as e:
                op_result = f"❌ 加载失败: {e}"
            return _console_ret("", op_result)
        
        # ===== 预设: 保存 =====
        elif 操作 == "💾 预设: 保存":
            try:
                if not 操作参数 or not 操作参数.strip():
                    op_result = "⚠️ 请在'操作参数'中填写预设名称（如 我的默认）"
                else:
                    name = 操作参数.strip()
                    save_preset(name, {
                        "temperature": 预设温度, "top_p": 预设top_p,
                        "top_k": 预设top_k, "max_tokens": 预设最大长度,
                        "system_prompt": "",
                    })
                    op_result = f"✅ 预设 '{name}' 已保存"
            except Exception as e:
                op_result = f"❌ 保存失败: {e}"
            return _console_ret("", op_result)
        
        # ===== 预设: 删除 =====
        elif 操作 == "🗑 预设: 删除":
            try:
                if not 操作参数 or not 操作参数.strip():
                    op_result = "⚠️ 请在'操作参数'中填写要删除的预设名称"
                else:
                    name = 操作参数.strip()
                    delete_preset(name)
                    op_result = f"✅ 预设 '{name}' 已删除"
            except Exception as e:
                op_result = f"❌ 删除失败: {e}"
            return _console_ret("", op_result)
        
        return _console_ret("", "未知操作")


# ============================================================
# 节点2: LLM 聊天（优化版）
# ============================================================
class LLMChat:
    """统一 LLM 聊天节点：纯API方式，不需要连线，生成按钮直接在JavaScript中调用API"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": {
                "用户输入": ("STRING", {"default": "", "multiline": True, "tooltip": "你要对模型说的话，按Enter发送，Shift+Enter换行。也可以不连接此端口，直接在节点的聊天框里输入消息"}),
                "输入图像": ("IMAGE", {"tooltip": "输入图片，连接后可让视觉模型（如GPT-4V、豆包视觉等）分析图片内容。注意：只有支持视觉的模型才能分析图片"}),
                "API地址": ("STRING", {"default": "http://127.0.0.1:8080/v1", "placeholder": "如 http://127.0.0.1:8080/v1", "tooltip": "模型API的地址，本地llama.cpp默认是http://127.0.0.1:端口/v1，必须填对才能连接模型"}),
                "API密钥": ("STRING", {"default": "", "placeholder": "留空=无密钥", "tooltip": "API密钥，本地模型一般留空即可，远程API（如OpenAI、阿里云百炼）需要填写密钥"}),
                "模型名称": ("STRING", {"default": "", "placeholder": "留空=自动使用第一个模型", "tooltip": "要调用的模型名称，留空会自动使用已加载的第一个模型，多个模型时需要指定"}),
                "系统提示词": ("STRING", {"default": "", "multiline": True, "placeholder": "设定模型角色和行为规则", "tooltip": "设定模型的角色、身份、行为规则和回答风格，比如'你是一个专业的Python程序员，用简洁的方式回答问题'"}),
                "温度": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.05, "tooltip": "控制随机性：0=完全确定保守，1=标准随机，2=非常创意随机。建议0.5-1.0，写代码用0.3，创作用0.9"}),
                "top_p": ("FLOAT", {"default": 0.9, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip": "核采样：只从累计概率达到此值的token中选择。越低越保守，越高越多样。建议0.8-0.95，和温度配合使用"}),
                "top_k": ("INT", {"default": 40, "min": 0, "max": 1000, "step": 1, "tooltip": "只从概率最高的前K个token中选择。0=禁用，建议20-50。越低越保守，越高越多样"}),
                "最大长度": ("INT", {"default": 1024, "min": 16, "max": 8192, "step": 16, "tooltip": "模型单次回复的最大token数（约1token=0.7个汉字）。越大回复越长，但越占显存和时间。短问答用512，长文用2048"}),
                "使用云端默认参数": ("BOOLEAN", {"default": False, "tooltip": "开启后不传递温度/top_p/top_k/最大长度，让云端API使用自己的默认值（适合豆包等云端模型）。关闭则使用节点上设置的参数值"}),
                "会话ID": ("STRING", {"default": "", "placeholder": "填写后启用多轮对话记忆", "tooltip": "填写后启用多轮对话记忆，相同ID的对话会保留历史记录。留空=每次都是新对话，不记忆之前的内容"}),
                "流式输出": ("BOOLEAN", {"default": True, "tooltip": "开启=逐字实时显示回答（像打字一样），速度感更快；关闭=等全部生成完一次性显示。建议开启"}),
            },
        }
    
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("图像Base64",)
    FUNCTION = "chat"
    CATEGORY = "susu-doubao/LLM"
    OUTPUT_NODE = True
    
    def chat(self, 用户输入="",
             输入图像=None,
             API地址="http://127.0.0.1:8080/v1", API密钥="", 模型名称="",
             系统提示词="", 温度=0.7, top_p=0.9, top_k=40, 最大长度=1024,
             使用云端默认参数=False, 会话ID="", 流式输出=True):
        # 这个函数主要是为了ComfyUI的工作流执行，实际的API调用在JavaScript端完成
        print(f"[susu-doubao] chat函数被调用, 输入图像是否为None: {输入图像 is None}")
        if 输入图像 is not None:
            print(f"[susu-doubao] 输入图像类型: {type(输入图像)}, shape: {getattr(输入图像, 'shape', 'N/A')}")
        # 处理输入图像：保存为临时PNG并转base64，供前端视觉分析使用
        image_b64 = ""
        if 输入图像 is not None:
            try:
                import torch
                from PIL import Image
                import io, base64
                # 输入图像是 [B,H,W,C] 格式的tensor，取第一张
                img_tensor = 输入图像[0] if hasattr(输入图像, '__len__') and len(输入图像) > 0 else 输入图像
                img_np = (img_tensor.cpu().numpy() * 255).astype('uint8')
                pil_img = Image.fromarray(img_np)
                buf = io.BytesIO()
                pil_img.save(buf, format='PNG')
                image_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
                print(f"[susu-doubao] 图像处理完成，base64长度: {len(image_b64)}")
            except Exception as e:
                import traceback
                print(f"[susu-doubao] 图像处理失败: {e}")
                print(traceback.format_exc())
        print(f"[susu-doubao] chat函数返回, image_b64长度: {len(image_b64)}")
        # 关键：必须返回包含ui的字典，否则output_ui为空，executed消息不会发送到前端，onExecuted不会触发
        # result用于输出端口连接，ui用于前端onExecuted接收
        return {"result": (image_b64,), "ui": {"0": [image_b64]}}


# ============================================================
# 注册节点
# ============================================================
# 插件加载时立即扫描模型，避免添加节点时卡顿
try:
    scan_gguf_models()
except Exception:
    pass

NODE_CLASS_MAPPINGS = {
    "LLMModelConsole": LLMModelConsole,
    "LLMChat": LLMChat,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LLMModelConsole": "🎛 LLM 模型控制台 (susu-doubao)",
    "LLMChat": "✨ LLM 聊天 (susu-doubao)",
}


# ============================================================
# API 路由（解决浏览器CORS跨域问题）
# ============================================================

def _call_llm_api(base_url, api_key, model, messages, temperature, top_p, top_k, max_tokens, stream=False, repeat_penalty=1.1, min_p=None, image_base64=None):
    """统一调用LLM API，支持requests和urllib两种方式。image_base64不为空时使用视觉模型格式"""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    # 如果有图像，将最后一条用户消息转为视觉格式
    api_messages = messages
    if image_base64:
        api_messages = []
        for i, msg in enumerate(messages):
            if i == len(messages) - 1 and msg.get("role") == "user":
                # 最后一条用户消息，附加图像
                api_messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": msg.get("content", "")},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
                    ]
                })
            else:
                api_messages.append(msg)
        print(f"[susu-doubao] 使用视觉模式，消息数: {len(api_messages)}")
    
    payload = {
        "messages": api_messages,
        "stream": stream,
        "repeat_penalty": float(repeat_penalty),
    }
    # 模型名称：同时传递 model 和 model_name（llama.cpp某些版本需要model_name）
    if model and model.strip() and model.lower() != "default":
        payload["model"] = model.strip()
        payload["model_name"] = model.strip()
    # 参数为None时不传递（使用云端默认参数）
    if temperature is not None:
        payload["temperature"] = float(temperature)
    if top_p is not None:
        payload["top_p"] = float(top_p)
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    if top_k is not None and int(top_k) > 0:
        payload["top_k"] = int(top_k)
    # min_p为0或None时不传递
    if min_p is not None and float(min_p) > 0:
        payload["min_p"] = float(min_p)
    
    print(f"[susu-doubao] 调用API: {url}")
    print(f"[susu-doubao] 请求参数: model={payload.get('model')}, temp={payload.get('temperature')}, top_p={payload.get('top_p')}, top_k={payload.get('top_k')}, max_tokens={payload.get('max_tokens')}")
    
    if HAS_REQUESTS:
        resp = requests.post(url, headers=headers, json=payload, timeout=300, stream=stream)
        # 强制使用UTF-8编码，避免中文乱码
        resp.encoding = 'utf-8'
        if resp.status_code != 200:
            print(f"[susu-doubao] API错误 {resp.status_code}: {resp.text[:500]}")
        return resp
    else:
        # urllib 方式（不支持流式，仅非流式）
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        resp = urllib.request.urlopen(req, timeout=300)
        return resp


@PromptServer.instance.routes.post("/susu-doubao/ai/generate")
async def susu_ai_generate(request):
    """非流式生成API"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "请求格式错误"}, status=400)
    
    base_url = data.get("base_url", "").strip()
    api_key = data.get("api_key", "").strip()
    model = data.get("model", "").strip()
    messages = data.get("messages", [])
    temperature = data.get("temperature", 0.7)
    top_p = data.get("top_p", 0.9)
    top_k = data.get("top_k", 40)
    max_tokens = data.get("max_tokens", 1024)
    repeat_penalty = data.get("repeat_penalty", _global_llm_params.get("repeat_penalty", 1.1))
    min_p = data.get("min_p", _global_llm_params.get("min_p"))
    
    if not base_url:
        return web.json_response({"error": "请填写API地址"}, status=400)
    if not messages:
        return web.json_response({"error": "请输入消息"}, status=400)
    
    try:
        resp = _call_llm_api(base_url, api_key, model, messages, temperature, top_p, top_k, max_tokens, stream=False, repeat_penalty=repeat_penalty, min_p=min_p)
        
        if HAS_REQUESTS:
            if resp.status_code != 200:
                try:
                    err = resp.json()
                except Exception:
                    err = resp.text
                return web.json_response({"error": f"API错误 ({resp.status_code}): {str(err)[:500]}"}, status=resp.status_code)
            resp_json = resp.json()
        else:
            resp_json = json.loads(resp.read().decode("utf-8"))
        
        msg_obj = resp_json["choices"][0]["message"]
        content = msg_obj.get("content", "") or ""
        reasoning = msg_obj.get("reasoning_content", "") or ""
        
        # 处理思考内容
        if not content.strip() and reasoning.strip():
            content = reasoning
        elif content.strip() and reasoning.strip():
            content = f"【思考过程】\n{reasoning}\n\n【回答】\n{content}"
        
        usage = resp_json.get("usage", {})
        return web.json_response({
            "content": content,
            "reasoning": reasoning,
            "usage": usage,
        })
    
    except Exception as e:
        return web.json_response({"error": f"请求失败: {str(e)}"}, status=500)


@PromptServer.instance.routes.post("/susu-doubao/ai/generate_stream")
async def susu_ai_generate_stream(request):
    """流式生成API（SSE）"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "请求格式错误"}, status=400)
    
    base_url = data.get("base_url", "").strip()
    api_key = data.get("api_key", "").strip()
    model = data.get("model", "").strip()
    messages = data.get("messages", [])
    use_cloud_default = data.get("use_cloud_default", False)
    if use_cloud_default:
        temperature = None
        top_p = None
        top_k = None
        max_tokens = None
        print("[susu-doubao] 使用云端默认参数（不传递temperature/top_p/top_k/max_tokens）")
    else:
        temperature = data.get("temperature", 0.7)
        top_p = data.get("top_p", 0.9)
        top_k = data.get("top_k", 40)
        max_tokens = data.get("max_tokens", 1024)
    repeat_penalty = data.get("repeat_penalty", _global_llm_params.get("repeat_penalty", 1.1))
    min_p = data.get("min_p", _global_llm_params.get("min_p"))
    image_base64 = data.get("image_base64", "") or ""
    
    if not base_url:
        return web.json_response({"error": "请填写API地址"}, status=400)
    if not messages:
        return web.json_response({"error": "请输入消息"}, status=400)
    if not HAS_REQUESTS:
        return web.json_response({"error": "流式输出需要requests库，请安装requests"}, status=500)
    
    import asyncio
    import threading

    # 使用队列+单独线程，避免同步iter_lines阻塞aiohttp事件循环
    queue = asyncio.Queue()

    def sync_stream_worker():
        """在单独线程中运行同步的流式读取"""
        # 每次新生成开始时重置全局停止标志
        reset_global_stop()
        try:
            resp = _call_llm_api(base_url, api_key, model, messages, temperature, top_p, top_k, max_tokens, stream=True, repeat_penalty=repeat_penalty, min_p=min_p, image_base64=image_base64 if image_base64 else None)
            if resp.status_code != 200:
                error_data = json.dumps({"error": f"API错误 ({resp.status_code})"}).encode("utf-8")
                queue.put_nowait(f"data: {error_data.decode('utf-8')}\n\n".encode("utf-8"))
                queue.put_nowait(None)
                return
            line_count = 0
            data_count = 0
            for line in resp.iter_lines(decode_unicode=True):
                # 检查全局停止标志（控制台"停止当前生成"操作）
                if is_global_stop():
                    print("[susu-doubao] 收到停止信号，中断流式生成")
                    try:
                        resp.close()
                    except Exception:
                        pass
                    queue.put_nowait("data: [DONE]\n\n".encode("utf-8"))
                    break
                if not line:
                    continue
                line_count += 1
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    data_count += 1
                    if data_str == "[DONE]":
                        print(f"[susu-doubao] 流式输出收到[DONE]，共{data_count}条数据")
                        queue.put_nowait("data: [DONE]\n\n".encode("utf-8"))
                        break
                    try:
                        json_data = json.loads(data_str)
                        delta = json_data.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        reasoning = delta.get("reasoning_content", "")
                        if content or reasoning:
                            queue.put_nowait(f"data: {json.dumps({'content': content, 'reasoning': reasoning}, ensure_ascii=False)}\n\n".encode("utf-8"))
                    except Exception:
                        pass
            print(f"[susu-doubao] 流式输出完成，共读取 {line_count} 行，{data_count} 条数据")
        except Exception as e:
            error_data = json.dumps({"error": str(e)}).encode("utf-8")
            queue.put_nowait(f"data: {error_data.decode('utf-8')}\n\n".encode("utf-8"))
        finally:
            queue.put_nowait(None)

    thread = threading.Thread(target=sync_stream_worker, daemon=True)
    thread.start()

    async def stream_generator():
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(request)
    print("[susu-doubao] 流式响应已准备，开始发送数据")
    chunk_count = 0
    async for chunk in stream_generator():
        await resp.write(chunk)
        await resp.drain()
        chunk_count += 1
        if chunk_count <= 5:
            print(f"[susu-doubao] 已发送第{chunk_count}块数据，长度{len(chunk)}")
    print(f"[susu-doubao] 流式响应完成，共发送{chunk_count}块数据")
    await resp.write_eof()
    return resp


# ========== 会话管理API（本地文件存储） ==========
import json as _json
import time as _time
import uuid as _uuid

_SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
os.makedirs(_SESSIONS_DIR, exist_ok=True)

def _get_session_path(session_id):
    """获取会话文件路径，防止路径遍历"""
    safe_id = "".join(c for c in session_id if c.isalnum() or c in "-_")
    return os.path.join(_SESSIONS_DIR, f"{safe_id}.json")

def _read_session(session_id):
    """读取单个会话文件"""
    path = _get_session_path(session_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception as e:
        print(f"[susu-doubao] 读取会话失败 {session_id}: {e}")
        return None

def _write_session(session_id, data):
    """写入会话文件"""
    path = _get_session_path(session_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[susu-doubao] 写入会话失败 {session_id}: {e}")
        return False

@PromptServer.instance.routes.get("/susu-doubao/sessions/list")
async def susu_sessions_list(request):
    """获取所有对话列表（元信息，不读取完整内容）"""
    try:
        sessions = []
        if os.path.exists(_SESSIONS_DIR):
            for filename in os.listdir(_SESSIONS_DIR):
                if not filename.endswith(".json"):
                    continue
                session_id = filename[:-5]
                path = os.path.join(_SESSIONS_DIR, filename)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = _json.load(f)
                    # 只取元信息
                    sessions.append({
                        "id": data.get("id", session_id),
                        "title": data.get("title", "未命名对话"),
                        "created_at": data.get("created_at", 0),
                        "updated_at": data.get("updated_at", 0),
                        "message_count": len(data.get("messages", [])),
                    })
                except Exception as e:
                    print(f"[susu-doubao] 读取会话元信息失败 {filename}: {e}")
        # 按最后更新时间倒序
        sessions.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
        return web.json_response({"success": True, "sessions": sessions})
    except Exception as e:
        print(f"[susu-doubao] 获取会话列表失败: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)

@PromptServer.instance.routes.get("/susu-doubao/sessions/get")
async def susu_sessions_get(request):
    """获取单个对话的完整内容"""
    try:
        session_id = request.rel_url.query.get("id", "")
        if not session_id:
            return web.json_response({"success": False, "error": "缺少会话ID"}, status=400)
        data = _read_session(session_id)
        if data is None:
            return web.json_response({"success": False, "error": "会话不存在"}, status=404)
        return web.json_response({"success": True, "session": data})
    except Exception as e:
        print(f"[susu-doubao] 获取会话失败: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)

@PromptServer.instance.routes.post("/susu-doubao/sessions/save")
async def susu_sessions_save(request):
    """保存对话（新建或更新）"""
    try:
        body = await request.json()
        session_id = body.get("id", "")
        title = body.get("title", "新对话")
        messages = body.get("messages", [])
        
        if not session_id:
            session_id = str(_uuid.uuid4())[:8]
        
        now = _time.time()
        existing = _read_session(session_id)
        created_at = existing.get("created_at", now) if existing else now
        
        data = {
            "id": session_id,
            "title": title,
            "created_at": created_at,
            "updated_at": now,
            "messages": messages,
        }
        
        if _write_session(session_id, data):
            return web.json_response({"success": True, "id": session_id, "updated_at": now})
        else:
            return web.json_response({"success": False, "error": "保存失败"}, status=500)
    except Exception as e:
        print(f"[susu-doubao] 保存会话失败: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)

@PromptServer.instance.routes.post("/susu-doubao/sessions/delete")
async def susu_sessions_delete(request):
    """删除对话"""
    try:
        body = await request.json()
        session_id = body.get("id", "")
        if not session_id:
            return web.json_response({"success": False, "error": "缺少会话ID"}, status=400)
        path = _get_session_path(session_id)
        if os.path.exists(path):
            os.remove(path)
        return web.json_response({"success": True})
    except Exception as e:
        print(f"[susu-doubao] 删除会话失败: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)

@PromptServer.instance.routes.post("/susu-doubao/sessions/rename")
async def susu_sessions_rename(request):
    """重命名对话"""
    try:
        body = await request.json()
        session_id = body.get("id", "")
        new_title = body.get("title", "")
        if not session_id:
            return web.json_response({"success": False, "error": "缺少会话ID"}, status=400)
        data = _read_session(session_id)
        if data is None:
            return web.json_response({"success": False, "error": "会话不存在"}, status=404)
        data["title"] = new_title or "未命名对话"
        data["updated_at"] = _time.time()
        if _write_session(session_id, data):
            return web.json_response({"success": True})
        else:
            return web.json_response({"success": False, "error": "保存失败"}, status=500)
    except Exception as e:
        print(f"[susu-doubao] 重命名会话失败: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


print("=" * 55)
print("susu-doubao 插件已加载")
print("API路由: /susu-doubao/ai/generate (非流式)")
print("API路由: /susu-doubao/ai/generate_stream (流式)")
print("=" * 55)


# 退出时清理
def cleanup():
    try:
        stop_all_servers()
    except Exception:
        pass

try:
    import atexit
    atexit.register(cleanup)
except Exception:
    pass
