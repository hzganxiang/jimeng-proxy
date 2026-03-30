"""
即梦AI Flask代理服务 - 专业版 v4.1
=====================================
功能：
1. 网页操作：分步骤生成（文案→分镜→图片选择→视频）
2. 飞书群@触发：快速生成
3. 历史记录
"""

from flask import Flask, request, jsonify, send_from_directory
import requests
import json
import os
import re
import time
import threading
import uuid
from datetime import datetime

app = Flask(__name__, static_folder='.')

# ============================================
# 配置
# ============================================
ARK_API_KEY = os.environ.get("ARK_API_KEY", "5adb80da-3c5f-4ea4-99d8-e73e78899ba7")

IMAGE_API_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
VIDEO_API_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
CHAT_API_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

IMAGE_MODEL = "doubao-seedream-4-0-250828"
VIDEO_MODEL = "doubao-seedance-1-5-pro-251215"
CHAT_MODEL = "doubao-1-5-pro-32k-250115"

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "cli_a94e4446ee7adcce")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "xMNkE0bbPQAKBffUmImCkhIYwV6BK3iQ")
FEISHU_BOT_WEBHOOK = os.environ.get("FEISHU_BOT_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/d8a5016b-99ac-413a-bba4-3e47d892a1af")

# 存储
projects = {}
projects_lock = threading.Lock()

# 风格模板
STYLE_TEMPLATES = {
    "科技简约": {
        "copy_style": "简洁专业，突出科技感和产品参数",
        "image_suffix": "minimalist tech style, clean white background, soft studio lighting, professional product photography, 8K",
        "video_suffix": "slow smooth camera movement, professional product showcase, 5 seconds"
    },
    "年轻时尚": {
        "copy_style": "活泼有趣，贴近年轻人",
        "image_suffix": "vibrant colorful background, dynamic angle, trendy lifestyle photography, 8K",
        "video_suffix": "energetic movement, colorful light effects, 5 seconds"
    },
    "商务专业": {
        "copy_style": "稳重大气，强调效率和可靠性",
        "image_suffix": "elegant business setting, dark professional background, dramatic lighting, 8K",
        "video_suffix": "slow elegant camera pan, sophisticated atmosphere, 5 seconds"
    },
    "电竞酷炫": {
        "copy_style": "热血激情，强调性能和游戏体验",
        "image_suffix": "RGB lighting effects, dark gaming setup, neon glow, cyberpunk style, 8K",
        "video_suffix": "fast dynamic movement, RGB light animations, 5 seconds"
    }
}


# ============================================
# 基础工具函数
# ============================================

def send_feishu_text(text):
    try:
        requests.post(FEISHU_BOT_WEBHOOK, json={"msg_type": "text", "content": {"text": text}}, timeout=10)
    except:
        pass


def send_feishu_message(title, content_blocks):
    try:
        message = {"msg_type": "post", "content": {"post": {"zh_cn": {"title": title, "content": content_blocks}}}}
        requests.post(FEISHU_BOT_WEBHOOK, json=message, timeout=10)
    except:
        pass


def get_feishu_tenant_token():
    try:
        resp = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                           json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=10)
        data = resp.json()
        return data.get("tenant_access_token") if data.get("code") == 0 else None
    except:
        return None


def upload_image_to_feishu(image_url):
    try:
        token = get_feishu_tenant_token()
        if not token:
            return None
        img_resp = requests.get(image_url, timeout=60)
        if img_resp.status_code != 200:
            return None
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.post("https://open.feishu.cn/open-apis/im/v1/images", headers=headers,
                           files={"image": (f"ai_{int(time.time())}.jpg", img_resp.content, "image/jpeg")},
                           data={"image_type": "message"}, timeout=30)
        result = resp.json()
        return result.get("data", {}).get("image_key") if result.get("code") == 0 else None
    except:
        return None


def chat_completion(system_prompt, user_prompt):
    try:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {ARK_API_KEY}"}
        payload = {"model": CHAT_MODEL, "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]}
        response = requests.post(CHAT_API_ENDPOINT, headers=headers, json=payload, timeout=60)
        result = response.json()
        if "choices" in result and len(result["choices"]) > 0:
            return {"success": True, "content": result["choices"][0].get("message", {}).get("content", "")}
        return {"success": False, "error": result.get("error", {}).get("message", str(result))}
    except Exception as e:
        return {"success": False, "error": str(e)}


def generate_image(prompt, size="1024x1024"):
    try:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {ARK_API_KEY}"}
        payload = {"model": IMAGE_MODEL, "prompt": prompt, "size": size, "response_format": "url", "watermark": False}
        response = requests.post(IMAGE_API_ENDPOINT, headers=headers, json=payload, timeout=120)
        result = response.json()
        if "data" in result and len(result["data"]) > 0:
            return {"success": True, "image_url": result["data"][0].get("url", "")}
        return {"success": False, "error": result.get("error", {}).get("message", str(result))}
    except Exception as e:
        return {"success": False, "error": str(e)}


def create_video_task(image_url, prompt, duration=5):
    try:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {ARK_API_KEY}"}
        payload = {
            "model": VIDEO_MODEL,
            "content": [
                {"type": "image_url", "image_url": {"url": image_url}, "role": "first_frame"},
                {"type": "text", "text": prompt}
            ],
            "duration": duration,
            "resolution": "1080p"
        }
        response = requests.post(VIDEO_API_ENDPOINT, headers=headers, json=payload, timeout=30)
        result = response.json()
        if "id" in result:
            return {"success": True, "task_id": result["id"]}
        return {"success": False, "error": result.get("error", {}).get("message", str(result))}
    except Exception as e:
        return {"success": False, "error": str(e)}


def query_video_task(task_id):
    try:
        headers = {"Authorization": f"Bearer {ARK_API_KEY}"}
        response = requests.get(f"{VIDEO_API_ENDPOINT}/{task_id}", headers=headers, timeout=30)
        return response.json()
    except:
        return {"error": "查询失败"}


def wait_for_video(task_id, max_wait=300):
    start = time.time()
    while time.time() - start < max_wait:
        result = query_video_task(task_id)
        status = result.get("status", "")
        if status == "succeeded":
            return {"success": True, "video_url": result.get("content", {}).get("video_url", "")}
        elif status in ["failed", "cancelled"]:
            return {"success": False, "error": f"任务{status}"}
        time.sleep(5)
    return {"success": False, "error": "超时"}


# ============================================
# 静态页面
# ============================================

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "version": "v4.1"})


# ============================================
# 网页API：分步骤生成
# ============================================

@app.route('/api/info', methods=['GET'])
def api_info():
    return jsonify({"service": "即梦AI视频生成器", "version": "v4.1", "styles": list(STYLE_TEMPLATES.keys())})


@app.route('/api/generate-copy', methods=['POST'])
def api_generate_copy():
    data = request.get_json() or {}
    product_name = data.get("product_name", "").strip()
    product_features = data.get("product_features", "").strip()
    style = data.get("style", "科技简约")
    
    if not product_name:
        return jsonify({"success": False, "error": "请输入产品名称"}), 400
    
    style_config = STYLE_TEMPLATES.get(style, STYLE_TEMPLATES["科技简约"])
    system_prompt = f"""你是电商短视频文案专家。风格：{style_config['copy_style']}
生成30秒短视频营销文案，80-120字，分4-5段。"""
    user_prompt = f"产品：{product_name}\n特点：{product_features}\n\n生成文案："
    
    return jsonify(chat_completion(system_prompt, user_prompt))


@app.route('/api/generate-storyboard', methods=['POST'])
def api_generate_storyboard():
    data = request.get_json() or {}
    product_name = data.get("product_name", "").strip()
    copywriting = data.get("copywriting", "").strip()
    style = data.get("style", "科技简约")
    num_scenes = data.get("num_scenes", 4)
    
    if not product_name or not copywriting:
        return jsonify({"success": False, "error": "请输入产品名称和文案"}), 400
    
    style_config = STYLE_TEMPLATES.get(style, STYLE_TEMPLATES["科技简约"])
    system_prompt = f"""你是视频分镜师。生成{num_scenes}个分镜，JSON格式：
{{"scenes": [{{"scene_id": 1, "description": "画面描述", "narration": "旁白", "image_prompt": "英文图片提示词, {style_config['image_suffix']}", "video_prompt": "英文视频提示词, {style_config['video_suffix']}"}}]}}
只输出JSON。"""
    user_prompt = f"产品：{product_name}\n文案：{copywriting}"
    
    result = chat_completion(system_prompt, user_prompt)
    if result.get("success"):
        try:
            content = result["content"]
            start, end = content.find("{"), content.rfind("}") + 1
            if start >= 0 and end > start:
                return jsonify({"success": True, "storyboard": json.loads(content[start:end])})
            return jsonify({"success": False, "error": "无法解析JSON"})
        except:
            return jsonify({"success": False, "error": "JSON解析失败"})
    return jsonify(result)


@app.route('/api/generate-images', methods=['POST'])
def api_generate_images():
    data = request.get_json() or {}
    prompt = data.get("prompt", "").strip()
    count = min(data.get("count", 3), 4)
    
    if not prompt:
        return jsonify({"success": False, "error": "请输入提示词"}), 400
    
    images = []
    for i in range(count):
        result = generate_image(prompt)
        images.append({"index": i + 1, "url": result.get("image_url") if result.get("success") else None})
    
    return jsonify({"success": True, "images": images})


@app.route('/api/generate-video', methods=['POST'])
def api_generate_video():
    data = request.get_json() or {}
    image_url = data.get("image_url", "").strip()
    prompt = data.get("prompt", "slow camera movement, 5 seconds").strip()
    
    if not image_url:
        return jsonify({"success": False, "error": "请提供图片URL"}), 400
    
    task_result = create_video_task(image_url, prompt, 5)
    if not task_result.get("success"):
        return jsonify(task_result)
    
    return jsonify(wait_for_video(task_result["task_id"]))


@app.route('/api/notify', methods=['POST'])
def api_notify():
    data = request.get_json() or {}
    if data.get("message"):
        send_feishu_text(data["message"])
    return jsonify({"success": True})


# ============================================
# 飞书群@触发（快速模式）
# ============================================

def feishu_quick_generate(prompt, with_video=False):
    """飞书快速生成"""
    try:
        img_result = generate_image(prompt)
        if not img_result.get("success"):
            send_feishu_text(f"❌ 图片生成失败：{img_result.get('error')}")
            return
        
        image_url = img_result["image_url"]
        image_key = upload_image_to_feishu(image_url)
        
        video_url = None
        if with_video:
            task = create_video_task(image_url, f"{prompt}，产品展示，缓慢旋转", 5)
            if task.get("success"):
                video = wait_for_video(task["task_id"])
                video_url = video.get("video_url") if video.get("success") else None
        
        # 发送结果
        content = [[{"tag": "text", "text": f"📝 {prompt}"}]]
        if image_key:
            content.append([{"tag": "img", "image_key": image_key}])
        content.append([{"tag": "a", "text": "🖼️ 原图", "href": image_url}])
        if video_url:
            content.append([{"tag": "a", "text": "🎬 视频", "href": video_url}])
        send_feishu_message("✅ 生成完成", content)
    except Exception as e:
        send_feishu_text(f"❌ 生成失败：{e}")


def feishu_pipeline_generate(product_name, product_features):
    """飞书流水线生成"""
    try:
        send_feishu_text(f"🎬 [{product_name}] 开始生成...")
        
        # 文案
        style = STYLE_TEMPLATES["科技简约"]
        copy_result = chat_completion(f"电商文案专家，风格：{style['copy_style']}，生成80-120字营销文案", 
                                     f"产品：{product_name}\n特点：{product_features}")
        if not copy_result.get("success"):
            send_feishu_text(f"❌ [{product_name}] 文案失败")
            return
        copywriting = copy_result["content"]
        send_feishu_text(f"✅ 文案完成\n{copywriting[:80]}...")
        
        # 分镜
        sb_result = chat_completion(
            f'分镜师，生成4个分镜JSON：{{"scenes":[{{"scene_id":1,"description":"描述","image_prompt":"英文提示词","video_prompt":"视频提示词"}}]}}',
            f"产品：{product_name}\n文案：{copywriting}"
        )
        if not sb_result.get("success"):
            send_feishu_text(f"❌ [{product_name}] 分镜失败")
            return
        
        try:
            content = sb_result["content"]
            scenes = json.loads(content[content.find("{"):content.rfind("}")+1]).get("scenes", [])
        except:
            send_feishu_text(f"❌ [{product_name}] 分镜解析失败")
            return
        
        send_feishu_text(f"✅ 分镜完成，{len(scenes)}个镜头")
        
        # 生成图片视频
        videos = []
        for i, scene in enumerate(scenes):
            send_feishu_text(f"🖼️ 生成镜头 {i+1}/{len(scenes)}...")
            img = generate_image(scene.get("image_prompt", scene.get("description", "")))
            if img.get("success"):
                task = create_video_task(img["image_url"], scene.get("video_prompt", "slow movement"), 5)
                if task.get("success"):
                    video = wait_for_video(task["task_id"])
                    if video.get("success"):
                        videos.append(video["video_url"])
        
        # 汇总
        summary = f"🎉 [{product_name}] 完成！\n\n📝 文案：{copywriting[:60]}...\n\n🎬 视频 {len(videos)}/{len(scenes)}："
        for i, url in enumerate(videos):
            summary += f"\n镜头{i+1}: {url}"
        send_feishu_text(summary)
        
    except Exception as e:
        send_feishu_text(f"❌ [{product_name}] 异常：{e}")


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
            print(f"[飞书] {text}")
            
            prompt = re.sub(r'@\S+', '', text).strip()
            
            # 统计
            if "统计" in text:
                send_feishu_text(f"📊 项目数：{len(projects)}")
                return jsonify({"code": 0})
            
            # 流水线
            if "流水线" in text:
                parts = prompt.replace("流水线", "").strip().split(" ", 1)
                name = parts[0].strip() if parts else ""
                features = parts[1].strip() if len(parts) > 1 else ""
                if name:
                    send_feishu_text(f"🚀 流水线启动：{name}")
                    threading.Thread(target=feishu_pipeline_generate, args=(name, features), daemon=True).start()
                return jsonify({"code": 0})
            
            # 快速生成
            clean = prompt.replace("视频", "").strip()
            if clean and len(clean) >= 2:
                with_video = "视频" in text
                threading.Thread(target=feishu_quick_generate, args=(clean, with_video), daemon=True).start()
    
    except Exception as e:
        print(f"[飞书回调异常] {e}")
    
    return jsonify({"code": 0})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 即梦AI v4.1 启动 - 端口: {port}")
    app.run(host="0.0.0.0", port=port)
