"""
即梦AI Flask代理服务 - 完整修复版 v2.0
=====================================
功能：
1. 图片生成 (Seedream-4.0)
2. 图片自动下载 + 群里显示 + 保存到飞书云盘
3. 视频生成 (Seedance-1.5-pro, 5秒)
4. 批量处理支持
5. 结果统计汇总

修复内容：
- 视频模型名称修正为 doubao-seedance-1-5-pro-251215
- 添加线程安全锁
- 优化云盘上传逻辑
- 增强错误处理和日志
"""

from flask import Flask, request, jsonify
import requests
import json
import os
import re
import traceback
import time
import threading
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

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "cli_a94e4446ee7adcce")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "xMNkE0bbPQAKBffUmImCkhIYwV6BK3iQ")
FEISHU_BOT_WEBHOOK = os.environ.get("FEISHU_BOT_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/d8a5016b-99ac-413a-bba4-3e47d892a1af")

stats_lock = threading.Lock()
stats = {
    "total_images": 0, "success_images": 0, "failed_images": 0,
    "total_videos": 0, "success_videos": 0, "failed_videos": 0,
    "last_reset": datetime.now().isoformat()
}

def update_stats(key, increment=1):
    with stats_lock:
        stats[key] = stats.get(key, 0) + increment

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "service": "即梦AI代理服务",
        "version": "v2.0-fixed",
        "status": "running",
        "models": {"image": IMAGE_MODEL, "video": VIDEO_MODEL}
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

@app.route('/stats', methods=['GET'])
def get_stats():
    with stats_lock:
        return jsonify(stats.copy())

@app.route('/stats/reset', methods=['POST'])
def reset_stats():
    global stats
    with stats_lock:
        stats = {
            "total_images": 0, "success_images": 0, "failed_images": 0,
            "total_videos": 0, "success_videos": 0, "failed_videos": 0,
            "last_reset": datetime.now().isoformat()
        }
        return jsonify({"message": "统计已重置"})

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
        print(f"[飞书上传] 图片大小: {len(image_data)} bytes")
        
        upload_url = "https://open.feishu.cn/open-apis/im/v1/images"
        headers = {"Authorization": f"Bearer {token}"}
        filename = filename or f"ai_{int(time.time())}.jpg"
        
        resp = requests.post(upload_url, headers=headers, 
                           files={"image": (filename, image_data, "image/jpeg")},
                           data={"image_type": "message"}, timeout=30)
        result = resp.json()
        
        if result.get("code") == 0:
            image_key = result.get("data", {}).get("image_key")
            print(f"[飞书上传] 成功: {image_key}")
            return image_key, image_data
        else:
            print(f"[飞书上传] 失败: {result}")
            return None, image_data
    except Exception as e:
        print(f"[飞书上传] 异常: {e}")
        return None, None

def save_to_feishu_drive(image_data, filename):
    try:
        token = get_feishu_tenant_token()
        if not token:
            return None
        
        upload_url = "https://open.feishu.cn/open-apis/im/v1/files"
        headers = {"Authorization": f"Bearer {token}"}
        
        resp = requests.post(upload_url, headers=headers,
                           files={"file": (filename, image_data, "image/jpeg")},
                           data={"file_type": "image", "file_name": filename}, timeout=30)
        result = resp.json()
        
        if result.get("code") == 0:
            print(f"[云盘] 保存成功")
            return result.get("data", {}).get("file_key", "")
        return None
    except:
        return None

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

def send_feishu_result(prompt, image_url, image_key=None, video_url=None, cloud_saved=False):
    content_blocks = [[{"tag": "text", "text": f"📝 提示词：{prompt}"}]]
    
    if image_key:
        content_blocks.append([{"tag": "img", "image_key": image_key}])
    
    if image_url:
        content_blocks.append([{"tag": "text", "text": "🖼️ "}, {"tag": "a", "text": "查看原图", "href": image_url}])
    
    if video_url:
        content_blocks.append([{"tag": "text", "text": "🎬 "}, {"tag": "a", "text": "查看视频", "href": video_url}])
    
    if cloud_saved:
        content_blocks.append([{"tag": "text", "text": "☁️ 已保存到飞书"}])
    
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
        
        print(f"[视频生成] 创建任务, 模型: {VIDEO_MODEL}")
        response = requests.post(VIDEO_API_ENDPOINT, headers=headers, json=payload, timeout=30)
        result = response.json()
        print(f"[视频生成] 响应: {json.dumps(result, ensure_ascii=False)[:200]}")
        
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
        print(f"[视频查询] {task_id}: {status}")
        
        if status == "succeeded":
            update_stats("success_videos")
            return {"success": True, "video_url": result.get("content", {}).get("video_url", "")}
        elif status in ["failed", "cancelled"]:
            update_stats("failed_videos")
            return {"success": False, "error": f"任务{status}"}
        
        time.sleep(5)
    
    update_stats("failed_videos")
    return {"success": False, "error": "生成超时"}

def process_generation_task(prompt, generate_video=False):
    try:
        print(f"[任务] 开始: {prompt}, 视频: {generate_video}")
        
        image_result = generate_image_internal(prompt)
        if not image_result.get("success"):
            send_feishu_error(prompt, image_result.get("error", "未知错误"))
            return
        
        image_url = image_result.get("image_url", "")
        filename = f"ai_{int(time.time())}.jpg"
        image_key, image_data = upload_image_to_feishu(image_url, filename)
        
        cloud_saved = False
        if image_data:
            cloud_saved = save_to_feishu_drive(image_data, filename) is not None
        
        video_url = None
        if generate_video:
            print(f"[任务] 开始生成视频...")
            task_result = create_video_task(image_url, f"{prompt}，产品展示，缓慢旋转，专业摄影")
            if task_result.get("success"):
                video_result = wait_for_video_completion(task_result["task_id"])
                video_url = video_result.get("video_url") if video_result.get("success") else None
        
        send_feishu_result(prompt, image_url, image_key, video_url, cloud_saved)
        print(f"[任务] 完成")
    except Exception as e:
        print(f"[任务] 异常: {e}")
        send_feishu_error(prompt, str(e))

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
            clean_prompt = prompt.replace("视频", "").strip()
            
            if clean_prompt and len(clean_prompt) >= 2:
                generate_video = "视频" in text
                thread = threading.Thread(target=process_generation_task, args=(clean_prompt, generate_video), daemon=True)
                thread.start()
                print(f"[飞书消息] 任务已启动, 视频: {generate_video}")
    except Exception as e:
        print(f"[飞书回调] 异常: {e}")
    
    return jsonify({"code": 0})

@app.route('/generate-image', methods=['POST'])
def generate_image_api():
    data = request.get_json() or {}
    prompt = data.get("prompt", "")
    if not prompt:
        return jsonify({"success": False, "error": "请输入提示词"}), 400
    
    result = generate_image_internal(prompt, data.get("size", "1024x1024"))
    if result.get("success"):
        return jsonify({"success": True, "image_urls": [result.get("image_url")], "message": "图片生成成功"})
    return jsonify({"success": False, "error_message": result.get("error")}), 500

@app.route('/generate-video', methods=['POST'])
def generate_video_api():
    data = request.get_json() or {}
    image_url = data.get("image_url", "")
    if not image_url:
        return jsonify({"success": False, "error": "请输入图片URL"}), 400
    
    task_result = create_video_task(image_url, data.get("prompt", "产品展示，缓慢旋转"))
    if not task_result.get("success"):
        return jsonify(task_result), 500
    
    video_result = wait_for_video_completion(task_result["task_id"])
    if video_result.get("success"):
        return jsonify({"success": True, "video_url": video_result.get("video_url"), "message": "视频生成成功"})
    return jsonify({"success": False, "error_message": video_result.get("error")}), 500

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
        task_result = create_video_task(result["image_url"], f"{prompt}，产品展示，缓慢旋转")
        if task_result.get("success"):
            video_result = wait_for_video_completion(task_result["task_id"])
            result["video_url"] = video_result.get("video_url") if video_result.get("success") else None
    
    return jsonify(result)

@app.route('/test-notify', methods=['GET'])
def test_notify():
    success = send_feishu_message("🧪 测试通知", [
        [{"tag": "text", "text": f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"}],
        [{"tag": "text", "text": f"图片模型: {IMAGE_MODEL}"}],
        [{"tag": "text", "text": f"视频模型: {VIDEO_MODEL}"}]
    ])
    return jsonify({"message": "测试通知已发送", "success": success})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 即梦AI代理服务启动 - 端口: {port}")
    app.run(host="0.0.0.0", port=port)
