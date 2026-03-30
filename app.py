"""
即梦AI Flask代理服务 v6.1
===========================
新增：快速图片生成模式（独立Tab，支持高分辨率）
"""

from flask import Flask, request, jsonify, Response
import requests
import json
import os
import re
import time
import threading

app = Flask(__name__)

# 配置
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

PRICE_IMAGE = 0.20
PRICE_VIDEO_5S = 0.50
PRICE_VIDEO_10S = 1.00

projects = {}

def send_feishu_text(text):
    try: requests.post(FEISHU_BOT_WEBHOOK, json={"msg_type": "text", "content": {"text": text}}, timeout=10)
    except: pass

def send_feishu_message(title, content_blocks):
    try: requests.post(FEISHU_BOT_WEBHOOK, json={"msg_type": "post", "content": {"post": {"zh_cn": {"title": title, "content": content_blocks}}}}, timeout=10)
    except: pass

def get_feishu_tenant_token():
    try:
        resp = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=10)
        return resp.json().get("tenant_access_token") if resp.json().get("code") == 0 else None
    except: return None

def upload_image_to_feishu(image_url):
    try:
        token = get_feishu_tenant_token()
        if not token: return None
        img_resp = requests.get(image_url, timeout=60)
        if img_resp.status_code != 200: return None
        resp = requests.post("https://open.feishu.cn/open-apis/im/v1/images", headers={"Authorization": f"Bearer {token}"}, files={"image": (f"ai_{int(time.time())}.jpg", img_resp.content, "image/jpeg")}, data={"image_type": "message"}, timeout=30)
        return resp.json().get("data", {}).get("image_key") if resp.json().get("code") == 0 else None
    except: return None

def chat_completion(system_prompt, user_prompt):
    try:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {ARK_API_KEY}"}
        payload = {"model": CHAT_MODEL, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]}
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
        print(f"[图片] 尺寸:{size} 提示:{prompt[:50]}...")
        response = requests.post(IMAGE_API_ENDPOINT, headers=headers, json=payload, timeout=120)
        result = response.json()
        if "data" in result and len(result["data"]) > 0:
            return {"success": True, "image_url": result["data"][0].get("url", "")}
        return {"success": False, "error": result.get("error", {}).get("message", str(result))}
    except Exception as e:
        return {"success": False, "error": str(e)}

def create_video_task(image_url, prompt, duration=5, resolution="1080p"):
    try:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {ARK_API_KEY}"}
        payload = {"model": VIDEO_MODEL, "content": [{"type": "image_url", "image_url": {"url": image_url}, "role": "first_frame"}, {"type": "text", "text": prompt}], "duration": duration, "resolution": resolution}
        response = requests.post(VIDEO_API_ENDPOINT, headers=headers, json=payload, timeout=30)
        result = response.json()
        return {"success": True, "task_id": result["id"]} if "id" in result else {"success": False, "error": result.get("error", {}).get("message", str(result))}
    except Exception as e:
        return {"success": False, "error": str(e)}

def query_video_task(task_id):
    try:
        response = requests.get(f"{VIDEO_API_ENDPOINT}/{task_id}", headers={"Authorization": f"Bearer {ARK_API_KEY}"}, timeout=30)
        return response.json()
    except: return {"error": "查询失败"}

def wait_for_video(task_id, max_wait=600):
    start = time.time()
    while time.time() - start < max_wait:
        result = query_video_task(task_id)
        status = result.get("status", "")
        if status == "succeeded":
            video_url = result.get("content", {}).get("video_url") or result.get("content", {}).get("url") or result.get("video_url")
            return {"success": True, "video_url": video_url} if video_url else {"success": False, "error": "无URL"}
        elif status in ["failed", "cancelled"]:
            return {"success": False, "error": f"任务{status}"}
        time.sleep(5)
    return {"success": False, "error": "超时"}

HTML_PAGE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI创作工具 v6.1</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); min-height: 100vh; color: #fff; }
        .container { max-width: 900px; margin: 0 auto; padding: 20px; }
        .header { display: flex; justify-content: space-between; align-items: center; padding: 20px 0; border-bottom: 1px solid rgba(255,255,255,0.1); margin-bottom: 20px; }
        .header h1 { font-size: 22px; }
        .version { font-size: 12px; background: #4CAF50; padding: 2px 8px; border-radius: 10px; margin-left: 8px; }
        
        /* 主Tab切换 */
        .main-tabs { display: flex; gap: 0; margin-bottom: 25px; background: rgba(255,255,255,0.05); border-radius: 12px; padding: 5px; }
        .main-tab { flex: 1; padding: 15px 20px; text-align: center; cursor: pointer; border-radius: 8px; transition: all 0.3s; font-weight: 600; }
        .main-tab:hover { background: rgba(255,255,255,0.1); }
        .main-tab.active { background: linear-gradient(135deg, #4CAF50, #45a049); }
        .main-tab .tab-icon { font-size: 24px; display: block; margin-bottom: 5px; }
        
        .card { background: rgba(255,255,255,0.05); border-radius: 16px; padding: 25px; margin-bottom: 20px; border: 1px solid rgba(255,255,255,0.1); }
        .card-title { font-size: 18px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 8px; color: rgba(255,255,255,0.8); font-size: 14px; }
        .form-group input, .form-group textarea, .form-group select { width: 100%; padding: 12px 16px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.2); background: rgba(255,255,255,0.05); color: #fff; font-size: 16px; }
        .form-group input:focus, .form-group textarea:focus { outline: none; border-color: #4CAF50; }
        .form-group textarea { min-height: 100px; resize: vertical; }
        .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        .form-row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 15px; }
        .btn { padding: 12px 24px; border-radius: 8px; border: none; font-size: 15px; font-weight: 600; cursor: pointer; transition: all 0.3s; }
        .btn-primary { background: linear-gradient(135deg, #4CAF50, #45a049); color: #fff; }
        .btn-secondary { background: rgba(255,255,255,0.1); color: #fff; border: 1px solid rgba(255,255,255,0.2); }
        .btn-small { padding: 6px 12px; font-size: 12px; }
        .btn:hover { transform: translateY(-2px); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        .btn-group { display: flex; gap: 15px; margin-top: 20px; flex-wrap: wrap; }
        
        .section-title { font-size: 14px; color: rgba(255,255,255,0.6); margin: 20px 0 10px 0; padding-bottom: 5px; border-bottom: 1px solid rgba(255,255,255,0.1); }
        .option-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
        .option-item { padding: 10px 5px; background: rgba(255,255,255,0.05); border: 2px solid rgba(255,255,255,0.1); border-radius: 8px; text-align: center; cursor: pointer; transition: all 0.3s; font-size: 12px; }
        .option-item:hover { border-color: rgba(255,255,255,0.3); }
        .option-item.selected { border-color: #4CAF50; background: rgba(76,175,80,0.2); }
        .option-item .icon { font-size: 20px; margin-bottom: 3px; }
        
        /* 图片结果展示 */
        .image-results { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }
        .image-result-item { background: rgba(0,0,0,0.2); border-radius: 12px; overflow: hidden; }
        .image-result-item img { width: 100%; display: block; cursor: pointer; }
        .image-result-item .img-info { padding: 10px; display: flex; justify-content: space-between; align-items: center; font-size: 12px; }
        
        .progress-bar { width: 100%; height: 8px; background: rgba(255,255,255,0.1); border-radius: 4px; overflow: hidden; margin: 15px 0; }
        .progress-fill { height: 100%; background: linear-gradient(90deg, #4CAF50, #8BC34A); transition: width 0.3s; }
        .progress-text { text-align: center; color: rgba(255,255,255,0.7); font-size: 14px; }
        
        .cost-box { background: linear-gradient(135deg, rgba(255,193,7,0.15), rgba(255,152,0,0.15)); border: 1px solid rgba(255,193,7,0.3); border-radius: 10px; padding: 12px 15px; margin: 15px 0; display: flex; justify-content: space-between; align-items: center; }
        .cost-box .cost-label { color: rgba(255,255,255,0.7); font-size: 13px; }
        .cost-box .cost-value { color: #FFC107; font-weight: bold; font-size: 16px; }
        
        .hidden { display: none !important; }
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.9); z-index: 1000; justify-content: center; align-items: center; }
        .modal.show { display: flex; }
        .modal img { max-width: 90%; max-height: 90%; border-radius: 10px; }
        .modal-close { position: absolute; top: 20px; right: 30px; font-size: 40px; color: #fff; cursor: pointer; }
        
        .size-preview { background: rgba(0,0,0,0.3); border-radius: 8px; padding: 15px; margin: 10px 0; text-align: center; }
        .size-preview .preview-box { display: inline-block; background: rgba(76,175,80,0.3); border: 2px solid #4CAF50; margin: 10px; }
        .size-preview .size-text { font-size: 12px; color: rgba(255,255,255,0.6); margin-top: 5px; }
        
        @media (max-width: 768px) { 
            .form-row, .form-row-3 { grid-template-columns: 1fr; } 
            .option-grid { grid-template-columns: repeat(3, 1fr); }
            .main-tabs { flex-direction: column; }
            .image-results { grid-template-columns: 1fr 1fr; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎨 AI创作工具 <span class="version">v6.1</span></h1>
            <button class="btn btn-secondary btn-small" onclick="showHistory()">📋 历史</button>
        </div>
        
        <!-- 主Tab切换 -->
        <div class="main-tabs">
            <div class="main-tab active" onclick="switchMode('image')">
                <span class="tab-icon">🖼️</span>
                快速生成图片
            </div>
            <div class="main-tab" onclick="switchMode('video')">
                <span class="tab-icon">🎬</span>
                生成视频
            </div>
        </div>
        
        <!-- ========== 图片生成模式 ========== -->
        <div id="imageMode">
            <div class="card">
                <div class="card-title">🖼️ 快速生成图片</div>
                
                <div class="form-group">
                    <label>图片描述 * <button class="btn btn-secondary btn-small" onclick="showPromptTemplates()" style="margin-left:10px;">📋 模板</button></label>
                    <textarea id="imgPrompt" placeholder="详细描述你想要的图片内容...&#10;例如：一台银色的笔记本电脑，放在简约的白色桌面上，旁边有一杯咖啡和一盆绿植，阳光从窗户照进来，画面温馨自然"></textarea>
                </div>
                
                <div class="form-group">
                    <label>负面提示词（排除不想要的元素）</label>
                    <input type="text" id="negativePrompt" placeholder="例如：模糊, 低质量, 变形, 水印, 文字">
                </div>
                
                <!-- 提示词模板弹窗 -->
                <div id="templateModal" class="hidden" style="position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:999;display:flex;justify-content:center;align-items:center;">
                    <div style="background:#1a1a2e;border-radius:16px;padding:25px;max-width:600px;width:90%;max-height:80vh;overflow-y:auto;">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
                            <h3>📋 提示词模板</h3>
                            <button class="btn btn-secondary btn-small" onclick="hidePromptTemplates()">✕</button>
                        </div>
                        <div id="templateList"></div>
                    </div>
                </div>
                
                <div class="section-title">🎨 图片风格</div>
                <div class="option-grid" id="imgStyleGrid">
                    <div class="option-item selected" data-style="realistic" onclick="selectImgStyle('realistic')"><div class="icon">📷</div>写实摄影</div>
                    <div class="option-item" data-style="commercial" onclick="selectImgStyle('commercial')"><div class="icon">🛍️</div>电商产品</div>
                    <div class="option-item" data-style="minimalist" onclick="selectImgStyle('minimalist')"><div class="icon">⬜</div>简约现代</div>
                    <div class="option-item" data-style="artistic" onclick="selectImgStyle('artistic')"><div class="icon">🎨</div>艺术插画</div>
                    <div class="option-item" data-style="3d" onclick="selectImgStyle('3d')"><div class="icon">🎮</div>3D渲染</div>
                </div>
                
                <div class="section-title">📐 图片尺寸</div>
                <div class="form-row">
                    <div class="form-group">
                        <label>预设尺寸</label>
                        <select id="imgSizePreset" onchange="updateImgSize()">
                            <option value="1440x1440">1440×1440 (1:1 方形)</option>
                            <option value="1440x1920">1440×1920 (3:4 竖版)</option>
                            <option value="1920x1440">1920×1440 (4:3 横版)</option>
                            <option value="1080x1920">1080×1920 (9:16 手机竖屏)</option>
                            <option value="1920x1080" selected>1920×1080 (16:9 横屏)</option>
                            <option value="2048x2048">2048×2048 (1:1 超清方形)</option>
                            <option value="custom">自定义尺寸...</option>
                        </select>
                    </div>
                    <div class="form-group" id="customSizeGroup" style="display:none;">
                        <label>自定义 (宽x高)</label>
                        <input type="text" id="customSize" placeholder="例如: 1440x1920">
                    </div>
                    <div class="form-group">
                        <label>生成数量</label>
                        <select id="imgCount">
                            <option value="1">1张</option>
                            <option value="2">2张</option>
                            <option value="3" selected>3张</option>
                            <option value="4">4张</option>
                        </select>
                    </div>
                </div>
                
                <div class="size-preview" id="sizePreview">
                    <div class="preview-box" id="previewBox" style="width:96px;height:54px;"></div>
                    <div class="size-text" id="sizeText">1920 × 1080 像素</div>
                </div>
                
                <div class="cost-box">
                    <span class="cost-label">预估费用</span>
                    <span class="cost-value" id="imgCost">¥0.60</span>
                </div>
                
                <div class="btn-group">
                    <button class="btn btn-primary" onclick="generateImages()" id="genImgBtn">🚀 生成图片</button>
                </div>
            </div>
            
            <!-- 图片生成进度 -->
            <div class="card hidden" id="imgProgress">
                <div class="card-title">⏳ 生成中...</div>
                <div class="progress-bar"><div class="progress-fill" id="imgProgressBar" style="width:0%"></div></div>
                <div class="progress-text" id="imgProgressText">准备中...</div>
            </div>
            
            <!-- 图片结果 -->
            <div class="card hidden" id="imgResults">
                <div class="card-title">🎉 生成完成！</div>
                <div class="image-results" id="imgResultGrid"></div>
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="downloadAllImages()">📥 全部下载</button>
                    <button class="btn btn-secondary" onclick="sendImagesToFeishu()">📤 发飞书</button>
                    <button class="btn btn-primary" onclick="resetImageMode()">✨ 继续生成</button>
                </div>
            </div>
        </div>
        
        <!-- ========== 视频生成模式 ========== -->
        <div id="videoMode" class="hidden">
            <div class="card">
                <div class="card-title">🎬 视频生成（完整流程）</div>
                
                <div class="section-title">📺 场景类型</div>
                <div class="option-grid" id="sceneTabs">
                    <div class="option-item selected" data-scene="product" onclick="selectScene('product')"><div class="icon">🖥️</div>电商产品</div>
                    <div class="option-item" data-scene="douyin" onclick="selectScene('douyin')"><div class="icon">📱</div>抖音视频</div>
                    <div class="option-item" data-scene="food" onclick="selectScene('food')"><div class="icon">🍜</div>美食探店</div>
                    <div class="option-item" data-scene="travel" onclick="selectScene('travel')"><div class="icon">✈️</div>旅游风景</div>
                    <div class="option-item" data-scene="custom" onclick="selectScene('custom')"><div class="icon">✨</div>自定义</div>
                </div>
                
                <div class="form-group">
                    <label id="videoInputLabel">产品名称 *</label>
                    <input type="text" id="videoMainInput" placeholder="例如：联想小新Pro16笔记本">
                </div>
                <div class="form-group">
                    <label>详细描述</label>
                    <textarea id="videoDetailInput" placeholder="例如：16英寸2.5K屏，i7处理器，性能强劲"></textarea>
                </div>
                
                <div class="section-title">⚙️ 生成设置</div>
                <div class="form-row-3">
                    <div class="form-group">
                        <label>镜头数量</label>
                        <select id="videoScenes">
                            <option value="3">3个镜头</option>
                            <option value="4" selected>4个镜头</option>
                            <option value="5">5个镜头</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>视频时长</label>
                        <select id="videoDuration">
                            <option value="5" selected>5秒/镜头</option>
                            <option value="10">10秒/镜头</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>生成模式</label>
                        <select id="videoGenMode">
                            <option value="full">图片+视频</option>
                            <option value="imageOnly">只生成图片</option>
                        </select>
                    </div>
                </div>
                
                <div class="cost-box">
                    <span class="cost-label">预估费用</span>
                    <span class="cost-value" id="videoCost">¥4.40</span>
                </div>
                
                <div class="btn-group">
                    <button class="btn btn-primary" onclick="generateVideo()">🚀 开始生成</button>
                </div>
            </div>
            
            <!-- 视频生成进度 -->
            <div class="card hidden" id="videoProgress">
                <div class="card-title">⏳ 生成中...</div>
                <div id="videoStatusList"></div>
                <div class="progress-bar"><div class="progress-fill" id="videoProgressBar" style="width:0%"></div></div>
                <div class="progress-text" id="videoProgressText">准备中...</div>
            </div>
            
            <!-- 视频结果 -->
            <div class="card hidden" id="videoResults">
                <div class="card-title">🎉 生成完成！</div>
                <div class="form-group">
                    <label>📝 文案</label>
                    <div style="background:rgba(0,0,0,0.2);border-radius:8px;padding:15px;white-space:pre-wrap;" id="videoCopyResult"></div>
                </div>
                <div class="image-results" id="videoResultGrid"></div>
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="downloadAllVideos()">📥 全部下载</button>
                    <button class="btn btn-primary" onclick="resetVideoMode()">✨ 新建</button>
                </div>
            </div>
        </div>
        
        <!-- 历史记录 -->
        <div class="card hidden" id="historyCard">
            <div class="card-title">📋 历史记录 <button class="btn btn-secondary btn-small" onclick="hideHistory()">返回</button></div>
            <div id="historyList"></div>
        </div>
    </div>
    
    <div class="modal" id="imageModal" onclick="closeModal()">
        <span class="modal-close">&times;</span>
        <img id="modalImage" src="">
    </div>
    
    <script>
        // 图片风格配置
        var IMG_STYLES = {
            realistic: ", photorealistic, professional photography, high quality, 8K UHD, sharp focus",
            commercial: ", commercial product photography, studio lighting, clean white background, professional, 8K",
            minimalist: ", minimalist style, clean composition, soft colors, modern aesthetic, 8K",
            artistic: ", artistic illustration, creative style, vibrant colors, detailed artwork, 8K",
            "3d": ", 3D render, octane render, cinema 4D, photorealistic 3D, studio lighting, 8K"
        };
        
        // 提示词模板
        var PROMPT_TEMPLATES = [
            { name: "📱 电子产品", prompt: "一台[产品名称]，放在简约的白色桌面上，柔和的自然光照射，背景干净整洁，产品细节清晰可见，专业产品摄影风格" },
            { name: "🛍️ 电商白底图", prompt: "[产品名称]，纯白色背景，专业摄影棚灯光，产品居中展示，高清细节，商业产品摄影" },
            { name: "🏠 生活场景", prompt: "[产品名称]放在温馨的家居环境中，旁边有绿植和装饰品，阳光从窗户照进来，画面温馨自然，生活方式摄影" },
            { name: "🎨 创意海报", prompt: "[主题]创意海报设计，现代简约风格，大胆的色彩搭配，几何图形元素，专业平面设计" },
            { name: "🍔 美食摄影", prompt: "[美食名称]特写，精美摆盘，柔和的侧光，浅景深背景虚化，让人垂涎欲滴，专业美食摄影" },
            { name: "👗 服装展示", prompt: "[服装描述]，模特穿着展示，简约背景，专业时尚摄影，展示服装细节和质感" },
            { name: "🏢 建筑空间", prompt: "[空间类型]室内设计，现代简约风格，宽敞明亮，专业建筑摄影，展示空间层次感" },
            { name: "🌸 自然风景", prompt: "[地点描述]自然风景，黄金时段光线，壮观的云彩，风景摄影大师作品风格" }
        ];
        
        var SCENE_CONFIG = {
            product: { label: "产品名称 *", placeholder: "例如：联想小新Pro16笔记本" },
            douyin: { label: "视频主题 *", placeholder: "例如：一个人的周末vlog" },
            food: { label: "店铺/美食 *", placeholder: "例如：深夜食堂居酒屋" },
            travel: { label: "目的地 *", placeholder: "例如：云南大理洱海" },
            custom: { label: "主题 *", placeholder: "输入你的主题" }
        };
        
        var currentImgStyle = "realistic";
        var currentScene = "product";
        var generatedImages = [];
        var videoData = { copywriting: "", scenes: [] };
        
        // 模式切换
        function switchMode(mode) {
            document.querySelectorAll('.main-tab').forEach(t => t.classList.remove('active'));
            event.target.closest('.main-tab').classList.add('active');
            document.getElementById('imageMode').classList.toggle('hidden', mode !== 'image');
            document.getElementById('videoMode').classList.toggle('hidden', mode !== 'video');
            document.getElementById('historyCard').classList.add('hidden');
        }
        
        // ========== 图片生成模式 ==========
        function selectImgStyle(style) {
            currentImgStyle = style;
            document.querySelectorAll('#imgStyleGrid .option-item').forEach(t => t.classList.remove('selected'));
            document.querySelector('#imgStyleGrid .option-item[data-style="'+style+'"]').classList.add('selected');
        }
        
        function updateImgSize() {
            var preset = document.getElementById('imgSizePreset').value;
            var customGroup = document.getElementById('customSizeGroup');
            customGroup.style.display = preset === 'custom' ? 'block' : 'none';
            
            var size = preset === 'custom' ? document.getElementById('customSize').value : preset;
            if (size && size.includes('x')) {
                var parts = size.split('x');
                var w = parseInt(parts[0]), h = parseInt(parts[1]);
                // 预览框（按比例缩放，最大100px）
                var scale = Math.min(100/w, 100/h);
                document.getElementById('previewBox').style.width = (w*scale) + 'px';
                document.getElementById('previewBox').style.height = (h*scale) + 'px';
                document.getElementById('sizeText').textContent = w + ' × ' + h + ' 像素';
            }
            updateImgCost();
        }
        
        function updateImgCost() {
            var count = parseInt(document.getElementById('imgCount').value);
            document.getElementById('imgCost').textContent = '¥' + (count * 0.20).toFixed(2);
        }
        
        function getImgSize() {
            var preset = document.getElementById('imgSizePreset').value;
            return preset === 'custom' ? document.getElementById('customSize').value.trim() : preset;
        }
        
        async function generateImages() {
            var prompt = document.getElementById('imgPrompt').value.trim();
            if (!prompt) { alert('请输入图片描述'); return; }
            
            var negPrompt = document.getElementById('negativePrompt').value.trim();
            var size = getImgSize();
            var count = parseInt(document.getElementById('imgCount').value);
            var stylePrompt = IMG_STYLES[currentImgStyle] || "";
            var fullPrompt = prompt + stylePrompt;
            if (negPrompt) {
                fullPrompt += ", avoid: " + negPrompt;
            }
            
            // 显示进度
            document.getElementById('imgProgress').classList.remove('hidden');
            document.getElementById('imgResults').classList.add('hidden');
            document.getElementById('genImgBtn').disabled = true;
            
            generatedImages = [];
            for (var i = 0; i < count; i++) {
                document.getElementById('imgProgressText').textContent = '生成第 ' + (i+1) + '/' + count + ' 张...';
                document.getElementById('imgProgressBar').style.width = ((i+1)/count*100) + '%';
                
                try {
                    var resp = await fetch('/api/generate-images', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ prompt: fullPrompt, count: 1, size: size })
                    });
                    var data = await resp.json();
                    if (data.images && data.images[0] && data.images[0].url) {
                        generatedImages.push(data.images[0].url);
                    }
                } catch(e) {
                    console.error(e);
                }
            }
            
            // 显示结果
            showImageResults();
            saveImageHistory(prompt, size, generatedImages);
        }
        
        function showImageResults() {
            document.getElementById('imgProgress').classList.add('hidden');
            document.getElementById('imgResults').classList.remove('hidden');
            document.getElementById('genImgBtn').disabled = false;
            
            var html = '';
            generatedImages.forEach(function(url, i) {
                html += '<div class="image-result-item">' +
                    '<img src="'+url+'" onclick="openModal(\''+url+'\')">' +
                    '<div class="img-info"><span>图片'+(i+1)+'</span>' +
                    '<div><button class="btn btn-secondary btn-small" onclick="regenerateSingle('+i+')" style="margin-right:5px;">🔄</button>' +
                    '<a href="'+url+'" target="_blank" class="btn btn-secondary btn-small">下载</a></div></div>' +
                    '</div>';
            });
            document.getElementById('imgResultGrid').innerHTML = html || '<p style="color:rgba(255,255,255,0.5);">生成失败</p>';
        }
        
        async function regenerateSingle(index) {
            var prompt = document.getElementById('imgPrompt').value.trim();
            var negPrompt = document.getElementById('negativePrompt').value.trim();
            var size = getImgSize();
            var stylePrompt = IMG_STYLES[currentImgStyle] || "";
            var fullPrompt = prompt + stylePrompt;
            if (negPrompt) fullPrompt += ", avoid: " + negPrompt;
            
            // 更新按钮状态
            event.target.disabled = true;
            event.target.textContent = '...';
            
            try {
                var resp = await fetch('/api/generate-images', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ prompt: fullPrompt, count: 1, size: size })
                });
                var data = await resp.json();
                if (data.images && data.images[0] && data.images[0].url) {
                    generatedImages[index] = data.images[0].url;
                    showImageResults();
                }
            } catch(e) {
                alert('重新生成失败');
            }
            event.target.disabled = false;
            event.target.textContent = '🔄';
        }
        
        function showPromptTemplates() {
            var html = '';
            PROMPT_TEMPLATES.forEach(function(t, i) {
                html += '<div style="padding:15px;background:rgba(255,255,255,0.05);border-radius:10px;margin-bottom:10px;cursor:pointer;" onclick="useTemplate('+i+')">' +
                    '<div style="font-weight:bold;margin-bottom:5px;">'+t.name+'</div>' +
                    '<div style="font-size:13px;color:rgba(255,255,255,0.6);">'+t.prompt+'</div></div>';
            });
            document.getElementById('templateList').innerHTML = html;
            document.getElementById('templateModal').classList.remove('hidden');
            document.getElementById('templateModal').style.display = 'flex';
        }
        
        function hidePromptTemplates() {
            document.getElementById('templateModal').classList.add('hidden');
            document.getElementById('templateModal').style.display = 'none';
        }
        
        function useTemplate(index) {
            document.getElementById('imgPrompt').value = PROMPT_TEMPLATES[index].prompt;
            hidePromptTemplates();
        }
        
        function downloadAllImages() {
            generatedImages.forEach(function(url, i) {
                var a = document.createElement('a');
                a.href = url; a.download = '图片' + (i+1) + '.jpg'; a.click();
            });
        }
        
        async function sendImagesToFeishu() {
            if (generatedImages.length === 0) { alert('没有可发送的图片'); return; }
            var prompt = document.getElementById('imgPrompt').value.trim();
            var msg = '🖼️ AI生成图片\n\n描述: ' + prompt.substring(0, 100) + '\n\n';
            generatedImages.forEach(function(url, i) {
                msg += '图片' + (i+1) + ': ' + url + '\n';
            });
            
            try {
                await fetch('/api/notify', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ message: msg })
                });
                alert('已发送到飞书！');
            } catch(e) {
                alert('发送失败');
            }
        }
        
        function resetImageMode() {
            document.getElementById('imgProgress').classList.add('hidden');
            document.getElementById('imgResults').classList.add('hidden');
            document.getElementById('imgPrompt').value = '';
            generatedImages = [];
        }
        
        // ========== 视频生成模式 ==========
        function selectScene(scene) {
            currentScene = scene;
            document.querySelectorAll('#sceneTabs .option-item').forEach(t => t.classList.remove('selected'));
            document.querySelector('#sceneTabs .option-item[data-scene="'+scene+'"]').classList.add('selected');
            var cfg = SCENE_CONFIG[scene];
            document.getElementById('videoInputLabel').textContent = cfg.label;
            document.getElementById('videoMainInput').placeholder = cfg.placeholder;
            updateVideoCost();
        }
        
        function updateVideoCost() {
            var scenes = parseInt(document.getElementById('videoScenes').value);
            var duration = parseInt(document.getElementById('videoDuration').value);
            var mode = document.getElementById('videoGenMode').value;
            var imgCost = scenes * 0.20;
            var vidCost = mode === 'imageOnly' ? 0 : scenes * (duration === 5 ? 0.50 : 1.00);
            document.getElementById('videoCost').textContent = '¥' + (imgCost + vidCost + 0.01).toFixed(2);
        }
        
        async function generateVideo() {
            var name = document.getElementById('videoMainInput').value.trim();
            if (!name) { alert('请输入主题'); return; }
            
            var detail = document.getElementById('videoDetailInput').value.trim();
            var numScenes = parseInt(document.getElementById('videoScenes').value);
            var duration = parseInt(document.getElementById('videoDuration').value);
            var mode = document.getElementById('videoGenMode').value;
            
            document.getElementById('videoProgress').classList.remove('hidden');
            document.getElementById('videoResults').classList.add('hidden');
            
            var statusHtml = '<div style="padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.1);">📝 文案生成...</div>';
            document.getElementById('videoStatusList').innerHTML = statusHtml;
            document.getElementById('videoProgressText').textContent = '生成文案...';
            
            try {
                // 1. 生成文案
                var resp = await fetch('/api/generate-copy', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ product_name: name, product_features: detail, scene_type: currentScene })
                });
                var data = await resp.json();
                if (!data.success) throw new Error(data.error);
                videoData.copywriting = data.content;
                
                // 2. 生成分镜
                document.getElementById('videoProgressText').textContent = '生成分镜...';
                document.getElementById('videoProgressBar').style.width = '20%';
                resp = await fetch('/api/generate-storyboard', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ product_name: name, copywriting: videoData.copywriting, num_scenes: numScenes })
                });
                data = await resp.json();
                if (!data.success) throw new Error(data.error);
                var storyboard = data.storyboard.scenes || [];
                
                // 3. 生成图片
                videoData.scenes = [];
                for (var i = 0; i < storyboard.length; i++) {
                    document.getElementById('videoProgressText').textContent = '生成图片 ' + (i+1) + '/' + storyboard.length;
                    document.getElementById('videoProgressBar').style.width = (20 + (i/storyboard.length)*40) + '%';
                    resp = await fetch('/api/generate-images', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ prompt: storyboard[i].image_prompt, count: 1, size: '1920x1080' })
                    });
                    data = await resp.json();
                    var imgUrl = (data.images && data.images[0]) ? data.images[0].url : null;
                    videoData.scenes.push({ image: imgUrl, video: null, prompt: storyboard[i].video_prompt });
                }
                
                // 4. 生成视频（如果需要）
                if (mode === 'full') {
                    for (var i = 0; i < videoData.scenes.length; i++) {
                        if (!videoData.scenes[i].image) continue;
                        document.getElementById('videoProgressText').textContent = '生成视频 ' + (i+1) + '/' + videoData.scenes.length + ' (约1-2分钟)';
                        document.getElementById('videoProgressBar').style.width = (60 + (i/videoData.scenes.length)*35) + '%';
                        resp = await fetch('/api/generate-video', {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ image_url: videoData.scenes[i].image, prompt: videoData.scenes[i].prompt || 'smooth movement', duration: duration })
                        });
                        data = await resp.json();
                        if (data.success && data.video_url) {
                            videoData.scenes[i].video = data.video_url;
                        }
                    }
                }
                
                document.getElementById('videoProgressBar').style.width = '100%';
                document.getElementById('videoProgressText').textContent = '完成！';
                setTimeout(showVideoResults, 500);
                
            } catch(e) {
                alert('生成失败: ' + e.message);
                document.getElementById('videoProgress').classList.add('hidden');
            }
        }
        
        function showVideoResults() {
            document.getElementById('videoProgress').classList.add('hidden');
            document.getElementById('videoResults').classList.remove('hidden');
            document.getElementById('videoCopyResult').textContent = videoData.copywriting;
            
            var html = '';
            videoData.scenes.forEach(function(s, i) {
                if (s.video) {
                    html += '<div class="image-result-item"><video src="'+s.video+'" controls style="width:100%;"></video><div class="img-info"><span>镜头'+(i+1)+'</span><a href="'+s.video+'" target="_blank" class="btn btn-secondary btn-small">下载</a></div></div>';
                } else if (s.image) {
                    html += '<div class="image-result-item"><img src="'+s.image+'" onclick="openModal(\''+s.image+'\')"><div class="img-info"><span>镜头'+(i+1)+'</span><a href="'+s.image+'" target="_blank" class="btn btn-secondary btn-small">下载</a></div></div>';
                }
            });
            document.getElementById('videoResultGrid').innerHTML = html || '<p style="color:rgba(255,255,255,0.5);">无内容</p>';
        }
        
        function downloadAllVideos() {
            videoData.scenes.forEach(function(s, i) {
                var url = s.video || s.image;
                if (url) { var a = document.createElement('a'); a.href = url; a.download = '镜头'+(i+1)+(s.video?'.mp4':'.jpg'); a.click(); }
            });
        }
        
        function resetVideoMode() {
            document.getElementById('videoProgress').classList.add('hidden');
            document.getElementById('videoResults').classList.add('hidden');
            document.getElementById('videoMainInput').value = '';
            document.getElementById('videoDetailInput').value = '';
            videoData = { copywriting: '', scenes: [] };
        }
        
        // ========== 通用功能 ==========
        function openModal(url) { document.getElementById('modalImage').src = url; document.getElementById('imageModal').classList.add('show'); }
        function closeModal() { document.getElementById('imageModal').classList.remove('show'); }
        
        function saveImageHistory(prompt, size, images) {
            var h = JSON.parse(localStorage.getItem('imgHistory') || '[]');
            h.unshift({ type: 'image', prompt: prompt.substring(0,50), size: size, images: images, time: new Date().toLocaleString() });
            if (h.length > 30) h.pop();
            localStorage.setItem('imgHistory', JSON.stringify(h));
        }
        
        function showHistory() {
            document.getElementById('imageMode').classList.add('hidden');
            document.getElementById('videoMode').classList.add('hidden');
            document.getElementById('historyCard').classList.remove('hidden');
            
            var h = JSON.parse(localStorage.getItem('imgHistory') || '[]');
            if (h.length === 0) {
                document.getElementById('historyList').innerHTML = '<p style="color:rgba(255,255,255,0.5);text-align:center;padding:20px;">暂无记录</p>';
            } else {
                var html = '';
                h.forEach(function(item) {
                    html += '<div style="padding:15px;background:rgba(255,255,255,0.05);border-radius:10px;margin-bottom:10px;"><div style="display:flex;justify-content:space-between;"><strong>🖼️ ' + item.prompt + '...</strong><span style="color:rgba(255,255,255,0.5);font-size:12px;">' + item.size + '</span></div><div style="font-size:12px;color:rgba(255,255,255,0.5);margin-top:5px;">' + item.time + ' · ' + (item.images ? item.images.length : 0) + '张图片</div></div>';
                });
                document.getElementById('historyList').innerHTML = html;
            }
        }
        
        function hideHistory() {
            document.getElementById('historyCard').classList.add('hidden');
            document.getElementById('imageMode').classList.remove('hidden');
        }
        
        // 初始化
        document.getElementById('imgSizePreset').addEventListener('change', updateImgSize);
        document.getElementById('imgCount').addEventListener('change', updateImgCost);
        document.getElementById('videoScenes').addEventListener('change', updateVideoCost);
        document.getElementById('videoDuration').addEventListener('change', updateVideoCost);
        document.getElementById('videoGenMode').addEventListener('change', updateVideoCost);
        updateImgSize();
        updateImgCost();
        updateVideoCost();
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    return Response(HTML_PAGE, mimetype='text/html')

@app.route('/health')
def health():
    return jsonify({"status": "ok", "version": "v6.1"})

@app.route('/api/generate-copy', methods=['POST'])
def api_generate_copy():
    data = request.get_json() or {}
    name = data.get("product_name", "").strip()
    features = data.get("product_features", "").strip()
    if not name: return jsonify({"success": False, "error": "请输入主题"}), 400
    system = "短视频文案专家。生成30秒文案，80-150字，分4段。"
    user = f"主题：{name}\n详情：{features}"
    return jsonify(chat_completion(system, user))

@app.route('/api/generate-storyboard', methods=['POST'])
def api_generate_storyboard():
    data = request.get_json() or {}
    name = data.get("product_name", "").strip()
    copy = data.get("copywriting", "").strip()
    num = data.get("num_scenes", 4)
    if not name or not copy: return jsonify({"success": False, "error": "缺少参数"}), 400
    system = f'分镜师。生成{num}个分镜。只输出JSON：{{"scenes":[{{"scene_id":1,"description":"描述","image_prompt":"English prompt, 8K UHD","video_prompt":"camera movement"}}]}}'
    user = f"主题：{name}\n文案：{copy}"
    result = chat_completion(system, user)
    if result.get("success"):
        try:
            c = result["content"]
            s, e = c.find("{"), c.rfind("}") + 1
            if s >= 0 and e > s: return jsonify({"success": True, "storyboard": json.loads(c[s:e])})
        except: pass
        return jsonify({"success": False, "error": "JSON解析失败"})
    return jsonify(result)

@app.route('/api/generate-images', methods=['POST'])
def api_generate_images():
    data = request.get_json() or {}
    prompt = data.get("prompt", "").strip()
    count = min(data.get("count", 1), 4)
    size = data.get("size", "1920x1080")
    if not prompt: return jsonify({"success": False, "error": "请输入提示词"}), 400
    images = []
    for i in range(count):
        r = generate_image(prompt, size)
        images.append({"index": i+1, "url": r.get("image_url") if r.get("success") else None, "error": r.get("error") if not r.get("success") else None})
    return jsonify({"success": True, "images": images})

@app.route('/api/generate-video', methods=['POST'])
def api_generate_video():
    data = request.get_json() or {}
    img = data.get("image_url", "").strip()
    prompt = data.get("prompt", "slow movement").strip()
    duration = data.get("duration", 5)
    if not img: return jsonify({"success": False, "error": "请提供图片URL"}), 400
    task = create_video_task(img, prompt, duration, "1080p")
    if not task.get("success"): return jsonify(task)
    return jsonify(wait_for_video(task["task_id"]))

@app.route('/api/notify', methods=['POST'])
def api_notify():
    data = request.get_json() or {}
    if data.get("message"): send_feishu_text(data["message"])
    return jsonify({"success": True})

def feishu_quick_generate(prompt, with_video=False):
    try:
        img = generate_image(prompt)
        if not img.get("success"): send_feishu_text(f"❌ 失败：{img.get('error')}"); return
        image_url = img["image_url"]
        image_key = upload_image_to_feishu(image_url)
        video_url = None
        if with_video:
            task = create_video_task(image_url, f"{prompt}, cinematic", 5, "1080p")
            if task.get("success"):
                v = wait_for_video(task["task_id"])
                video_url = v.get("video_url") if v.get("success") else None
        content = [[{"tag": "text", "text": f"📝 {prompt}"}]]
        if image_key: content.append([{"tag": "img", "image_key": image_key}])
        content.append([{"tag": "a", "text": "🖼️原图", "href": image_url}])
        if video_url: content.append([{"tag": "a", "text": "🎬视频", "href": video_url}])
        send_feishu_message("✅ 完成", content)
    except Exception as e: send_feishu_text(f"❌ 失败：{e}")

@app.route('/feishu-callback', methods=['POST'])
def feishu_callback():
    data = request.get_json() or {}
    if 'challenge' in data: return jsonify({"challenge": data['challenge']})
    try:
        if data.get('header', {}).get('event_type') == 'im.message.receive_v1':
            msg = data.get('event', {}).get('message', {})
            if msg.get('message_type') != 'text': return jsonify({"code": 0})
            text = json.loads(msg.get('content', '{}')).get('text', '')
            prompt = re.sub(r'@\S+', '', text).strip()
            if "统计" in text: send_feishu_text(f"📊 项目数：{len(projects)}"); return jsonify({"code": 0})
            clean = prompt.replace("视频", "").strip()
            if clean and len(clean) >= 2:
                threading.Thread(target=feishu_quick_generate, args=(clean, "视频" in text), daemon=True).start()
    except Exception as e: print(f"飞书回调异常: {e}")
    return jsonify({"code": 0})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 即梦AI v6.1 启动 - 端口: {port}")
    app.run(host="0.0.0.0", port=port)
