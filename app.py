"""
即梦AI v10.5 - 图片功能完整版
================================
v10.0 基础 + 5个新功能:
1. 生成历史记录（会话内保留）
2. 提示词收藏/常用
3. 图片弹窗大图预览
4. 批量Tab加分辨率和比例
5. 生成结果显示参数信息
"""

from flask import Flask, request, jsonify, Response
import requests, json, os, re, uuid, time, base64
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

# ========== 配置 ==========
JIMENG_FREE_API = os.environ.get("JIMENG_FREE_API", "https://wyzxhy168.zeabur.app")
JIMENG_SESSION_IDS = os.environ.get("JIMENG_SESSION_IDS", "")
JIMENG_IMAGE_MODEL = os.environ.get("JIMENG_IMAGE_MODEL", "jimeng-5.0")

ARK_API_KEY = os.environ.get("ARK_API_KEY", "")
CHAT_API_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
CHAT_MODEL = os.environ.get("CHAT_MODEL", "doubao-1-5-pro-32k-250115")
VISION_MODEL = os.environ.get("VISION_MODEL", "doubao-1-5-vision-pro-32k-250115")

FEISHU_BOT_WEBHOOK = os.environ.get("FEISHU_BOT_WEBHOOK", "")
executor = ThreadPoolExecutor(max_workers=5)

# ========== 临时图片存储 ==========
TEMP_IMAGES = {}
TEMP_IMAGE_TTL = 600

def cleanup_temp_images():
    now = time.time()
    for k in [k for k, v in TEMP_IMAGES.items() if now - v["ts"] > TEMP_IMAGE_TTL]:
        del TEMP_IMAGES[k]

def store_temp_image(b64_data):
    cleanup_temp_images()
    if "," in b64_data:
        header, b64_data = b64_data.split(",", 1)
        mime = header.split(":")[1].split(";")[0] if ":" in header else "image/jpeg"
    else:
        mime = "image/jpeg"
    img_bytes = base64.b64decode(b64_data)
    img_id = uuid.uuid4().hex[:12]
    TEMP_IMAGES[img_id] = {"data": img_bytes, "mime": mime, "ts": time.time()}
    host = request.host_url.rstrip("/")
    return f"{host}/api/temp-image/{img_id}"

# ========== 像素尺寸映射 ==========
PIXEL_TO_PARAMS = {
    "800x800":   {"ratio":"1:1","resolution":"1k"},
    "1080x1080": {"ratio":"1:1","resolution":"2k"},
    "1440x1440": {"ratio":"1:1","resolution":"2k"},
    "1440x1920": {"ratio":"3:4","resolution":"2k"},
    "2048x2048": {"ratio":"1:1","resolution":"4k"},
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
    if not ARK_API_KEY: return {"success":False,"error":"未配置ARK_API_KEY，请在环境变量中设置"}
    try:
        resp = requests.post(CHAT_API_ENDPOINT, headers={"Content-Type":"application/json","Authorization":f"Bearer {ARK_API_KEY}"},
            json={"model":model or CHAT_MODEL,"messages":[{"role":"system","content":system},{"role":"user","content":user}]}, timeout=60)
        if resp.status_code == 401: return {"success":False,"error":"ARK_API_KEY无效，请检查环境变量"}
        if resp.status_code != 200: return {"success":False,"error":f"豆包API HTTP {resp.status_code}: {resp.text[:150]}"}
        result = resp.json()
        choices = result.get("choices") or []
        if choices: return {"success":True,"content":choices[0].get("message",{}).get("content","")}
        return {"success":False,"error":result.get("error",{}).get("message","豆包API返回异常")}
    except requests.exceptions.Timeout: return {"success":False,"error":"豆包API超时(60秒)"}
    except Exception as e: return {"success":False,"error":f"豆包API异常: {str(e)}"}

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
        if resp.status_code == 401: return {"success":False,"error":"ARK_API_KEY无效"}
        if resp.status_code != 200:
            err = resp.text[:300]
            if "ModelNotOpen" in err or "not activated" in err:
                return {"success":False,"error":f"视觉模型 {VISION_MODEL} 未开通，请在火山方舟控制台激活"}
            return {"success":False,"error":f"视觉API HTTP {resp.status_code}: {err[:100]}"}
        result = resp.json()
        choices = result.get("choices") or []
        if choices: return {"success":True,"content":choices[0].get("message",{}).get("content","")}
        return {"success":False,"error":"视觉API返回异常"}
    except requests.exceptions.Timeout: return {"success":False,"error":"视觉API超时(60秒)"}
    except Exception as e: return {"success":False,"error":f"视觉API异常: {str(e)}"}

def gen_image(prompt, ratio="1:1", resolution="2k", ref_images=None, strength=0.5, model=None):
    if not JIMENG_SESSION_IDS: return {"success":False,"error":"未配置JIMENG_SESSION_IDS"}
    try:
        headers = {"Content-Type":"application/json","Authorization":f"Bearer {JIMENG_SESSION_IDS}"}
        resolution = resolution.lower()
        payload = {"model":model or JIMENG_IMAGE_MODEL,"prompt":prompt,"ratio":ratio,"resolution":resolution}
        if ref_images:
            valid = [r for r in ref_images if r and r.startswith("http")]
            if valid: payload["images"] = valid[:10]; payload["sample_strength"] = strength
        print(f"[图片] {prompt[:50]}... 比例:{ratio} 分辨率:{resolution} 模型:{payload['model']}", flush=True)
        resp = requests.post(f"{JIMENG_FREE_API}/v1/images/generations", headers=headers, json=payload, timeout=180)
        if resp.status_code == 500: return {"success":False,"error":"即梦API服务错误(500)，可能需要重启jimeng-free-api-all"}
        if resp.status_code != 200: return {"success":False,"error":f"即梦API HTTP {resp.status_code}: {resp.text[:150]}"}
        result = resp.json()
        data = result.get("data") or []
        if data:
            url = data[0].get("url","")
            if url: return {"success":True,"url":url}
        err = result.get("message") or result.get("error",{}).get("message") or ""
        if "browserContext" in err or "browser" in err.lower():
            return {"success":False,"error":"即梦API浏览器崩溃，请重启jimeng-free-api-all"}
        return {"success":False,"error":err or "即梦API未返回图片"}
    except requests.exceptions.Timeout: return {"success":False,"error":"图片生成超时(180秒)"}
    except requests.exceptions.ConnectionError: return {"success":False,"error":"无法连接即梦API，请检查JIMENG_FREE_API地址"}
    except Exception as e: return {"success":False,"error":f"图片生成异常: {str(e)}"}

# ========== API路由 ==========
@app.route('/api/temp-image/<img_id>')
def api_temp_image(img_id):
    cleanup_temp_images()
    img = TEMP_IMAGES.get(img_id)
    if not img: return Response("图片已过期", status=404)
    return Response(img["data"], content_type=img["mime"], headers={"Cache-Control":"public, max-age=600"})

@app.route('/api/upload-image', methods=['POST'])
def api_upload_image():
    d = request.get_json() or {}
    b64 = d.get("image_base64","").strip()
    if not b64: return jsonify({"success":False,"error":"请提供图片"}), 400
    try: return jsonify({"success":True,"url":store_temp_image(b64)})
    except Exception as e: return jsonify({"success":False,"error":f"图片处理失败: {str(e)}"})

@app.route('/api/generate-images', methods=['POST'])
def api_generate_images():
    d = request.get_json() or {}
    prompt = (d.get("prompt") or "").strip()
    count = min(int(d.get("count",1)),4)
    pixel_size = d.get("pixel_size","")
    ratio = d.get("ratio","1:1")
    resolution = d.get("resolution","2k")
    ref_url = (d.get("ref_image") or "").strip()
    strength = float(d.get("strength",0.5))
    img_model = d.get("model") or None
    if not prompt: return jsonify({"success":False,"error":"请输入图片描述"}), 400
    if pixel_size and pixel_size in PIXEL_TO_PARAMS:
        p = PIXEL_TO_PARAMS[pixel_size]; ratio = p["ratio"]; resolution = p["resolution"]
    resolution = resolution.lower()
    ref_imgs = [ref_url] if ref_url and ref_url.startswith("http") else None
    if count == 1:
        r = gen_image(prompt, ratio, resolution, ref_images=ref_imgs, strength=strength, model=img_model)
        if r.get("success"): return jsonify({"success":True,"images":[r["url"]]})
        return jsonify(r)
    images, errors = [], []
    def gen(i): return gen_image(prompt, ratio, resolution, ref_images=ref_imgs, strength=strength, model=img_model)
    futures = {executor.submit(gen, i): i for i in range(count)}
    for f in as_completed(futures):
        try:
            r = f.result()
            if r.get("success") and r.get("url"): images.append({"i":futures[f],"url":r["url"]})
            else: errors.append(r.get("error",""))
        except Exception as e: errors.append(str(e))
    images.sort(key=lambda x:x["i"])
    if not images: return jsonify({"success":False,"error":errors[0] if errors else "生成失败"})
    return jsonify({"success":True,"images":[x["url"] for x in images],"failed":len(errors)})

@app.route('/api/batch-images', methods=['POST'])
def api_batch_images():
    d = request.get_json() or {}
    prompt = (d.get("prompt") or "").strip()
    count = min(int(d.get("count",4)),40)
    pixel_size = d.get("pixel_size","")
    ratio = d.get("ratio","1:1")
    resolution = d.get("resolution","2k")
    variations = d.get("variations",False)
    if not prompt: return jsonify({"success":False,"error":"请输入提示词"}), 400
    if pixel_size and pixel_size in PIXEL_TO_PARAMS:
        p = PIXEL_TO_PARAMS[pixel_size]; ratio = p["ratio"]; resolution = p["resolution"]
    resolution = resolution.lower()
    images, errors = [], []
    def gen(i):
        p2 = f"{prompt}，variation {i}" if variations and i > 0 else prompt
        return gen_image(p2, ratio, resolution)
    futures = {executor.submit(gen, i): i for i in range(count)}
    for f in as_completed(futures):
        i = futures[f]
        try:
            r = f.result()
            if r.get("success") and r.get("url"): images.append({"i":i,"url":r["url"]})
            else: errors.append(r.get("error",""))
        except Exception as e: errors.append(str(e))
    images.sort(key=lambda x:x["i"])
    if images: send_feishu(f"批量生图完成：{len(images)}/{count}张","🖼️ 批量生图")
    return jsonify({"success":len(images)>0,"images":[x["url"] for x in images],"total":count,"done":len(images),"failed":len(errors)})

@app.route('/api/reverse-prompt', methods=['POST'])
def api_reverse_prompt():
    d = request.get_json() or {}
    img_b64 = (d.get("image_base64") or "").strip()
    img_url = (d.get("image_url") or "").strip()
    style = d.get("style","detailed")
    if not img_url and not img_b64: return jsonify({"success":False,"error":"请上传图片"}), 400
    prompts = {"detailed":"详细分析这张图片，生成AI绘图提示词。包括主体、风格、光影、色彩、构图、细节。直接输出提示词。",
        "simple":"用简洁一句话描述图片，适合AI绘图。直接输出。","artistic":"以艺术家视角生成提示词，强调风格氛围。直接输出。"}
    r = vision("你是专业的AI绘图提示词工程师。", prompts.get(style,prompts["detailed"]), image_url=img_url, image_base64=img_b64)
    return jsonify({"success":True,"prompt":r["content"]}) if r.get("success") else jsonify(r)

@app.route('/api/optimize-prompt', methods=['POST'])
def api_optimize_prompt():
    d = request.get_json() or {}
    prompt = (d.get("prompt") or "").strip()
    if not prompt: return jsonify({"success":False,"error":"请提供提示词"}), 400
    styles = {"enhance":"增强细节，添加光影、材质、氛围","artistic":"转化为艺术风格","commercial":"转化为商业广告风格","anime":"转化为日系动漫风格"}
    r = chat("你是AI绘图提示词专家。", f"优化提示词，{styles.get(d.get('style','enhance'),styles['enhance'])}。\n原始：{prompt}\n直接输出优化后的提示词。")
    return jsonify({"success":True,"optimized":r["content"]}) if r.get("success") else jsonify(r)

@app.route('/api/merge-images', methods=['POST'])
def api_merge_images():
    d = request.get_json() or {}
    images = d.get("images",[])
    prompt = (d.get("prompt") or "").strip() or "融合这些图片的风格和内容"
    strength = float(d.get("strength",0.5))
    valid = [img for img in images if img and img.startswith("http")]
    if len(valid) < 2: return jsonify({"success":False,"error":"请至少提供2张图片"}), 400
    return jsonify(gen_image(prompt, ref_images=valid, strength=strength))

@app.route('/api/templates', methods=['GET'])
def api_templates():
    return jsonify({"success":True,"templates":[
        {"id":"product","name":"📦 产品展示","prompt":"商业产品摄影，{product}，纯白色背景，专业棚拍三点打光，柔和阴影，超高清产品细节，金属和玻璃材质反射，居中构图，微距特写质感，8K渲染，广告级品质"},
        {"id":"food","name":"🍔 美食摄影","prompt":"美食摄影大片，{product}，精致瓷盘摆盘，暖黄色侧光，浅景深虚化背景，食材新鲜水珠，木质桌面，烟雾缭绕热气，色彩饱满诱人，顶级餐厅风格，4K超清"},
        {"id":"fashion","name":"👗 时尚穿搭","prompt":"时尚杂志封面级，{product}，专业模特展示，极简纯色背景，伦勃朗打光，高级灰色调，面料质感细腻，Vogue风格，高端时装周氛围"},
        {"id":"tech","name":"💻 科技感","prompt":"未来科技概念，{product}，深邃暗色背景，蓝紫色霓虹光效轮廓，金属拉丝质感，全息投影元素，粒子光效，赛博朋克氛围，8K超写实"},
        {"id":"nature","name":"🌿 自然清新","prompt":"自然清新生活方式，{product}，阳光透过窗帘柔和光线，绿植鲜花点缀，浅木色背景，INS风格，莫兰迪色系，日系杂志摄影"},
        {"id":"luxury","name":"✨ 高端奢华","prompt":"顶级奢华品牌，{product}，黑金配色，大理石台面，水晶光影折射，丝绸衬底，戏剧性侧光，暗调高对比度，极致精致"},
        {"id":"cute","name":"🎀 可爱萌系","prompt":"甜美可爱少女风，{product}，粉色薰衣草渐变背景，星星亮片，柔和梦幻光晕，蝴蝶结蕾丝，棉花糖质感，日系卡哇伊"},
        {"id":"chinese","name":"🏮 国潮中式","prompt":"新中式国潮美学，{product}，朱红金色主调，水墨山水背景，传统纹样装饰，宣纸质感，古典灯笼光影，故宫配色，东方美学"},
    ]})

@app.route('/api/notify', methods=['POST'])
def api_notify():
    d = request.get_json() or {}; send_feishu(d.get("message",""), d.get("title")); return jsonify({"success":True})

@app.route('/')
def index(): return Response(HTML_PAGE, content_type='text/html; charset=utf-8')

@app.route('/health')
def health(): return jsonify({"status":"ok","version":"10.5"})

# ========== HTML ==========
HTML_PAGE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>即梦AI v10.5</title>
<style>
:root{--p:#667eea;--pg:linear-gradient(135deg,#667eea,#764ba2);--bg:#0f1117;--card:#1a1d2e;--border:#2a2d3e;--t:#e4e4e7;--tm:#71717a;--s:#22c55e;--err:#ef4444}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,"Segoe UI",sans-serif;background:var(--bg);min-height:100vh;color:var(--t)}
.container{max-width:960px;margin:0 auto;padding:16px}
.header{text-align:center;padding:20px 0 12px}
.header h1{font-size:1.5em;color:var(--t)}
.header h1 span{background:var(--pg);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.header p{color:var(--tm);font-size:12px;margin-top:4px}
.tabs{display:flex;gap:3px;margin-bottom:14px;background:var(--card);border-radius:10px;padding:3px;border:1px solid var(--border)}
.tab{flex:1;padding:9px 3px;text-align:center;cursor:pointer;border-radius:7px;font-size:12px;color:var(--tm);transition:all .2s}
.tab:hover{color:var(--t)}.tab.active{background:var(--pg);color:#fff}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:14px}
.card-title{font-size:14px;font-weight:600;margin-bottom:14px}
.hidden{display:none!important}
.form-group{margin-bottom:12px}
.form-group label{display:block;margin-bottom:5px;color:var(--tm);font-size:12px}
input[type=text],textarea,select{width:100%;padding:9px 11px;background:#12141f;border:1px solid var(--border);border-radius:7px;color:var(--t);font-size:13px;outline:none;transition:border .2s}
input:focus,textarea:focus,select:focus{border-color:var(--p)}
textarea{min-height:70px;resize:vertical}
.form-row{display:flex;gap:8px;flex-wrap:wrap}.form-row>*{flex:1;min-width:75px}
.btn{padding:9px 16px;border:none;border-radius:7px;font-size:13px;cursor:pointer;transition:all .2s}
.btn-primary{background:var(--pg);color:#fff;width:100%}
.btn-primary:hover{opacity:0.9}.btn-primary:disabled{opacity:0.4;cursor:not-allowed}
.btn-sm{padding:5px 10px;font-size:11px;border-radius:5px}
.btn-ghost{background:rgba(255,255,255,0.06);color:var(--t);border:1px solid var(--border)}
.btn-ghost:hover{background:rgba(255,255,255,0.1)}
.btn-group{display:flex;gap:5px;flex-wrap:wrap}
.chips{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:12px}
.chip{padding:5px 12px;background:rgba(255,255,255,0.06);border:1px solid var(--border);border-radius:16px;font-size:11px;cursor:pointer;color:var(--tm);transition:all .2s}
.chip:hover{border-color:var(--p);color:var(--t)}.chip.active{background:var(--pg);border-color:var(--p);color:#fff}
.upload-area{border:1px dashed var(--border);border-radius:8px;padding:14px;text-align:center;cursor:pointer;color:var(--tm);font-size:12px;transition:all .2s;margin-bottom:12px}
.upload-area:hover{border-color:var(--p)}.upload-area.has-img{border-color:var(--p);background:rgba(102,126,234,0.05)}
.upload-area img{max-height:70px;border-radius:5px;margin-top:6px;display:block;margin-left:auto;margin-right:auto}
.result-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;margin-top:10px}
.result-item{position:relative;border-radius:7px;overflow:hidden;aspect-ratio:1;background:#12141f;cursor:pointer}
.result-item img{width:100%;height:100%;object-fit:cover;display:block}
.result-item .actions{position:absolute;bottom:0;left:0;right:0;padding:5px;background:linear-gradient(transparent,rgba(0,0,0,0.85));display:flex;gap:3px;justify-content:center;opacity:0;transition:opacity .2s}
.result-item:hover .actions{opacity:1}
.result-item .actions a,.result-item .actions button{background:rgba(255,255,255,0.15);border:none;color:#fff;padding:3px 7px;border-radius:3px;font-size:10px;cursor:pointer;text-decoration:none}
.result-item .actions a:hover,.result-item .actions button:hover{background:rgba(255,255,255,0.3)}
.param-info{font-size:11px;color:var(--tm);margin-top:8px;padding:6px 10px;background:#12141f;border-radius:6px}
.progress-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:14px}
.progress-bar{height:3px;background:var(--border);border-radius:2px;overflow:hidden;margin-top:8px}
.progress-fill{height:100%;background:var(--pg);transition:width .3s}
.progress-fill.pulse{animation:pulse 1.5s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.5}}
.stats{display:flex;gap:10px;margin-bottom:10px}
.stat{background:#12141f;padding:6px 12px;border-radius:6px;text-align:center}
.stat b{font-size:16px;color:var(--p);display:block}.stat small{font-size:10px;color:var(--tm)}
.toast{position:fixed;bottom:16px;right:16px;background:var(--card);border:1px solid var(--border);color:var(--t);padding:8px 16px;border-radius:7px;font-size:12px;z-index:9999;animation:fadeIn .3s}
@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.template-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:6px}
.template-item{background:#12141f;border:1px solid var(--border);padding:10px 6px;border-radius:7px;cursor:pointer;text-align:center;font-size:12px;transition:all .2s}
.template-item:hover{border-color:var(--p);transform:translateY(-1px)}
.preview-grid{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;justify-content:center}
.preview-item{width:80px;height:80px;border-radius:6px;overflow:hidden}
.preview-item img{width:100%;height:100%;object-fit:cover}
/* 弹窗大图预览 */
.lightbox{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.9);z-index:10000;display:flex;align-items:center;justify-content:center;cursor:zoom-out}
.lightbox img{max-width:90vw;max-height:90vh;border-radius:8px;box-shadow:0 0 40px rgba(0,0,0,0.5)}
.lightbox .lb-close{position:absolute;top:16px;right:20px;color:#fff;font-size:28px;cursor:pointer}
.lightbox .lb-actions{position:absolute;bottom:20px;display:flex;gap:10px}
.lightbox .lb-actions a,.lightbox .lb-actions button{background:rgba(255,255,255,0.15);border:none;color:#fff;padding:8px 16px;border-radius:6px;cursor:pointer;text-decoration:none;font-size:13px}
/* 历史记录 */
.history-section{margin-top:16px}
.history-title{font-size:13px;color:var(--tm);margin-bottom:8px;display:flex;justify-content:space-between;align-items:center}
.history-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(70px,1fr));gap:4px}
.history-grid img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:4px;cursor:pointer;transition:opacity .2s}
.history-grid img:hover{opacity:0.8}
/* 收藏提示词 */
.fav-list{margin-bottom:12px}
.fav-item{display:flex;justify-content:space-between;align-items:center;padding:6px 10px;background:#12141f;border-radius:6px;margin-bottom:4px;font-size:12px;color:var(--tm)}
.fav-item span{flex:1;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fav-item span:hover{color:var(--t)}
.fav-item button{background:none;border:none;color:var(--err);cursor:pointer;font-size:12px;margin-left:6px}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>🎨 <span>即梦AI</span> 创作工具</h1>
<p>v10.5 · 图片功能完整版</p>
</div>

<div class="tabs" id="mainTabs">
<div class="tab active" data-tab="image">🖼️ 生成</div>
<div class="tab" data-tab="reverse">🔍 反推</div>
<div class="tab" data-tab="batch">📦 批量</div>
<div class="tab" data-tab="merge">🎨 融合</div>
<div class="tab" data-tab="template">📋 模板</div>
<div class="tab" data-tab="history">📜 历史</div>
</div>

<!-- ===== 图片生成 ===== -->
<div id="imageTab" class="tab-content">
<div class="card">
<div class="card-title">🖼️ 图片生成</div>
<div class="chips" id="styleChips">
<div class="chip active" data-v="realistic">📷 写实</div><div class="chip" data-v="anime">🎌 动漫</div><div class="chip" data-v="3d">🎮 3D</div><div class="chip" data-v="art">🎨 艺术</div><div class="chip" data-v="poster">📰 海报</div>
</div>
<!-- 收藏提示词 -->
<div id="favList" class="fav-list"></div>
<div class="form-group"><label>图片描述 <button class="btn-ghost btn-sm" id="favAddBtn" style="float:right;padding:2px 8px">⭐ 收藏当前</button></label>
<textarea id="imgPrompt" placeholder="描述你想要的图片，越详细越好..."></textarea></div>
<div class="upload-area" id="refArea">📎 点击上传参考图（可选，以图生图）<input type="file" id="refFile" accept="image/*" hidden></div>
<div id="refOpts" class="form-group hidden">
<label>参考强度: <span id="refVal">0.5</span></label>
<input type="range" id="refRange" min="0.1" max="0.9" step="0.1" value="0.5">
<div style="font-size:11px;color:var(--tm);margin-top:3px">越大越接近参考图</div>
</div>
<div class="form-row">
<div class="form-group"><label>像素</label><select id="imgPixel"><option value="800x800">800×800</option><option value="1080x1080">1080×1080</option><option value="1440x1440" selected>1440×1440</option><option value="1440x1920">1440×1920</option><option value="2048x2048">2048×2048</option></select></div>
<div class="form-group"><label>分辨率</label><select id="imgRes"><option value="1k">1K</option><option value="2k" selected>2K</option><option value="4k">4K</option></select></div>
<div class="form-group"><label>比例</label><select id="imgRatio"><option value="1:1" selected>1:1</option><option value="4:3">4:3</option><option value="3:4">3:4</option><option value="16:9">16:9</option><option value="9:16">9:16</option><option value="21:9">21:9</option></select></div>
</div>
<div class="form-row">
<div class="form-group"><label>模型</label><select id="imgModel"><option value="">jimeng-5.0</option><option value="jimeng-4.6">jimeng-4.6</option><option value="jimeng-4.5">jimeng-4.5</option></select></div>
<div class="form-group"><label>数量</label><select id="imgCount"><option value="1">1张</option><option value="4" selected>4张</option></select></div>
</div>
<button class="btn btn-primary" id="genBtn">🚀 生成图片</button>
</div>
<div id="genProgress" class="progress-card hidden"><div id="genStatus" style="font-size:12px;color:var(--tm)">⏳ 正在生成...</div><div class="progress-bar"><div id="genBar" class="progress-fill pulse" style="width:50%"></div></div></div>
<div id="genResults" class="card hidden">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
<div class="card-title" style="margin:0">生成结果</div>
<button class="btn btn-ghost btn-sm" id="dlAllBtn">⬇️ 全部下载</button>
</div>
<div id="genGrid" class="result-grid"></div>
<div id="genParams" class="param-info"></div>
</div>
</div>

<!-- ===== 反推 ===== -->
<div id="reverseTab" class="tab-content hidden">
<div class="card">
<div class="card-title">🔍 反推提示词</div>
<p style="color:var(--tm);font-size:12px;margin-bottom:12px">上传图片，AI分析生成提示词</p>
<div class="upload-area" id="revArea"><div style="font-size:24px;margin-bottom:4px">🖼️</div>点击上传图片<input type="file" id="revFile" accept="image/*" hidden></div>
<div id="revPreview" class="preview-grid"></div>
<div class="chips" id="revChips"><div class="chip active" data-v="detailed">📝 详细</div><div class="chip" data-v="simple">✨ 简洁</div><div class="chip" data-v="artistic">🎨 艺术</div></div>
<button class="btn btn-primary" id="revBtn">🔍 分析图片</button>
</div>
<div id="revResult" class="card hidden">
<div class="card-title">分析结果</div>
<textarea id="revPrompt" style="background:#12141f;border:1px solid var(--border);color:var(--t);padding:10px;border-radius:7px;width:100%;min-height:80px"></textarea>
<div class="btn-group" style="margin-top:8px">
<button class="btn btn-ghost btn-sm" id="revCopy">📋 复制</button>
<button class="btn btn-ghost btn-sm" id="revOptimize">✨ 优化</button>
<button class="btn btn-ghost btn-sm" id="revFav">⭐ 收藏</button>
<button class="btn btn-primary btn-sm" id="revUse">🚀 用这个生成</button>
</div></div></div>

<!-- ===== 批量 ===== -->
<div id="batchTab" class="tab-content hidden">
<div class="card">
<div class="card-title">📦 批量生图</div>
<div class="form-group"><label>提示词</label><textarea id="batchPrompt" placeholder="输入提示词，批量生成多张"></textarea></div>
<div class="form-row">
<div class="form-group"><label>数量</label><select id="batchCount"><option value="10">10张</option><option value="20">20张</option><option value="40">40张</option></select></div>
<div class="form-group"><label>像素</label><select id="batchPixel"><option value="800x800">800×800</option><option value="1080x1080">1080×1080</option><option value="1440x1440" selected>1440×1440</option><option value="1440x1920">1440×1920</option><option value="2048x2048">2048×2048</option></select></div>
</div>
<div class="form-row">
<div class="form-group"><label>分辨率</label><select id="batchRes"><option value="1k">1K</option><option value="2k" selected>2K</option><option value="4k">4K</option></select></div>
<div class="form-group"><label>比例</label><select id="batchRatio"><option value="1:1" selected>1:1</option><option value="4:3">4:3</option><option value="3:4">3:4</option><option value="16:9">16:9</option><option value="9:16">9:16</option><option value="21:9">21:9</option></select></div>
</div>
<label style="display:flex;align-items:center;gap:5px;font-size:12px;color:var(--tm);margin-bottom:12px"><input type="checkbox" id="batchVar"> 生成变体</label>
<button class="btn btn-primary" id="batchBtn">🚀 批量生成</button>
</div>
<div id="batchProgress" class="progress-card hidden">
<div class="stats"><div class="stat"><b id="bTotal">0</b><small>总数</small></div><div class="stat"><b id="bDone">0</b><small>完成</small></div><div class="stat"><b id="bFail">0</b><small>失败</small></div></div>
<div class="progress-bar"><div id="bBar" class="progress-fill pulse" style="width:0%"></div></div></div>
<div id="batchResults" class="card hidden">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
<div class="card-title" style="margin:0">批量结果</div><button class="btn btn-ghost btn-sm" id="batchDlAll">⬇️ 全部下载</button></div>
<div id="batchGrid" class="result-grid"></div></div></div>

<!-- ===== 融合 ===== -->
<div id="mergeTab" class="tab-content hidden">
<div class="card">
<div class="card-title">🎨 图片融合</div>
<p style="color:var(--tm);font-size:12px;margin-bottom:12px">上传2张以上图片进行融合</p>
<div class="upload-area" id="mergeArea">📎 点击上传图片（至少2张）<input type="file" id="mergeFile" accept="image/*" multiple hidden></div>
<div id="mergePreview" class="preview-grid"></div>
<div id="mergeUrlInfo" class="hidden" style="font-size:11px;color:var(--tm);margin:6px 0"></div>
<div class="form-group"><label>融合指导（可选）</label><textarea id="mergePrompt" placeholder="如：融合风格" style="min-height:50px"></textarea></div>
<div class="form-group"><label>融合强度: <span id="mergeVal">0.5</span></label><input type="range" id="mergeRange" min="0.1" max="0.9" step="0.1" value="0.5"></div>
<button class="btn btn-primary" id="mergeBtn">🎨 融合生成</button>
</div>
<div id="mergeResult" class="card hidden"><div class="card-title">融合结果</div><div id="mergeGrid" class="result-grid"></div></div></div>

<!-- ===== 模板 ===== -->
<div id="templateTab" class="tab-content hidden">
<div class="card"><div class="card-title">📋 场景模板</div><div id="tplGrid" class="template-grid"></div></div>
<div id="tplForm" class="card hidden"><div class="card-title" id="tplName">模板</div>
<div class="form-group"><label>产品/主题</label><input type="text" id="tplProduct" placeholder="输入产品名称"></div>
<button class="btn btn-primary" id="tplUse">🚀 生成</button></div></div>

<!-- ===== 历史 ===== -->
<div id="historyTab" class="tab-content hidden">
<div class="card">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
<div class="card-title" style="margin:0">📜 生成历史</div>
<button class="btn btn-ghost btn-sm" id="clearHistory">🗑️ 清空</button>
</div>
<p style="color:var(--tm);font-size:12px;margin-bottom:10px">本次会话生成的所有图片（刷新后清空）</p>
<div id="historyGrid" class="history-grid"></div>
<div id="historyEmpty" style="text-align:center;color:var(--tm);font-size:13px;padding:20px">暂无历史记录</div>
</div></div>

</div>

<!-- 弹窗大图预览 -->
<div id="lightbox" class="lightbox hidden">
<span class="lb-close">&times;</span>
<img id="lbImg" src="">
<div class="lb-actions">
<a id="lbOpen" href="" target="_blank">🔍 原图</a>
<a id="lbDl" href="" download="image.jpg">⬇️ 下载</a>
<button id="lbCopy">📋 复制URL</button>
</div>
</div>

<script>
(function(){
var $=function(s){return document.querySelector(s)},$$=function(s){return document.querySelectorAll(s)};
function toast(m){var t=document.createElement('div');t.className='toast';t.textContent=m;document.body.appendChild(t);setTimeout(function(){t.remove()},3000)}
function safe(u){return u.replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/"/g,'&quot;')}

// ===== 历史记录 =====
var historyList=[];
function addHistory(urls,params){
    urls.forEach(function(u){historyList.unshift({url:u,params:params||'',ts:Date.now()})});
    if(historyList.length>200) historyList=historyList.slice(0,200);
    renderHistory();
}
function renderHistory(){
    if(!historyList.length){$('#historyGrid').innerHTML='';$('#historyEmpty').classList.remove('hidden');return}
    $('#historyEmpty').classList.add('hidden');
    $('#historyGrid').innerHTML=historyList.map(function(h){return '<img src="'+h.url+'" onclick="showLB(\''+safe(h.url)+'\')" title="'+h.params+'">'}).join('');
}
$('#clearHistory').onclick=function(){historyList=[];renderHistory();toast('已清空')};

// ===== 收藏提示词 =====
var favs=JSON.parse(localStorage.getItem('jimeng_favs')||'[]');
function renderFavs(){
    if(!favs.length){$('#favList').innerHTML='';return}
    $('#favList').innerHTML=favs.map(function(f,i){
        return '<div class="fav-item"><span onclick="document.getElementById(\'imgPrompt\').value=this.title;toast(\'已填入\')" title="'+f.replace(/"/g,'&quot;')+'">⭐ '+f.substring(0,40)+(f.length>40?'...':'')+'</span><button onclick="removeFav('+i+')">✕</button></div>';
    }).join('');
}
function saveFavs(){localStorage.setItem('jimeng_favs',JSON.stringify(favs));renderFavs()}
window.removeFav=function(i){favs.splice(i,1);saveFavs()};
$('#favAddBtn').onclick=function(){
    var p=$('#imgPrompt').value.trim();if(!p){toast('请先输入提示词');return}
    if(favs.indexOf(p)>=0){toast('已收藏过');return}
    favs.unshift(p);if(favs.length>20)favs=favs.slice(0,20);saveFavs();toast('已收藏');
};
renderFavs();

// ===== 弹窗大图 =====
window.showLB=function(u){
    $('#lbImg').src=u;$('#lbOpen').href=u;$('#lbDl').href=u;$('#lightbox').classList.remove('hidden');
};
$('#lightbox').onclick=function(e){if(e.target===this||e.target.className==='lb-close')this.classList.add('hidden')};
$('#lbCopy').onclick=function(e){e.stopPropagation();navigator.clipboard.writeText($('#lbImg').src);toast('已复制URL')};

// ===== 图片结果项 =====
function mkImg(u){
    var s=safe(u);
    return '<div class="result-item" onclick="showLB(\''+s+'\')"><img src="'+u+'" loading="lazy"><div class="actions" onclick="event.stopPropagation()">'
    +'<a href="'+u+'" target="_blank" onclick="event.stopPropagation()">🔍</a>'
    +'<a href="'+u+'" download="image.jpg" onclick="event.stopPropagation()">⬇️</a>'
    +'<button onclick="event.stopPropagation();navigator.clipboard.writeText(\''+s+'\');toast(\'已复制\')">📋</button>'
    +'</div></div>';
}

function downloadAll(urls){
    toast('开始下载 '+urls.length+' 张');
    urls.forEach(function(u,i){setTimeout(function(){
        fetch(u).then(function(r){return r.blob()}).then(function(b){
            var a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='image_'+(i+1)+'.jpg';a.click();URL.revokeObjectURL(a.href);
        }).catch(function(){var a=document.createElement('a');a.href=u;a.download='image_'+(i+1)+'.jpg';a.target='_blank';a.click()});
    },i*500)});
}

async function api(url,data){
    try{var r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});return await r.json()}
    catch(e){return{success:false,error:'网络请求失败: '+e.message}}
}

// ===== 状态 =====
var style='realistic',revStyle='detailed',revData=null,refUrl=null,tpl=null,mergeUrls=[];
var STYLES={realistic:'超高清摄影，真实质感',anime:'日系动漫风格，精致',art:'艺术插画风格',poster:'商业海报设计','3d':'3D渲染，精细建模'};
var lastImages=[],lastParams='';

// ===== Tab =====
$$('#mainTabs .tab').forEach(function(t){t.onclick=function(){
    $$('#mainTabs .tab').forEach(function(x){x.classList.remove('active')});this.classList.add('active');
    var n=this.dataset.tab;$$('.tab-content').forEach(function(c){c.classList.add('hidden')});$('#'+n+'Tab').classList.remove('hidden');
    if(n==='template')loadTpl();if(n==='history')renderHistory();
}});
function initChips(sel,cb){$$(sel+' .chip').forEach(function(c){c.onclick=function(){$$(sel+' .chip').forEach(function(x){x.classList.remove('active')});this.classList.add('active');cb(this.dataset.v)}})}
initChips('#styleChips',function(v){style=v});
initChips('#revChips',function(v){revStyle=v});

// ===== 参考图上传 =====
$('#refArea').onclick=function(e){if(e.target.tagName==='BUTTON')return;$('#refFile').click()};
$('#refFile').onchange=async function(){
    if(!this.files[0])return;
    var statusDiv=document.createElement('div');statusDiv.textContent='⏳ 上传中...';$('#refArea').insertBefore(statusDiv,$('#refArea').firstChild);
    var reader=new FileReader();reader.onload=async function(e){
        var b64=e.target.result;var r=await api('/api/upload-image',{image_base64:b64});statusDiv.remove();
        if(r.success){
            refUrl=r.url;var old=document.getElementById('refBox');if(old)old.remove();
            var box=document.createElement('div');box.id='refBox';
            box.innerHTML='<div style="margin-top:6px">✅ 已上传</div><img src="'+b64+'"><button class="btn-ghost btn-sm" style="margin-top:6px" onclick="event.stopPropagation();this.parentElement.remove();refUrl=null;document.getElementById(\'refArea\').classList.remove(\'has-img\');document.getElementById(\'refOpts\').classList.add(\'hidden\')">✕ 清除</button>';
            $('#refArea').appendChild(box);$('#refArea').classList.add('has-img');$('#refOpts').classList.remove('hidden');
        }else{refUrl=null;toast('上传失败: '+(r.error||''))}
    };reader.readAsDataURL(this.files[0]);
};
$('#refRange').oninput=function(){$('#refVal').textContent=this.value};

// ===== 图片生成 =====
$('#genBtn').onclick=async function(){
    var p=$('#imgPrompt').value.trim();if(!p){alert('请输入图片描述');return}
    this.disabled=true;this.textContent='⏳ 生成中...';$('#genProgress').classList.remove('hidden');$('#genResults').classList.add('hidden');
    var px=$('#imgPixel').value,res=$('#imgRes').value,ratio=$('#imgRatio').value,mdl=$('#imgModel').value||'jimeng-5.0';
    var payload={prompt:p+'，'+STYLES[style],count:parseInt($('#imgCount').value),pixel_size:px,resolution:res,ratio:ratio,model:$('#imgModel').value||undefined};
    if(refUrl){payload.ref_image=refUrl;payload.strength=parseFloat($('#refRange').value)}
    var d=await api('/api/generate-images',payload);
    this.disabled=false;this.textContent='🚀 生成图片';$('#genProgress').classList.add('hidden');
    if(d.success&&d.images&&d.images.length>0){
        lastImages=d.images;lastParams='风格:'+style+' 像素:'+px+' 分辨率:'+res+' 比例:'+ratio+' 模型:'+mdl;
        $('#genGrid').innerHTML=d.images.map(mkImg).join('');
        $('#genParams').textContent='📊 '+lastParams+(d.failed?' | ❌'+d.failed+'张失败':'');
        $('#genResults').classList.remove('hidden');
        addHistory(d.images,lastParams);
        if(d.failed>0)toast(d.failed+'张生成失败');
    }else alert('生成失败: '+(d.error||'未知错误'));
};
$('#dlAllBtn').onclick=function(){if(lastImages.length)downloadAll(lastImages)};

// ===== 反推 =====
$('#revArea').onclick=function(){$('#revFile').click()};
$('#revFile').onchange=function(){if(!this.files[0])return;var r=new FileReader();r.onload=function(e){revData=e.target.result;$('#revPreview').innerHTML='<div class="preview-item"><img src="'+revData+'"></div>'};r.readAsDataURL(this.files[0])};
$('#revBtn').onclick=async function(){if(!revData){alert('请上传图片');return}this.disabled=true;this.textContent='⏳ 分析中...';var d=await api('/api/reverse-prompt',{image_base64:revData,style:revStyle});this.disabled=false;this.textContent='🔍 分析图片';if(d.success){$('#revPrompt').value=d.prompt;$('#revResult').classList.remove('hidden')}else alert('分析失败: '+(d.error||''))};
$('#revCopy').onclick=function(){navigator.clipboard.writeText($('#revPrompt').value);toast('已复制')};
$('#revOptimize').onclick=async function(){var p=$('#revPrompt').value;if(!p)return;this.disabled=true;var d=await api('/api/optimize-prompt',{prompt:p});this.disabled=false;if(d.success){$('#revPrompt').value=d.optimized;toast('已优化')}};
$('#revFav').onclick=function(){var p=$('#revPrompt').value.trim();if(!p)return;if(favs.indexOf(p)>=0){toast('已收藏过');return}favs.unshift(p);if(favs.length>20)favs=favs.slice(0,20);saveFavs();toast('已收藏')};
$('#revUse').onclick=function(){$('#imgPrompt').value=$('#revPrompt').value;$$('#mainTabs .tab').forEach(function(t){t.classList.remove('active')});$$('#mainTabs .tab')[0].classList.add('active');$$('.tab-content').forEach(function(c){c.classList.add('hidden')});$('#imageTab').classList.remove('hidden')};

// ===== 批量 =====
var batchImages=[];
$('#batchBtn').onclick=async function(){
    var p=$('#batchPrompt').value.trim();if(!p){alert('请输入提示词');return}
    this.disabled=true;$('#batchProgress').classList.remove('hidden');$('#batchResults').classList.add('hidden');
    $('#bTotal').textContent=$('#batchCount').value;$('#bDone').textContent='0';$('#bFail').textContent='0';$('#bBar').style.width='10%';
    var d=await api('/api/batch-images',{prompt:p+'，'+STYLES[style],count:parseInt($('#batchCount').value),pixel_size:$('#batchPixel').value,resolution:$('#batchRes').value,ratio:$('#batchRatio').value,variations:$('#batchVar').checked});
    this.disabled=false;$('#bBar').style.width='100%';$('#bBar').classList.remove('pulse');$('#bDone').textContent=d.done||0;$('#bFail').textContent=d.failed||0;
    if(d.images&&d.images.length>0){batchImages=d.images;$('#batchGrid').innerHTML=d.images.map(mkImg).join('');$('#batchResults').classList.remove('hidden');addHistory(d.images,'批量生成')}else alert('批量生成失败')
};
$('#batchDlAll').onclick=function(){if(batchImages.length)downloadAll(batchImages)};

// ===== 融合 =====
$('#mergeArea').onclick=function(){$('#mergeFile').click()};
$('#mergeFile').onchange=async function(){
    if(!this.files||this.files.length<2){alert('请选择至少2张图片');return}
    mergeUrls=[];$('#mergePreview').innerHTML='';$('#mergeUrlInfo').classList.remove('hidden');$('#mergeArea').classList.add('has-img');
    var files=Array.from(this.files);$('#mergeUrlInfo').textContent='⏳ 上传 0/'+files.length;
    for(var i=0;i<files.length;i++){
        var b64=await new Promise(function(resolve){var r=new FileReader();r.onload=function(e){resolve(e.target.result)};r.readAsDataURL(files[i])});
        $('#mergePreview').innerHTML+='<div class="preview-item"><img src="'+b64+'"></div>';
        var r=await api('/api/upload-image',{image_base64:b64});
        if(r.success)mergeUrls.push(r.url);$('#mergeUrlInfo').textContent='⏳ 上传 '+(i+1)+'/'+files.length;
    }
    $('#mergeUrlInfo').textContent='✅ 已上传 '+mergeUrls.length+'/'+files.length;
};
$('#mergeRange').oninput=function(){$('#mergeVal').textContent=this.value};
$('#mergeBtn').onclick=async function(){
    if(mergeUrls.length<2){alert('请上传至少2张图片');return}
    this.disabled=true;this.textContent='⏳ 融合中...';
    var d=await api('/api/merge-images',{images:mergeUrls,prompt:$('#mergePrompt').value||'融合风格',strength:parseFloat($('#mergeRange').value)});
    this.disabled=false;this.textContent='🎨 融合生成';
    if(d.success&&d.url){$('#mergeGrid').innerHTML=mkImg(d.url);$('#mergeResult').classList.remove('hidden');addHistory([d.url],'融合')}
    else alert('融合失败: '+(d.error||''))
};

// ===== 模板 =====
async function loadTpl(){try{var r=await fetch('/api/templates');var d=await r.json();if(d.templates){$('#tplGrid').innerHTML=d.templates.map(function(t){return'<div class="template-item" data-prompt="'+t.prompt.replace(/"/g,'&quot;')+'">'+t.name+'</div>'}).join('');$$('#tplGrid .template-item').forEach(function(el){el.onclick=function(){tpl=this.dataset.prompt;$('#tplName').textContent=this.textContent;$('#tplForm').classList.remove('hidden')}})}}catch(e){}}
$('#tplUse').onclick=function(){if(!tpl)return;var p=$('#tplProduct').value.trim();if(!p){alert('请输入产品名称');return}$('#imgPrompt').value=tpl.replace('{product}',p);$$('#mainTabs .tab').forEach(function(t){t.classList.remove('active')});$$('#mainTabs .tab')[0].classList.add('active');$$('.tab-content').forEach(function(c){c.classList.add('hidden')});$('#imageTab').classList.remove('hidden')};

})();
</script>
</body>
</html>'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    print(f"🚀 即梦AI v10.5 - 端口: {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
