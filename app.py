"""
即梦AI Flask代理服务 v8.5 修复版
=================================
修复的BUG：
1. base64参考图处理
2. HTTP状态检查
3. 前端$函数兼容
4. Tab切换逻辑
5. 返回字段统一
"""

from flask import Flask, request, jsonify, Response
import requests
import json
import os
import re
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

# ========== 配置 ==========
JIMENG_FREE_API = os.environ.get("JIMENG_FREE_API", "https://wyzxhy168.zeabur.app")
JIMENG_SESSION_IDS = os.environ.get("JIMENG_SESSION_IDS", "")
JIMENG_IMAGE_MODEL = os.environ.get("JIMENG_IMAGE_MODEL", "jimeng-5.0")
JIMENG_VIDEO_MODEL_TEXT = "jimeng-video-3.5-pro"
JIMENG_VIDEO_MODEL_IMAGE = "jimeng-video-seedance-2.0"

ARK_API_KEY = os.environ.get("ARK_API_KEY", "")
CHAT_API_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
CHAT_MODEL = "doubao-1-5-pro-32k-250115"
VISION_MODEL = "doubao-1-5-vision-pro-32k-250115"

FEISHU_BOT_WEBHOOK = os.environ.get("FEISHU_BOT_WEBHOOK", "")

executor = ThreadPoolExecutor(max_workers=5)

# ========== 工具函数 ==========
def send_feishu(text, title=None):
    if not FEISHU_BOT_WEBHOOK: 
        return
    try:
        if title:
            requests.post(FEISHU_BOT_WEBHOOK, json={
                "msg_type": "interactive",
                "card": {
                    "header": {"title": {"tag": "plain_text", "content": title}},
                    "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": text}}]
                }
            }, timeout=10)
        else:
            requests.post(FEISHU_BOT_WEBHOOK, json={"msg_type": "text", "content": {"text": text}}, timeout=10)
    except Exception:
        pass

def chat(system, user, model=None):
    """文本对话"""
    if not ARK_API_KEY: 
        return {"success": False, "error": "未配置ARK_API_KEY"}
    try:
        resp = requests.post(
            CHAT_API_ENDPOINT,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {ARK_API_KEY}"},
            json={
                "model": model or CHAT_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ]
            },
            timeout=60
        )
        if resp.status_code != 200:
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        
        result = resp.json()
        choices = result.get("choices") or []
        if choices:
            content = choices[0].get("message", {}).get("content", "")
            return {"success": True, "content": content}
        return {"success": False, "error": result.get("error", {}).get("message", str(result))}
    except Exception as e:
        return {"success": False, "error": str(e)}

def vision(system, user, image_url=None, image_base64=None):
    """视觉对话"""
    if not ARK_API_KEY:
        return {"success": False, "error": "未配置ARK_API_KEY"}
    try:
        content = []
        if image_base64:
            # 处理base64前缀
            if "," in image_base64:
                image_base64 = image_base64.split(",")[1]
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
            })
        elif image_url:
            content.append({
                "type": "image_url",
                "image_url": {"url": image_url}
            })
        content.append({"type": "text", "text": user})
        
        resp = requests.post(
            CHAT_API_ENDPOINT,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {ARK_API_KEY}"},
            json={
                "model": VISION_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": content}
                ]
            },
            timeout=60
        )
        if resp.status_code != 200:
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        
        result = resp.json()
        choices = result.get("choices") or []
        if choices:
            return {"success": True, "content": choices[0].get("message", {}).get("content", "")}
        return {"success": False, "error": str(result)}
    except Exception as e:
        return {"success": False, "error": str(e)}

def gen_image(prompt, ratio="1:1", resolution="2k", ref_images=None, strength=0.5, model=None):
    """生成图片 - ref_images必须是URL列表"""
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {JIMENG_SESSION_IDS}"
        }
        
        # 分辨率统一转小写
        resolution = resolution.lower() if resolution else "2k"
        
        payload = {
            "model": model or JIMENG_IMAGE_MODEL,
            "prompt": prompt,
            "ratio": ratio,
            "resolution": resolution
        }
        
        if ref_images and len(ref_images) > 0:
            # 过滤掉非URL的参考图
            valid_refs = [r for r in ref_images if r and r.startswith("http")]
            if valid_refs:
                payload["images"] = valid_refs[:10]
                payload["sample_strength"] = strength
        
        print(f"[图片生成] 提示词: {prompt[:50]}... 比例:{ratio} 分辨率:{resolution}", flush=True)
        
        resp = requests.post(f"{JIMENG_FREE_API}/v1/images/generations", headers=headers, json=payload, timeout=180)
        
        print(f"[图片API] 状态码: {resp.status_code}", flush=True)
        
        if resp.status_code != 200:
            error_text = resp.text[:300]
            print(f"[图片API错误] {error_text}", flush=True)
            return {"success": False, "error": f"HTTP {resp.status_code}: {error_text[:100]}"}
        
        result = resp.json()
        print(f"[图片API返回] {str(result)[:500]}", flush=True)  # 调试：打印完整返回
        
        data = result.get("data") or []
        if data and len(data) > 0:
            url = data[0].get("url", "")
            if url:
                print(f"[图片成功] URL: {url[:60]}...", flush=True)
                return {"success": True, "url": url, "image_url": url}
            else:
                print(f"[图片警告] data存在但url为空", flush=True)
        
        error_msg = result.get("message") or result.get("error", {}).get("message") or str(result)[:200]
        print(f"[图片失败] {error_msg}", flush=True)
        return {"success": False, "error": error_msg}
    except requests.exceptions.Timeout:
        return {"success": False, "error": "图片生成超时(180秒)"}
    except Exception as e:
        print(f"[图片异常] {type(e).__name__}: {str(e)}", flush=True)
        return {"success": False, "error": str(e)}

def gen_video(prompt, image_url=None, duration=5, model=None, ratio="16:9"):
    """生成视频"""
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {JIMENG_SESSION_IDS}"
        }
        duration = max(4, min(duration, 15))
        
        if image_url:
            use_model = model or JIMENG_VIDEO_MODEL_IMAGE
            payload = {
                "model": use_model,
                "prompt": f"@1 {prompt}",
                "ratio": ratio,
                "duration": duration,
                "file_paths": [image_url]
            }
            print(f"[视频生成] 图生视频模式 模型:{use_model}", flush=True)
        else:
            use_model = model or JIMENG_VIDEO_MODEL_TEXT
            payload = {
                "model": use_model,
                "prompt": prompt,
                "ratio": ratio,
                "duration": duration
            }
            print(f"[视频生成] 纯文生视频模式 模型:{use_model}", flush=True)
        
        print(f"[视频生成] 提示词: {prompt[:50]}... 比例:{ratio} 时长:{duration}s", flush=True)
        
        resp = requests.post(f"{JIMENG_FREE_API}/v1/videos/generations", headers=headers, json=payload, timeout=600)
        
        print(f"[视频API] 状态码: {resp.status_code}", flush=True)
        
        if resp.status_code != 200:
            error_text = resp.text[:300]
            print(f"[视频API错误] {error_text}", flush=True)
            return {"success": False, "error": f"HTTP {resp.status_code}: {error_text[:100]}"}
        
        result = resp.json()
        data = result.get("data") or []
        if data and len(data) > 0:
            url = data[0].get("url", "")
            print(f"[视频成功] URL: {url[:60]}...", flush=True)
            return {"success": True, "url": url, "video_url": url}  # 兼容两种字段名
        
        error_msg = result.get("message") or result.get("error", {}).get("message") or str(result)
        return {"success": False, "error": error_msg}
    except requests.exceptions.Timeout:
        return {"success": False, "error": "视频生成超时(600秒)"}
    except Exception as e:
        print(f"[视频异常] {type(e).__name__}: {str(e)}", flush=True)
        return {"success": False, "error": str(e)}

def upload_base64_image(base64_data, prompt="参考图"):
    """将base64图片上传并获取URL（通过生成一张相似图片）"""
    # 即梦API不支持直接上传base64，需要通过图生图方式
    # 这里我们先生成一张图片获取URL，然后可以用这个URL作为参考
    # 注意：这会消耗一次生成配额
    result = gen_image(prompt)
    if result.get("success"):
        return result.get("url")
    return None

# ========== API路由 ==========

@app.route('/api/reverse-prompt', methods=['POST'])
def api_reverse_prompt():
    """反推提示词 - 分析图片生成提示词"""
    d = request.get_json() or {}
    img_url = (d.get("image_url") or "").strip()
    img_b64 = (d.get("image_base64") or "").strip()
    style = d.get("style", "detailed")
    
    if not img_url and not img_b64:
        return jsonify({"success": False, "error": "请提供图片"}), 400
    
    prompts = {
        "detailed": "详细分析这张图片，生成一段用于AI绘图的提示词。包括：主体描述、艺术风格、光影效果、色彩搭配、构图方式、细节特征。直接输出提示词，不要其他解释。",
        "simple": "用简洁的一句话描述这张图片的主要内容，适合用作AI绘图提示词。直接输出，不要解释。",
        "artistic": "以专业艺术家的视角分析这张图片，生成富有艺术感的AI绘图提示词，强调风格和氛围。直接输出提示词。"
    }
    
    r = vision("你是专业的AI绘图提示词工程师。", prompts.get(style, prompts["detailed"]), image_url=img_url, image_base64=img_b64)
    if r.get("success"):
        return jsonify({"success": True, "prompt": r["content"]})
    return jsonify(r)

@app.route('/api/optimize-prompt', methods=['POST'])
def api_optimize_prompt():
    """优化提示词"""
    d = request.get_json() or {}
    prompt = (d.get("prompt") or "").strip()
    style = d.get("style", "enhance")
    
    if not prompt:
        return jsonify({"success": False, "error": "请提供提示词"}), 400
    
    styles = {
        "enhance": "增强细节描述，添加光影效果、材质质感、氛围渲染等专业描述词",
        "artistic": "转化为艺术风格，添加艺术流派、著名画家风格、艺术技法等",
        "commercial": "转化为商业广告风格，强调产品质感、视觉冲击力和商业吸引力",
        "anime": "转化为日系动漫风格，添加动漫特有的描述词和风格特征"
    }
    
    r = chat(
        "你是AI绘图提示词专家，擅长优化和扩展提示词。",
        f"请优化以下提示词，{styles.get(style, styles['enhance'])}。\n\n原始提示词：{prompt}\n\n要求：\n1. 保持原始主题不变\n2. 添加专业的描述词\n3. 直接输出优化后的提示词，不要其他解释"
    )
    if r.get("success"):
        return jsonify({"success": True, "optimized": r["content"]})
    return jsonify(r)

@app.route('/api/batch-images', methods=['POST'])
def api_batch_images():
    """批量生图"""
    d = request.get_json() or {}
    prompt = (d.get("prompt") or "").strip()
    count = min(int(d.get("count", 4)), 40)
    ratio = d.get("ratio", "1:1")
    resolution = d.get("resolution", "2k")
    variations = d.get("variations", False)
    
    if not prompt:
        return jsonify({"success": False, "error": "请提供提示词"}), 400
    
    images = []
    errors = []
    
    def gen(i):
        p = f"{prompt}，variation {i}" if variations and i > 0 else prompt
        return gen_image(p, ratio, resolution)
    
    futures = {executor.submit(gen, i): i for i in range(count)}
    for f in as_completed(futures):
        i = futures[f]
        try:
            r = f.result()
            if r.get("success"):
                images.append({"i": i, "url": r.get("url") or r.get("image_url")})
            else:
                errors.append({"i": i, "err": r.get("error")})
        except Exception as e:
            errors.append({"i": i, "err": str(e)})
    
    images.sort(key=lambda x: x["i"])
    
    if images:
        send_feishu(f"批量生图完成：{len(images)}/{count}张", "🖼️ 批量生图")
    
    return jsonify({
        "success": len(images) > 0,
        "images": [x["url"] for x in images],
        "total": count,
        "done": len(images),
        "failed": len(errors),
        "errors": errors[:5] if errors else None
    })

@app.route('/api/images-to-video', methods=['POST'])
def api_images_to_video():
    """组图生视频"""
    d = request.get_json() or {}
    images = d.get("images", [])
    prompts = d.get("prompts", [])
    duration = d.get("duration", 5)
    ratio = d.get("ratio", "16:9")
    
    if not images:
        return jsonify({"success": False, "error": "请提供图片列表"}), 400
    
    videos = []
    errors = []
    
    def gen(i, img):
        p = prompts[i].strip() if i < len(prompts) and prompts[i].strip() else "smooth cinematic movement"
        return gen_video(p, image_url=img, duration=duration, ratio=ratio)
    
    futures = {executor.submit(gen, i, img): i for i, img in enumerate(images)}
    for f in as_completed(futures):
        i = futures[f]
        try:
            r = f.result()
            if r.get("success"):
                videos.append({"i": i, "url": r.get("url") or r.get("video_url")})
            else:
                errors.append({"i": i, "err": r.get("error")})
        except Exception as e:
            errors.append({"i": i, "err": str(e)})
    
    videos.sort(key=lambda x: x["i"])
    
    if videos:
        send_feishu(f"组图生视频完成：{len(videos)}/{len(images)}个", "🎬 组图生视频")
    
    return jsonify({
        "success": len(videos) > 0,
        "videos": [x["url"] for x in videos],
        "total": len(images),
        "done": len(videos),
        "failed": len(errors)
    })

@app.route('/api/merge-images', methods=['POST'])
def api_merge_images():
    """图片融合"""
    d = request.get_json() or {}
    images = d.get("images", [])
    prompt = (d.get("prompt") or "").strip() or "融合这些图片的风格和内容"
    strength = float(d.get("strength", 0.5))
    
    if len(images) < 2:
        return jsonify({"success": False, "error": "请至少提供2张图片URL"}), 400
    
    # 过滤有效的URL
    valid_images = [img for img in images if img and img.startswith("http")]
    if len(valid_images) < 2:
        return jsonify({"success": False, "error": "有效图片URL不足2张"}), 400
    
    r = gen_image(prompt, ref_images=valid_images, strength=strength)
    return jsonify(r)

@app.route('/api/style-transfer', methods=['POST'])
def api_style_transfer():
    """风格迁移"""
    d = request.get_json() or {}
    style_img = (d.get("style_image") or "").strip()
    content_img = (d.get("content_image") or "").strip()
    prompt = (d.get("prompt") or "").strip() or "将第一张图的艺术风格应用到第二张图的内容上"
    
    if not style_img or not content_img:
        return jsonify({"success": False, "error": "请提供风格图和内容图的URL"}), 400
    
    if not style_img.startswith("http") or not content_img.startswith("http"):
        return jsonify({"success": False, "error": "图片必须是有效的URL"}), 400
    
    r = gen_image(prompt, ref_images=[style_img, content_img], strength=0.6)
    return jsonify(r)

@app.route('/api/image-to-copy', methods=['POST'])
def api_image_to_copy():
    """智能文案 - 根据图片生成营销文案"""
    d = request.get_json() or {}
    img_url = (d.get("image_url") or "").strip()
    img_b64 = (d.get("image_base64") or "").strip()
    style = d.get("style", "douyin")
    product = (d.get("product_name") or "").strip()
    
    if not img_url and not img_b64:
        return jsonify({"success": False, "error": "请提供图片"}), 400
    
    styles = {
        "douyin": "抖音风格：简短有力，带emoji表情，吸引眼球，适合15秒短视频",
        "xiaohongshu": "小红书风格：亲切种草，分享真实体验，带话题标签#",
        "taobao": "淘宝风格：突出卖点和优惠，促销感强，引导下单",
        "formal": "正式风格：专业描述，突出品质和价值"
    }
    
    user_prompt = f"""分析这张图片，为{'产品【' + product + '】' if product else '图中产品'}生成营销文案。

风格要求：{styles.get(style, styles['douyin'])}

输出格式：
1. 主标题（10字以内）
2. 副标题（15字以内）  
3. 正文（50-100字）
4. 标签（3-5个）"""

    r = vision("你是专业的电商文案师和社交媒体运营专家。", user_prompt, image_url=img_url, image_base64=img_b64)
    if r.get("success"):
        return jsonify({"success": True, "copy": r["content"]})
    return jsonify(r)

@app.route('/api/batch-videos', methods=['POST'])
def api_batch_videos():
    """批量生视频"""
    d = request.get_json() or {}
    prompts = d.get("prompts", [])[:8]
    duration = d.get("duration", 5)
    ratio = d.get("ratio", "16:9")
    
    if not prompts:
        return jsonify({"success": False, "error": "请提供提示词列表"}), 400
    
    # 过滤空提示词
    prompts = [p.strip() for p in prompts if p and p.strip()]
    if not prompts:
        return jsonify({"success": False, "error": "没有有效的提示词"}), 400
    
    videos = []
    errors = []
    
    def gen(i, p):
        return gen_video(p, duration=duration, ratio=ratio)
    
    futures = {executor.submit(gen, i, p): i for i, p in enumerate(prompts)}
    for f in as_completed(futures):
        i = futures[f]
        try:
            r = f.result()
            if r.get("success"):
                videos.append({"i": i, "url": r.get("url") or r.get("video_url"), "prompt": prompts[i]})
            else:
                errors.append({"i": i, "err": r.get("error")})
        except Exception as e:
            errors.append({"i": i, "err": str(e)})
    
    videos.sort(key=lambda x: x["i"])
    
    if videos:
        send_feishu(f"批量生视频完成：{len(videos)}/{len(prompts)}个", "🎬 批量生视频")
    
    return jsonify({
        "success": len(videos) > 0,
        "videos": [{"url": x["url"], "prompt": x.get("prompt")} for x in videos],
        "total": len(prompts),
        "done": len(videos),
        "failed": len(errors)
    })

@app.route('/api/generate-copy', methods=['POST'])
def api_generate_copy():
    """生成文案"""
    d = request.get_json() or {}
    name = (d.get("product_name") or "").strip()
    features = (d.get("product_features") or "").strip()
    
    if not name:
        return jsonify({"success": False, "error": "请输入产品名称"}), 400
    
    prompt = f"为【{name}】写一段30-50字的短视频文案，要求朗朗上口、有感染力。"
    if features:
        prompt += f"产品特点：{features}"
    
    r = chat("你是专业的电商文案师，擅长写吸引人的短视频文案。", prompt)
    if r.get("success"):
        return jsonify({"success": True, "copy": r["content"]})
    return jsonify(r)

@app.route('/api/generate-storyboard', methods=['POST'])
def api_generate_storyboard():
    """生成分镜"""
    d = request.get_json() or {}
    name = (d.get("product_name") or "").strip()
    copy = (d.get("copywriting") or "").strip()
    count = int(d.get("count", 3))
    
    if not name:
        return jsonify({"success": False, "error": "请输入产品名称"}), 400
    
    prompt = f'''为【{name}】设计{count}个视频分镜。
文案参考：{copy if copy else '无'}

输出严格按照以下JSON格式，不要其他内容：
{{"scenes":[{{"image_prompt":"详细的图片描述，包含场景、人物、光影等","video_prompt":"视频动作描述，如镜头移动、人物动作等"}}]}}'''
    
    r = chat("你是专业的视频分镜师，擅长设计短视频分镜脚本。请严格按JSON格式输出。", prompt)
    if not r.get("success"):
        return jsonify(r)
    
    try:
        content = r["content"]
        # 提取JSON
        match = re.search(r'\{[\s\S]*\}', content)
        if match:
            storyboard = json.loads(match.group())
            return jsonify({"success": True, "storyboard": storyboard})
        return jsonify({"success": False, "error": "无法解析分镜脚本"})
    except json.JSONDecodeError as e:
        return jsonify({"success": False, "error": f"JSON解析错误: {str(e)}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/generate-images', methods=['POST'])
def api_generate_images():
    """生成图片"""
    d = request.get_json() or {}
    prompt = (d.get("prompt") or "").strip()
    count = min(int(d.get("count", 1)), 4)
    ratio = d.get("ratio", "1:1")
    resolution = d.get("resolution", "2k")
    ref = d.get("ref_image")
    
    if not prompt:
        return jsonify({"success": False, "error": "请提供提示词"}), 400
    
    images = []
    ref_imgs = None
    
    # 处理参考图
    if ref:
        if isinstance(ref, str) and ref.startswith("http"):
            ref_imgs = [ref]
        # 注意：base64参考图暂不支持，因为即梦API需要URL
        # 如果需要支持base64，需要先将图片上传到某个服务器获取URL
    
    for i in range(count):
        # 只有第一张图使用参考图
        r = gen_image(prompt, ratio, resolution, ref_images=ref_imgs if i == 0 else None)
        if r.get("success"):
            url = r.get("url") or r.get("image_url")
            if url:
                images.append(url)
    
    if not images:
        return jsonify({"success": False, "error": "所有图片生成失败"})
    
    return jsonify({"success": True, "images": images})

@app.route('/api/generate-video', methods=['POST'])
def api_generate_video():
    """生成视频"""
    d = request.get_json() or {}
    img = (d.get("image_url") or "").strip() or None
    prompt = (d.get("prompt") or "").strip()
    duration = int(d.get("duration", 5))
    model = (d.get("model") or "").strip() or None
    ratio = (d.get("ratio") or "").strip() or "16:9"
    
    if not prompt:
        return jsonify({"success": False, "error": "请提供视频描述"}), 400
    
    # 验证图片URL
    if img and not img.startswith("http"):
        img = None
    
    r = gen_video(prompt, img, duration, model, ratio)
    
    if r.get("success"):
        send_feishu(f"视频生成完成", "🎬 视频生成")
    
    return jsonify(r)

@app.route('/api/notify', methods=['POST'])
def api_notify():
    """飞书通知"""
    d = request.get_json() or {}
    send_feishu(d.get("message", ""), d.get("title"))
    return jsonify({"success": True})

@app.route('/api/templates', methods=['GET'])
def api_templates():
    """获取模板列表"""
    templates = [
        {"id": "product", "name": "📦 产品展示", "prompt": "商业产品摄影，{product}，纯白背景，专业棚拍打光，高清产品细节，4K画质"},
        {"id": "food", "name": "🍔 美食摄影", "prompt": "美食摄影，{product}，精致摆盘，暖色调灯光，食欲感，微距特写"},
        {"id": "fashion", "name": "👗 时尚穿搭", "prompt": "时尚杂志封面风格，{product}，模特展示，简约纯色背景，高级感"},
        {"id": "tech", "name": "💻 科技感", "prompt": "未来科技风格，{product}，蓝色霓虹光效，金属质感，深色背景"},
        {"id": "nature", "name": "🌿 自然清新", "prompt": "自然清新风格，{product}，绿植背景，自然阳光，清新氛围"},
        {"id": "luxury", "name": "✨ 高端奢华", "prompt": "奢华高端风格，{product}，金色元素，大理石背景，精致质感"},
        {"id": "cute", "name": "🎀 可爱萌系", "prompt": "可爱萌系风格，{product}，粉色系配色，柔和光线，少女心"},
        {"id": "chinese", "name": "🏮 国潮中式", "prompt": "国潮中国风，{product}，传统中国元素，红金配色，古典韵味"},
    ]
    return jsonify({"success": True, "templates": templates})

# ========== 页面 ==========

@app.route('/')
def index():
    return Response(HTML_PAGE, content_type='text/html; charset=utf-8')

@app.route('/health')
def health():
    return jsonify({"status": "ok", "version": "8.5-fixed"})

# ========== HTML页面 ==========
HTML_PAGE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI创作工具 v8.5</title>
<style>
:root{--p:linear-gradient(135deg,#667eea,#764ba2);--s:#00c853;--bg:linear-gradient(135deg,#1a1a2e,#16213e);--c:rgba(255,255,255,0.05);--t:#fff;--tm:#888}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,sans-serif;background:var(--bg);min-height:100vh;color:var(--t)}
.container{max-width:1000px;margin:0 auto;padding:20px}
.header{text-align:center;padding:20px 0}
.header h1{font-size:1.8em;background:var(--p);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.badge{display:inline-block;background:var(--s);color:#000;padding:3px 10px;border-radius:15px;font-size:11px;font-weight:bold;margin-left:8px}
.tabs{display:flex;gap:6px;margin-bottom:20px;flex-wrap:wrap}
.tab{flex:1;min-width:70px;padding:10px 6px;background:var(--c);border-radius:10px;text-align:center;cursor:pointer;transition:all .3s;font-size:12px}
.tab:hover{background:rgba(255,255,255,0.1)}
.tab.active{background:var(--p)}
.card{background:var(--c);border-radius:14px;padding:18px;margin-bottom:18px}
.card-title{font-size:15px;font-weight:600;margin-bottom:15px;display:flex;align-items:center;gap:8px}
.hidden{display:none!important}
.form-group{margin-bottom:14px}
.form-group label{display:block;margin-bottom:5px;color:var(--tm);font-size:13px}
.form-group input,.form-group textarea,.form-group select{width:100%;padding:10px 12px;background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.1);border-radius:8px;color:var(--t);font-size:14px}
.form-group textarea{min-height:80px;resize:vertical}
.form-row{display:flex;gap:10px;flex-wrap:wrap}
.form-row>*{flex:1;min-width:100px}
.btn{padding:10px 20px;border:none;border-radius:8px;font-size:14px;cursor:pointer;transition:all .3s}
.btn-primary{background:var(--p);color:#fff}
.btn-primary:hover{transform:translateY(-2px);box-shadow:0 4px 15px rgba(102,126,234,0.4)}
.btn-primary:disabled{opacity:0.5;cursor:not-allowed;transform:none}
.btn-secondary{background:rgba(255,255,255,0.1);color:#fff}
.btn-sm{padding:6px 12px;font-size:12px}
.btn-group{display:flex;gap:8px;flex-wrap:wrap}
.upload-area{border:2px dashed rgba(255,255,255,0.2);border-radius:12px;padding:25px;text-align:center;cursor:pointer;transition:all .3s}
.upload-area:hover{border-color:rgba(255,255,255,0.4);background:rgba(255,255,255,0.02)}
.preview-grid{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.preview-item{width:70px;height:70px;border-radius:8px;overflow:hidden;position:relative}
.preview-item img{width:100%;height:100%;object-fit:cover}
.preview-item .remove{position:absolute;top:2px;right:2px;width:18px;height:18px;background:rgba(255,0,0,0.8);border-radius:50%;border:none;color:#fff;cursor:pointer;font-size:10px}
.result-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;margin-top:15px}
.result-item{position:relative;border-radius:10px;overflow:hidden;aspect-ratio:1;background:rgba(0,0,0,0.3)}
.result-item img,.result-item video{width:100%;height:100%;object-fit:cover}
.result-item .overlay{position:absolute;bottom:0;left:0;right:0;padding:8px;background:linear-gradient(transparent,rgba(0,0,0,0.8));display:flex;gap:5px;justify-content:center;opacity:0;transition:opacity .3s}
.result-item:hover .overlay{opacity:1}
.progress-bar{height:4px;background:rgba(255,255,255,0.1);border-radius:2px;overflow:hidden;margin:12px 0}
.progress-fill{height:100%;background:var(--p);transition:width .3s}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.chip{padding:6px 14px;background:rgba(255,255,255,0.08);border-radius:20px;font-size:12px;cursor:pointer;transition:all .2s}
.chip:hover{background:rgba(255,255,255,0.15)}
.chip.active{background:var(--p)}
.stats{display:flex;gap:15px;margin-bottom:15px;flex-wrap:wrap}
.stat{background:rgba(255,255,255,0.05);padding:10px 15px;border-radius:8px;text-align:center}
.stat-val{font-size:20px;font-weight:bold;color:#667eea}
.stat-lbl{font-size:11px;color:var(--tm)}
.toast{position:fixed;bottom:20px;right:20px;background:#333;color:#fff;padding:12px 20px;border-radius:8px;z-index:9999;animation:slideIn .3s}
@keyframes slideIn{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}
.template-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px}
.template-item{background:rgba(255,255,255,0.05);padding:12px;border-radius:10px;cursor:pointer;text-align:center;transition:all .2s}
.template-item:hover{background:rgba(255,255,255,0.1);transform:translateY(-2px)}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>🎨 AI创作工具 <span class="badge">v8.5</span></h1>
<p style="color:var(--tm);margin-top:8px;font-size:13px">即梦AI Agent增强版</p>
</div>

<div class="tabs" id="mainTabs">
<div class="tab active" data-tab="image">🖼️ 图片</div>
<div class="tab" data-tab="video">🎬 视频</div>
<div class="tab" data-tab="reverse">🔍 反推</div>
<div class="tab" data-tab="batch">📦 批量</div>
<div class="tab" data-tab="merge">🎨 融合</div>
<div class="tab" data-tab="template">📋 模板</div>
<div class="tab" data-tab="workflow">⚡ 工作流</div>
</div>

<!-- 图片生成 -->
<div id="imageTab" class="tab-content">
<div class="card">
<div class="card-title">🖼️ 图片生成</div>
<div class="chips" id="styleChips">
<div class="chip active" data-style="realistic">📷 写实</div>
<div class="chip" data-style="anime">🎌 动漫</div>
<div class="chip" data-style="3d">🎮 3D</div>
<div class="chip" data-style="art">🎨 艺术</div>
<div class="chip" data-style="poster">📰 海报</div>
</div>
<div class="form-group">
<label>图片描述</label>
<textarea id="imagePrompt" placeholder="描述你想要的图片..."></textarea>
</div>
<div class="form-row">
<div class="form-group"><label>比例</label>
<select id="imageRatio"><option value="1:1">1:1</option><option value="16:9">16:9</option><option value="9:16">9:16</option><option value="4:3">4:3</option></select>
</div>
<div class="form-group"><label>画质</label>
<select id="imageRes"><option value="1k">1k</option><option value="2k" selected>2k</option><option value="4k">4k</option></select>
</div>
<div class="form-group"><label>数量</label>
<select id="imageCount"><option value="1">1张</option><option value="4" selected>4张</option></select>
</div>
</div>
<button class="btn btn-primary" style="width:100%" id="genImageBtn">🚀 生成图片</button>
</div>
<div id="imageResults" class="card hidden">
<div class="card-title">生成结果</div>
<div id="imageGrid" class="result-grid"></div>
</div>
</div>

<!-- 视频生成 -->
<div id="videoTab" class="tab-content hidden">
<div class="card">
<div class="card-title">🎬 视频生成</div>
<div class="form-group">
<label>视频描述</label>
<textarea id="videoPrompt" placeholder="描述视频内容..."></textarea>
</div>
<div class="form-row">
<div class="form-group"><label>模型</label>
<select id="videoModel"><option value="jimeng-video-3.5-pro">3.5 Pro（纯文生视频）</option><option value="jimeng-video-seedance-2.0">Seedance 2.0</option></select>
</div>
<div class="form-group"><label>比例</label>
<select id="videoRatio"><option value="16:9">16:9</option><option value="9:16">9:16</option><option value="1:1">1:1</option></select>
</div>
<div class="form-group"><label>时长</label>
<select id="videoDuration"><option value="5">5秒</option><option value="10">10秒</option></select>
</div>
</div>
<button class="btn btn-primary" style="width:100%" id="genVideoBtn">🚀 生成视频</button>
</div>
<div id="videoProgress" class="card hidden">
<div id="videoProgressText">生成中...</div>
<div class="progress-bar"><div id="videoProgressBar" class="progress-fill" style="width:0%"></div></div>
</div>
<div id="videoResults" class="card hidden">
<div class="card-title">生成结果</div>
<div id="videoGrid" class="result-grid"></div>
</div>
</div>

<!-- 反推提示词 -->
<div id="reverseTab" class="tab-content hidden">
<div class="card">
<div class="card-title">🔍 反推提示词</div>
<p style="color:var(--tm);font-size:13px;margin-bottom:15px">上传图片，AI分析生成提示词</p>
<div class="form-group">
<div class="upload-area" id="reverseUpload">
<div style="font-size:30px;margin-bottom:8px">🖼️</div>
<div>点击上传图片</div>
<input type="file" id="reverseInput" accept="image/*" hidden>
</div>
<div id="reversePreview" class="preview-grid" style="justify-content:center"></div>
</div>
<div class="chips" id="reverseStyleChips">
<div class="chip active" data-style="detailed">📝 详细</div>
<div class="chip" data-style="simple">✨ 简洁</div>
<div class="chip" data-style="artistic">🎨 艺术</div>
</div>
<button class="btn btn-primary" style="width:100%" id="reverseBtn">🔍 分析图片</button>
</div>
<div id="reverseResult" class="card hidden">
<div class="card-title">分析结果</div>
<textarea id="reversedPrompt" style="width:100%;min-height:100px;background:rgba(255,255,255,0.1);border:none;color:#fff;padding:12px;border-radius:8px"></textarea>
<div class="btn-group" style="margin-top:12px">
<button class="btn btn-secondary btn-sm" id="copyPromptBtn">📋 复制</button>
<button class="btn btn-secondary btn-sm" id="optimizeBtn">✨ 优化</button>
<button class="btn btn-primary btn-sm" id="usePromptBtn">🚀 用这个生成</button>
</div>
</div>
</div>

<!-- 批量生成 -->
<div id="batchTab" class="tab-content hidden">
<div class="card">
<div class="card-title">📦 批量生成</div>
<div class="chips" id="batchModeChips">
<div class="chip active" data-mode="images">🖼️ 批量生图</div>
<div class="chip" data-mode="videos">🎬 批量视频</div>
</div>
<div id="batchImagesMode">
<div class="form-group">
<label>提示词</label>
<textarea id="batchImagePrompt" placeholder="输入提示词，批量生成"></textarea>
</div>
<div class="form-row">
<div class="form-group"><label>数量</label>
<select id="batchImageCount"><option value="10">10张</option><option value="20">20张</option><option value="40">40张</option></select>
</div>
<div class="form-group"><label>比例</label>
<select id="batchImageRatio"><option value="1:1">1:1</option><option value="16:9">16:9</option><option value="9:16">9:16</option></select>
</div>
</div>
<label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--tm);margin-bottom:12px">
<input type="checkbox" id="batchVariations"> 生成变体
</label>
<button class="btn btn-primary" style="width:100%" id="batchImagesBtn">🚀 批量生成</button>
</div>
<div id="batchVideosMode" class="hidden">
<div class="form-group">
<label>提示词列表（每行一个）</label>
<textarea id="batchVideoPrompts" placeholder="小猫奔跑&#10;日出海滩&#10;城市夜景" style="min-height:120px"></textarea>
</div>
<button class="btn btn-primary" style="width:100%" id="batchVideosBtn">🚀 批量生成</button>
</div>
</div>
<div id="batchProgress" class="card hidden">
<div class="stats">
<div class="stat"><div class="stat-val" id="batchTotal">0</div><div class="stat-lbl">总数</div></div>
<div class="stat"><div class="stat-val" id="batchDone">0</div><div class="stat-lbl">完成</div></div>
<div class="stat"><div class="stat-val" id="batchFailed">0</div><div class="stat-lbl">失败</div></div>
</div>
<div class="progress-bar"><div id="batchProgressBar" class="progress-fill" style="width:0%"></div></div>
</div>
<div id="batchResults" class="card hidden">
<div class="card-title">批量结果</div>
<div id="batchGrid" class="result-grid"></div>
</div>
</div>

<!-- 融合 -->
<div id="mergeTab" class="tab-content hidden">
<div class="card">
<div class="card-title">🎨 图片融合</div>
<p style="color:var(--tm);font-size:13px;margin-bottom:15px">上传多张图片URL进行融合（需先生成图片获取URL）</p>
<div class="form-group">
<label>图片URL（每行一个，至少2个）</label>
<textarea id="mergeUrls" placeholder="https://example.com/image1.jpg&#10;https://example.com/image2.jpg" style="min-height:100px"></textarea>
</div>
<div class="form-group">
<label>融合指导（可选）</label>
<textarea id="mergePrompt" placeholder="如：融合这些图片的风格"></textarea>
</div>
<div class="form-group">
<label>融合强度: <span id="strengthVal">0.5</span></label>
<input type="range" id="mergeStrength" min="0.1" max="0.9" step="0.1" value="0.5">
</div>
<button class="btn btn-primary" style="width:100%" id="mergeBtn">🎨 融合生成</button>
</div>
<div id="mergeResult" class="card hidden">
<div class="card-title">融合结果</div>
<div id="mergeGrid" class="result-grid"></div>
</div>
</div>

<!-- 模板 -->
<div id="templateTab" class="tab-content hidden">
<div class="card">
<div class="card-title">📋 场景模板</div>
<div id="templateGrid" class="template-grid"></div>
</div>
<div id="templateForm" class="card hidden">
<div class="card-title" id="templateName">模板</div>
<div class="form-group">
<label>产品/主题</label>
<input type="text" id="templateProduct" placeholder="输入产品名称">
</div>
<button class="btn btn-primary" style="width:100%" id="useTemplateBtn">🚀 生成</button>
</div>
</div>

<!-- 工作流 -->
<div id="workflowTab" class="tab-content hidden">
<div class="card">
<div class="card-title">⚡ 一键工作流</div>
<p style="color:var(--tm);font-size:13px;margin-bottom:15px">产品→文案→分镜→图片→视频</p>
<div class="form-group">
<label>产品名称</label>
<input type="text" id="workflowName" placeholder="如：新款智能手表">
</div>
<div class="form-group">
<label>特点/卖点（可选）</label>
<textarea id="workflowFeatures" placeholder="超长续航、心率监测..."></textarea>
</div>
<div class="form-row">
<div class="form-group"><label>分镜数</label><select id="workflowScenes"><option value="3">3个</option><option value="5">5个</option></select></div>
</div>
<button class="btn btn-primary" style="width:100%" id="workflowBtn">⚡ 一键生成</button>
</div>
<div id="workflowProgress" class="card hidden">
<div id="workflowStep">准备中...</div>
<div class="progress-bar"><div id="workflowProgressBar" class="progress-fill" style="width:0%"></div></div>
</div>
<div id="workflowResults" class="card hidden">
<div class="card-title">📝 文案</div>
<p id="workflowCopy" style="color:#ccc;line-height:1.6;margin-bottom:20px"></p>
<div class="card-title">🎬 视频</div>
<div id="workflowGrid" class="result-grid"></div>
</div>
</div>
</div>

<script>
(function(){
// 工具函数
function $(s){return document.querySelector(s)}
function $$(s){return document.querySelectorAll(s)}
function showToast(m){var t=document.createElement('div');t.className='toast';t.textContent=m;document.body.appendChild(t);setTimeout(function(){t.remove()},3000)}

// 状态
var currentStyle='realistic';
var currentReverseStyle='detailed';
var currentBatchMode='images';
var reverseImageData=null;
var currentTemplate=null;
var STYLES={realistic:'超高清摄影，真实质感',anime:'日系动漫风格',art:'艺术插画',poster:'商业海报设计','3d':'3D渲染'};

// Tab切换
$$('#mainTabs .tab').forEach(function(tab){
    tab.onclick=function(){
        $$('#mainTabs .tab').forEach(function(t){t.classList.remove('active')});
        this.classList.add('active');
        var tabName=this.dataset.tab;
        $$('.tab-content').forEach(function(c){c.classList.add('hidden')});
        $('#'+tabName+'Tab').classList.remove('hidden');
        if(tabName==='template')loadTemplates();
    };
});

// 样式选择
$$('#styleChips .chip').forEach(function(c){
    c.onclick=function(){
        $$('#styleChips .chip').forEach(function(x){x.classList.remove('active')});
        this.classList.add('active');
        currentStyle=this.dataset.style;
    };
});

$$('#reverseStyleChips .chip').forEach(function(c){
    c.onclick=function(){
        $$('#reverseStyleChips .chip').forEach(function(x){x.classList.remove('active')});
        this.classList.add('active');
        currentReverseStyle=this.dataset.style;
    };
});

$$('#batchModeChips .chip').forEach(function(c){
    c.onclick=function(){
        $$('#batchModeChips .chip').forEach(function(x){x.classList.remove('active')});
        this.classList.add('active');
        currentBatchMode=this.dataset.mode;
        $('#batchImagesMode').classList.toggle('hidden',currentBatchMode!=='images');
        $('#batchVideosMode').classList.toggle('hidden',currentBatchMode!=='videos');
    };
});

// 融合强度滑块
$('#mergeStrength').oninput=function(){$('#strengthVal').textContent=this.value};

// 反推图片上传
$('#reverseUpload').onclick=function(){$('#reverseInput').click()};
$('#reverseInput').onchange=function(){
    if(this.files[0]){
        var reader=new FileReader();
        reader.onload=function(e){
            reverseImageData=e.target.result;
            $('#reversePreview').innerHTML='<div class="preview-item" style="width:120px;height:120px"><img src="'+reverseImageData+'"></div>';
        };
        reader.readAsDataURL(this.files[0]);
    }
};

// API调用封装
async function api(url,data){
    try{
        var resp=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
        if(!resp.ok){
            var errText=await resp.text();
            throw new Error('HTTP '+resp.status+': '+errText.substring(0,100));
        }
        return await resp.json();
    }catch(e){
        return {success:false,error:e.message};
    }
}

// 图片生成
$('#genImageBtn').onclick=async function(){
    var prompt=$('#imagePrompt').value.trim();
    if(!prompt){alert('请输入描述');return}
    
    this.disabled=true;this.textContent='⏳ 生成中...';
    
    var d=await api('/api/generate-images',{
        prompt:prompt+'，'+STYLES[currentStyle],
        count:parseInt($('#imageCount').value),
        ratio:$('#imageRatio').value,
        resolution:$('#imageRes').value
    });
    
    this.disabled=false;this.textContent='🚀 生成图片';
    
    if(d.success&&d.images&&d.images.length>0){
        $('#imageGrid').innerHTML=d.images.map(function(u){
            return'<div class="result-item"><img src="'+u+'"><div class="overlay"><button class="btn btn-sm" onclick="window.open(\''+u+'\')">查看</button></div></div>';
        }).join('');
        $('#imageResults').classList.remove('hidden');
    }else{
        alert('生成失败: '+(d.error||'未知错误'));
    }
};

// 视频生成
$('#genVideoBtn').onclick=async function(){
    var prompt=$('#videoPrompt').value.trim();
    if(!prompt){alert('请输入描述');return}
    
    this.disabled=true;this.textContent='⏳ 生成中...';
    $('#videoProgress').classList.remove('hidden');
    $('#videoProgressText').textContent='正在生成视频（约1-3分钟）...';
    $('#videoProgressBar').style.width='30%';
    
    var d=await api('/api/generate-video',{
        prompt:prompt,
        duration:parseInt($('#videoDuration').value),
        model:$('#videoModel').value,
        ratio:$('#videoRatio').value
    });
    
    this.disabled=false;this.textContent='🚀 生成视频';
    $('#videoProgress').classList.add('hidden');
    
    if(d.success&&(d.url||d.video_url)){
        var url=d.url||d.video_url;
        $('#videoProgressBar').style.width='100%';
        $('#videoGrid').innerHTML='<div class="result-item" style="aspect-ratio:16/9"><video src="'+url+'" controls></video></div>';
        $('#videoResults').classList.remove('hidden');
    }else{
        alert('生成失败: '+(d.error||'未知错误'));
    }
};

// 反推提示词
$('#reverseBtn').onclick=async function(){
    if(!reverseImageData){alert('请上传图片');return}
    
    this.disabled=true;this.textContent='⏳ 分析中...';
    
    var d=await api('/api/reverse-prompt',{image_base64:reverseImageData,style:currentReverseStyle});
    
    this.disabled=false;this.textContent='🔍 分析图片';
    
    if(d.success){
        $('#reversedPrompt').value=d.prompt;
        $('#reverseResult').classList.remove('hidden');
    }else{
        alert('分析失败: '+(d.error||'未知错误'));
    }
};

$('#copyPromptBtn').onclick=function(){
    navigator.clipboard.writeText($('#reversedPrompt').value);
    showToast('已复制');
};

$('#optimizeBtn').onclick=async function(){
    var p=$('#reversedPrompt').value;
    if(!p)return;
    
    this.disabled=true;
    var d=await api('/api/optimize-prompt',{prompt:p,style:'enhance'});
    this.disabled=false;
    
    if(d.success){
        $('#reversedPrompt').value=d.optimized;
        showToast('已优化');
    }
};

$('#usePromptBtn').onclick=function(){
    $('#imagePrompt').value=$('#reversedPrompt').value;
    $$('#mainTabs .tab').forEach(function(t){t.classList.remove('active')});
    $$('#mainTabs .tab')[0].classList.add('active');
    $$('.tab-content').forEach(function(c){c.classList.add('hidden')});
    $('#imageTab').classList.remove('hidden');
};

// 批量生图
$('#batchImagesBtn').onclick=async function(){
    var prompt=$('#batchImagePrompt').value.trim();
    if(!prompt){alert('请输入提示词');return}
    
    this.disabled=true;
    $('#batchProgress').classList.remove('hidden');
    $('#batchTotal').textContent=$('#batchImageCount').value;
    $('#batchDone').textContent='0';
    $('#batchFailed').textContent='0';
    $('#batchProgressBar').style.width='10%';
    
    var d=await api('/api/batch-images',{
        prompt:prompt+'，'+STYLES[currentStyle],
        count:parseInt($('#batchImageCount').value),
        ratio:$('#batchImageRatio').value,
        variations:$('#batchVariations').checked
    });
    
    this.disabled=false;
    $('#batchProgressBar').style.width='100%';
    $('#batchDone').textContent=d.done||0;
    $('#batchFailed').textContent=d.failed||0;
    
    if(d.images&&d.images.length>0){
        $('#batchGrid').innerHTML=d.images.map(function(u){
            return'<div class="result-item"><img src="'+u+'"></div>';
        }).join('');
        $('#batchResults').classList.remove('hidden');
    }else{
        alert('生成失败');
    }
};

// 批量生视频
$('#batchVideosBtn').onclick=async function(){
    var ps=$('#batchVideoPrompts').value.trim();
    if(!ps){alert('请输入提示词');return}
    
    var prompts=ps.split('\n').filter(function(p){return p.trim()});
    
    this.disabled=true;
    $('#batchProgress').classList.remove('hidden');
    $('#batchTotal').textContent=prompts.length;
    
    var d=await api('/api/batch-videos',{prompts:prompts,duration:5,ratio:'16:9'});
    
    this.disabled=false;
    $('#batchProgressBar').style.width='100%';
    $('#batchDone').textContent=d.done||0;
    
    if(d.videos&&d.videos.length>0){
        $('#batchGrid').innerHTML=d.videos.map(function(v){
            var url=typeof v==='string'?v:v.url;
            return'<div class="result-item" style="aspect-ratio:16/9"><video src="'+url+'" controls></video></div>';
        }).join('');
        $('#batchResults').classList.remove('hidden');
    }
};

// 图片融合
$('#mergeBtn').onclick=async function(){
    var urls=$('#mergeUrls').value.trim().split('\n').filter(function(u){return u.trim().startsWith('http')});
    if(urls.length<2){alert('请至少提供2个图片URL');return}
    
    this.disabled=true;this.textContent='⏳ 融合中...';
    
    var d=await api('/api/merge-images',{
        images:urls,
        prompt:$('#mergePrompt').value||'融合风格',
        strength:parseFloat($('#mergeStrength').value)
    });
    
    this.disabled=false;this.textContent='🎨 融合生成';
    
    if(d.success&&(d.url||d.image_url)){
        var url=d.url||d.image_url;
        $('#mergeGrid').innerHTML='<div class="result-item"><img src="'+url+'"></div>';
        $('#mergeResult').classList.remove('hidden');
    }else{
        alert('融合失败: '+(d.error||'未知错误'));
    }
};

// 模板
async function loadTemplates(){
    try{
        var resp=await fetch('/api/templates');
        var d=await resp.json();
        if(d.templates){
            $('#templateGrid').innerHTML=d.templates.map(function(t){
                return'<div class="template-item" data-id="'+t.id+'" data-prompt="'+t.prompt.replace(/"/g,'&quot;')+'">'+t.name+'</div>';
            }).join('');
            
            $$('#templateGrid .template-item').forEach(function(item){
                item.onclick=function(){
                    currentTemplate={id:this.dataset.id,prompt:this.dataset.prompt};
                    $('#templateName').textContent=this.textContent;
                    $('#templateForm').classList.remove('hidden');
                };
            });
        }
    }catch(e){}
}

$('#useTemplateBtn').onclick=function(){
    if(!currentTemplate)return;
    var product=$('#templateProduct').value.trim();
    if(!product){alert('请输入产品名称');return}
    
    var prompt=currentTemplate.prompt.replace('{product}',product);
    $('#imagePrompt').value=prompt;
    
    $$('#mainTabs .tab').forEach(function(t){t.classList.remove('active')});
    $$('#mainTabs .tab')[0].classList.add('active');
    $$('.tab-content').forEach(function(c){c.classList.add('hidden')});
    $('#imageTab').classList.remove('hidden');
};

// 工作流
$('#workflowBtn').onclick=async function(){
    var name=$('#workflowName').value.trim();
    if(!name){alert('请输入产品名称');return}
    
    this.disabled=true;
    $('#workflowProgress').classList.remove('hidden');
    $('#workflowResults').classList.add('hidden');
    
    try{
        // 1. 生成文案
        $('#workflowStep').textContent='📝 生成文案...';
        $('#workflowProgressBar').style.width='15%';
        var cd=await api('/api/generate-copy',{product_name:name,product_features:$('#workflowFeatures').value});
        if(!cd.success)throw new Error(cd.error||'文案生成失败');
        var copy=cd.copy;
        
        // 2. 生成分镜
        $('#workflowStep').textContent='🎬 生成分镜...';
        $('#workflowProgressBar').style.width='30%';
        var sd=await api('/api/generate-storyboard',{product_name:name,copywriting:copy,count:parseInt($('#workflowScenes').value)});
        if(!sd.success)throw new Error(sd.error||'分镜生成失败');
        var scenes=(sd.storyboard&&sd.storyboard.scenes)||[];
        
        if(scenes.length===0)throw new Error('未生成有效分镜');
        
        // 3. 生成图片 - 保存图片和场景的对应关系
        $('#workflowStep').textContent='🖼️ 生成图片...';
        var imageScenePairs=[];  // {image: url, scene: scene}
        for(var i=0;i<scenes.length;i++){
            $('#workflowProgressBar').style.width=(30+i*20/scenes.length)+'%';
            $('#workflowStep').textContent='🖼️ 生成图片 '+(i+1)+'/'+scenes.length+'...';
            var id=await api('/api/generate-images',{prompt:scenes[i].image_prompt,count:1});
            if(id.images&&id.images[0]){
                imageScenePairs.push({image:id.images[0],scene:scenes[i]});
            }
        }
        
        if(imageScenePairs.length===0)throw new Error('所有图片生成失败');
        
        // 4. 生成视频 - 使用配对的scene
        $('#workflowStep').textContent='🎬 生成视频...';
        var videos=[];
        for(var i=0;i<imageScenePairs.length;i++){
            var pair=imageScenePairs[i];
            $('#workflowProgressBar').style.width=(50+i*45/imageScenePairs.length)+'%';
            $('#workflowStep').textContent='🎬 生成视频 '+(i+1)+'/'+imageScenePairs.length+'...';
            var vd=await api('/api/generate-video',{
                prompt:pair.scene.video_prompt||'smooth cinematic movement',
                image_url:pair.image,
                duration:5
            });
            if(vd.url||vd.video_url)videos.push(vd.url||vd.video_url);
        }
        
        if(videos.length===0)throw new Error('所有视频生成失败');
        
        // 显示结果
        $('#workflowProgressBar').style.width='100%';
        $('#workflowStep').textContent='✅ 完成！';
        $('#workflowCopy').textContent=copy;
        $('#workflowGrid').innerHTML=videos.map(function(v){
            return'<div class="result-item" style="aspect-ratio:16/9"><video src="'+v+'" controls></video></div>';
        }).join('');
        $('#workflowResults').classList.remove('hidden');
        
    }catch(e){
        alert('工作流失败: '+e.message);
    }finally{
        this.disabled=false;
        $('#workflowProgress').classList.add('hidden');
    }
};

})();
</script>
</body>
</html>'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    print(f"🚀 即梦AI v8.5 修复版启动 - 端口: {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
