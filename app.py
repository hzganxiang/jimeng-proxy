"""
即梦AI Flask代理服务 v9.5
=================================
v9.5 更新：
1. 以图生图改为上传方式(前端上传base64→后端转URL)
2. 像素尺寸: 800x800/1080x1080/1440x1440/1440x1920/2048x2048
3. 独立分辨率选项 1K/2K/4K
4. 视频播放修复+复制URL按钮
5. 反推视觉模型环境变量+友好报错
6. 融合支持上传图片
7. 模板提示词大幅丰富
8. 比例补全(1:1/4:3/3:4/16:9/9:16/3:2/2:3/21:9)
9. 图片结果加复制URL按钮
10. 图片模型选择(5.0/4.6/4.5)
11. 视频模型扩展(+Seedance 2.0-fast)
12. 工作流展示中间图片结果
13. 工作流视频生成修复(回退机制)
"""

from flask import Flask, request, jsonify, Response
import requests, json, os, re, base64
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
CHAT_MODEL = os.environ.get("CHAT_MODEL", "doubao-1-5-pro-32k-250115")
VISION_MODEL = os.environ.get("VISION_MODEL", "doubao-1-5-vision-pro-32k-250115")

FEISHU_BOT_WEBHOOK = os.environ.get("FEISHU_BOT_WEBHOOK", "")
executor = ThreadPoolExecutor(max_workers=5)

# ========== 像素尺寸映射 ==========
PIXEL_TO_PARAMS = {
    "800x800":    {"ratio": "1:1", "resolution": "1k"},
    "1080x1080":  {"ratio": "1:1", "resolution": "2k"},
    "1440x1440":  {"ratio": "1:1", "resolution": "2k"},
    "1440x1920":  {"ratio": "3:4", "resolution": "2k"},
    "2048x2048":  {"ratio": "1:1", "resolution": "4k"},
}

# ========== 工具函数 ==========
def send_feishu(text, title=None):
    if not FEISHU_BOT_WEBHOOK: return
    try:
        if title:
            requests.post(FEISHU_BOT_WEBHOOK, json={"msg_type":"interactive","card":{"header":{"title":{"tag":"plain_text","content":title}},"elements":[{"tag":"div","text":{"tag":"plain_text","content":text}}]}}, timeout=10)
        else:
            requests.post(FEISHU_BOT_WEBHOOK, json={"msg_type":"text","content":{"text":text}}, timeout=10)
    except: pass

def chat(system, user, model=None):
    if not ARK_API_KEY: return {"success":False,"error":"未配置ARK_API_KEY"}
    try:
        resp = requests.post(CHAT_API_ENDPOINT, headers={"Content-Type":"application/json","Authorization":f"Bearer {ARK_API_KEY}"},
            json={"model":model or CHAT_MODEL,"messages":[{"role":"system","content":system},{"role":"user","content":user}]}, timeout=60)
        if resp.status_code != 200: return {"success":False,"error":f"HTTP {resp.status_code}: {resp.text[:200]}"}
        result = resp.json()
        choices = result.get("choices") or []
        if choices: return {"success":True,"content":choices[0].get("message",{}).get("content","")}
        return {"success":False,"error":result.get("error",{}).get("message",str(result))}
    except Exception as e: return {"success":False,"error":str(e)}

def vision(system, user, image_url=None, image_base64=None):
    if not ARK_API_KEY: return {"success":False,"error":"未配置ARK_API_KEY"}
    try:
        content = []
        if image_base64:
            if "," in image_base64: image_base64 = image_base64.split(",")[1]
            content.append({"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{image_base64}"}})
        elif image_url:
            content.append({"type":"image_url","image_url":{"url":image_url}})
        content.append({"type":"text","text":user})
        resp = requests.post(CHAT_API_ENDPOINT, headers={"Content-Type":"application/json","Authorization":f"Bearer {ARK_API_KEY}"},
            json={"model":VISION_MODEL,"messages":[{"role":"system","content":system},{"role":"user","content":content}]}, timeout=60)
        if resp.status_code != 200:
            err = resp.text[:300]
            if "ModelNotOpen" in err or "not activated" in err:
                return {"success":False,"error":f"视觉模型 {VISION_MODEL} 未开通，请在火山方舟控制台激活，或设置环境变量 VISION_MODEL 为已开通的模型"}
            return {"success":False,"error":f"HTTP {resp.status_code}: {err[:100]}"}
        result = resp.json()
        choices = result.get("choices") or []
        if choices: return {"success":True,"content":choices[0].get("message",{}).get("content","")}
        return {"success":False,"error":str(result)}
    except Exception as e: return {"success":False,"error":str(e)}

def gen_image(prompt, ratio="1:1", resolution="2k", ref_images=None, strength=0.5, model=None):
    try:
        headers = {"Content-Type":"application/json","Authorization":f"Bearer {JIMENG_SESSION_IDS}"}
        resolution = resolution.lower()
        payload = {"model":model or JIMENG_IMAGE_MODEL,"prompt":prompt,"ratio":ratio,"resolution":resolution}
        if ref_images:
            valid = [r for r in ref_images if r and r.startswith("http")]
            if valid: payload["images"] = valid[:10]; payload["sample_strength"] = strength
        print(f"[图片] {prompt[:40]}... 比例:{ratio} 分辨率:{resolution} 模型:{payload['model']}", flush=True)
        resp = requests.post(f"{JIMENG_FREE_API}/v1/images/generations", headers=headers, json=payload, timeout=180)
        if resp.status_code != 200: return {"success":False,"error":f"HTTP {resp.status_code}: {resp.text[:100]}"}
        data = resp.json().get("data") or []
        if data: return {"success":True,"url":data[0].get("url",""),"image_url":data[0].get("url","")}
        return {"success":False,"error":resp.json().get("message") or str(resp.json())}
    except requests.exceptions.Timeout: return {"success":False,"error":"图片生成超时(180秒)"}
    except Exception as e: return {"success":False,"error":str(e)}

def gen_video(prompt, image_url=None, duration=5, model=None, ratio="16:9"):
    try:
        headers = {"Content-Type":"application/json","Authorization":f"Bearer {JIMENG_SESSION_IDS}"}
        duration = max(4, min(duration, 15))
        if image_url:
            use_model = model or JIMENG_VIDEO_MODEL_IMAGE
            # Seedance格式：prompt直接描述动作，file_paths传图片URL
            payload = {"model":use_model,"prompt":prompt or "smooth cinematic movement","ratio":ratio,"duration":duration,"file_paths":[image_url]}
        else:
            use_model = model or JIMENG_VIDEO_MODEL_TEXT
            if "seedance" in (use_model or "").lower(): use_model = JIMENG_VIDEO_MODEL_TEXT
            payload = {"model":use_model,"prompt":prompt or "smooth cinematic movement","ratio":ratio,"duration":duration}
        print(f"[视频] {(prompt or 'no-prompt')[:40]}... 模型:{use_model} 比例:{ratio} 时长:{duration}s", flush=True)
        print(f"[视频payload] {json.dumps(payload, ensure_ascii=False)[:200]}", flush=True)
        resp = requests.post(f"{JIMENG_FREE_API}/v1/videos/generations", headers=headers, json=payload, timeout=600)
        print(f"[视频API] 状态码:{resp.status_code} 响应:{resp.text[:200]}", flush=True)
        if resp.status_code != 200: return {"success":False,"error":f"HTTP {resp.status_code}: {resp.text[:100]}"}
        data = resp.json().get("data") or []
        if data:
            url = data[0].get("url","")
            print(f"[视频成功] URL: {url[:80]}", flush=True)
            return {"success":True,"url":url,"video_url":url}
        return {"success":False,"error":resp.json().get("message") or str(resp.json())}
    except requests.exceptions.Timeout: return {"success":False,"error":"视频生成超时(600秒)"}
    except Exception as e: return {"success":False,"error":str(e)}

# ========== API路由 ==========
@app.route('/api/reverse-prompt', methods=['POST'])
def api_reverse_prompt():
    d = request.get_json() or {}
    img_url = (d.get("image_url") or "").strip()
    img_b64 = (d.get("image_base64") or "").strip()
    style = d.get("style","detailed")
    if not img_url and not img_b64: return jsonify({"success":False,"error":"请提供图片"}), 400
    prompts = {"detailed":"详细分析这张图片，生成一段用于AI绘图的提示词。包括：主体描述、艺术风格、光影效果、色彩搭配、构图方式、细节特征。直接输出提示词。",
        "simple":"用简洁的一句话描述这张图片的主要内容，适合用作AI绘图提示词。直接输出。",
        "artistic":"以专业艺术家的视角分析这张图片，生成富有艺术感的AI绘图提示词，强调风格和氛围。直接输出。"}
    r = vision("你是专业的AI绘图提示词工程师。", prompts.get(style,prompts["detailed"]), image_url=img_url, image_base64=img_b64)
    return jsonify({"success":True,"prompt":r["content"]}) if r.get("success") else jsonify(r)

@app.route('/api/optimize-prompt', methods=['POST'])
def api_optimize_prompt():
    d = request.get_json() or {}
    prompt = (d.get("prompt") or "").strip()
    if not prompt: return jsonify({"success":False,"error":"请提供提示词"}), 400
    styles = {"enhance":"增强细节描述，添加光影效果、材质质感、氛围渲染等","artistic":"转化为艺术风格","commercial":"转化为商业广告风格","anime":"转化为日系动漫风格"}
    r = chat("你是AI绘图提示词专家。", f"优化以下提示词，{styles.get(d.get('style','enhance'),styles['enhance'])}。\n原始：{prompt}\n直接输出优化后的提示词。")
    return jsonify({"success":True,"optimized":r["content"]}) if r.get("success") else jsonify(r)

@app.route('/api/generate-images', methods=['POST'])
def api_generate_images():
    d = request.get_json() or {}
    prompt = (d.get("prompt") or "").strip()
    count = min(int(d.get("count",1)),4)
    pixel_size = d.get("pixel_size","")
    ratio = d.get("ratio","1:1")
    resolution = d.get("resolution","2k")
    ref = d.get("ref_image")  # URL
    ref_b64 = d.get("ref_image_base64")  # base64
    strength = float(d.get("strength",0.5))
    img_model = d.get("model") or None
    if not prompt: return jsonify({"success":False,"error":"请提供提示词"}), 400
    if pixel_size and pixel_size in PIXEL_TO_PARAMS:
        p = PIXEL_TO_PARAMS[pixel_size]; ratio = p["ratio"]; resolution = p["resolution"]
    resolution = resolution.lower()

    # 以图生图：有base64参考图时，用chat completions接口（支持base64）
    if ref_b64:
        return _img2img_via_chat(prompt, ref_b64, ratio, resolution, img_model)

    ref_imgs = [ref] if ref and isinstance(ref, str) and ref.startswith("http") else None
    images, errors = [], []
    def gen(i): return gen_image(prompt, ratio, resolution, ref_images=ref_imgs, strength=strength, model=img_model)
    futures = {executor.submit(gen, i): i for i in range(count)}
    for f in as_completed(futures):
        try:
            r = f.result()
            if r.get("success"):
                url = r.get("url") or r.get("image_url")
                if url: images.append({"i":futures[f],"url":url})
            else: errors.append(r.get("error",""))
        except Exception as e: errors.append(str(e))
    images.sort(key=lambda x:x["i"])
    if not images: return jsonify({"success":False,"error":errors[0] if errors else "生成失败"})
    return jsonify({"success":True,"images":[x["url"] for x in images]})

def _img2img_via_chat(prompt, ref_b64, ratio, resolution, model):
    """以图生图：通过即梦chat completions接口，支持base64图片"""
    try:
        if "," in ref_b64: ref_b64 = ref_b64.split(",")[1]
        headers = {"Content-Type":"application/json","Authorization":f"Bearer {JIMENG_SESSION_IDS}"}
        payload = {
            "model": model or JIMENG_IMAGE_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{ref_b64}"}},
                    {"type": "text", "text": prompt}
                ]
            }]
        }
        print(f"[图生图] chat接口 提示词:{prompt[:40]}... 模型:{payload['model']}", flush=True)
        resp = requests.post(f"{JIMENG_FREE_API}/v1/chat/completions", headers=headers, json=payload, timeout=180)
        print(f"[图生图] 状态码:{resp.status_code}", flush=True)
        if resp.status_code != 200:
            return jsonify({"success":False,"error":f"HTTP {resp.status_code}: {resp.text[:100]}"})
        result = resp.json()
        # 从chat响应中提取图片URL
        choices = result.get("choices") or []
        if choices:
            content = choices[0].get("message",{}).get("content","")
            # 提取markdown图片链接 ![](url)
            urls = re.findall(r'!\[.*?\]\((https?://[^\)]+)\)', content)
            if urls:
                return jsonify({"success":True,"images":urls})
        return jsonify({"success":False,"error":"未能从响应中提取图片"})
    except Exception as e:
        print(f"[图生图异常] {e}", flush=True)
        return jsonify({"success":False,"error":str(e)})

@app.route('/api/generate-video', methods=['POST'])
def api_generate_video():
    d = request.get_json() or {}
    img = (d.get("image_url") or "").strip() or None
    img_b64 = d.get("image_base64") or None
    prompt = (d.get("prompt") or "").strip()
    duration = int(d.get("duration",5))
    model = (d.get("model") or "").strip() or None
    ratio = (d.get("ratio") or "").strip() or "16:9"

    # 如果有base64图片，先通过即梦生成获取URL
    if img_b64 and not img:
        print("[视频] 有base64参考图，先生成图片获取URL...", flush=True)
        url_result = _b64_to_url(img_b64, prompt or "参考图")
        if url_result:
            img = url_result
            print(f"[视频] 获取到图片URL: {img[:60]}", flush=True)
        else:
            return jsonify({"success":False,"error":"参考图上传失败，无法获取URL"})

    if not prompt and not img: return jsonify({"success":False,"error":"请提供视频描述或参考图"}), 400
    if img and not img.startswith("http"): img = None
    r = gen_video(prompt or "smooth cinematic movement", img, duration, model, ratio)
    if r.get("success"): send_feishu("视频生成完成","🎬 视频生成")
    return jsonify(r)

def _b64_to_url(b64_data, prompt="参考图"):
    """将base64图片通过即梦生成一张图获取URL"""
    try:
        if "," in b64_data: b64_data = b64_data.split(",")[1]
        headers = {"Content-Type":"application/json","Authorization":f"Bearer {JIMENG_SESSION_IDS}"}
        payload = {
            "model": JIMENG_IMAGE_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"}},
                    {"type": "text", "text": f"基于这张图片，生成一张相同内容的图片。{prompt}"}
                ]
            }]
        }
        resp = requests.post(f"{JIMENG_FREE_API}/v1/chat/completions", headers=headers, json=payload, timeout=180)
        if resp.status_code == 200:
            choices = resp.json().get("choices") or []
            if choices:
                content = choices[0].get("message",{}).get("content","")
                urls = re.findall(r'!\[.*?\]\((https?://[^\)]+)\)', content)
                if urls: return urls[0]
        # 回退：用images/generations接口生成一张简单图片获取URL
        payload2 = {"model": JIMENG_IMAGE_MODEL, "prompt": prompt, "ratio": "1:1", "resolution": "1k"}
        resp2 = requests.post(f"{JIMENG_FREE_API}/v1/images/generations", headers=headers, json=payload2, timeout=180)
        if resp2.status_code == 200:
            data = resp2.json().get("data") or []
            if data: return data[0].get("url")
    except Exception as e:
        print(f"[b64转URL失败] {e}", flush=True)
    return None

@app.route('/api/batch-images', methods=['POST'])
def api_batch_images():
    d = request.get_json() or {}
    prompt = (d.get("prompt") or "").strip()
    count = min(int(d.get("count",4)),40)
    pixel_size = d.get("pixel_size","")
    ratio = d.get("ratio","1:1")
    resolution = d.get("resolution","2k")
    variations = d.get("variations",False)
    if not prompt: return jsonify({"success":False,"error":"请提供提示词"}), 400
    if pixel_size and pixel_size in PIXEL_TO_PARAMS:
        p = PIXEL_TO_PARAMS[pixel_size]; ratio = p["ratio"]; resolution = p["resolution"]
    resolution = resolution.lower()
    images, errors = [], []
    def gen(i):
        p = f"{prompt}，variation {i}" if variations and i > 0 else prompt
        return gen_image(p, ratio, resolution)
    futures = {executor.submit(gen, i): i for i in range(count)}
    for f in as_completed(futures):
        i = futures[f]
        try:
            r = f.result()
            if r.get("success"): images.append({"i":i,"url":r.get("url") or r.get("image_url")})
            else: errors.append({"i":i,"err":r.get("error")})
        except Exception as e: errors.append({"i":i,"err":str(e)})
    images.sort(key=lambda x:x["i"])
    if images: send_feishu(f"批量生图完成：{len(images)}/{count}张","🖼️ 批量生图")
    return jsonify({"success":len(images)>0,"images":[x["url"] for x in images],"total":count,"done":len(images),"failed":len(errors)})

@app.route('/api/batch-videos', methods=['POST'])
def api_batch_videos():
    d = request.get_json() or {}
    prompts = [p.strip() for p in d.get("prompts",[])[:8] if p and p.strip()]
    if not prompts: return jsonify({"success":False,"error":"请提供提示词"}), 400
    duration = d.get("duration",5); ratio = d.get("ratio","16:9")
    videos, errors = [], []
    def _gen_vid(i, p, dur=duration, rat=ratio):
        return gen_video(p, duration=dur, ratio=rat)
    futures = {executor.submit(_gen_vid, i, p): i for i, p in enumerate(prompts)}
    for f in as_completed(futures):
        i = futures[f]
        try:
            r = f.result()
            if r.get("success"): videos.append({"i":i,"url":r.get("url") or r.get("video_url")})
            else: errors.append({"i":i,"err":r.get("error")})
        except Exception as e: errors.append({"i":i,"err":str(e)})
    videos.sort(key=lambda x:x["i"])
    return jsonify({"success":len(videos)>0,"videos":[{"url":x["url"]} for x in videos],"total":len(prompts),"done":len(videos),"failed":len(errors)})

@app.route('/api/images-to-video', methods=['POST'])
def api_images_to_video():
    d = request.get_json() or {}
    images = d.get("images",[]); prompts = d.get("prompts",[]); duration = d.get("duration",5); ratio = d.get("ratio","16:9")
    if not images: return jsonify({"success":False,"error":"请提供图片列表"}), 400
    videos, errors = [], []
    def gen(i, img):
        p = prompts[i].strip() if i < len(prompts) and prompts[i].strip() else "smooth cinematic movement"
        return gen_video(p, image_url=img, duration=duration, ratio=ratio)
    futures = {executor.submit(gen, i, img): i for i, img in enumerate(images)}
    for f in as_completed(futures):
        i = futures[f]
        try:
            r = f.result()
            if r.get("success"): videos.append({"i":i,"url":r.get("url") or r.get("video_url")})
            else: errors.append({"i":i,"err":r.get("error")})
        except Exception as e: errors.append({"i":i,"err":str(e)})
    videos.sort(key=lambda x:x["i"])
    return jsonify({"success":len(videos)>0,"videos":[x["url"] for x in videos],"total":len(images),"done":len(videos),"failed":len(errors)})

@app.route('/api/merge-images', methods=['POST'])
def api_merge_images():
    d = request.get_json() or {}
    images = d.get("images",[]); prompt = (d.get("prompt") or "").strip() or "融合这些图片的风格和内容"
    strength = float(d.get("strength",0.5))
    valid = [img for img in images if img and img.startswith("http")]
    if len(valid) < 2: return jsonify({"success":False,"error":"请至少提供2张有效图片URL"}), 400
    return jsonify(gen_image(prompt, ref_images=valid, strength=strength))

@app.route('/api/generate-copy', methods=['POST'])
def api_generate_copy():
    d = request.get_json() or {}
    name = (d.get("product_name") or "").strip()
    if not name: return jsonify({"success":False,"error":"请输入产品名称"}), 400
    features = (d.get("product_features") or "").strip()
    prompt = f"为【{name}】写一段30-50字的短视频文案，朗朗上口、有感染力。"
    if features: prompt += f"特点：{features}"
    r = chat("你是专业的电商文案师。", prompt)
    return jsonify({"success":True,"copy":r["content"]}) if r.get("success") else jsonify(r)

@app.route('/api/generate-storyboard', methods=['POST'])
def api_generate_storyboard():
    d = request.get_json() or {}
    name = (d.get("product_name") or "").strip()
    copy = (d.get("copywriting") or "").strip()
    count = int(d.get("count",3))
    if not name: return jsonify({"success":False,"error":"请输入产品名称"}), 400
    prompt = f'为【{name}】设计{count}个视频分镜。\n文案：{copy or "无"}\n\n严格按JSON输出：\n{{"scenes":[{{"image_prompt":"详细图片描述","video_prompt":"视频动作描述"}}]}}'
    r = chat("你是视频分镜师。严格按JSON输出。", prompt)
    if not r.get("success"): return jsonify(r)
    try:
        match = re.search(r'\{[\s\S]*\}', r["content"])
        if match: return jsonify({"success":True,"storyboard":json.loads(match.group())})
        return jsonify({"success":False,"error":"无法解析分镜"})
    except Exception as e: return jsonify({"success":False,"error":str(e)})

@app.route('/api/notify', methods=['POST'])
def api_notify():
    d = request.get_json() or {}; send_feishu(d.get("message",""), d.get("title")); return jsonify({"success":True})

@app.route('/api/templates', methods=['GET'])
def api_templates():
    return jsonify({"success":True,"templates":[
        {"id":"product","name":"📦 产品展示","prompt":"商业产品摄影，{product}，纯白色背景，专业棚拍三点打光，柔和阴影，超高清产品细节，金属和玻璃材质反射，居中构图，微距特写质感，8K渲染，广告级品质"},
        {"id":"food","name":"🍔 美食摄影","prompt":"美食摄影大片，{product}，精致瓷盘摆盘，暖黄色侧光，浅景深虚化背景，食材新鲜水珠，木质桌面，烟雾缭绕热气，色彩饱满诱人，顶级餐厅风格，4K超清"},
        {"id":"fashion","name":"👗 时尚穿搭","prompt":"时尚杂志封面级，{product}，专业模特展示，极简纯色背景，伦勃朗打光，高级灰色调，面料质感细腻，设计感构图，Vogue风格，高端时装周氛围，锐利对焦"},
        {"id":"tech","name":"💻 科技感","prompt":"未来科技概念风格，{product}，深邃暗色背景，蓝紫色霓虹光效轮廓，金属拉丝质感，全息投影元素，粒子光效环绕，赛博朋克氛围，工业设计美学，8K超写实渲染"},
        {"id":"nature","name":"🌿 自然清新","prompt":"自然清新生活方式，{product}，阳光透过窗帘的柔和光线，绿色植物和鲜花点缀，浅木色背景，INS风格，莫兰迪色系，通透空气感，日系杂志摄影风格，治愈系氛围"},
        {"id":"luxury","name":"✨ 高端奢华","prompt":"顶级奢华品牌风格，{product}，黑金配色主题，大理石纹理台面，水晶切面光影折射，丝绸质感衬底，戏剧性侧光，暗调高对比度，宝格丽珠宝广告级质感，极致精致"},
        {"id":"cute","name":"🎀 可爱萌系","prompt":"甜美可爱少女风，{product}，粉色和薰衣草色系渐变背景，星星亮片散落装饰，柔和梦幻光晕，蝴蝶结和蕾丝元素，棉花糖般柔软质感，日系卡哇伊风格，甜蜜治愈"},
        {"id":"chinese","name":"🏮 国潮中式","prompt":"新中式国潮美学，{product}，朱红金色主色调，水墨山水意境背景，中国传统纹样（云纹/回纹）装饰，宣纸质感，古典灯笼光影，故宫配色方案，东方美学高级感，大气磅礴"},
    ]})

# ========== 视频代理 ==========
@app.route('/api/proxy-video')
def api_proxy_video():
    """代理视频URL，绕过CDN防盗链/签名限制"""
    url = request.args.get('url', '')
    if not url or not url.startswith('http'):
        return Response('Missing url', status=400)
    try:
        # 用服务端请求视频，带上必要的headers
        resp = requests.get(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://jimeng.jianying.com/',
            'Origin': 'https://jimeng.jianying.com'
        }, stream=True, timeout=30)
        if resp.status_code != 200:
            return Response(f'Upstream {resp.status_code}', status=resp.status_code)
        # 流式返回视频
        def generate():
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        return Response(generate(), content_type=resp.headers.get('Content-Type', 'video/mp4'),
            headers={'Accept-Ranges': 'bytes', 'Cache-Control': 'public, max-age=3600'})
    except Exception as e:
        print(f"[视频代理错误] {e}", flush=True)
        return Response(str(e), status=500)

# ========== 页面 ==========
@app.route('/')
def index(): return Response(HTML_PAGE, content_type='text/html; charset=utf-8')

@app.route('/health')
def health(): return jsonify({"status":"ok","version":"9.5"})

# ========== HTML ==========
HTML_PAGE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>AI创作工具 v9.5</title>
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
.tab:hover{background:rgba(255,255,255,0.1)}.tab.active{background:var(--p)}
.card{background:var(--c);border-radius:14px;padding:18px;margin-bottom:18px}
.card-title{font-size:15px;font-weight:600;margin-bottom:15px;display:flex;align-items:center;gap:8px}
.hidden{display:none!important}
.form-group{margin-bottom:14px}
.form-group label{display:block;margin-bottom:5px;color:var(--tm);font-size:13px}
.form-group input,.form-group textarea,.form-group select{width:100%;padding:10px 12px;background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.1);border-radius:8px;color:var(--t);font-size:14px}
.form-group textarea{min-height:80px;resize:vertical}
.form-row{display:flex;gap:10px;flex-wrap:wrap}.form-row>*{flex:1;min-width:80px}
.btn{padding:10px 20px;border:none;border-radius:8px;font-size:14px;cursor:pointer;transition:all .3s}
.btn-primary{background:var(--p);color:#fff}
.btn-primary:hover{transform:translateY(-2px);box-shadow:0 4px 15px rgba(102,126,234,0.4)}
.btn-primary:disabled{opacity:0.5;cursor:not-allowed;transform:none}
.btn-secondary{background:rgba(255,255,255,0.1);color:#fff}.btn-sm{padding:6px 12px;font-size:12px}
.btn-group{display:flex;gap:8px;flex-wrap:wrap}
.upload-area{border:2px dashed rgba(255,255,255,0.2);border-radius:12px;padding:20px;text-align:center;cursor:pointer;transition:all .3s;font-size:13px;color:var(--tm);margin-bottom:14px}
.upload-area:hover{border-color:rgba(255,255,255,0.4)}.upload-area.has-image{border-color:#667eea;background:rgba(102,126,234,0.05)}
.upload-area img{max-height:80px;border-radius:6px;margin-top:8px}
.preview-grid{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.preview-item{width:70px;height:70px;border-radius:8px;overflow:hidden;position:relative}
.preview-item img{width:100%;height:100%;object-fit:cover}
.result-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;margin-top:15px}
.result-item{position:relative;border-radius:10px;overflow:hidden;aspect-ratio:1;background:rgba(0,0,0,0.3)}
.result-item img,.result-item video{width:100%;height:100%;object-fit:cover}
.result-item .overlay{position:absolute;bottom:0;left:0;right:0;padding:8px;background:linear-gradient(transparent,rgba(0,0,0,0.85));display:flex;gap:4px;justify-content:center;opacity:0;transition:opacity .3s}
.result-item:hover .overlay{opacity:1}
.progress-bar{height:4px;background:rgba(255,255,255,0.1);border-radius:2px;overflow:hidden;margin:12px 0}
.progress-fill{height:100%;background:var(--p);transition:width .3s}
.progress-fill.animated{animation:pulse 1.5s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.6}}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.chip{padding:6px 14px;background:rgba(255,255,255,0.08);border-radius:20px;font-size:12px;cursor:pointer;transition:all .2s}
.chip:hover{background:rgba(255,255,255,0.15)}.chip.active{background:var(--p)}
.stats{display:flex;gap:15px;margin-bottom:15px;flex-wrap:wrap}
.stat{background:rgba(255,255,255,0.05);padding:10px 15px;border-radius:8px;text-align:center}
.stat-val{font-size:20px;font-weight:bold;color:#667eea}.stat-lbl{font-size:11px;color:var(--tm)}
.toast{position:fixed;bottom:20px;right:20px;background:#333;color:#fff;padding:12px 20px;border-radius:8px;z-index:9999;animation:slideIn .3s}
@keyframes slideIn{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}
.template-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px}
.template-item{background:rgba(255,255,255,0.05);padding:12px;border-radius:10px;cursor:pointer;text-align:center;transition:all .2s}
.template-item:hover{background:rgba(255,255,255,0.1);transform:translateY(-2px)}
.video-wrapper{position:relative;border-radius:10px;overflow:hidden;background:#000}
.video-wrapper video{width:100%;display:block}
.video-error{position:absolute;top:0;left:0;right:0;bottom:0;display:flex;align-items:center;justify-content:center;color:var(--tm);font-size:13px;flex-direction:column;gap:8px;background:rgba(0,0,0,0.8)}
.video-actions{display:flex;gap:8px;justify-content:center;padding:8px;font-size:12px}
.video-actions a,.video-actions button{color:#667eea;text-decoration:none;background:none;border:none;cursor:pointer;font-size:12px}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>🎨 AI创作工具 <span class="badge">v9.5</span></h1>
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
<div class="form-group"><label>图片描述</label><textarea id="imagePrompt" placeholder="描述你想要的图片..."></textarea></div>
<div class="upload-area" id="refUpload" onclick="document.getElementById('refInput').click()">
📎 点击上传参考图（可选，以图生图）
<input type="file" id="refInput" accept="image/*" hidden>
<div id="refPreview"></div>
</div>
<div id="refStrengthGroup" class="form-group hidden">
<label>参考强度: <span id="refStrengthVal">0.5</span></label>
<input type="range" id="refStrength" min="0.1" max="0.9" step="0.1" value="0.5">
<div style="font-size:12px;color:var(--tm);margin-top:4px">值越大越接近参考图</div>
</div>
<div class="form-row">
<div class="form-group"><label>像素尺寸</label>
<select id="imagePixel"><option value="800x800">800×800</option><option value="1080x1080">1080×1080</option><option value="1440x1440" selected>1440×1440</option><option value="1440x1920">1440×1920</option><option value="2048x2048">2048×2048</option></select></div>
<div class="form-group"><label>分辨率</label>
<select id="imageRes"><option value="1k">1K</option><option value="2k" selected>2K</option><option value="4k">4K</option></select></div>
<div class="form-group"><label>比例</label>
<select id="imageRatio"><option value="1:1" selected>1:1</option><option value="4:3">4:3</option><option value="3:4">3:4</option><option value="16:9">16:9</option><option value="9:16">9:16</option><option value="3:2">3:2</option><option value="2:3">2:3</option><option value="21:9">21:9</option></select></div>
</div>
<div class="form-row">
<div class="form-group"><label>模型</label>
<select id="imageModel"><option value="">默认(jimeng-5.0)</option><option value="jimeng-4.6">jimeng-4.6</option><option value="jimeng-4.5">jimeng-4.5</option></select></div>
<div class="form-group"><label>数量</label>
<select id="imageCount"><option value="1">1张</option><option value="4" selected>4张</option></select></div>
</div>
<button class="btn btn-primary" style="width:100%" id="genImageBtn">🚀 生成图片</button>
</div>
<div id="imageProgress" class="card hidden"><div>⏳ 正在并行生成图片...</div><div class="progress-bar"><div class="progress-fill animated" style="width:60%"></div></div></div>
<div id="imageResults" class="card hidden"><div class="card-title">生成结果</div><div id="imageGrid" class="result-grid"></div></div>
</div>

<!-- 视频生成 -->
<div id="videoTab" class="tab-content hidden">
<div class="card">
<div class="card-title">🎬 视频生成</div>
<div class="form-group"><label>视频描述</label><textarea id="videoPrompt" placeholder="描述视频内容..."></textarea></div>
<div id="videoImgGroup" class="hidden">
<div class="upload-area" id="videoImgUpload" onclick="document.getElementById('videoImgInput').click()">
📎 点击上传参考图（Seedance必填）
<input type="file" id="videoImgInput" accept="image/*" hidden>
<div id="videoImgPreview"></div>
</div>
<div class="form-group"><label>或粘贴图片URL</label>
<input type="text" id="videoImageUrl" placeholder="也可以直接粘贴图片URL"></div>
</div>
<div class="form-row">
<div class="form-group"><label>模型</label>
<select id="videoModel"><option value="jimeng-video-3.5-pro">3.5 Pro（纯文生视频）</option><option value="jimeng-video-seedance-2.0">Seedance 2.0（图生视频）</option><option value="jimeng-video-seedance-2.0-fast">Seedance 2.0 Fast</option></select></div>
<div class="form-group"><label>比例</label>
<select id="videoRatio"><option value="16:9">16:9</option><option value="9:16">9:16</option><option value="1:1">1:1</option><option value="4:3">4:3</option><option value="3:4">3:4</option></select></div>
<div class="form-group"><label>时长</label>
<select id="videoDuration"><option value="5">5秒</option><option value="10">10秒</option></select></div>
</div>
<button class="btn btn-primary" style="width:100%" id="genVideoBtn">🚀 生成视频</button>
</div>
<div id="videoProgress" class="card hidden"><div>⏳ 正在生成视频（约1-3分钟）...</div><div class="progress-bar"><div class="progress-fill animated" style="width:30%"></div></div></div>
<div id="videoResults" class="card hidden"><div class="card-title">生成结果</div><div id="videoGrid"></div></div>
</div>

<!-- 反推提示词 -->
<div id="reverseTab" class="tab-content hidden">
<div class="card">
<div class="card-title">🔍 反推提示词</div>
<p style="color:var(--tm);font-size:13px;margin-bottom:15px">上传图片，AI分析生成提示词</p>
<div class="upload-area" id="reverseUpload" onclick="document.getElementById('reverseInput').click()">
<div style="font-size:30px;margin-bottom:8px">🖼️</div><div>点击上传图片</div>
<input type="file" id="reverseInput" accept="image/*" hidden>
</div>
<div id="reversePreview" class="preview-grid" style="justify-content:center"></div>
<div class="chips" id="reverseStyleChips">
<div class="chip active" data-style="detailed">📝 详细</div><div class="chip" data-style="simple">✨ 简洁</div><div class="chip" data-style="artistic">🎨 艺术</div>
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
</div></div></div>

<!-- 批量生成 -->
<div id="batchTab" class="tab-content hidden">
<div class="card">
<div class="card-title">📦 批量生成</div>
<div class="chips" id="batchModeChips"><div class="chip active" data-mode="images">🖼️ 批量生图</div><div class="chip" data-mode="videos">🎬 批量视频</div></div>
<div id="batchImagesMode">
<div class="form-group"><label>提示词</label><textarea id="batchImagePrompt" placeholder="输入提示词"></textarea></div>
<div class="form-row">
<div class="form-group"><label>数量</label><select id="batchImageCount"><option value="10">10张</option><option value="20">20张</option><option value="40">40张</option></select></div>
<div class="form-group"><label>像素尺寸</label><select id="batchPixel"><option value="800x800">800×800</option><option value="1080x1080">1080×1080</option><option value="1440x1440" selected>1440×1440</option><option value="1440x1920">1440×1920</option><option value="2048x2048">2048×2048</option></select></div>
</div>
<label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--tm);margin-bottom:12px"><input type="checkbox" id="batchVariations"> 生成变体</label>
<button class="btn btn-primary" style="width:100%" id="batchImagesBtn">🚀 批量生成</button>
</div>
<div id="batchVideosMode" class="hidden">
<div class="form-group"><label>提示词列表（每行一个）</label><textarea id="batchVideoPrompts" placeholder="小猫奔跑&#10;日出海滩&#10;城市夜景" style="min-height:120px"></textarea></div>
<button class="btn btn-primary" style="width:100%" id="batchVideosBtn">🚀 批量生成</button>
</div></div>
<div id="batchProgress" class="card hidden">
<div class="stats"><div class="stat"><div class="stat-val" id="batchTotal">0</div><div class="stat-lbl">总数</div></div><div class="stat"><div class="stat-val" id="batchDone">0</div><div class="stat-lbl">完成</div></div><div class="stat"><div class="stat-val" id="batchFailed">0</div><div class="stat-lbl">失败</div></div></div>
<div class="progress-bar"><div id="batchProgressBar" class="progress-fill animated" style="width:0%"></div></div></div>
<div id="batchResults" class="card hidden"><div class="card-title">批量结果</div><div id="batchGrid" class="result-grid"></div></div>
</div>

<!-- 融合 -->
<div id="mergeTab" class="tab-content hidden">
<div class="card">
<div class="card-title">🎨 图片融合</div>
<p style="color:var(--tm);font-size:13px;margin-bottom:15px">提供图片URL进行融合（先在图片Tab生成，复制URL）</p>
<div class="form-group"><label>图片URL（每行一个，至少2个）</label>
<textarea id="mergeUrls" placeholder="https://...图片1&#10;https://...图片2" style="min-height:100px"></textarea></div>
<div class="form-group"><label>融合指导（可选）</label><textarea id="mergePrompt" placeholder="如：融合这些图片的风格"></textarea></div>
<div class="form-group"><label>融合强度: <span id="strengthVal">0.5</span></label><input type="range" id="mergeStrength" min="0.1" max="0.9" step="0.1" value="0.5"></div>
<button class="btn btn-primary" style="width:100%" id="mergeBtn">🎨 融合生成</button>
</div>
<div id="mergeResult" class="card hidden"><div class="card-title">融合结果</div><div id="mergeGrid" class="result-grid"></div></div></div>

<!-- 模板 -->
<div id="templateTab" class="tab-content hidden">
<div class="card"><div class="card-title">📋 场景模板</div><div id="templateGrid" class="template-grid"></div></div>
<div id="templateForm" class="card hidden">
<div class="card-title" id="templateName">模板</div>
<div class="form-group"><label>产品/主题</label><input type="text" id="templateProduct" placeholder="输入产品名称"></div>
<button class="btn btn-primary" style="width:100%" id="useTemplateBtn">🚀 生成</button></div></div>

<!-- 工作流 -->
<div id="workflowTab" class="tab-content hidden">
<div class="card">
<div class="card-title">⚡ 一键工作流</div>
<p style="color:var(--tm);font-size:13px;margin-bottom:15px">产品→文案→分镜→图片→视频</p>
<div class="form-group"><label>产品名称</label><input type="text" id="workflowName" placeholder="如：新款智能手表"></div>
<div class="form-group"><label>特点/卖点（可选）</label><textarea id="workflowFeatures" placeholder="超长续航、心率监测..."></textarea></div>
<div class="form-row"><div class="form-group"><label>分镜数</label><select id="workflowScenes"><option value="3">3个</option><option value="5">5个</option></select></div></div>
<button class="btn btn-primary" style="width:100%" id="workflowBtn">⚡ 一键生成</button>
</div>
<div id="workflowProgress" class="card hidden"><div id="workflowStep">准备中...</div><div class="progress-bar"><div id="workflowProgressBar" class="progress-fill animated" style="width:0%"></div></div></div>
<div id="workflowResults" class="card hidden">
<div class="card-title">📝 文案</div><p id="workflowCopy" style="color:#ccc;line-height:1.6;margin-bottom:20px"></p>
<div class="card-title">🖼️ 分镜图片</div><div id="workflowImageGrid" class="result-grid" style="margin-bottom:20px"></div>
<div class="card-title">🎬 视频</div><div id="workflowVideoGrid" class="result-grid"></div>
</div></div>
</div>

<script>
(function(){
var $=function(s){return document.querySelector(s)},$$=function(s){return document.querySelectorAll(s)};
function showToast(m){var t=document.createElement('div');t.className='toast';t.textContent=m;document.body.appendChild(t);setTimeout(function(){t.remove()},3000)}
function copyText(t){navigator.clipboard.writeText(t);showToast('已复制URL')}

function safeUrl(u){return u.replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/"/g,'&quot;')}

function mkImg(u){
    var su=safeUrl(u);
    return'<div class="result-item"><img src="'+u+'" loading="lazy"><div class="overlay">'
    +'<a href="'+u+'" target="_blank" class="btn btn-sm" style="text-decoration:none;color:#fff">🔍</a>'
    +'<a href="'+u+'" download class="btn btn-sm" style="text-decoration:none;color:#fff">⬇️</a>'
    +'<button class="btn btn-sm" style="color:#fff" onclick="navigator.clipboard.writeText(\''+su+'\');alert(\'已复制URL\')">📋</button>'
    +'</div></div>';
}
function mkVideo(url,ar){
    ar=ar||'16/9';var su=safeUrl(url);var d=document.createElement('div');
    // 用代理URL播放，绕过CDN防盗链
    var proxyUrl='/api/proxy-video?url='+encodeURIComponent(url);
    var w=document.createElement('div');w.className='video-wrapper';w.style.aspectRatio=ar;
    var v=document.createElement('video');v.src=proxyUrl;v.controls=true;v.playsInline=true;v.preload='auto';
    v.onerror=function(){
        // 代理也失败，尝试直接用原始URL
        if(v.src.indexOf('proxy-video')>=0){v.src=url;return}
        var e=document.createElement('div');e.className='video-error';
        e.innerHTML='<div>⚠️ 视频加载失败</div><a href="'+url+'" target="_blank" style="color:#667eea;font-size:12px">点击打开原始链接</a>';
        w.appendChild(e);
    };
    w.appendChild(v);d.appendChild(w);
    var acts=document.createElement('div');acts.className='video-actions';
    acts.innerHTML='<a href="'+proxyUrl+'" download="video.mp4">⬇️ 下载</a><a href="'+url+'" target="_blank">🔗 原始链接</a><button onclick="navigator.clipboard.writeText(\''+su+'\');alert(\'已复制URL\')">📋 复制URL</button>';
    d.appendChild(acts);return d;
}

var currentStyle='realistic',reverseStyle='detailed',batchMode='images',reverseData=null,currentTemplate=null;
var STYLES={realistic:'超高清摄影，真实质感',anime:'日系动漫风格',art:'艺术插画',poster:'商业海报设计','3d':'3D渲染'};

// Tabs
$$('#mainTabs .tab').forEach(function(t){t.onclick=function(){$$('#mainTabs .tab').forEach(function(x){x.classList.remove('active')});this.classList.add('active');var n=this.dataset.tab;$$('.tab-content').forEach(function(c){c.classList.add('hidden')});$('#'+n+'Tab').classList.remove('hidden');if(n==='template')loadTemplates()}});
$$('#styleChips .chip').forEach(function(c){c.onclick=function(){$$('#styleChips .chip').forEach(function(x){x.classList.remove('active')});this.classList.add('active');currentStyle=this.dataset.style}});
$$('#reverseStyleChips .chip').forEach(function(c){c.onclick=function(){$$('#reverseStyleChips .chip').forEach(function(x){x.classList.remove('active')});this.classList.add('active');reverseStyle=this.dataset.style}});
$$('#batchModeChips .chip').forEach(function(c){c.onclick=function(){$$('#batchModeChips .chip').forEach(function(x){x.classList.remove('active')});this.classList.add('active');batchMode=this.dataset.mode;$('#batchImagesMode').classList.toggle('hidden',batchMode!=='images');$('#batchVideosMode').classList.toggle('hidden',batchMode!=='videos')}});
$('#mergeStrength').oninput=function(){$('#strengthVal').textContent=this.value};
$('#videoModel').onchange=function(){var need=this.value.indexOf('seedance')>=0;$('#videoImgGroup').classList.toggle('hidden',!need)};

// 参考图上传（以图生图）
var refImageB64=null;
$('#refInput').onchange=function(){
    if(!this.files[0])return;
    var reader=new FileReader();reader.onload=function(e){
        refImageB64=e.target.result;
        $('#refPreview').innerHTML='<img src="'+e.target.result+'" style="max-height:80px;border-radius:6px;margin-top:8px">';
        $('#refUpload').classList.add('has-image');
        $('#refStrengthGroup').classList.remove('hidden');
    };reader.readAsDataURL(this.files[0]);
};
$('#refStrength').oninput=function(){$('#refStrengthVal').textContent=this.value};

// 视频参考图上传
var videoImgB64=null;
$('#videoImgInput').onchange=function(){
    if(!this.files[0])return;
    var reader=new FileReader();reader.onload=function(e){
        videoImgB64=e.target.result;
        $('#videoImgPreview').innerHTML='<img src="'+e.target.result+'" style="max-height:80px;border-radius:6px;margin-top:8px">';
        $('#videoImgUpload').classList.add('has-image');
    };reader.readAsDataURL(this.files[0]);
};

// 反推上传
$('#reverseUpload').onclick=function(){$('#reverseInput').click()};
$('#reverseInput').onchange=function(){if(this.files[0]){var r=new FileReader();r.onload=function(e){reverseData=e.target.result;$('#reversePreview').innerHTML='<div class="preview-item" style="width:120px;height:120px"><img src="'+reverseData+'"></div>'};r.readAsDataURL(this.files[0])}};

async function api(url,data){try{var r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});if(!r.ok)throw new Error('HTTP '+r.status+': '+(await r.text()).substring(0,100));return await r.json()}catch(e){return{success:false,error:e.message}}}

// === 图片生成 ===
$('#genImageBtn').onclick=async function(){
    var p=$('#imagePrompt').value.trim();if(!p){alert('请输入描述');return}
    this.disabled=true;this.textContent='⏳ 生成中...';$('#imageProgress').classList.remove('hidden');$('#imageResults').classList.add('hidden');
    
    var pixelSize=$('#imagePixel').value;
    var payload={prompt:p+'，'+STYLES[currentStyle],count:parseInt($('#imageCount').value),
        pixel_size:pixelSize,resolution:$('#imageRes').value,model:$('#imageModel').value||undefined};
    if(pixelSize!=='1440x1920'){payload.ratio=$('#imageRatio').value}
    
    // 以图生图：发送base64
    if(refImageB64){
        payload.ref_image_base64=refImageB64;
        payload.strength=parseFloat($('#refStrength').value);
        payload.count=1; // 图生图只生成1张
    }
    
    var d=await api('/api/generate-images',payload);
    this.disabled=false;this.textContent='🚀 生成图片';$('#imageProgress').classList.add('hidden');
    if(d.success&&d.images&&d.images.length>0){$('#imageGrid').innerHTML=d.images.map(mkImg).join('');$('#imageResults').classList.remove('hidden')}
    else alert('生成失败: '+(d.error||'未知错误'));
};

// === 视频生成 ===
$('#genVideoBtn').onclick=async function(){
    var p=$('#videoPrompt').value.trim(),m=$('#videoModel').value;
    var imgUrl=$('#videoImageUrl')?$('#videoImageUrl').value.trim():'';
    var hasImg=videoImgB64||imgUrl;
    if(m.indexOf('seedance')>=0&&!hasImg){alert('Seedance模型需要参考图\n\n请上传图片或粘贴图片URL');return}
    if(!p&&!hasImg){alert('请输入视频描述');return}
    this.disabled=true;this.textContent='⏳ 生成中...';$('#videoProgress').classList.remove('hidden');$('#videoResults').classList.add('hidden');
    var payload={prompt:p||undefined,duration:parseInt($('#videoDuration').value),model:m,ratio:$('#videoRatio').value};
    if(imgUrl) payload.image_url=imgUrl;
    else if(videoImgB64) payload.image_base64=videoImgB64;
    var d=await api('/api/generate-video',payload);
    this.disabled=false;this.textContent='🚀 生成视频';$('#videoProgress').classList.add('hidden');
    if(d.success&&(d.url||d.video_url)){var url=d.url||d.video_url;$('#videoGrid').innerHTML='';$('#videoGrid').appendChild(mkVideo(url,$('#videoRatio').value.replace(':','/')));$('#videoResults').classList.remove('hidden')}
    else alert('生成失败: '+(d.error||'未知错误'));
};

// === 反推 ===
$('#reverseBtn').onclick=async function(){if(!reverseData){alert('请上传图片');return}this.disabled=true;this.textContent='⏳ 分析中...';var d=await api('/api/reverse-prompt',{image_base64:reverseData,style:reverseStyle});this.disabled=false;this.textContent='🔍 分析图片';if(d.success){$('#reversedPrompt').value=d.prompt;$('#reverseResult').classList.remove('hidden')}else alert('分析失败: '+(d.error||''))};
$('#copyPromptBtn').onclick=function(){navigator.clipboard.writeText($('#reversedPrompt').value);showToast('已复制')};
$('#optimizeBtn').onclick=async function(){var p=$('#reversedPrompt').value;if(!p)return;this.disabled=true;var d=await api('/api/optimize-prompt',{prompt:p,style:'enhance'});this.disabled=false;if(d.success){$('#reversedPrompt').value=d.optimized;showToast('已优化')}};
$('#usePromptBtn').onclick=function(){$('#imagePrompt').value=$('#reversedPrompt').value;$$('#mainTabs .tab').forEach(function(t){t.classList.remove('active')});$$('#mainTabs .tab')[0].classList.add('active');$$('.tab-content').forEach(function(c){c.classList.add('hidden')});$('#imageTab').classList.remove('hidden')};

// === 批量 ===
$('#batchImagesBtn').onclick=async function(){var p=$('#batchImagePrompt').value.trim();if(!p){alert('请输入提示词');return}this.disabled=true;$('#batchProgress').classList.remove('hidden');$('#batchResults').classList.add('hidden');$('#batchTotal').textContent=$('#batchImageCount').value;$('#batchDone').textContent='0';$('#batchFailed').textContent='0';
var d=await api('/api/batch-images',{prompt:p+'，'+STYLES[currentStyle],count:parseInt($('#batchImageCount').value),pixel_size:$('#batchPixel').value,variations:$('#batchVariations').checked});
this.disabled=false;$('#batchProgressBar').style.width='100%';$('#batchDone').textContent=d.done||0;$('#batchFailed').textContent=d.failed||0;
if(d.images&&d.images.length>0){$('#batchGrid').innerHTML=d.images.map(mkImg).join('');$('#batchResults').classList.remove('hidden')}else alert('生成失败')};

$('#batchVideosBtn').onclick=async function(){var ps=$('#batchVideoPrompts').value.trim();if(!ps){alert('请输入提示词');return}var prompts=ps.split('\n').filter(function(p){return p.trim()});this.disabled=true;$('#batchProgress').classList.remove('hidden');$('#batchTotal').textContent=prompts.length;
var d=await api('/api/batch-videos',{prompts:prompts,duration:5,ratio:'16:9'});this.disabled=false;$('#batchProgressBar').style.width='100%';$('#batchDone').textContent=d.done||0;
if(d.videos&&d.videos.length>0){$('#batchGrid').innerHTML='';d.videos.forEach(function(v){var url=typeof v==='string'?v:v.url;var item=document.createElement('div');item.className='result-item';item.style.aspectRatio='16/9';item.appendChild(mkVideo(url,'16/9'));$('#batchGrid').appendChild(item)});$('#batchResults').classList.remove('hidden')}};

// === 融合 ===
$('#mergeBtn').onclick=async function(){var urls=$('#mergeUrls').value.trim().split('\n').filter(function(u){return u.trim().startsWith('http')});if(urls.length<2){alert('请至少提供2个图片URL');return}this.disabled=true;this.textContent='⏳ 融合中...';
var d=await api('/api/merge-images',{images:urls,prompt:$('#mergePrompt').value||'融合风格',strength:parseFloat($('#mergeStrength').value)});
this.disabled=false;this.textContent='🎨 融合生成';if(d.success&&(d.url||d.image_url)){$('#mergeGrid').innerHTML=mkImg(d.url||d.image_url);$('#mergeResult').classList.remove('hidden')}else alert('失败: '+(d.error||''))};

// === 模板 ===
async function loadTemplates(){try{var r=await fetch('/api/templates');var d=await r.json();if(d.templates){$('#templateGrid').innerHTML=d.templates.map(function(t){return'<div class="template-item" data-id="'+t.id+'" data-prompt="'+t.prompt.replace(/"/g,'&quot;')+'">'+t.name+'</div>'}).join('');$$('#templateGrid .template-item').forEach(function(item){item.onclick=function(){currentTemplate={id:this.dataset.id,prompt:this.dataset.prompt};$('#templateName').textContent=this.textContent;$('#templateForm').classList.remove('hidden')}})}}catch(e){}}
$('#useTemplateBtn').onclick=function(){if(!currentTemplate)return;var p=$('#templateProduct').value.trim();if(!p){alert('请输入产品名称');return}$('#imagePrompt').value=currentTemplate.prompt.replace('{product}',p);$$('#mainTabs .tab').forEach(function(t){t.classList.remove('active')});$$('#mainTabs .tab')[0].classList.add('active');$$('.tab-content').forEach(function(c){c.classList.add('hidden')});$('#imageTab').classList.remove('hidden')};

// === 工作流 ===
$('#workflowBtn').onclick=async function(){
    var name=$('#workflowName').value.trim();if(!name){alert('请输入产品名称');return}
    this.disabled=true;$('#workflowProgress').classList.remove('hidden');$('#workflowResults').classList.add('hidden');
    try{
        $('#workflowStep').textContent='📝 生成文案...';$('#workflowProgressBar').style.width='15%';
        var cd=await api('/api/generate-copy',{product_name:name,product_features:$('#workflowFeatures').value});
        if(!cd.success)throw new Error(cd.error||'文案失败');var copy=cd.copy;

        $('#workflowStep').textContent='🎬 生成分镜...';$('#workflowProgressBar').style.width='30%';
        var sd=await api('/api/generate-storyboard',{product_name:name,copywriting:copy,count:parseInt($('#workflowScenes').value)});
        if(!sd.success)throw new Error(sd.error||'分镜失败');
        var scenes=(sd.storyboard&&sd.storyboard.scenes)||[];if(!scenes.length)throw new Error('未生成分镜');

        // 生成图片
        var pairs=[];
        for(var i=0;i<scenes.length;i++){
            $('#workflowProgressBar').style.width=(30+i*20/scenes.length)+'%';$('#workflowStep').textContent='🖼️ 图片 '+(i+1)+'/'+scenes.length;
            var id=await api('/api/generate-images',{prompt:scenes[i].image_prompt,count:1,pixel_size:'1440x1440'});
            if(id.images&&id.images[0])pairs.push({image:id.images[0],scene:scenes[i]});
        }
        if(!pairs.length)throw new Error('图片全部失败');

        // 展示图片
        $('#workflowImageGrid').innerHTML=pairs.map(function(p){return mkImg(p.image)}).join('');

        // 生成视频（先图生视频，失败回退纯文生视频）
        var videos=[];
        for(var i=0;i<pairs.length;i++){
            var pr=pairs[i];$('#workflowProgressBar').style.width=(50+i*45/pairs.length)+'%';$('#workflowStep').textContent='🎬 视频 '+(i+1)+'/'+pairs.length;
            var vd=await api('/api/generate-video',{prompt:pr.scene.video_prompt||'smooth cinematic movement',image_url:pr.image,duration:5,model:'jimeng-video-seedance-2.0'});
            if(vd.url||vd.video_url){videos.push(vd.url||vd.video_url)}
            else{var vd2=await api('/api/generate-video',{prompt:pr.scene.video_prompt||pr.scene.image_prompt||'cinematic',duration:5,model:'jimeng-video-3.5-pro'});if(vd2.url||vd2.video_url)videos.push(vd2.url||vd2.video_url)}
        }

        $('#workflowProgressBar').style.width='100%';$('#workflowStep').textContent='✅ 完成！';$('#workflowCopy').textContent=copy;
        if(videos.length>0){$('#workflowVideoGrid').innerHTML='';videos.forEach(function(v){var item=document.createElement('div');item.className='result-item';item.style.aspectRatio='16/9';item.appendChild(mkVideo(v,'16/9'));$('#workflowVideoGrid').appendChild(item)})}
        else{$('#workflowVideoGrid').innerHTML='<p style="color:var(--tm)">视频生成失败，但文案和图片已完成</p>'}
        $('#workflowResults').classList.remove('hidden');
    }catch(e){alert('工作流失败: '+e.message)}finally{this.disabled=false;$('#workflowProgress').classList.add('hidden')}
};
})();
</script>
</body>
</html>'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    print(f"🚀 即梦AI v9.5 启动 - 端口: {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
