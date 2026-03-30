"""
即梦AI Flask代理服务 v6.0
===========================
完整功能版：多场景 + 图片设置 + 参考图上传 + 草稿保存 + 字幕生成 + 背景音乐
"""

from flask import Flask, request, jsonify, Response
import requests
import json
import os
import re
import time
import threading
import base64
import uuid

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

# 价格配置
PRICE_IMAGE = 0.20
PRICE_VIDEO_5S = 0.50
PRICE_VIDEO_10S = 1.00
PRICE_CHAT = 0.002

projects = {}

# 工具函数
def send_feishu_text(text):
    try: requests.post(FEISHU_BOT_WEBHOOK, json={"msg_type": "text", "content": {"text": text}}, timeout=10)
    except: pass

def send_feishu_message(title, content_blocks):
    try: requests.post(FEISHU_BOT_WEBHOOK, json={"msg_type": "post", "content": {"post": {"zh_cn": {"title": title, "content": content_blocks}}}}, timeout=10)
    except: pass

def get_feishu_tenant_token():
    try:
        resp = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=10)
        data = resp.json()
        return data.get("tenant_access_token") if data.get("code") == 0 else None
    except: return None

def upload_image_to_feishu(image_url):
    try:
        token = get_feishu_tenant_token()
        if not token: return None
        img_resp = requests.get(image_url, timeout=60)
        if img_resp.status_code != 200: return None
        resp = requests.post("https://open.feishu.cn/open-apis/im/v1/images", headers={"Authorization": f"Bearer {token}"}, files={"image": (f"ai_{int(time.time())}.jpg", img_resp.content, "image/jpeg")}, data={"image_type": "message"}, timeout=30)
        result = resp.json()
        return result.get("data", {}).get("image_key") if result.get("code") == 0 else None
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
        print(f"[图片] 返回: {str(result)[:200]}")
        if "data" in result and len(result["data"]) > 0:
            url = result["data"][0].get("url", "")
            return {"success": True, "image_url": url}
        return {"success": False, "error": result.get("error", {}).get("message", str(result))}
    except Exception as e:
        return {"success": False, "error": str(e)}

def create_video_task(image_url, prompt, duration=5, resolution="1080p"):
    try:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {ARK_API_KEY}"}
        payload = {"model": VIDEO_MODEL, "content": [{"type": "image_url", "image_url": {"url": image_url}, "role": "first_frame"}, {"type": "text", "text": prompt}], "duration": duration, "resolution": resolution}
        print(f"[视频] 创建任务...")
        response = requests.post(VIDEO_API_ENDPOINT, headers=headers, json=payload, timeout=30)
        result = response.json()
        if "id" in result:
            return {"success": True, "task_id": result["id"]}
        return {"success": False, "error": result.get("error", {}).get("message", str(result))}
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
        print(f"[视频] 状态: {status}")
        if status == "succeeded":
            video_url = None
            if "content" in result:
                content = result["content"]
                if isinstance(content, dict):
                    video_url = content.get("video_url") or content.get("url")
            if not video_url:
                video_url = result.get("video_url") or result.get("output", {}).get("video_url")
            return {"success": True, "video_url": video_url} if video_url else {"success": False, "error": "无URL"}
        elif status in ["failed", "cancelled"]:
            return {"success": False, "error": f"任务{status}"}
        time.sleep(5)
    return {"success": False, "error": "超时"}

# HTML页面
HTML_PAGE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI视频生成器 v6.0</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); min-height: 100vh; color: #fff; }
        .container { max-width: 900px; margin: 0 auto; padding: 20px; }
        .header { display: flex; justify-content: space-between; align-items: center; padding: 20px 0; border-bottom: 1px solid rgba(255,255,255,0.1); margin-bottom: 30px; flex-wrap: wrap; gap: 10px; }
        .header h1 { font-size: 22px; }
        .header-btns { display: flex; gap: 10px; }
        .version { font-size: 12px; background: #4CAF50; padding: 2px 8px; border-radius: 10px; margin-left: 10px; }
        .card { background: rgba(255,255,255,0.05); border-radius: 16px; padding: 25px; margin-bottom: 20px; border: 1px solid rgba(255,255,255,0.1); }
        .card-title { font-size: 18px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 8px; color: rgba(255,255,255,0.8); font-size: 14px; }
        .form-group input, .form-group textarea, .form-group select { width: 100%; padding: 12px 16px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.2); background: rgba(255,255,255,0.05); color: #fff; font-size: 16px; }
        .form-group input:focus, .form-group textarea:focus { outline: none; border-color: #4CAF50; }
        .form-group textarea { min-height: 80px; resize: vertical; }
        .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        .form-row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 15px; }
        .form-row-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; }
        .btn { padding: 12px 24px; border-radius: 8px; border: none; font-size: 15px; font-weight: 600; cursor: pointer; transition: all 0.3s; }
        .btn-primary { background: linear-gradient(135deg, #4CAF50, #45a049); color: #fff; }
        .btn-auto { background: linear-gradient(135deg, #FF6B6B, #FF8E53); color: #fff; }
        .btn-secondary { background: rgba(255,255,255,0.1); color: #fff; border: 1px solid rgba(255,255,255,0.2); }
        .btn-small { padding: 6px 12px; font-size: 12px; }
        .btn:hover { transform: translateY(-2px); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        .btn-group { display: flex; gap: 15px; margin-top: 20px; flex-wrap: wrap; }
        .progress-bar { width: 100%; height: 8px; background: rgba(255,255,255,0.1); border-radius: 4px; overflow: hidden; margin: 15px 0; }
        .progress-fill { height: 100%; background: linear-gradient(90deg, #4CAF50, #8BC34A); transition: width 0.3s; }
        .progress-text { text-align: center; color: rgba(255,255,255,0.7); font-size: 14px; }
        .status-item { display: flex; align-items: center; gap: 15px; padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.1); }
        .status-icon { width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 16px; }
        .status-icon.pending { background: rgba(255,255,255,0.1); }
        .status-icon.loading { background: #FF9800; animation: pulse 1s infinite; }
        .status-icon.done { background: #4CAF50; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .scene-card { background: rgba(0,0,0,0.2); border-radius: 12px; padding: 20px; margin-bottom: 15px; }
        .scene-title { font-weight: 600; margin-bottom: 10px; color: #4CAF50; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
        .scene-images { display: flex; gap: 10px; margin: 15px 0; flex-wrap: wrap; }
        .scene-image { width: 100px; height: 100px; border-radius: 8px; object-fit: cover; cursor: pointer; border: 3px solid transparent; transition: all 0.3s; }
        .scene-image:hover { border-color: #4CAF50; transform: scale(1.05); }
        .scene-image.selected { border-color: #4CAF50; box-shadow: 0 0 10px rgba(76,175,80,0.5); }
        .result-videos { display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; margin: 20px 0; }
        .result-video-item { background: rgba(0,0,0,0.2); border-radius: 12px; overflow: hidden; }
        .result-video-item video, .result-video-item img { width: 100%; display: block; cursor: pointer; }
        .result-video-item .video-info { padding: 10px 15px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 5px; }
        .content-box { background: rgba(0,0,0,0.2); border-radius: 8px; padding: 15px; margin: 10px 0; white-space: pre-wrap; line-height: 1.6; font-size: 14px; }
        .hidden { display: none !important; }
        .tabs { display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; }
        .tab { padding: 8px 16px; background: rgba(255,255,255,0.1); border-radius: 20px; cursor: pointer; font-size: 14px; transition: all 0.3s; }
        .tab:hover { background: rgba(255,255,255,0.2); }
        .tab.active { background: linear-gradient(135deg, #4CAF50, #45a049); }
        .section-title { font-size: 14px; color: rgba(255,255,255,0.6); margin: 20px 0 10px 0; padding-bottom: 5px; border-bottom: 1px solid rgba(255,255,255,0.1); }
        .option-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
        .option-item { padding: 8px 5px; background: rgba(255,255,255,0.05); border: 2px solid rgba(255,255,255,0.1); border-radius: 8px; text-align: center; cursor: pointer; transition: all 0.3s; font-size: 11px; }
        .option-item:hover { border-color: rgba(255,255,255,0.3); }
        .option-item.selected { border-color: #4CAF50; background: rgba(76,175,80,0.2); }
        .option-item .icon { font-size: 18px; margin-bottom: 3px; }
        .cost-estimate { background: linear-gradient(135deg, rgba(255,193,7,0.2), rgba(255,152,0,0.2)); border: 1px solid rgba(255,193,7,0.3); border-radius: 10px; padding: 15px; margin: 20px 0; }
        .cost-estimate .cost-title { font-size: 14px; color: #FFC107; margin-bottom: 10px; }
        .cost-estimate .cost-detail { font-size: 13px; color: rgba(255,255,255,0.7); line-height: 1.6; }
        .cost-estimate .cost-total { font-size: 18px; color: #FFC107; font-weight: bold; margin-top: 10px; }
        .checkbox-group { display: flex; align-items: center; gap: 10px; margin: 10px 0; }
        .checkbox-group input[type="checkbox"] { width: 18px; height: 18px; cursor: pointer; }
        .checkbox-group label { cursor: pointer; font-size: 14px; }
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.9); z-index: 1000; justify-content: center; align-items: center; }
        .modal.show { display: flex; }
        .modal img { max-width: 90%; max-height: 90%; border-radius: 10px; }
        .modal-close { position: absolute; top: 20px; right: 30px; font-size: 40px; color: #fff; cursor: pointer; }
        .upload-area { border: 2px dashed rgba(255,255,255,0.3); border-radius: 12px; padding: 30px; text-align: center; cursor: pointer; transition: all 0.3s; margin: 10px 0; }
        .upload-area:hover { border-color: #4CAF50; background: rgba(76,175,80,0.1); }
        .upload-area.has-image { border-style: solid; border-color: #4CAF50; }
        .upload-preview { max-width: 200px; max-height: 150px; border-radius: 8px; margin-top: 10px; }
        .music-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 10px 0; }
        .music-item { padding: 12px; background: rgba(255,255,255,0.05); border: 2px solid rgba(255,255,255,0.1); border-radius: 10px; text-align: center; cursor: pointer; transition: all 0.3s; }
        .music-item:hover { border-color: rgba(255,255,255,0.3); }
        .music-item.selected { border-color: #4CAF50; background: rgba(76,175,80,0.2); }
        .music-item .music-icon { font-size: 24px; margin-bottom: 5px; }
        .music-item .music-name { font-size: 13px; }
        .srt-box { background: rgba(0,0,0,0.3); border-radius: 8px; padding: 15px; font-family: monospace; font-size: 12px; white-space: pre-wrap; max-height: 200px; overflow-y: auto; }
        .draft-notice { background: rgba(76,175,80,0.2); border: 1px solid rgba(76,175,80,0.5); border-radius: 8px; padding: 15px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
        @media (max-width: 768px) { 
            .form-row, .form-row-3, .form-row-4 { grid-template-columns: 1fr 1fr; } 
            .result-videos { grid-template-columns: 1fr; } 
            .btn-group { flex-direction: column; } 
            .option-grid { grid-template-columns: repeat(3, 1fr); }
            .music-grid { grid-template-columns: repeat(2, 1fr); }
        }
        @media (max-width: 480px) {
            .form-row, .form-row-3, .form-row-4 { grid-template-columns: 1fr; }
            .option-grid { grid-template-columns: repeat(2, 1fr); }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎬 AI视频生成器 <span class="version">v6.0</span></h1>
            <div class="header-btns">
                <button class="btn btn-secondary btn-small" onclick="showDrafts()">📑 草稿</button>
                <button class="btn btn-secondary btn-small" onclick="showHistory()">📋 历史</button>
            </div>
        </div>
        
        <!-- 草稿恢复提示 -->
        <div class="draft-notice hidden" id="draftNotice">
            <span>📝 发现未完成的草稿，是否继续？</span>
            <div>
                <button class="btn btn-primary btn-small" onclick="loadDraft()">继续编辑</button>
                <button class="btn btn-secondary btn-small" onclick="clearDraft()">放弃</button>
            </div>
        </div>
        
        <!-- Step 1: 基础设置 -->
        <div class="card" id="step1">
            <div class="card-title">📝 Step 1: 视频设置</div>
            
            <div class="section-title">📺 场景类型</div>
            <div class="tabs" id="sceneTabs">
                <div class="tab active" data-scene="product" onclick="selectScene('product')">🖥️ 电商产品</div>
                <div class="tab" data-scene="douyin" onclick="selectScene('douyin')">📱 抖音视频</div>
                <div class="tab" data-scene="food" onclick="selectScene('food')">🍜 美食探店</div>
                <div class="tab" data-scene="travel" onclick="selectScene('travel')">✈️ 旅游风景</div>
                <div class="tab" data-scene="fashion" onclick="selectScene('fashion')">👗 服装穿搭</div>
                <div class="tab" data-scene="realestate" onclick="selectScene('realestate')">🏠 房产展示</div>
                <div class="tab" data-scene="custom" onclick="selectScene('custom')">✨ 自定义</div>
            </div>
            
            <div class="form-group">
                <label id="inputLabel">产品名称 *</label>
                <input type="text" id="mainInput" placeholder="例如：联想小新Pro16笔记本" oninput="saveDraftDebounce()">
            </div>
            <div class="form-group">
                <label id="detailLabel">产品卖点/详细描述</label>
                <textarea id="detailInput" placeholder="例如：16英寸2.5K屏，i7处理器，32G内存" oninput="saveDraftDebounce()"></textarea>
            </div>
            
            <!-- 参考图上传 -->
            <div class="section-title">🖼️ 参考图片（可选）</div>
            <div class="upload-area" id="uploadArea" onclick="document.getElementById('refImageInput').click()">
                <input type="file" id="refImageInput" accept="image/*" style="display:none" onchange="handleImageUpload(event)">
                <div id="uploadText">📤 点击上传参考图片<br><small>上传后AI会参考这张图的风格</small></div>
                <img id="uploadPreview" class="upload-preview hidden">
            </div>
            
            <div class="section-title">🎨 视觉风格</div>
            <div class="option-grid" id="styleGrid">
                <div class="option-item selected" data-style="simple" onclick="selectStyle('simple')"><div class="icon">🔷</div>简约科技</div>
                <div class="option-item" data-style="vibrant" onclick="selectStyle('vibrant')"><div class="icon">🌈</div>活力多彩</div>
                <div class="option-item" data-style="elegant" onclick="selectStyle('elegant')"><div class="icon">✨</div>高端优雅</div>
                <div class="option-item" data-style="cyberpunk" onclick="selectStyle('cyberpunk')"><div class="icon">🎮</div>赛博朋克</div>
                <div class="option-item" data-style="natural" onclick="selectStyle('natural')"><div class="icon">🌿</div>自然清新</div>
                <div class="option-item" data-style="retro" onclick="selectStyle('retro')"><div class="icon">📼</div>复古怀旧</div>
                <div class="option-item" data-style="minimal" onclick="selectStyle('minimal')"><div class="icon">⬜</div>极简黑白</div>
                <div class="option-item" data-style="warm" onclick="selectStyle('warm')"><div class="icon">🌅</div>温暖治愈</div>
                <div class="option-item" data-style="cool" onclick="selectStyle('cool')"><div class="icon">❄️</div>冷酷高级</div>
                <div class="option-item" data-style="cartoon" onclick="selectStyle('cartoon')"><div class="icon">🎨</div>卡通插画</div>
            </div>
            
            <div class="section-title">🖼️ 图片设置</div>
            <div class="form-row-4">
                <div class="form-group">
                    <label>画面比例</label>
                    <select id="aspectRatio" onchange="updateCostEstimate()">
                        <option value="1:1">1:1 方形</option>
                        <option value="16:9" selected>16:9 横屏</option>
                        <option value="9:16">9:16 竖屏</option>
                        <option value="4:3">4:3 传统</option>
                        <option value="3:4">3:4 竖版</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>图片质量</label>
                    <select id="imageQuality">
                        <option value="standard">标准 1K</option>
                        <option value="hd" selected>高清 2K</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>镜头数量</label>
                    <select id="numScenes" onchange="updateCostEstimate()">
                        <option value="2">2个</option>
                        <option value="3">3个</option>
                        <option value="4" selected>4个</option>
                        <option value="5">5个</option>
                        <option value="6">6个</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>每镜头图片</label>
                    <select id="imagesPerScene" onchange="updateCostEstimate()">
                        <option value="1">1张</option>
                        <option value="3" selected>3张</option>
                    </select>
                </div>
            </div>
            
            <div class="section-title">🎬 视频设置</div>
            <div class="form-row-3">
                <div class="form-group">
                    <label>视频时长</label>
                    <select id="videoDuration" onchange="updateCostEstimate()">
                        <option value="5" selected>5秒/镜头</option>
                        <option value="10">10秒/镜头</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>视频分辨率</label>
                    <select id="videoResolution">
                        <option value="720p">720P</option>
                        <option value="1080p" selected>1080P</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>背景音乐</label>
                    <select id="bgMusic">
                        <option value="none">无</option>
                        <option value="upbeat" selected>轻快活力</option>
                        <option value="elegant">优雅舒缓</option>
                        <option value="epic">史诗激昂</option>
                        <option value="emotional">温情感人</option>
                        <option value="tech">科技电子</option>
                    </select>
                </div>
            </div>
            
            <div class="checkbox-group">
                <input type="checkbox" id="skipVideo">
                <label for="skipVideo">⚡ 只生成图片，跳过视频</label>
            </div>
            <div class="checkbox-group">
                <input type="checkbox" id="generateSrt" checked>
                <label for="generateSrt">📝 生成字幕文件 (SRT)</label>
            </div>
            
            <div class="cost-estimate">
                <div class="cost-title">💰 费用估算</div>
                <div class="cost-detail" id="costDetail"></div>
                <div class="cost-total" id="costTotal"></div>
            </div>
            
            <div class="btn-group">
                <button class="btn btn-auto" onclick="oneClickGenerate()">🚀 一键全自动</button>
                <button class="btn btn-primary" onclick="stepGenerate()">📝 分步生成 →</button>
            </div>
        </div>
        
        <!-- Step 2-5 和结果页 -->
        <div class="card hidden" id="step2">
            <div class="card-title">📄 Step 2: 确认文案 <button class="btn btn-secondary btn-small" onclick="goBack(1)">返回</button></div>
            <p style="color:rgba(255,255,255,0.6);margin-bottom:10px;">AI生成的文案，可直接编辑：</p>
            <textarea id="copyText" style="width:100%;min-height:150px;padding:15px;border-radius:8px;border:1px solid rgba(255,255,255,0.2);background:rgba(255,255,255,0.05);color:#fff;font-size:15px;line-height:1.8;"></textarea>
            <div class="btn-group">
                <button class="btn btn-secondary" onclick="regenerateCopy()">🔄 重新生成</button>
                <button class="btn btn-secondary" onclick="saveDraftManual()">💾 保存草稿</button>
                <button class="btn btn-primary" onclick="goToStep3()">下一步 →</button>
            </div>
        </div>
        
        <div class="card hidden" id="step3">
            <div class="card-title">🎬 Step 3: 确认分镜 <button class="btn btn-secondary btn-small" onclick="goBack(2)">返回</button></div>
            <div id="storyboardList"></div>
            <div class="btn-group">
                <button class="btn btn-secondary" onclick="regenerateStoryboard()">🔄 重新生成</button>
                <button class="btn btn-secondary" onclick="saveDraftManual()">💾 保存草稿</button>
                <button class="btn btn-primary" onclick="goToStep4()">下一步 →</button>
            </div>
        </div>
        
        <div class="card hidden" id="step4">
            <div class="card-title">🖼️ Step 4: 选择图片 <button class="btn btn-secondary btn-small" onclick="goBack(3)">返回</button></div>
            <p style="color:rgba(255,255,255,0.6);margin-bottom:15px;">点击选择图片（双击放大）：</p>
            <div id="imageSelectList"></div>
            <div class="btn-group">
                <button class="btn btn-primary" onclick="goToStep5()" id="toStep5Btn">生成视频 →</button>
            </div>
        </div>
        
        <div class="card hidden" id="step5">
            <div class="card-title">⏳ 生成中...</div>
            <div id="statusList"></div>
            <div class="progress-bar"><div class="progress-fill" id="progressBar" style="width:0%"></div></div>
            <div class="progress-text" id="progressText">准备中...</div>
        </div>
        
        <div class="card hidden" id="resultCard">
            <div class="card-title">🎉 生成完成！</div>
            <div class="form-group">
                <label>📝 营销文案</label>
                <div class="content-box" id="resultCopy"></div>
                <button class="btn btn-secondary btn-small" onclick="copyToClipboard()">📋 复制</button>
            </div>
            <div class="form-group" id="srtSection">
                <label>📝 字幕文件 (SRT) <button class="btn btn-secondary btn-small" onclick="downloadSrt()">📥 下载</button></label>
                <div class="srt-box" id="srtContent"></div>
            </div>
            <div class="form-group" id="musicSection">
                <label>🎵 推荐背景音乐</label>
                <div class="content-box" id="musicRecommend"></div>
            </div>
            <div class="form-group">
                <label id="resultLabel">🎬 视频片段</label>
                <div class="result-videos" id="resultVideos"></div>
            </div>
            <div class="btn-group">
                <button class="btn btn-secondary" onclick="downloadAll()">📥 全部下载</button>
                <button class="btn btn-secondary" onclick="sendFeishu()">📤 发飞书</button>
                <button class="btn btn-primary" onclick="restart()">✨ 新建</button>
            </div>
        </div>

        <div class="card hidden" id="historyCard">
            <div class="card-title">📋 历史记录 <button class="btn btn-secondary btn-small" onclick="hideHistory()">返回</button></div>
            <div id="historyList" style="margin-top:15px;"></div>
        </div>
        
        <div class="card hidden" id="draftsCard">
            <div class="card-title">📑 草稿箱 <button class="btn btn-secondary btn-small" onclick="hideDrafts()">返回</button></div>
            <div id="draftsList" style="margin-top:15px;"></div>
        </div>
    </div>
    
    <div class="modal" id="imageModal" onclick="closeModal()">
        <span class="modal-close">&times;</span>
        <img id="modalImage" src="">
    </div>
    
    <script>
        var SCENE_CONFIG = {
            product: { label: "产品名称 *", placeholder: "例如：联想小新Pro16笔记本", detailLabel: "产品卖点", detailPlaceholder: "例如：16英寸2.5K屏，i7处理器", copyPrompt: "电商产品营销文案专家" },
            douyin: { label: "视频主题 *", placeholder: "例如：一个人的周末vlog", detailLabel: "内容描述", detailPlaceholder: "例如：宅家做饭、看电影", copyPrompt: "抖音短视频文案专家" },
            food: { label: "店铺/美食名称 *", placeholder: "例如：深夜食堂居酒屋", detailLabel: "特色菜品", detailPlaceholder: "例如：和牛寿喜烧", copyPrompt: "美食探店博主" },
            travel: { label: "目的地 *", placeholder: "例如：云南大理洱海", detailLabel: "旅行亮点", detailPlaceholder: "例如：环海骑行、日出", copyPrompt: "旅游博主" },
            fashion: { label: "穿搭主题 *", placeholder: "例如：秋冬通勤穿搭", detailLabel: "单品描述", detailPlaceholder: "例如：驼色大衣", copyPrompt: "时尚穿搭博主" },
            realestate: { label: "楼盘名称 *", placeholder: "例如：中海城市广场", detailLabel: "房源亮点", detailPlaceholder: "例如：地铁口、精装修", copyPrompt: "房产销售专家" },
            custom: { label: "主题 *", placeholder: "输入主题", detailLabel: "详细描述", detailPlaceholder: "详细描述", copyPrompt: "创意文案专家" }
        };
        
        var STYLE_CONFIG = {
            simple: { name: "简约科技", image: "minimalist tech style, clean background, soft lighting, 8K", video: "smooth slow camera movement" },
            vibrant: { name: "活力多彩", image: "vibrant colorful, dynamic, energetic, 8K", video: "energetic dynamic movement" },
            elegant: { name: "高端优雅", image: "luxury elegant, sophisticated lighting, 8K", video: "graceful slow motion" },
            cyberpunk: { name: "赛博朋克", image: "cyberpunk, neon lights, RGB glow, 8K", video: "fast dynamic, glitch effects" },
            natural: { name: "自然清新", image: "natural fresh, soft daylight, greenery, 8K", video: "gentle breeze movement" },
            retro: { name: "复古怀旧", image: "vintage retro, film grain, warm tones, 8K", video: "slow vintage film" },
            minimal: { name: "极简黑白", image: "minimalist black white, high contrast, 8K", video: "slow artistic movement" },
            warm: { name: "温暖治愈", image: "warm cozy, golden hour, soft inviting, 8K", video: "gentle warm movement" },
            cool: { name: "冷酷高级", image: "cool sophisticated, blue tones, sleek, 8K", video: "smooth professional" },
            cartoon: { name: "卡通插画", image: "cartoon illustration, cute, colorful anime, 8K", video: "playful animated" }
        };
        
        var MUSIC_RECOMMEND = {
            upbeat: "推荐: 轻快流行风格\n- Upbeat Pop (120-140 BPM)\n- 适合产品展示、日常vlog\n- 关键词搜索: upbeat, happy, energetic",
            elegant: "推荐: 优雅钢琴/弦乐\n- Classical Light (60-80 BPM)\n- 适合高端产品、房产展示\n- 关键词搜索: elegant, piano, sophisticated",
            epic: "推荐: 史诗级配乐\n- Cinematic Epic (100-120 BPM)\n- 适合旅游大片、科技发布\n- 关键词搜索: epic, cinematic, powerful",
            emotional: "推荐: 温情感人\n- Emotional Piano (70-90 BPM)\n- 适合故事类、美食探店\n- 关键词搜索: emotional, touching, warm",
            tech: "推荐: 电子科技风\n- Electronic Tech (120-140 BPM)\n- 适合数码产品、游戏设备\n- 关键词搜索: tech, electronic, modern"
        };
        
        // 尺寸说明：API要求最小像素>=921600(1280x720)，最大<=16777216(4096x4096)
        var SIZE_MAP = {
            "1:1": { standard: "1024x1024", hd: "2048x2048" },
            "16:9": { standard: "1280x720", hd: "1920x1080" },
            "9:16": { standard: "720x1280", hd: "1080x1920" },
            "4:3": { standard: "1280x960", hd: "1920x1440" },
            "3:4": { standard: "960x1280", hd: "1440x1920" }
        };
        
        var projectData = { copywriting: "", scenes: [], storyboard: [], srt: "" };
        var currentScene = "product";
        var currentStyle = "simple";
        var refImageBase64 = null;
        var draftTimer = null;
        
        function selectScene(scene) {
            currentScene = scene;
            document.querySelectorAll("#sceneTabs .tab").forEach(t => t.classList.remove("active"));
            document.querySelector("#sceneTabs .tab[data-scene='"+scene+"']").classList.add("active");
            var cfg = SCENE_CONFIG[scene];
            document.getElementById("inputLabel").textContent = cfg.label;
            document.getElementById("mainInput").placeholder = cfg.placeholder;
            document.getElementById("detailLabel").textContent = cfg.detailLabel;
            document.getElementById("detailInput").placeholder = cfg.detailPlaceholder;
            if (scene === "douyin" || scene === "fashion") {
                document.getElementById("aspectRatio").value = "9:16";
            } else {
                document.getElementById("aspectRatio").value = "16:9";
            }
            updateCostEstimate();
        }
        
        function selectStyle(style) {
            currentStyle = style;
            document.querySelectorAll("#styleGrid .option-item").forEach(t => t.classList.remove("selected"));
            document.querySelector("#styleGrid .option-item[data-style='"+style+"']").classList.add("selected");
        }
        
        function handleImageUpload(e) {
            var file = e.target.files[0];
            if (!file) return;
            var reader = new FileReader();
            reader.onload = function(ev) {
                refImageBase64 = ev.target.result;
                document.getElementById("uploadPreview").src = refImageBase64;
                document.getElementById("uploadPreview").classList.remove("hidden");
                document.getElementById("uploadText").innerHTML = "✅ 已上传参考图<br><small>点击更换</small>";
                document.getElementById("uploadArea").classList.add("has-image");
            };
            reader.readAsDataURL(file);
        }
        
        function updateCostEstimate() {
            var n = parseInt(document.getElementById("numScenes").value);
            var img = parseInt(document.getElementById("imagesPerScene").value);
            var dur = parseInt(document.getElementById("videoDuration").value);
            var skip = document.getElementById("skipVideo").checked;
            var imgCost = n * img * 0.20;
            var vidCost = skip ? 0 : n * (dur === 5 ? 0.50 : 1.00);
            var total = imgCost + vidCost + 0.01;
            document.getElementById("costDetail").innerHTML = 
                "文案+分镜：约 ¥0.01<br>" +
                "图片：" + n + "镜头 × " + img + "张 = ¥" + imgCost.toFixed(2) + "<br>" +
                (skip ? "视频：跳过" : "视频：" + n + "个 × " + dur + "秒 = ¥" + vidCost.toFixed(2));
            document.getElementById("costTotal").textContent = "预计总费用：约 ¥" + total.toFixed(2);
        }
        
        function hideAll() {
            ["step1","step2","step3","step4","step5","resultCard","historyCard","draftsCard"].forEach(id => {
                document.getElementById(id).classList.add("hidden");
            });
        }
        function showStep(n) { hideAll(); document.getElementById("step" + n).classList.remove("hidden"); }
        function goBack(n) { showStep(n); }
        
        function getImageSize() {
            var r = document.getElementById("aspectRatio").value;
            var q = document.getElementById("imageQuality").value;
            return SIZE_MAP[r][q];
        }
        
        function getSettings() {
            return {
                scene: currentScene, style: currentStyle,
                name: document.getElementById("mainInput").value.trim(),
                detail: document.getElementById("detailInput").value.trim(),
                imageSize: getImageSize(),
                numScenes: parseInt(document.getElementById("numScenes").value),
                videoDuration: parseInt(document.getElementById("videoDuration").value),
                videoResolution: document.getElementById("videoResolution").value,
                imagesPerScene: parseInt(document.getElementById("imagesPerScene").value),
                skipVideo: document.getElementById("skipVideo").checked,
                generateSrt: document.getElementById("generateSrt").checked,
                bgMusic: document.getElementById("bgMusic").value,
                refImage: refImageBase64
            };
        }
        
        function openModal(url) { document.getElementById("modalImage").src = url; document.getElementById("imageModal").classList.add("show"); }
        function closeModal() { document.getElementById("imageModal").classList.remove("show"); }
        
        // 草稿功能
        function saveDraftDebounce() {
            clearTimeout(draftTimer);
            draftTimer = setTimeout(saveDraftAuto, 2000);
        }
        function saveDraftAuto() {
            var data = { ...getSettings(), copywriting: document.getElementById("copyText")?.value || "", step: 1, time: new Date().toLocaleString() };
            localStorage.setItem("currentDraft", JSON.stringify(data));
        }
        function saveDraftManual() {
            var drafts = JSON.parse(localStorage.getItem("drafts") || "[]");
            var data = { id: Date.now(), ...getSettings(), copywriting: projectData.copywriting, storyboard: projectData.storyboard, time: new Date().toLocaleString() };
            drafts.unshift(data);
            if (drafts.length > 10) drafts.pop();
            localStorage.setItem("drafts", JSON.stringify(drafts));
            alert("草稿已保存！");
        }
        function checkDraft() {
            var d = localStorage.getItem("currentDraft");
            if (d) {
                var data = JSON.parse(d);
                if (data.name) document.getElementById("draftNotice").classList.remove("hidden");
            }
        }
        function loadDraft() {
            var d = JSON.parse(localStorage.getItem("currentDraft") || "{}");
            if (d.name) document.getElementById("mainInput").value = d.name;
            if (d.detail) document.getElementById("detailInput").value = d.detail;
            if (d.scene) selectScene(d.scene);
            if (d.style) selectStyle(d.style);
            document.getElementById("draftNotice").classList.add("hidden");
        }
        function clearDraft() { localStorage.removeItem("currentDraft"); document.getElementById("draftNotice").classList.add("hidden"); }
        function showDrafts() {
            hideAll(); document.getElementById("draftsCard").classList.remove("hidden");
            var drafts = JSON.parse(localStorage.getItem("drafts") || "[]");
            if (drafts.length === 0) {
                document.getElementById("draftsList").innerHTML = "<p style='color:rgba(255,255,255,0.5);text-align:center;'>暂无草稿</p>";
            } else {
                var html = "";
                drafts.forEach((d, i) => {
                    html += "<div style='padding:15px;background:rgba(255,255,255,0.05);border-radius:10px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;'><div><strong>" + d.name + "</strong><br><small style='color:rgba(255,255,255,0.5);'>" + d.time + "</small></div><div><button class='btn btn-primary btn-small' onclick='loadDraftById("+d.id+")'>加载</button> <button class='btn btn-secondary btn-small' onclick='deleteDraft("+d.id+")'>删除</button></div></div>";
                });
                document.getElementById("draftsList").innerHTML = html;
            }
        }
        function hideDrafts() { showStep(1); }
        function loadDraftById(id) {
            var drafts = JSON.parse(localStorage.getItem("drafts") || "[]");
            var d = drafts.find(x => x.id === id);
            if (d) {
                document.getElementById("mainInput").value = d.name || "";
                document.getElementById("detailInput").value = d.detail || "";
                if (d.scene) selectScene(d.scene);
                if (d.style) selectStyle(d.style);
                projectData.copywriting = d.copywriting || "";
                projectData.storyboard = d.storyboard || [];
                showStep(1);
            }
        }
        function deleteDraft(id) {
            var drafts = JSON.parse(localStorage.getItem("drafts") || "[]");
            drafts = drafts.filter(x => x.id !== id);
            localStorage.setItem("drafts", JSON.stringify(drafts));
            showDrafts();
        }
        
        // 生成SRT字幕
        function generateSRT(copy, numScenes, duration) {
            var lines = copy.split(/\n+/).filter(l => l.trim());
            var srt = "";
            var perScene = duration;
            for (var i = 0; i < Math.min(lines.length, numScenes); i++) {
                var start = i * perScene;
                var end = (i + 1) * perScene;
                srt += (i + 1) + "\n";
                srt += formatTime(start) + " --> " + formatTime(end) + "\n";
                srt += lines[i].trim() + "\n\n";
            }
            return srt;
        }
        function formatTime(sec) {
            var h = Math.floor(sec / 3600);
            var m = Math.floor((sec % 3600) / 60);
            var s = sec % 60;
            return pad(h) + ":" + pad(m) + ":" + pad(s) + ",000";
        }
        function pad(n) { return n < 10 ? "0" + n : n; }
        function downloadSrt() {
            var blob = new Blob([projectData.srt], { type: "text/plain" });
            var a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = "subtitles.srt";
            a.click();
        }
        
        // API调用
        async function stepGenerate() {
            var s = getSettings();
            if (!s.name) { alert("请输入主题/名称"); return; }
            projectData = { ...projectData, ...s };
            showStep(5);
            document.getElementById("statusList").innerHTML = "<div class='status-item'><div class='status-icon loading'>🔄</div><div>生成文案...</div></div>";
            document.getElementById("progressText").textContent = "生成文案中...";
            try {
                var sceneCfg = SCENE_CONFIG[s.scene];
                var styleCfg = STYLE_CONFIG[s.style];
                var resp = await fetch("/api/generate-copy", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({ product_name: s.name, product_features: s.detail, scene_type: s.scene, style: styleCfg.name, copy_prompt: sceneCfg.copyPrompt })
                });
                var data = await resp.json();
                if (!data.success) throw new Error(data.error || "文案生成失败");
                projectData.copywriting = data.content;
                document.getElementById("copyText").value = data.content;
                showStep(2);
            } catch(e) { alert("失败: " + e.message); showStep(1); }
        }
        
        async function regenerateCopy() {
            showStep(5);
            document.getElementById("statusList").innerHTML = "<div class='status-item'><div class='status-icon loading'>🔄</div><div>重新生成...</div></div>";
            try {
                var sceneCfg = SCENE_CONFIG[projectData.scene];
                var styleCfg = STYLE_CONFIG[projectData.style];
                var resp = await fetch("/api/generate-copy", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({ product_name: projectData.name, product_features: projectData.detail, scene_type: projectData.scene, style: styleCfg.name, copy_prompt: sceneCfg.copyPrompt })
                });
                var data = await resp.json();
                if (!data.success) throw new Error(data.error);
                projectData.copywriting = data.content;
                document.getElementById("copyText").value = data.content;
                showStep(2);
            } catch(e) { alert("失败: " + e.message); showStep(2); }
        }
        
        async function goToStep3() {
            projectData.copywriting = document.getElementById("copyText").value.trim();
            showStep(5);
            document.getElementById("statusList").innerHTML = "<div class='status-item'><div class='status-icon loading'>🔄</div><div>生成分镜...</div></div>";
            try {
                var styleCfg = STYLE_CONFIG[projectData.style];
                var resp = await fetch("/api/generate-storyboard", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({ product_name: projectData.name, copywriting: projectData.copywriting, scene_type: projectData.scene, style: styleCfg.name, image_style: styleCfg.image, video_style: styleCfg.video, num_scenes: projectData.numScenes })
                });
                var data = await resp.json();
                if (!data.success) throw new Error(data.error || "分镜失败");
                projectData.storyboard = data.storyboard.scenes || [];
                renderStoryboard();
                showStep(3);
            } catch(e) { alert("失败: " + e.message); showStep(2); }
        }
        
        function renderStoryboard() {
            var html = "";
            projectData.storyboard.forEach((s, i) => {
                html += "<div class='scene-card'><div class='scene-title'>镜头 " + (i+1) + "</div>";
                html += "<div class='form-group'><label>画面描述</label><textarea id='scene_desc_"+i+"' style='width:100%;min-height:50px;padding:10px;border-radius:8px;border:1px solid rgba(255,255,255,0.2);background:rgba(255,255,255,0.05);color:#fff;'>" + (s.description || "") + "</textarea></div>";
                html += "<div class='form-group'><label>图片提示词</label><textarea id='scene_img_"+i+"' style='width:100%;min-height:60px;padding:10px;border-radius:8px;border:1px solid rgba(255,255,255,0.2);background:rgba(255,255,255,0.05);color:#fff;font-size:13px;'>" + (s.image_prompt || "") + "</textarea></div></div>";
            });
            document.getElementById("storyboardList").innerHTML = html;
        }
        
        async function regenerateStoryboard() {
            showStep(5);
            document.getElementById("statusList").innerHTML = "<div class='status-item'><div class='status-icon loading'>🔄</div><div>重新生成...</div></div>";
            try {
                var styleCfg = STYLE_CONFIG[projectData.style];
                var resp = await fetch("/api/generate-storyboard", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({ product_name: projectData.name, copywriting: projectData.copywriting, scene_type: projectData.scene, style: styleCfg.name, image_style: styleCfg.image, video_style: styleCfg.video, num_scenes: projectData.numScenes })
                });
                var data = await resp.json();
                if (!data.success) throw new Error(data.error);
                projectData.storyboard = data.storyboard.scenes || [];
                renderStoryboard();
                showStep(3);
            } catch(e) { alert("失败: " + e.message); showStep(3); }
        }
        
        async function goToStep4() {
            projectData.storyboard.forEach((s, i) => {
                s.description = document.getElementById("scene_desc_"+i).value;
                s.image_prompt = document.getElementById("scene_img_"+i).value;
            });
            showStep(5);
            var statusHtml = "";
            projectData.storyboard.forEach((s, i) => {
                statusHtml += "<div class='status-item'><div class='status-icon pending' id='img_s"+i+"'>⏳</div><div>镜头"+(i+1)+"</div></div>";
            });
            document.getElementById("statusList").innerHTML = statusHtml;
            
            projectData.scenes = [];
            for (var i = 0; i < projectData.storyboard.length; i++) {
                document.getElementById("img_s"+i).className = "status-icon loading";
                document.getElementById("img_s"+i).textContent = "🔄";
                document.getElementById("progressText").textContent = "生成镜头"+(i+1)+"图片...";
                document.getElementById("progressBar").style.width = ((i+1)/projectData.storyboard.length*100)+"%";
                try {
                    var resp = await fetch("/api/generate-images", {
                        method: "POST", headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({ prompt: projectData.storyboard[i].image_prompt, count: projectData.imagesPerScene, size: projectData.imageSize })
                    });
                    var data = await resp.json();
                    var images = [];
                    if (data.images) data.images.forEach(img => { if(img.url) images.push(img.url); });
                    projectData.scenes.push({ index: i, images: images, selected: images[0] || null, video_prompt: projectData.storyboard[i].video_prompt || "smooth movement", video_url: null });
                    document.getElementById("img_s"+i).className = "status-icon done";
                    document.getElementById("img_s"+i).textContent = "✅";
                } catch(e) {
                    projectData.scenes.push({ index: i, images: [], selected: null, video_prompt: "", video_url: null });
                    document.getElementById("img_s"+i).textContent = "❌";
                }
            }
            renderImageSelect();
            showStep(4);
            document.getElementById("toStep5Btn").textContent = projectData.skipVideo ? "完成 ✓" : "生成视频 →";
        }
        
        function renderImageSelect() {
            var html = "";
            projectData.scenes.forEach((s, i) => {
                html += "<div class='scene-card'><div class='scene-title'><span>镜头 " + (i+1) + "</span><button class='btn btn-secondary btn-small' onclick='regenerateSceneImage("+i+")'>🔄 重新生成</button></div><div class='scene-images'>";
                if (s.images.length === 0) {
                    html += "<p style='color:rgba(255,255,255,0.5);'>生成失败</p>";
                } else {
                    s.images.forEach((url, j) => {
                        var sel = (url === s.selected) ? " selected" : "";
                        html += "<img src='"+url+"' class='scene-image"+sel+"' onclick='selectImage("+i+","+j+")' ondblclick='openModal(\""+url+"\")' />";
                    });
                }
                html += "</div></div>";
            });
            document.getElementById("imageSelectList").innerHTML = html;
        }
        
        function selectImage(si, ii) { projectData.scenes[si].selected = projectData.scenes[si].images[ii]; renderImageSelect(); }
        
        async function regenerateSceneImage(si) {
            var btn = event.target; btn.disabled = true; btn.textContent = "...";
            try {
                var resp = await fetch("/api/generate-images", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({ prompt: projectData.storyboard[si].image_prompt, count: projectData.imagesPerScene, size: projectData.imageSize })
                });
                var data = await resp.json();
                var images = [];
                if (data.images) data.images.forEach(img => { if(img.url) images.push(img.url); });
                projectData.scenes[si].images = images;
                projectData.scenes[si].selected = images[0] || null;
                renderImageSelect();
            } catch(e) { alert("失败"); btn.disabled = false; btn.textContent = "🔄"; }
        }
        
        async function goToStep5() {
            if (projectData.skipVideo) { showResult(); saveHistory(); return; }
            showStep(5);
            var statusHtml = "";
            projectData.scenes.forEach((s, i) => {
                statusHtml += "<div class='status-item'><div class='status-icon pending' id='vid_s"+i+"'>⏳</div><div>镜头"+(i+1)+" 视频</div></div>";
            });
            document.getElementById("statusList").innerHTML = statusHtml;
            
            for (var i = 0; i < projectData.scenes.length; i++) {
                var s = projectData.scenes[i];
                if (!s.selected) continue;
                document.getElementById("vid_s"+i).className = "status-icon loading";
                document.getElementById("vid_s"+i).textContent = "🔄";
                document.getElementById("progressText").textContent = "生成镜头"+(i+1)+"视频（约1-2分钟）...";
                document.getElementById("progressBar").style.width = ((i+1)/projectData.scenes.length*100)+"%";
                try {
                    var resp = await fetch("/api/generate-video", {
                        method: "POST", headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({ image_url: s.selected, prompt: s.video_prompt, duration: projectData.videoDuration, resolution: projectData.videoResolution })
                    });
                    var data = await resp.json();
                    if (data.success && data.video_url) {
                        s.video_url = data.video_url;
                        document.getElementById("vid_s"+i).className = "status-icon done";
                        document.getElementById("vid_s"+i).textContent = "✅";
                    } else { document.getElementById("vid_s"+i).textContent = "❌"; }
                } catch(e) { document.getElementById("vid_s"+i).textContent = "❌"; }
            }
            document.getElementById("progressText").textContent = "完成！";
            setTimeout(showResult, 500);
            saveHistory();
        }
        
        async function oneClickGenerate() {
            var s = getSettings();
            if (!s.name) { alert("请输入主题/名称"); return; }
            projectData = { ...projectData, ...s };
            showStep(5);
            var statusHtml = "<div class='status-item'><div class='status-icon loading' id='as1'>🔄</div><div>文案</div></div>" +
                "<div class='status-item'><div class='status-icon pending' id='as2'>⏳</div><div>分镜</div></div>" +
                "<div class='status-item'><div class='status-icon pending' id='as3'>⏳</div><div>图片</div></div>";
            if (!s.skipVideo) statusHtml += "<div class='status-item'><div class='status-icon pending' id='as4'>⏳</div><div>视频</div></div>";
            document.getElementById("statusList").innerHTML = statusHtml;
            
            try {
                var sceneCfg = SCENE_CONFIG[s.scene];
                var styleCfg = STYLE_CONFIG[s.style];
                
                document.getElementById("progressText").textContent = "生成文案...";
                document.getElementById("progressBar").style.width = "10%";
                var resp = await fetch("/api/generate-copy", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({product_name:s.name, product_features:s.detail, scene_type:s.scene, style:styleCfg.name, copy_prompt:sceneCfg.copyPrompt})});
                var data = await resp.json();
                if (!data.success) throw new Error("文案失败");
                projectData.copywriting = data.content;
                document.getElementById("as1").className = "status-icon done"; document.getElementById("as1").textContent = "✅";
                document.getElementById("as2").className = "status-icon loading"; document.getElementById("as2").textContent = "🔄";
                
                document.getElementById("progressText").textContent = "生成分镜...";
                document.getElementById("progressBar").style.width = "20%";
                resp = await fetch("/api/generate-storyboard", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({product_name:s.name, copywriting:projectData.copywriting, scene_type:s.scene, style:styleCfg.name, image_style:styleCfg.image, video_style:styleCfg.video, num_scenes:s.numScenes})});
                data = await resp.json();
                if (!data.success) throw new Error("分镜失败");
                projectData.storyboard = data.storyboard.scenes || [];
                document.getElementById("as2").className = "status-icon done"; document.getElementById("as2").textContent = "✅";
                document.getElementById("as3").className = "status-icon loading"; document.getElementById("as3").textContent = "🔄";
                
                projectData.scenes = [];
                for (var i = 0; i < projectData.storyboard.length; i++) {
                    document.getElementById("progressText").textContent = "生成图片 "+(i+1)+"/"+projectData.storyboard.length;
                    document.getElementById("progressBar").style.width = (20 + (i/projectData.storyboard.length)*30)+"%";
                    resp = await fetch("/api/generate-images", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({prompt:projectData.storyboard[i].image_prompt, count:1, size:s.imageSize})});
                    data = await resp.json();
                    var imgUrl = (data.images && data.images[0]) ? data.images[0].url : null;
                    projectData.scenes.push({selected:imgUrl, video_prompt:projectData.storyboard[i].video_prompt||"smooth movement", video_url:null});
                }
                document.getElementById("as3").className = "status-icon done"; document.getElementById("as3").textContent = "✅";
                
                if (!s.skipVideo) {
                    document.getElementById("as4").className = "status-icon loading"; document.getElementById("as4").textContent = "🔄";
                    for (var i = 0; i < projectData.scenes.length; i++) {
                        var sc = projectData.scenes[i];
                        if (!sc.selected) continue;
                        document.getElementById("progressText").textContent = "生成视频 "+(i+1)+"/"+projectData.scenes.length;
                        document.getElementById("progressBar").style.width = (50 + (i/projectData.scenes.length)*45)+"%";
                        resp = await fetch("/api/generate-video", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({image_url:sc.selected, prompt:sc.video_prompt, duration:s.videoDuration, resolution:s.videoResolution})});
                        data = await resp.json();
                        if (data.success && data.video_url) sc.video_url = data.video_url;
                    }
                    document.getElementById("as4").className = "status-icon done"; document.getElementById("as4").textContent = "✅";
                }
                
                document.getElementById("progressBar").style.width = "100%";
                document.getElementById("progressText").textContent = "完成！";
                setTimeout(showResult, 500);
                saveHistory();
            } catch(e) {
                document.getElementById("progressText").textContent = "失败: " + e.message;
                alert("生成失败: " + e.message);
            }
        }
        
        function showResult() {
            hideAll();
            document.getElementById("resultCard").classList.remove("hidden");
            document.getElementById("resultCopy").textContent = projectData.copywriting;
            document.getElementById("resultLabel").textContent = projectData.skipVideo ? "🖼️ 图片" : "🎬 视频片段";
            
            // SRT
            if (projectData.generateSrt) {
                projectData.srt = generateSRT(projectData.copywriting, projectData.numScenes, projectData.videoDuration);
                document.getElementById("srtContent").textContent = projectData.srt;
                document.getElementById("srtSection").classList.remove("hidden");
            } else {
                document.getElementById("srtSection").classList.add("hidden");
            }
            
            // 音乐推荐
            if (projectData.bgMusic && projectData.bgMusic !== "none") {
                document.getElementById("musicRecommend").textContent = MUSIC_RECOMMEND[projectData.bgMusic] || "";
                document.getElementById("musicSection").classList.remove("hidden");
            } else {
                document.getElementById("musicSection").classList.add("hidden");
            }
            
            var html = "";
            projectData.scenes.forEach((s, i) => {
                if (s.video_url) {
                    html += "<div class='result-video-item'><video src='"+s.video_url+"' controls playsinline></video><div class='video-info'><span>镜头"+(i+1)+"</span><a href='"+s.video_url+"' target='_blank' class='btn btn-secondary btn-small'>下载</a></div></div>";
                } else if (s.selected) {
                    html += "<div class='result-video-item'><img src='"+s.selected+"' onclick='openModal(\""+s.selected+"\")' style='cursor:pointer;'><div class='video-info'><span>镜头"+(i+1)+"</span><a href='"+s.selected+"' target='_blank' class='btn btn-secondary btn-small'>下载</a></div></div>";
                }
            });
            document.getElementById("resultVideos").innerHTML = html || "<p style='color:rgba(255,255,255,0.5);'>没有内容</p>";
        }
        
        function copyToClipboard() { navigator.clipboard.writeText(projectData.copywriting).then(() => alert("已复制！")); }
        function downloadAll() {
            projectData.scenes.forEach((s, i) => {
                var url = s.video_url || s.selected;
                if (url) { var a = document.createElement("a"); a.href = url; a.download = "镜头"+(i+1)+(s.video_url?".mp4":".jpg"); a.click(); }
            });
            if (projectData.srt) downloadSrt();
        }
        async function sendFeishu() {
            var items = [];
            projectData.scenes.forEach((s, i) => { var url = s.video_url || s.selected; if (url) items.push("镜头"+(i+1)+": "+url); });
            var msg = "🎉 生成完成！\n\n📝 文案：\n" + projectData.copywriting + "\n\n🎬 内容：\n" + items.join("\n");
            await fetch("/api/notify", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({message:msg})});
            alert("已发送！");
        }
        function restart() { projectData = {copywriting:"", scenes:[], storyboard:[], srt:""}; refImageBase64 = null; document.getElementById("uploadPreview").classList.add("hidden"); document.getElementById("uploadText").innerHTML = "📤 点击上传参考图片<br><small>上传后AI会参考这张图的风格</small>"; document.getElementById("uploadArea").classList.remove("has-image"); showStep(1); updateCostEstimate(); }
        
        function saveHistory() {
            var h = JSON.parse(localStorage.getItem("videoHistory") || "[]");
            h.unshift({id:Date.now(), name:projectData.name, scene:projectData.scene, copywriting:projectData.copywriting, scenes:projectData.scenes, srt:projectData.srt, skipVideo:projectData.skipVideo, time:new Date().toLocaleString()});
            if (h.length > 20) h.pop();
            localStorage.setItem("videoHistory", JSON.stringify(h));
            localStorage.removeItem("currentDraft");
        }
        function showHistory() {
            hideAll(); document.getElementById("historyCard").classList.remove("hidden");
            var h = JSON.parse(localStorage.getItem("videoHistory") || "[]");
            if (h.length === 0) {
                document.getElementById("historyList").innerHTML = "<p style='color:rgba(255,255,255,0.5);text-align:center;'>暂无记录</p>";
            } else {
                var html = "";
                h.forEach(item => {
                    var icon = {product:"🖥️",douyin:"📱",food:"🍜",travel:"✈️",fashion:"👗",realestate:"🏠",custom:"✨"}[item.scene]||"📹";
                    html += "<div style='padding:15px;background:rgba(255,255,255,0.05);border-radius:10px;margin-bottom:10px;cursor:pointer;' onclick='loadHistory("+item.id+")'><strong>"+icon+" "+item.name+"</strong><br><small style='color:rgba(255,255,255,0.5);'>"+item.time+"</small></div>";
                });
                document.getElementById("historyList").innerHTML = html;
            }
        }
        function hideHistory() { showStep(1); }
        function loadHistory(id) {
            var h = JSON.parse(localStorage.getItem("videoHistory") || "[]");
            var item = h.find(x => x.id === id);
            if (item) { projectData.copywriting = item.copywriting; projectData.scenes = item.scenes; projectData.srt = item.srt || ""; projectData.skipVideo = item.skipVideo; showResult(); }
        }
        
        // 初始化
        document.getElementById("skipVideo").addEventListener("change", updateCostEstimate);
        updateCostEstimate();
        checkDraft();
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    return Response(HTML_PAGE, mimetype='text/html')

@app.route('/health')
def health():
    return jsonify({"status": "ok", "version": "v6.0"})

@app.route('/api/generate-copy', methods=['POST'])
def api_generate_copy():
    data = request.get_json() or {}
    name = data.get("product_name", "").strip()
    features = data.get("product_features", "").strip()
    style = data.get("style", "简约科技")
    copy_prompt = data.get("copy_prompt", "文案专家")
    if not name: return jsonify({"success": False, "error": "请输入主题"}), 400
    system = f"你是{copy_prompt}。风格：{style}。生成30秒短视频文案，80-150字，分4-5段。"
    user = f"主题：{name}\n详情：{features}\n\n请生成文案："
    return jsonify(chat_completion(system, user))

@app.route('/api/generate-storyboard', methods=['POST'])
def api_generate_storyboard():
    data = request.get_json() or {}
    name = data.get("product_name", "").strip()
    copy = data.get("copywriting", "").strip()
    style = data.get("style", "简约科技")
    image_style = data.get("image_style", "8K UHD")
    video_style = data.get("video_style", "smooth movement")
    num = data.get("num_scenes", 4)
    if not name or not copy: return jsonify({"success": False, "error": "缺少参数"}), 400
    system = f'分镜师。生成{num}个分镜。只输出JSON：{{"scenes":[{{"scene_id":1,"description":"中文描述","image_prompt":"English prompt, {image_style}","video_prompt":"English prompt, {video_style}"}}]}}'
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
    count = min(data.get("count", 1), 3)
    size = data.get("size", "1024x1024")
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
    resolution = data.get("resolution", "1080p")
    if not img: return jsonify({"success": False, "error": "请提供图片URL"}), 400
    task = create_video_task(img, prompt, duration, resolution)
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
    print(f"🚀 即梦AI v6.0 启动 - 端口: {port}")
    app.run(host="0.0.0.0", port=port)
