"""
即梦AI Flask代理服务 - 专业版 v4.0
=====================================
功能：
1. 分步骤生成：文案 → 分镜 → 图片(多张可选) → 视频
2. 每个步骤可编辑Prompt
3. 图片多选功能
4. 历史记录
5. 飞书通知

API设计：
- POST /api/generate-copy - 生成文案
- POST /api/generate-storyboard - 生成分镜
- POST /api/generate-images - 生成图片(可多张)
- POST /api/generate-video - 生成视频
- GET /api/history - 获取历史记录
- GET /api/project/<id> - 获取项目详情
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

app = Flask(__name__, static_folder='static')

# ============================================
# 🔑 配置区域
# ============================================
ARK_API_KEY = os.environ.get("ARK_API_KEY", "5adb80da-3c5f-4ea4-99d8-e73e78899ba7")

IMAGE_API_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
VIDEO_API_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
CHAT_API_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

IMAGE_MODEL = "doubao-seedream-4-0-250828"
VIDEO_MODEL = "doubao-seedance-1-5-pro-251215"
CHAT_MODEL = "doubao-pro-32k-241215"

FEISHU_BOT_WEBHOOK = os.environ.get("FEISHU_BOT_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/d8a5016b-99ac-413a-bba4-3e47d892a1af")

# 项目存储（内存）
projects = {}
projects_lock = threading.Lock()

# 风格模板
STYLE_TEMPLATES = {
    "科技简约": {
        "copy_style": "简洁专业，突出科技感和产品参数，用数据说话",
        "image_suffix": "minimalist tech style, clean white background, soft studio lighting, professional product photography, 8K",
        "video_suffix": "slow smooth camera movement, subtle reflections, professional product showcase, 5 seconds"
    },
    "年轻时尚": {
        "copy_style": "活泼有趣，贴近年轻人，使用流行语和emoji",
        "image_suffix": "vibrant colorful background, dynamic angle, trendy lifestyle photography, young energetic vibe, 8K",
        "video_suffix": "energetic movement, colorful light effects, dynamic transitions, 5 seconds"
    },
    "商务专业": {
        "copy_style": "稳重大气，强调效率和可靠性，适合企业采购",
        "image_suffix": "elegant business setting, dark professional background, dramatic lighting, executive style, 8K",
        "video_suffix": "slow elegant camera pan, sophisticated atmosphere, business professional, 5 seconds"
    },
    "电竞酷炫": {
        "copy_style": "热血激情，强调性能和游戏体验，使用电竞术语",
        "image_suffix": "RGB lighting effects, dark gaming setup, neon glow, cyberpunk style, dramatic angles, 8K",
        "video_suffix": "fast dynamic movement, RGB light animations, gaming energy, intense atmosphere, 5 seconds"
    }
}


# ============================================
# 工具函数
# ============================================

def send_feishu_text(text):
    try:
        requests.post(FEISHU_BOT_WEBHOOK, json={"msg_type": "text", "content": {"text": text}}, timeout=10)
    except:
        pass


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
    return send_from_directory('static', 'index.html')


@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)


# ============================================
# API: 基础信息
# ============================================

@app.route('/api/info', methods=['GET'])
def api_info():
    return jsonify({
        "service": "即梦AI视频生成器",
        "version": "v4.0-pro",
        "styles": list(STYLE_TEMPLATES.keys())
    })


@app.route('/api/styles', methods=['GET'])
def get_styles():
    return jsonify({"styles": list(STYLE_TEMPLATES.keys())})


# ============================================
# API: Step 1 - 生成文案
# ============================================

@app.route('/api/generate-copy', methods=['POST'])
def api_generate_copy():
    data = request.get_json() or {}
    product_name = data.get("product_name", "").strip()
    product_features = data.get("product_features", "").strip()
    style = data.get("style", "科技简约")
    
    if not product_name:
        return jsonify({"success": False, "error": "请输入产品名称"}), 400
    
    style_config = STYLE_TEMPLATES.get(style, STYLE_TEMPLATES["科技简约"])
    
    system_prompt = f"""你是一位专业的电商短视频文案专家。
风格要求：{style_config['copy_style']}

请根据产品信息生成30秒短视频的营销文案。
要求：
1. 文案简洁有力，适合配音朗读
2. 突出产品卖点和优势  
3. 有吸引力的开头和行动号召结尾
4. 总字数控制在80-120字
5. 分成4-5个自然段落，每段对应一个镜头"""

    user_prompt = f"产品名称：{product_name}\n产品特点：{product_features}\n\n请生成营销文案："
    
    result = chat_completion(system_prompt, user_prompt)
    return jsonify(result)


# ============================================
# API: Step 2 - 生成分镜
# ============================================

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
    
    system_prompt = f"""你是一位专业的视频分镜师。请根据产品和文案生成{num_scenes}个分镜。

风格：{style}

请严格按照JSON格式输出，不要添加其他文字：
{{
  "scenes": [
    {{
      "scene_id": 1,
      "description": "画面描述（中文，详细描述画面内容）",
      "narration": "这个镜头的旁白文字（从文案中截取）",
      "image_prompt": "英文图片生成提示词，格式：A [detailed product/scene description], [camera angle], [lighting], [background/setting], {style_config['image_suffix']}",
      "video_prompt": "英文视频提示词，描述画面如何运动，{style_config['video_suffix']}"
    }}
  ]
}}

image_prompt要求：
1. 必须用英文
2. 详细描述产品外观、角度、光线、背景
3. 结尾加上风格后缀

video_prompt要求：
1. 必须用英文
2. 描述镜头运动（推进、环绕、缓慢移动等）
3. 描述画面元素的动态效果"""

    user_prompt = f"产品名称：{product_name}\n营销文案：{copywriting}\n\n请生成{num_scenes}个分镜（只输出JSON）："
    
    result = chat_completion(system_prompt, user_prompt)
    
    if result.get("success"):
        try:
            content = result["content"]
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                storyboard = json.loads(content[start:end])
                return jsonify({"success": True, "storyboard": storyboard})
            return jsonify({"success": False, "error": "无法解析JSON", "raw": content})
        except json.JSONDecodeError as e:
            return jsonify({"success": False, "error": f"JSON解析错误: {e}", "raw": result["content"]})
    
    return jsonify(result)


# ============================================
# API: Step 3 - 生成图片（支持多张）
# ============================================

@app.route('/api/generate-images', methods=['POST'])
def api_generate_images():
    data = request.get_json() or {}
    prompt = data.get("prompt", "").strip()
    count = min(data.get("count", 3), 4)  # 最多4张
    
    if not prompt:
        return jsonify({"success": False, "error": "请输入提示词"}), 400
    
    images = []
    for i in range(count):
        result = generate_image(prompt)
        if result.get("success"):
            images.append({"index": i + 1, "url": result["image_url"]})
        else:
            images.append({"index": i + 1, "url": None, "error": result.get("error")})
    
    success_count = len([img for img in images if img.get("url")])
    return jsonify({
        "success": success_count > 0,
        "images": images,
        "success_count": success_count,
        "total_count": count
    })


# ============================================
# API: Step 4 - 生成视频
# ============================================

@app.route('/api/generate-video', methods=['POST'])
def api_generate_video():
    data = request.get_json() or {}
    image_url = data.get("image_url", "").strip()
    prompt = data.get("prompt", "").strip()
    duration = data.get("duration", 5)
    
    if not image_url:
        return jsonify({"success": False, "error": "请提供图片URL"}), 400
    
    if not prompt:
        prompt = "Slow camera movement, professional product showcase, 5 seconds"
    
    # 创建任务
    task_result = create_video_task(image_url, prompt, duration)
    if not task_result.get("success"):
        return jsonify(task_result)
    
    # 等待完成
    video_result = wait_for_video(task_result["task_id"])
    return jsonify(video_result)


# ============================================
# API: 异步视频生成（返回task_id）
# ============================================

@app.route('/api/generate-video-async', methods=['POST'])
def api_generate_video_async():
    data = request.get_json() or {}
    image_url = data.get("image_url", "").strip()
    prompt = data.get("prompt", "").strip()
    duration = data.get("duration", 5)
    
    if not image_url:
        return jsonify({"success": False, "error": "请提供图片URL"}), 400
    
    if not prompt:
        prompt = "Slow camera movement, professional product showcase, 5 seconds"
    
    task_result = create_video_task(image_url, prompt, duration)
    return jsonify(task_result)


@app.route('/api/video-status/<task_id>', methods=['GET'])
def api_video_status(task_id):
    result = query_video_task(task_id)
    status = result.get("status", "unknown")
    video_url = result.get("content", {}).get("video_url", "") if status == "succeeded" else ""
    return jsonify({
        "task_id": task_id,
        "status": status,
        "video_url": video_url
    })


# ============================================
# API: 项目管理（历史记录）
# ============================================

@app.route('/api/project/create', methods=['POST'])
def create_project():
    data = request.get_json() or {}
    
    project_id = str(uuid.uuid4())[:8]
    project = {
        "id": project_id,
        "product_name": data.get("product_name", ""),
        "product_features": data.get("product_features", ""),
        "style": data.get("style", "科技简约"),
        "num_scenes": data.get("num_scenes", 4),
        "copywriting": "",
        "storyboard": None,
        "scenes": [],
        "status": "created",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    }
    
    with projects_lock:
        projects[project_id] = project
    
    return jsonify({"success": True, "project": project})


@app.route('/api/project/<project_id>', methods=['GET'])
def get_project(project_id):
    with projects_lock:
        project = projects.get(project_id)
    
    if not project:
        return jsonify({"success": False, "error": "项目不存在"}), 404
    
    return jsonify({"success": True, "project": project})


@app.route('/api/project/<project_id>', methods=['PUT'])
def update_project(project_id):
    data = request.get_json() or {}
    
    with projects_lock:
        if project_id not in projects:
            return jsonify({"success": False, "error": "项目不存在"}), 404
        
        project = projects[project_id]
        
        # 更新允许的字段
        for key in ["copywriting", "storyboard", "scenes", "status"]:
            if key in data:
                project[key] = data[key]
        
        project["updated_at"] = datetime.now().isoformat()
        projects[project_id] = project
    
    return jsonify({"success": True, "project": project})


@app.route('/api/projects', methods=['GET'])
def list_projects():
    with projects_lock:
        project_list = sorted(
            projects.values(),
            key=lambda x: x.get("created_at", ""),
            reverse=True
        )
    
    # 返回简化版列表
    simple_list = [{
        "id": p["id"],
        "product_name": p["product_name"],
        "style": p["style"],
        "status": p["status"],
        "created_at": p["created_at"]
    } for p in project_list]
    
    return jsonify({"success": True, "projects": simple_list})


@app.route('/api/project/<project_id>', methods=['DELETE'])
def delete_project(project_id):
    with projects_lock:
        if project_id in projects:
            del projects[project_id]
    
    return jsonify({"success": True})


# ============================================
# API: 飞书通知
# ============================================

@app.route('/api/notify', methods=['POST'])
def api_notify():
    data = request.get_json() or {}
    message = data.get("message", "")
    if message:
        send_feishu_text(message)
    return jsonify({"success": True})


# ============================================
# 飞书回调（保留旧版功能）
# ============================================

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "cli_a94e4446ee7adcce")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "xMNkE0bbPQAKBffUmImCkhIYwV6BK3iQ")


def get_feishu_tenant_token():
    try:
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        resp = requests.post(url, json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=10)
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


def send_feishu_message(title, content_blocks):
    try:
        message = {
            "msg_type": "post",
            "content": {"post": {"zh_cn": {"title": title, "content": content_blocks}}}
        }
        requests.post(FEISHU_BOT_WEBHOOK, json=message, timeout=10)
    except:
        pass


def send_feishu_result(prompt, image_url, image_key=None, video_url=None):
    content_blocks = [[{"tag": "text", "text": f"📝 提示词：{prompt}"}]]
    if image_key:
        content_blocks.append([{"tag": "img", "image_key": image_key}])
    if image_url:
        content_blocks.append([{"tag": "text", "text": "🖼️ "}, {"tag": "a", "text": "查看原图", "href": image_url}])
    if video_url:
        content_blocks.append([{"tag": "text", "text": "🎬 "}, {"tag": "a", "text": "查看视频", "href": video_url}])
    title = "✅ 图片+视频生成成功" if video_url else "✅ 素材生成成功"
    send_feishu_message(title, content_blocks)


def process_feishu_generation(prompt, generate_video=False):
    """飞书群触发的快速生成"""
    try:
        # 生成图片
        img_result = generate_image(prompt)
        if not img_result.get("success"):
            send_feishu_text(f"❌ 图片生成失败：{img_result.get('error')}")
            return
        
        image_url = img_result["image_url"]
        image_key = upload_image_to_feishu(image_url)
        
        video_url = None
        if generate_video:
            task_result = create_video_task(image_url, f"{prompt}，产品展示，缓慢旋转", 5)
            if task_result.get("success"):
                video_result = wait_for_video(task_result["task_id"])
                video_url = video_result.get("video_url") if video_result.get("success") else None
        
        send_feishu_result(prompt, image_url, image_key, video_url)
    except Exception as e:
        send_feishu_text(f"❌ 生成失败：{str(e)}")


def run_pipeline_feishu(product_name, product_features, num_scenes=4):
    """飞书群触发的流水线生成"""
    try:
        send_feishu_text(f"🎬 [{product_name}] 开始生成文案...")
        
        # 生成文案
        style_config = STYLE_TEMPLATES.get("科技简约")
        system_prompt = f"""你是一位专业的电商短视频文案专家。风格要求：{style_config['copy_style']}
请根据产品信息生成30秒短视频的营销文案，80-120字。"""
        user_prompt = f"产品名称：{product_name}\n产品特点：{product_features}\n\n请生成营销文案："
        copy_result = chat_completion(system_prompt, user_prompt)
        
        if not copy_result.get("success"):
            send_feishu_text(f"❌ [{product_name}] 文案生成失败")
            return
        
        copywriting = copy_result["content"]
        send_feishu_text(f"✅ [{product_name}] 文案完成\n📝 {copywriting[:80]}...")
        
        # 生成分镜
        send_feishu_text(f"🎬 [{product_name}] 开始生成分镜...")
        storyboard_prompt = f"""你是专业分镜师。生成{num_scenes}个分镜，JSON格式：
{{"scenes": [{{"scene_id": 1, "description": "画面描述", "image_prompt": "英文图片提示词", "video_prompt": "英文视频提示词"}}]}}"""
        sb_result = chat_completion(storyboard_prompt, f"产品：{product_name}\n文案：{copywriting}")
        
        if not sb_result.get("success"):
            send_feishu_text(f"❌ [{product_name}] 分镜生成失败")
            return
        
        # 解析分镜
        try:
            content = sb_result["content"]
            start = content.find("{")
            end = content.rfind("}") + 1
            storyboard = json.loads(content[start:end])
            scenes = storyboard.get("scenes", [])
        except:
            send_feishu_text(f"❌ [{product_name}] 分镜解析失败")
            return
        
        send_feishu_text(f"✅ [{product_name}] 分镜完成，共{len(scenes)}个镜头")
        
        # 生成图片和视频
        results = []
        for i, scene in enumerate(scenes):
            send_feishu_text(f"🖼️ [{product_name}] 生成镜头 {i+1}/{len(scenes)}...")
            
            prompt = scene.get("image_prompt", scene.get("description", ""))
            img_result = generate_image(prompt)
            
            video_url = None
            if img_result.get("success"):
                image_url = img_result["image_url"]
                video_prompt = scene.get("video_prompt", "slow camera movement, 5 seconds")
                task_result = create_video_task(image_url, video_prompt, 5)
                if task_result.get("success"):
                    video_result = wait_for_video(task_result["task_id"])
                    video_url = video_result.get("video_url") if video_result.get("success") else None
            
            results.append({"scene": i+1, "video_url": video_url})
            status = "✅" if video_url else "❌"
            send_feishu_text(f"{status} [{product_name}] 镜头{i+1} 完成")
        
        # 汇总
        success_count = len([r for r in results if r["video_url"]])
        summary = f"🎉 [{product_name}] 流水线完成！\n\n📝 文案：{copywriting[:60]}...\n\n🎬 视频 {success_count}/{len(results)}："
        for r in results:
            if r["video_url"]:
                summary += f"\n镜头{r['scene']}: {r['video_url']}"
        send_feishu_text(summary)
        
    except Exception as e:
        send_feishu_text(f"❌ [{product_name}] 流水线异常：{str(e)}")


@app.route('/feishu-callback', methods=['POST'])
def feishu_callback():
    data = request.get_json() or {}
    
    # 验证回调
    if 'challenge' in data:
        return jsonify({"challenge": data['challenge']})
    
    try:
        if data.get('header', {}).get('event_type') == 'im.message.receive_v1':
            message = data.get('event', {}).get('message', {})
            if message.get('message_type') != 'text':
                return jsonify({"code": 0})
            
            text = json.loads(message.get('content', '{}')).get('text', '')
            print(f"[飞书消息] 收到: {text}")
            
            # 统计报告
            if "统计" in text:
                with projects_lock:
                    count = len(projects)
                send_feishu_text(f"📊 当前有 {count} 个项目记录")
                return jsonify({"code": 0})
            
            # 提取提示词
            prompt = re.sub(r'@\S+', '', text).strip()
            
            # 流水线触发
            if "流水线" in text:
                parts = prompt.replace("流水线", "").strip().split(" ", 1)
                product_name = parts[0].strip() if len(parts) > 0 else ""
                product_features = parts[1].strip() if len(parts) > 1 else ""
                
                if product_name:
                    send_feishu_text(f"🚀 流水线已启动！\n产品：{product_name}\n预计10-15分钟完成")
                    thread = threading.Thread(
                        target=run_pipeline_feishu,
                        args=(product_name, product_features, 4),
                        daemon=True
                    )
                    thread.start()
                return jsonify({"code": 0})
            
            # 普通图片/视频生成
            clean_prompt = prompt.replace("视频", "").strip()
            if clean_prompt and len(clean_prompt) >= 2:
                generate_video = "视频" in text
                thread = threading.Thread(
                    target=process_feishu_generation,
                    args=(clean_prompt, generate_video),
                    daemon=True
                )
                thread.start()
    except Exception as e:
        print(f"[飞书回调] 异常: {e}")
    
    return jsonify({"code": 0})


# ============================================
# 飞书回调（保留旧版功能）
# ============================================

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "cli_a94e4446ee7adcce")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "xMNkE0bbPQAKBffUmImCkhIYwV6BK3iQ")


def get_feishu_tenant_token():
    try:
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        resp = requests.post(url, json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=10)
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


def send_feishu_rich_message(title, content_blocks):
    try:
        message = {
            "msg_type": "post",
            "content": {"post": {"zh_cn": {"title": title, "content": content_blocks}}}
        }
        requests.post(FEISHU_BOT_WEBHOOK, json=message, timeout=10)
    except:
        pass


def send_feishu_result(prompt, image_url, image_key=None, video_url=None):
    content_blocks = [[{"tag": "text", "text": f"📝 提示词：{prompt}"}]]
    if image_key:
        content_blocks.append([{"tag": "img", "image_key": image_key}])
    if image_url:
        content_blocks.append([{"tag": "text", "text": "🖼️ "}, {"tag": "a", "text": "查看原图", "href": image_url}])
    if video_url:
        content_blocks.append([{"tag": "text", "text": "🎬 "}, {"tag": "a", "text": "查看视频", "href": video_url}])
    title = "✅ 图片+视频生成成功" if video_url else "✅ 图片生成成功"
    send_feishu_rich_message(title, content_blocks)


def send_feishu_error(prompt, error_msg):
    send_feishu_rich_message("❌ 生成失败", [
        [{"tag": "text", "text": f"📝 提示词：{prompt}"}],
        [{"tag": "text", "text": f"❌ 错误：{error_msg}"}]
    ])


def send_stats_report():
    with projects_lock:
        total = len(projects)
        completed = len([p for p in projects.values() if p.get("status") == "completed"])
    send_feishu_rich_message("📈 生成统计报告", [
        [{"tag": "text", "text": f"📊 项目总数：{total}"}],
        [{"tag": "text", "text": f"✅ 已完成：{completed}"}],
        [{"tag": "text", "text": f"🌐 网页地址：https://wyzxhy.zeabur.app"}]
    ])


def process_simple_generation(prompt, generate_video=False):
    """简单模式：直接生成图片/视频"""
    try:
        # 生成图片
        img_result = generate_image(prompt)
        if not img_result.get("success"):
            send_feishu_error(prompt, img_result.get("error", "图片生成失败"))
            return
        
        image_url = img_result["image_url"]
        image_key = upload_image_to_feishu(image_url)
        
        video_url = None
        if generate_video:
            task_result = create_video_task(image_url, f"{prompt}, slow camera movement, professional product showcase", 5)
            if task_result.get("success"):
                video_result = wait_for_video(task_result["task_id"])
                if video_result.get("success"):
                    video_url = video_result["video_url"]
        
        send_feishu_result(prompt, image_url, image_key, video_url)
    except Exception as e:
        send_feishu_error(prompt, str(e))


def run_pipeline_from_feishu(product_name, product_features, style="科技简约", num_scenes=4):
    """飞书触发的完整流水线"""
    try:
        send_feishu_text(f"🎬 [{product_name}] 开始生成文案...")
        
        # 生成文案
        style_config = STYLE_TEMPLATES.get(style, STYLE_TEMPLATES["科技简约"])
        system_prompt = f"""你是一位专业的电商短视频文案专家。风格要求：{style_config['copy_style']}
请根据产品信息生成30秒短视频营销文案，80-120字，分4-5段。"""
        user_prompt = f"产品名称：{product_name}\n产品特点：{product_features}"
        
        copy_result = chat_completion(system_prompt, user_prompt)
        if not copy_result.get("success"):
            send_feishu_text(f"❌ [{product_name}] 文案生成失败")
            return
        
        copywriting = copy_result["content"]
        send_feishu_text(f"✅ [{product_name}] 文案完成\n📝 {copywriting[:80]}...")
        
        # 生成分镜
        send_feishu_text(f"🎬 [{product_name}] 开始生成分镜...")
        storyboard_prompt = f"""生成{num_scenes}个分镜，JSON格式：
{{"scenes": [{{"scene_id": 1, "description": "描述", "image_prompt": "英文prompt", "video_prompt": "英文视频prompt"}}]}}"""
        
        sb_result = chat_completion(storyboard_prompt, f"产品：{product_name}\n文案：{copywriting}")
        if not sb_result.get("success"):
            send_feishu_text(f"❌ [{product_name}] 分镜生成失败")
            return
        
        # 解析分镜
        content = sb_result["content"]
        start = content.find("{")
        end = content.rfind("}") + 1
        if start < 0 or end <= start:
            send_feishu_text(f"❌ [{product_name}] 分镜解析失败")
            return
        
        storyboard = json.loads(content[start:end])
        scenes = storyboard.get("scenes", [])
        send_feishu_text(f"✅ [{product_name}] 分镜完成，共{len(scenes)}个镜头")
        
        # 生成图片和视频
        results = []
        for i, scene in enumerate(scenes):
            send_feishu_text(f"🖼️ [{product_name}] 生成镜头 {i+1}/{len(scenes)}...")
            
            img_result = generate_image(scene.get("image_prompt", scene.get("description", "")))
            if not img_result.get("success"):
                results.append({"scene": i+1, "video_url": None})
                continue
            
            image_url = img_result["image_url"]
            
            # 生成视频
            task_result = create_video_task(image_url, scene.get("video_prompt", "slow camera movement"), 5)
            if task_result.get("success"):
                video_result = wait_for_video(task_result["task_id"])
                results.append({"scene": i+1, "video_url": video_result.get("video_url")})
            else:
                results.append({"scene": i+1, "video_url": None})
            
            status = "✅" if results[-1]["video_url"] else "⚠️"
            send_feishu_text(f"{status} [{product_name}] 镜头{i+1} 完成")
        
        # 汇总
        success_count = len([r for r in results if r["video_url"]])
        summary = f"🎉 [{product_name}] 流水线完成！\n\n📝 文案：{copywriting[:60]}...\n\n📊 视频：{success_count}/{len(scenes)} 个"
        for r in results:
            if r["video_url"]:
                summary += f"\n镜头{r['scene']}: {r['video_url']}"
        send_feishu_text(summary)
        
    except Exception as e:
        send_feishu_text(f"❌ [{product_name}] 流水线异常: {e}")


@app.route('/feishu-callback', methods=['POST'])
def feishu_callback():
    data = request.get_json() or {}
    
    # 验证回调
    if 'challenge' in data:
        return jsonify({"challenge": data['challenge']})
    
    try:
        if data.get('header', {}).get('event_type') == 'im.message.receive_v1':
            message = data.get('event', {}).get('message', {})
            if message.get('message_type') != 'text':
                return jsonify({"code": 0})
            
            text = json.loads(message.get('content', '{}')).get('text', '')
            print(f"[飞书消息] 收到: {text}")
            
            # 统计报告
            if "统计" in text or "报告" in text:
                send_stats_report()
                return jsonify({"code": 0})
            
            # 提取提示词（去掉@）
            prompt = re.sub(r'@\S+', '', text).strip()
            
            # 流水线模式
            if "流水线" in text:
                parts = prompt.replace("流水线", "").strip().split(" ", 1)
                product_name = parts[0].strip() if len(parts) > 0 else ""
                product_features = parts[1].strip() if len(parts) > 1 else ""
                
                if product_name:
                    send_feishu_text(f"🚀 流水线已启动！\n产品：{product_name}\n预计10-15分钟完成")
                    thread = threading.Thread(
                        target=run_pipeline_from_feishu,
                        args=(product_name, product_features),
                        daemon=True
                    )
                    thread.start()
                return jsonify({"code": 0})
            
            # 简单模式
            clean_prompt = prompt.replace("视频", "").strip()
            if clean_prompt and len(clean_prompt) >= 2:
                generate_video = "视频" in text
                thread = threading.Thread(
                    target=process_simple_generation,
                    args=(clean_prompt, generate_video),
                    daemon=True
                )
                thread.start()
    
    except Exception as e:
        print(f"[飞书回调] 异常: {e}")
    
    return jsonify({"code": 0})


# ============================================
# 健康检查
# ============================================

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "version": "v4.0"})


if __name__ == '__main__':
    # 确保static目录存在
    os.makedirs('static', exist_ok=True)
    
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 即梦AI视频生成器 v4.0 启动 - 端口: {port}")
    app.run(host="0.0.0.0", port=port)
