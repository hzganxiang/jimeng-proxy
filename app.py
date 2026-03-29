"""
即梦AI Flask代理服务 - 完整版 v3.0
=====================================
功能：
1. 图片生成 (Seedream-4.0)
2. 视频生成 (Seedance-1.5-pro)
3. AI文案生成 (Doubao-pro)
4. AI分镜脚本生成
5. 完整视频流水线（异步）
6. 飞书通知

更新：
- 流水线改为异步模式，避免超时
- 每个步骤完成都发飞书通知
- 优化prompt生成质量
"""

from flask import Flask, request, jsonify
import requests
import json
import os
import re
import traceback
import time
import threading
import uuid
from datetime import datetime

app = Flask(__name__)

# ============================================
# 🔑 配置区域
# ============================================
ARK_API_KEY = os.environ.get("ARK_API_KEY", "5adb80da-3c5f-4ea4-99d8-e73e78899ba7")

IMAGE_API_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
VIDEO_API_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"

IMAGE_MODEL = "doubao-seedream-4-0-250828"
VIDEO_MODEL = "doubao-seedance-1-5-pro-251215"
VIDEO_DURATION = 5

# 豆包大模型（文案+分镜）
CHAT_API_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
CHAT_MODEL = "doubao-pro-32k-241215"

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "cli_a94e4446ee7adcce")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "xMNkE0bbPQAKBffUmImCkhIYwV6BK3iQ")
FEISHU_BOT_WEBHOOK = os.environ.get("FEISHU_BOT_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/d8a5016b-99ac-413a-bba4-3e47d892a1af")

# 线程安全锁
stats_lock = threading.Lock()
tasks_lock = threading.Lock()

# 统计数据
stats = {
    "total_images": 0, "success_images": 0, "failed_images": 0,
    "total_videos": 0, "success_videos": 0, "failed_videos": 0,
    "last_reset": datetime.now().isoformat()
}

# 任务存储
pipeline_tasks = {}


def update_stats(key, increment=1):
    with stats_lock:
        stats[key] = stats.get(key, 0) + increment


# ============================================
# 基础路由
# ============================================

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "service": "即梦AI代理服务",
        "version": "v3.0-async",
        "status": "running",
        "models": {"image": IMAGE_MODEL, "video": VIDEO_MODEL, "chat": CHAT_MODEL}
    })


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


@app.route('/stats', methods=['GET'])
def get_stats():
    with stats_lock:
        return jsonify(stats.copy())


# ============================================
# 飞书消息
# ============================================

def send_feishu_message(title, content_blocks):
    try:
        message = {
            "msg_type": "post",
            "content": {"post": {"zh_cn": {"title": title, "content": content_blocks}}}
        }
        resp = requests.post(FEISHU_BOT_WEBHOOK, json=message, timeout=10)
        return resp.status_code == 200
    except:
        return False


def send_feishu_text(text):
    try:
        message = {"msg_type": "text", "content": {"text": text}}
        resp = requests.post(FEISHU_BOT_WEBHOOK, json=message, timeout=10)
        return resp.status_code == 200
    except:
        return False


def get_feishu_tenant_token():
    try:
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        resp = requests.post(url, json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=10)
        data = resp.json()
        return data.get("tenant_access_token") if data.get("code") == 0 else None
    except:
        return None


def upload_image_to_feishu(image_url, filename=None):
    try:
        token = get_feishu_tenant_token()
        if not token:
            return None, None
        img_resp = requests.get(image_url, timeout=60)
        if img_resp.status_code != 200:
            return None, None
        image_data = img_resp.content
        headers = {"Authorization": f"Bearer {token}"}
        filename = filename or f"ai_{int(time.time())}.jpg"
        resp = requests.post("https://open.feishu.cn/open-apis/im/v1/images", headers=headers,
                           files={"image": (filename, image_data, "image/jpeg")},
                           data={"image_type": "message"}, timeout=30)
        result = resp.json()
        if result.get("code") == 0:
            return result.get("data", {}).get("image_key"), image_data
        return None, image_data
    except:
        return None, None


def send_feishu_result(prompt, image_url, image_key=None, video_url=None):
    content_blocks = [[{"tag": "text", "text": f"📝 提示词：{prompt}"}]]
    if image_key:
        content_blocks.append([{"tag": "img", "image_key": image_key}])
    if image_url:
        content_blocks.append([{"tag": "text", "text": "🖼️ "}, {"tag": "a", "text": "查看原图", "href": image_url}])
    if video_url:
        content_blocks.append([{"tag": "text", "text": "🎬 "}, {"tag": "a", "text": "查看视频", "href": video_url}])
    title = "✅ 图片+视频生成成功" if video_url else "✅ 素材生成成功"
    return send_feishu_message(title, content_blocks)


def send_feishu_error(prompt, error_msg):
    return send_feishu_message("❌ 生成失败", [
        [{"tag": "text", "text": f"📝 提示词：{prompt}"}],
        [{"tag": "text", "text": f"❌ 错误：{error_msg}"}]
    ])


def send_stats_report():
    with stats_lock:
        s = stats.copy()
    return send_feishu_message("📈 生成统计报告", [
        [{"tag": "text", "text": f"📊 统计时间：{s['last_reset']} 至今"}],
        [{"tag": "text", "text": f"🖼️ 图片：成功 {s['success_images']}/{s['total_images']}"}],
        [{"tag": "text", "text": f"🎬 视频：成功 {s['success_videos']}/{s['total_videos']}"}]
    ])


# ============================================
# 豆包大模型：文案 + 分镜
# ============================================

def chat_completion(system_prompt, user_prompt):
    try:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {ARK_API_KEY}"}
        payload = {
            "model": CHAT_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        }
        print(f"[豆包] 请求中...")
        response = requests.post(CHAT_API_ENDPOINT, headers=headers, json=payload, timeout=60)
        result = response.json()
        if "choices" in result and len(result["choices"]) > 0:
            content = result["choices"][0].get("message", {}).get("content", "")
            print(f"[豆包] 成功")
            return {"success": True, "content": content}
        else:
            return {"success": False, "error": result.get("error", {}).get("message", str(result))}
    except Exception as e:
        return {"success": False, "error": str(e)}


def generate_copywriting(product_name, product_features, style="专业"):
    system_prompt = """你是一位专业的电商营销文案专家。请根据产品信息生成30秒短视频的营销文案。
要求：
1. 文案简洁有力，适合配音朗读
2. 突出产品卖点和优势
3. 有吸引力的开头和行动号召结尾
4. 总字数控制在80-120字"""

    user_prompt = f"""产品名称：{product_name}
产品特点：{product_features}
风格：{style}

请生成营销文案："""

    return chat_completion(system_prompt, user_prompt)


def generate_storyboard(product_name, copywriting, num_scenes=4):
    system_prompt = f"""你是一位专业的视频分镜师。请根据产品和文案，生成{num_scenes}个分镜。

请严格按照以下JSON格式输出，不要添加任何其他文字：
{{
  "scenes": [
    {{
      "scene_id": 1,
      "duration": 5,
      "description": "镜头描述（中文）",
      "prompt": "英文prompt，格式：A [product], [angle], [lighting], [background], professional product photography, 4K",
      "narration": "旁白文字"
    }}
  ]
}}

prompt要求：必须用英文，包含产品、角度、光线、背景，结尾加 professional product photography, 4K"""

    user_prompt = f"""产品名称：{product_name}
营销文案：{copywriting}
分镜数量：{num_scenes}个

请生成分镜脚本（只输出JSON）："""

    result = chat_completion(system_prompt, user_prompt)
    if result.get("success"):
        try:
            content = result["content"]
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                storyboard = json.loads(content[start:end])
                return {"success": True, "storyboard": storyboard}
            return {"success": False, "error": "无法解析JSON", "raw": content}
        except json.JSONDecodeError as e:
            return {"success": False, "error": f"JSON解析错误: {e}", "raw": result["content"]}
    return result


# ============================================
# 图片生成
# ============================================

def generate_image_internal(prompt, size="1024x1024"):
    update_stats("total_images")
    try:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {ARK_API_KEY}"}
        payload = {"model": IMAGE_MODEL, "prompt": prompt, "size": size, "response_format": "url", "watermark": False}
        print(f"[文生图] 请求: {prompt[:50]}...")
        response = requests.post(IMAGE_API_ENDPOINT, headers=headers, json=payload, timeout=120)
        result = response.json()
        if "data" in result and len(result["data"]) > 0:
            update_stats("success_images")
            return {"success": True, "image_url": result["data"][0].get("url", "")}
        else:
            update_stats("failed_images")
            return {"success": False, "error": result.get("error", {}).get("message", str(result))}
    except Exception as e:
        update_stats("failed_images")
        return {"success": False, "error": str(e)}


# ============================================
# 视频生成
# ============================================

def create_video_task(image_url, prompt):
    update_stats("total_videos")
    try:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {ARK_API_KEY}"}
        payload = {
            "model": VIDEO_MODEL,
            "content": [
                {"type": "image_url", "image_url": {"url": image_url}, "role": "first_frame"},
                {"type": "text", "text": prompt}
            ],
            "duration": VIDEO_DURATION,
            "resolution": "1080p"
        }
        response = requests.post(VIDEO_API_ENDPOINT, headers=headers, json=payload, timeout=30)
        result = response.json()
        if "id" in result:
            return {"success": True, "task_id": result["id"]}
        else:
            update_stats("failed_videos")
            return {"success": False, "error": result.get("error", {}).get("message", str(result))}
    except Exception as e:
        update_stats("failed_videos")
        return {"success": False, "error": str(e)}


def query_video_task(task_id):
    try:
        headers = {"Authorization": f"Bearer {ARK_API_KEY}"}
        response = requests.get(f"{VIDEO_API_ENDPOINT}/{task_id}", headers=headers, timeout=30)
        return response.json()
    except:
        return {"error": "查询失败"}


def wait_for_video_completion(task_id, max_wait=300):
    start_time = time.time()
    while time.time() - start_time < max_wait:
        result = query_video_task(task_id)
        status = result.get("status", "")
        if status == "succeeded":
            update_stats("success_videos")
            return {"success": True, "video_url": result.get("content", {}).get("video_url", "")}
        elif status in ["failed", "cancelled"]:
            update_stats("failed_videos")
            return {"success": False, "error": f"任务{status}"}
        time.sleep(5)
    update_stats("failed_videos")
    return {"success": False, "error": "生成超时"}


# ============================================
# 异步流水线
# ============================================

def run_pipeline_async(task_id, product_name, product_features, style, num_scenes):
    def update_task(data):
        with tasks_lock:
            if task_id in pipeline_tasks:
                pipeline_tasks[task_id].update(data)
    
    try:
        # Step 1: 文案
        send_feishu_text(f"🎬 [{product_name}] 开始生成文案...")
        copy_result = generate_copywriting(product_name, product_features, style)
        if not copy_result.get("success"):
            error = f"文案生成失败: {copy_result.get('error')}"
            update_task({"status": "failed", "error": error})
            send_feishu_text(f"❌ [{product_name}] {error}")
            return
        
        copywriting = copy_result["content"]
        update_task({"copywriting": copywriting, "status": "generating_storyboard"})
        send_feishu_text(f"✅ [{product_name}] 文案完成\n📝 {copywriting[:80]}...")
        
        # Step 2: 分镜
        send_feishu_text(f"🎬 [{product_name}] 开始生成分镜...")
        storyboard_result = generate_storyboard(product_name, copywriting, num_scenes)
        if not storyboard_result.get("success"):
            error = f"分镜生成失败: {storyboard_result.get('error')}"
            update_task({"status": "failed", "error": error})
            send_feishu_text(f"❌ [{product_name}] {error}")
            return
        
        storyboard = storyboard_result["storyboard"]
        scenes = storyboard.get("scenes", [])
        update_task({"storyboard": storyboard, "status": "generating_scenes"})
        send_feishu_text(f"✅ [{product_name}] 分镜完成，共{len(scenes)}个镜头")
        
        # Step 3: 图片+视频
        scene_results = []
        for i, scene in enumerate(scenes):
            scene_num = i + 1
            send_feishu_text(f"🖼️ [{product_name}] 生成镜头 {scene_num}/{len(scenes)}...")
            
            scene_result = {
                "scene_id": scene.get("scene_id", scene_num),
                "description": scene.get("description", ""),
                "narration": scene.get("narration", ""),
                "image_url": None,
                "video_url": None
            }
            
            prompt = scene.get("prompt", scene.get("description", ""))
            img_result = generate_image_internal(prompt)
            
            if img_result.get("success"):
                scene_result["image_url"] = img_result["image_url"]
                
                # 生成视频
                video_prompt = scene.get("description", "产品展示，缓慢旋转")
                task_result = create_video_task(scene_result["image_url"], video_prompt)
                if task_result.get("success"):
                    video_result = wait_for_video_completion(task_result["task_id"])
                    if video_result.get("success"):
                        scene_result["video_url"] = video_result["video_url"]
            
            status_icon = "✅" if scene_result["video_url"] else ("🖼️" if scene_result["image_url"] else "❌")
            send_feishu_text(f"{status_icon} [{product_name}] 镜头{scene_num} 完成")
            
            scene_results.append(scene_result)
            update_task({"scenes": scene_results})
        
        # 完成
        update_task({"status": "completed", "scenes": scene_results})
        
        # 发送汇总
        success_images = len([s for s in scene_results if s.get("image_url")])
        success_videos = len([s for s in scene_results if s.get("video_url")])
        
        summary_lines = [
            f"🎉 [{product_name}] 流水线完成！",
            f"",
            f"📝 文案：{copywriting[:60]}...",
            f"",
            f"📊 结果：图片 {success_images}/{len(scenes)}，视频 {success_videos}/{len(scenes)}",
            f"",
            f"🎬 视频链接："
        ]
        for s in scene_results:
            if s.get("video_url"):
                summary_lines.append(f"镜头{s['scene_id']}: {s['video_url']}")
        
        send_feishu_text("\n".join(summary_lines))
        
    except Exception as e:
        update_task({"status": "failed", "error": str(e)})
        send_feishu_text(f"❌ [{product_name}] 流水线异常: {e}")


def process_generation_task(prompt, generate_video=False):
    try:
        image_result = generate_image_internal(prompt)
        if not image_result.get("success"):
            send_feishu_error(prompt, image_result.get("error", "未知错误"))
            return
        image_url = image_result.get("image_url", "")
        image_key, _ = upload_image_to_feishu(image_url)
        video_url = None
        if generate_video:
            task_result = create_video_task(image_url, f"{prompt}，产品展示，缓慢旋转")
            if task_result.get("success"):
                video_result = wait_for_video_completion(task_result["task_id"])
                video_url = video_result.get("video_url") if video_result.get("success") else None
        send_feishu_result(prompt, image_url, image_key, video_url)
    except Exception as e:
        send_feishu_error(prompt, str(e))


# ============================================
# 飞书回调
# ============================================

@app.route('/feishu-callback', methods=['POST'])
def feishu_callback():
    data = request.get_json() or {}
    
    if 'challenge' in data:
        return jsonify({"challenge": data['challenge']})
    
    try:
        if data.get('header', {}).get('event_type') == 'im.message.receive_v1':
            message = data.get('event', {}).get('message', {})
            if message.get('message_type') != 'text':
                return jsonify({"code": 0})
            
            text = json.loads(message.get('content', '{}')).get('text', '')
            print(f"[飞书消息] 收到: {text}")
            
            if "统计" in text or "报告" in text:
                send_stats_report()
                return jsonify({"code": 0})
            
            prompt = re.sub(r'@\S+', '', text).strip()
            
            # 流水线触发
            if "流水线" in text:
                parts = prompt.replace("流水线", "").strip().split(" ", 1)
                product_name = parts[0].strip() if len(parts) > 0 else ""
                product_features = parts[1].strip() if len(parts) > 1 else ""
                
                if product_name:
                    task_id = str(uuid.uuid4())[:8]
                    with tasks_lock:
                        pipeline_tasks[task_id] = {
                            "task_id": task_id, "product_name": product_name,
                            "status": "started", "created_at": datetime.now().isoformat()
                        }
                    thread = threading.Thread(
                        target=run_pipeline_async,
                        args=(task_id, product_name, product_features, "专业", 4),
                        daemon=True
                    )
                    thread.start()
                    send_feishu_text(f"🚀 流水线已启动！\n产品：{product_name}\n任务ID：{task_id}\n预计10-15分钟完成")
                return jsonify({"code": 0})
            
            # 普通生成
            clean_prompt = prompt.replace("视频", "").strip()
            if clean_prompt and len(clean_prompt) >= 2:
                generate_video = "视频" in text
                thread = threading.Thread(target=process_generation_task, args=(clean_prompt, generate_video), daemon=True)
                thread.start()
    except Exception as e:
        print(f"[飞书回调] 异常: {e}")
    
    return jsonify({"code": 0})


# ============================================
# HTTP API
# ============================================

@app.route('/generate-image', methods=['POST'])
def generate_image_api():
    data = request.get_json() or {}
    prompt = data.get("prompt", "")
    if not prompt:
        return jsonify({"success": False, "error": "请输入提示词"}), 400
    result = generate_image_internal(prompt)
    if result.get("success"):
        return jsonify({"success": True, "image_urls": [result.get("image_url")]})
    return jsonify({"success": False, "error": result.get("error")}), 500


@app.route('/generate-video', methods=['POST'])
def generate_video_api():
    data = request.get_json() or {}
    image_url = data.get("image_url", "")
    if not image_url:
        return jsonify({"success": False, "error": "请输入图片URL"}), 400
    task_result = create_video_task(image_url, data.get("prompt", "产品展示"))
    if not task_result.get("success"):
        return jsonify(task_result), 500
    video_result = wait_for_video_completion(task_result["task_id"])
    if video_result.get("success"):
        return jsonify({"success": True, "video_url": video_result.get("video_url")})
    return jsonify({"success": False, "error": video_result.get("error")}), 500


@app.route('/generate-all', methods=['POST'])
def generate_all_api():
    data = request.get_json() or {}
    prompt = data.get("prompt", "")
    if not prompt:
        return jsonify({"success": False, "error": "请输入提示词"}), 400
    result = {"prompt": prompt, "image_url": None, "video_url": None, "success": False}
    image_result = generate_image_internal(prompt)
    if not image_result.get("success"):
        result["error"] = image_result.get("error")
        return jsonify(result), 500
    result["image_url"] = image_result.get("image_url")
    result["success"] = True
    if data.get("generate_video", False):
        task_result = create_video_task(result["image_url"], f"{prompt}，产品展示")
        if task_result.get("success"):
            video_result = wait_for_video_completion(task_result["task_id"])
            result["video_url"] = video_result.get("video_url") if video_result.get("success") else None
    return jsonify(result)


@app.route('/generate-copy', methods=['POST'])
def generate_copy_api():
    data = request.get_json() or {}
    product_name = data.get("product_name", "")
    if not product_name:
        return jsonify({"success": False, "error": "请输入产品名称"}), 400
    return jsonify(generate_copywriting(product_name, data.get("product_features", ""), data.get("style", "专业")))


@app.route('/generate-storyboard', methods=['POST'])
def generate_storyboard_api():
    data = request.get_json() or {}
    if not data.get("product_name") or not data.get("copywriting"):
        return jsonify({"success": False, "error": "请输入产品名称和文案"}), 400
    return jsonify(generate_storyboard(data["product_name"], data["copywriting"], data.get("num_scenes", 4)))


@app.route('/pipeline/start', methods=['POST'])
def start_pipeline_api():
    """启动流水线（异步）- n8n调用这个接口"""
    data = request.get_json() or {}
    product_name = data.get("product_name", "")
    if not product_name:
        return jsonify({"success": False, "error": "请输入产品名称"}), 400
    
    task_id = str(uuid.uuid4())[:8]
    with tasks_lock:
        pipeline_tasks[task_id] = {
            "task_id": task_id,
            "product_name": product_name,
            "status": "started",
            "created_at": datetime.now().isoformat()
        }
    
    thread = threading.Thread(
        target=run_pipeline_async,
        args=(task_id, product_name, data.get("product_features", ""), data.get("style", "专业"), data.get("num_scenes", 4)),
        daemon=True
    )
    thread.start()
    
    return jsonify({"success": True, "task_id": task_id, "message": "流水线已启动，结果将发送到飞书"})


@app.route('/pipeline/status/<task_id>', methods=['GET'])
def get_pipeline_status(task_id):
    with tasks_lock:
        task = pipeline_tasks.get(task_id)
    if not task:
        return jsonify({"success": False, "error": "任务不存在"}), 404
    return jsonify({"success": True, "task": task})


@app.route('/test-notify', methods=['GET'])
def test_notify():
    success = send_feishu_message("🧪 测试通知 v3.0", [
        [{"tag": "text", "text": f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"}],
        [{"tag": "text", "text": f"图片模型: {IMAGE_MODEL}"}],
        [{"tag": "text", "text": f"视频模型: {VIDEO_MODEL}"}],
        [{"tag": "text", "text": f"文案模型: {CHAT_MODEL}"}]
    ])
    return jsonify({"success": success})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 即梦AI代理服务 v3.0 启动 - 端口: {port}")
    app.run(host="0.0.0.0", port=port)
