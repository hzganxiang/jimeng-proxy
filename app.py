"""
即梦AI Flask代理服务 - 专业版 v4.1
=====================================
功能：
1. 网页操作：分步骤生成（文案→分镜→图片选择→视频）
2. 飞书群@触发：快速生成
3. 历史记录
"""

from flask import Flask, request, jsonify
import requests
import json
import os
import re
import time
import threading
import uuid
from datetime import datetime

app = Flask(__name__)

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
    return '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI视频生成器</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            color: #fff;
        }
        
        .container {
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
        }
        
        /* 头部 */
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 20px 0;
            border-bottom: 1px solid rgba(255,255,255,0.1);
            margin-bottom: 30px;
        }
        
        .header h1 {
            font-size: 24px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .header-btn {
            background: rgba(255,255,255,0.1);
            border: 1px solid rgba(255,255,255,0.2);
            color: #fff;
            padding: 10px 20px;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .header-btn:hover {
            background: rgba(255,255,255,0.2);
        }
        
        /* 步骤指示器 */
        .steps-indicator {
            display: flex;
            justify-content: center;
            gap: 10px;
            margin-bottom: 30px;
        }
        
        .step-dot {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: rgba(255,255,255,0.1);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            transition: all 0.3s;
        }
        
        .step-dot.active {
            background: #4CAF50;
        }
        
        .step-dot.completed {
            background: #2196F3;
        }
        
        .step-line {
            width: 50px;
            height: 2px;
            background: rgba(255,255,255,0.2);
            align-self: center;
        }
        
        /* 卡片 */
        .card {
            background: rgba(255,255,255,0.05);
            border-radius: 16px;
            padding: 30px;
            margin-bottom: 20px;
            border: 1px solid rgba(255,255,255,0.1);
        }
        
        .card-title {
            font-size: 18px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        /* 表单 */
        .form-group {
            margin-bottom: 20px;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 8px;
            color: rgba(255,255,255,0.8);
            font-size: 14px;
        }
        
        .form-group input,
        .form-group textarea,
        .form-group select {
            width: 100%;
            padding: 12px 16px;
            border-radius: 8px;
            border: 1px solid rgba(255,255,255,0.2);
            background: rgba(255,255,255,0.05);
            color: #fff;
            font-size: 16px;
            transition: all 0.3s;
        }
        
        .form-group input:focus,
        .form-group textarea:focus,
        .form-group select:focus {
            outline: none;
            border-color: #4CAF50;
            background: rgba(255,255,255,0.1);
        }
        
        .form-group textarea {
            min-height: 100px;
            resize: vertical;
        }
        
        .form-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }
        
        /* 按钮 */
        .btn {
            padding: 14px 28px;
            border-radius: 8px;
            border: none;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }
        
        .btn-primary {
            background: linear-gradient(135deg, #4CAF50, #45a049);
            color: #fff;
        }
        
        .btn-primary:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 15px rgba(76, 175, 80, 0.4);
        }
        
        .btn-secondary {
            background: rgba(255,255,255,0.1);
            color: #fff;
            border: 1px solid rgba(255,255,255,0.2);
        }
        
        .btn-secondary:hover {
            background: rgba(255,255,255,0.2);
        }
        
        .btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
        }
        
        .btn-group {
            display: flex;
            gap: 15px;
            margin-top: 20px;
        }
        
        /* 文案显示/编辑区 */
        .content-box {
            background: rgba(0,0,0,0.2);
            border-radius: 8px;
            padding: 20px;
            margin: 15px 0;
            white-space: pre-wrap;
            line-height: 1.8;
        }
        
        .editable-content {
            width: 100%;
            min-height: 150px;
            background: rgba(0,0,0,0.2);
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 8px;
            padding: 15px;
            color: #fff;
            font-size: 15px;
            line-height: 1.8;
            resize: vertical;
        }
        
        /* 分镜卡片 */
        .scene-card {
            background: rgba(0,0,0,0.2);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            border: 1px solid rgba(255,255,255,0.1);
        }
        
        .scene-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        
        .scene-number {
            background: #4CAF50;
            color: #fff;
            padding: 5px 15px;
            border-radius: 20px;
            font-weight: bold;
        }
        
        .scene-nav {
            display: flex;
            gap: 10px;
        }
        
        .scene-nav button {
            background: rgba(255,255,255,0.1);
            border: none;
            color: #fff;
            width: 36px;
            height: 36px;
            border-radius: 50%;
            cursor: pointer;
            font-size: 18px;
        }
        
        .scene-nav button:hover {
            background: rgba(255,255,255,0.2);
        }
        
        .prompt-section {
            margin-top: 15px;
        }
        
        .prompt-label {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 8px;
            font-size: 14px;
            color: rgba(255,255,255,0.7);
        }
        
        /* 图片选择网格 */
        .images-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 15px;
            margin: 20px 0;
        }
        
        .image-option {
            position: relative;
            aspect-ratio: 1;
            border-radius: 12px;
            overflow: hidden;
            cursor: pointer;
            border: 3px solid transparent;
            transition: all 0.3s;
        }
        
        .image-option:hover {
            transform: scale(1.02);
        }
        
        .image-option.selected {
            border-color: #4CAF50;
        }
        
        .image-option img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        
        .image-option .check-mark {
            position: absolute;
            top: 10px;
            right: 10px;
            width: 30px;
            height: 30px;
            background: #4CAF50;
            border-radius: 50%;
            display: none;
            align-items: center;
            justify-content: center;
            font-size: 18px;
        }
        
        .image-option.selected .check-mark {
            display: flex;
        }
        
        .image-placeholder {
            width: 100%;
            height: 100%;
            background: rgba(255,255,255,0.05);
            display: flex;
            align-items: center;
            justify-content: center;
            color: rgba(255,255,255,0.3);
        }
        
        /* 视频预览 */
        .video-preview {
            width: 100%;
            max-width: 400px;
            border-radius: 12px;
            margin: 15px 0;
        }
        
        /* 进度条 */
        .progress-bar {
            width: 100%;
            height: 8px;
            background: rgba(255,255,255,0.1);
            border-radius: 4px;
            overflow: hidden;
            margin: 15px 0;
        }
        
        .progress-bar-fill {
            height: 100%;
            background: linear-gradient(90deg, #4CAF50, #8BC34A);
            transition: width 0.3s;
        }
        
        /* 状态列表 */
        .status-list {
            margin: 20px 0;
        }
        
        .status-item {
            display: flex;
            align-items: center;
            gap: 15px;
            padding: 12px 0;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        
        .status-icon {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
        }
        
        .status-icon.pending {
            background: rgba(255,255,255,0.1);
        }
        
        .status-icon.loading {
            background: #FF9800;
            animation: pulse 1s infinite;
        }
        
        .status-icon.done {
            background: #4CAF50;
        }
        
        .status-icon.error {
            background: #f44336;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        
        /* 历史记录列表 */
        .history-list {
            margin-top: 20px;
        }
        
        .history-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 15px;
            background: rgba(255,255,255,0.05);
            border-radius: 10px;
            margin-bottom: 10px;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .history-item:hover {
            background: rgba(255,255,255,0.1);
        }
        
        .history-info h4 {
            margin-bottom: 5px;
        }
        
        .history-info span {
            font-size: 13px;
            color: rgba(255,255,255,0.5);
        }
        
        .history-status {
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 13px;
        }
        
        .history-status.completed {
            background: rgba(76, 175, 80, 0.2);
            color: #4CAF50;
        }
        
        .history-status.in-progress {
            background: rgba(255, 152, 0, 0.2);
            color: #FF9800;
        }
        
        /* 完成页面 */
        .result-videos {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 20px;
            margin: 20px 0;
        }
        
        .result-video-item {
            background: rgba(0,0,0,0.2);
            border-radius: 12px;
            overflow: hidden;
        }
        
        .result-video-item video {
            width: 100%;
            display: block;
        }
        
        .result-video-item .video-info {
            padding: 10px 15px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        /* 加载动画 */
        .loading-spinner {
            width: 40px;
            height: 40px;
            border: 3px solid rgba(255,255,255,0.1);
            border-top-color: #4CAF50;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin: 20px auto;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        /* 隐藏 */
        .hidden {
            display: none !important;
        }
        
        /* 响应式 */
        @media (max-width: 600px) {
            .form-row {
                grid-template-columns: 1fr;
            }
            
            .images-grid {
                grid-template-columns: repeat(2, 1fr);
            }
            
            .result-videos {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- 头部 -->
        <div class="header">
            <h1>🎬 AI视频生成器</h1>
            <button class="header-btn" onclick="showHistory()">📋 历史记录</button>
        </div>
        
        <!-- 步骤指示器 -->
        <div class="steps-indicator" id="stepsIndicator">
            <div class="step-dot active" data-step="1">1</div>
            <div class="step-line"></div>
            <div class="step-dot" data-step="2">2</div>
            <div class="step-line"></div>
            <div class="step-dot" data-step="3">3</div>
            <div class="step-line"></div>
            <div class="step-dot" data-step="4">4</div>
            <div class="step-line"></div>
            <div class="step-dot" data-step="5">5</div>
        </div>
        
        <!-- Step 1: 输入产品信息 -->
        <div class="card step-content" id="step1">
            <div class="card-title">📝 Step 1: 输入产品信息</div>
            
            <div class="form-group">
                <label>产品名称 *</label>
                <input type="text" id="productName" placeholder="例如：联想小新Pro16笔记本">
            </div>
            
            <div class="form-group">
                <label>产品卖点/特点</label>
                <textarea id="productFeatures" placeholder="例如：16英寸2.5K超清屏，酷睿i7处理器，32G大内存，轻薄便携，长续航"></textarea>
            </div>
            
            <div class="form-row">
                <div class="form-group">
                    <label>风格</label>
                    <select id="styleSelect">
                        <option value="科技简约">科技简约</option>
                        <option value="年轻时尚">年轻时尚</option>
                        <option value="商务专业">商务专业</option>
                        <option value="电竞酷炫">电竞酷炫</option>
                    </select>
                </div>
                
                <div class="form-group">
                    <label>镜头数量</label>
                    <select id="numScenes">
                        <option value="3">3 个镜头</option>
                        <option value="4" selected>4 个镜头</option>
                        <option value="5">5 个镜头</option>
                    </select>
                </div>
            </div>
            
            <div class="btn-group">
                <button class="btn btn-primary" onclick="generateCopy()">
                    分步生成 →
                </button>
                <button class="btn btn-primary" onclick="oneClickGenerate()" style="background: linear-gradient(135deg, #FF6B6B, #FF8E53);">
                    🚀 一键全自动
                </button>
            </div>
        </div>
        
        <!-- Step 2: 确认文案 -->
        <div class="card step-content hidden" id="step2">
            <div class="card-title">📄 Step 2: 确认文案</div>
            
            <p style="color: rgba(255,255,255,0.6); margin-bottom: 15px;">AI生成的营销文案，你可以直接编辑修改：</p>
            
            <textarea class="editable-content" id="copywritingText"></textarea>
            
            <div class="btn-group">
                <button class="btn btn-secondary" onclick="regenerateCopy()">🔄 重新生成</button>
                <button class="btn btn-primary" onclick="generateStoryboard()">下一步：生成分镜 →</button>
            </div>
        </div>
        
        <!-- Step 3: 确认分镜 -->
        <div class="card step-content hidden" id="step3">
            <div class="card-title">🎬 Step 3: 确认分镜</div>
            
            <div id="scenesContainer"></div>
            
            <div class="btn-group">
                <button class="btn btn-secondary" onclick="regenerateStoryboard()">🔄 重新生成分镜</button>
                <button class="btn btn-primary" onclick="startGenerateImages()">下一步：生成图片 →</button>
            </div>
        </div>
        
        <!-- Step 4: 选择图片 -->
        <div class="card step-content hidden" id="step4">
            <div class="card-title">🖼️ Step 4: 选择图片</div>
            
            <div id="imagesContainer"></div>
            
            <div class="btn-group">
                <button class="btn btn-primary" onclick="startGenerateVideos()" id="generateVideosBtn" disabled>
                    下一步：生成视频 →
                </button>
            </div>
        </div>
        
        <!-- Step 5: 生成视频 -->
        <div class="card step-content hidden" id="step5">
            <div class="card-title">🎥 Step 5: 生成视频</div>
            
            <div class="status-list" id="videoStatusList"></div>
            
            <div class="progress-bar">
                <div class="progress-bar-fill" id="videoProgress" style="width: 0%"></div>
            </div>
            
            <p id="videoProgressText" style="text-align: center; color: rgba(255,255,255,0.6);">准备中...</p>
        </div>
        
        <!-- 完成页面 -->
        <div class="card step-content hidden" id="stepComplete">
            <div class="card-title">🎉 生成完成！</div>
            
            <div class="form-group">
                <label>📝 营销文案（复制到剪映做配音）</label>
                <div class="content-box" id="finalCopywriting"></div>
                <button class="btn btn-secondary" onclick="copyCopywriting()" style="margin-top: 10px;">📋 复制文案</button>
            </div>
            
            <div class="form-group">
                <label>🎬 视频片段</label>
                <div class="result-videos" id="resultVideos"></div>
            </div>
            
            <div class="btn-group">
                <button class="btn btn-secondary" onclick="downloadAll()">📥 全部下载</button>
                <button class="btn btn-secondary" onclick="notifyFeishu()">📤 发送到飞书</button>
                <button class="btn btn-primary" onclick="startNew()">✨ 新建任务</button>
            </div>
        </div>
        
        <!-- 历史记录页面 -->
        <div class="card hidden" id="historyPage">
            <div class="card-title">
                <span>📋 历史记录</span>
                <button class="btn btn-secondary" onclick="hideHistory()" style="margin-left: auto;">← 返回</button>
            </div>
            
            <div class="history-list" id="historyList">
                <p style="color: rgba(255,255,255,0.5); text-align: center; padding: 40px;">
                    暂无历史记录
                </p>
            </div>
        </div>
        
        <!-- 加载遮罩 -->
        <div class="card hidden" id="loadingCard">
            <div class="loading-spinner"></div>
            <p id="loadingText" style="text-align: center; color: rgba(255,255,255,0.6);">处理中...</p>
        </div>
    </div>
    
    <script>
        // 全局状态
        let currentStep = 1;
        let projectData = {
            productName: '',
            productFeatures: '',
            style: '科技简约',
            numScenes: 4,
            copywriting: '',
            storyboard: null,
            scenes: []
        };
        
        // API基础地址
        const API_BASE = '';
        
        // 更新步骤指示器
        function updateStepIndicator(step) {
            currentStep = step;
            document.querySelectorAll('.step-dot').forEach((dot, index) => {
                const stepNum = index + 1;
                dot.classList.remove('active', 'completed');
                if (stepNum < step) {
                    dot.classList.add('completed');
                } else if (stepNum === step) {
                    dot.classList.add('active');
                }
            });
        }
        
        // 显示指定步骤
        function showStep(step) {
            document.querySelectorAll('.step-content').forEach(el => el.classList.add('hidden'));
            document.getElementById('historyPage').classList.add('hidden');
            document.getElementById('loadingCard').classList.add('hidden');
            
            if (step === 'complete') {
                document.getElementById('stepComplete').classList.remove('hidden');
            } else {
                document.getElementById(`step${step}`).classList.remove('hidden');
            }
            
            updateStepIndicator(typeof step === 'number' ? step : 5);
        }
        
        // 显示加载
        function showLoading(text) {
            document.querySelectorAll('.step-content').forEach(el => el.classList.add('hidden'));
            document.getElementById('loadingCard').classList.remove('hidden');
            document.getElementById('loadingText').textContent = text;
        }
        
        // Step 1 → Step 2: 生成文案
        async function generateCopy() {
            projectData.productName = document.getElementById('productName').value.trim();
            projectData.productFeatures = document.getElementById('productFeatures').value.trim();
            projectData.style = document.getElementById('styleSelect').value;
            projectData.numScenes = parseInt(document.getElementById('numScenes').value);
            
            if (!projectData.productName) {
                alert('请输入产品名称');
                return;
            }
            
            showLoading('正在生成营销文案...');
            
            try {
                const resp = await fetch(`${API_BASE}/api/generate-copy`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        product_name: projectData.productName,
                        product_features: projectData.productFeatures,
                        style: projectData.style
                    })
                });
                
                const data = await resp.json();
                
                if (data.success) {
                    projectData.copywriting = data.content;
                    document.getElementById('copywritingText').value = data.content;
                    showStep(2);
                } else {
                    alert('生成失败：' + (data.error || '未知错误'));
                    showStep(1);
                }
            } catch (e) {
                alert('请求失败：' + e.message);
                showStep(1);
            }
        }
        
        // 重新生成文案
        async function regenerateCopy() {
            showLoading('重新生成文案...');
            await generateCopy();
        }
        
        // Step 2 → Step 3: 生成分镜
        async function generateStoryboard() {
            projectData.copywriting = document.getElementById('copywritingText').value.trim();
            
            if (!projectData.copywriting) {
                alert('文案不能为空');
                return;
            }
            
            showLoading('正在生成分镜脚本...');
            
            try {
                const resp = await fetch(`${API_BASE}/api/generate-storyboard`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        product_name: projectData.productName,
                        copywriting: projectData.copywriting,
                        style: projectData.style,
                        num_scenes: projectData.numScenes
                    })
                });
                
                const data = await resp.json();
                
                if (data.success) {
                    projectData.storyboard = data.storyboard;
                    renderScenes();
                    showStep(3);
                } else {
                    alert('生成失败：' + (data.error || '未知错误'));
                    showStep(2);
                }
            } catch (e) {
                alert('请求失败：' + e.message);
                showStep(2);
            }
        }
        
        // 重新生成分镜
        async function regenerateStoryboard() {
            showLoading('重新生成分镜...');
            await generateStoryboard();
        }
        
        // 渲染分镜编辑界面
        function renderScenes() {
            const container = document.getElementById('scenesContainer');
            const scenes = projectData.storyboard.scenes || [];
            
            container.innerHTML = scenes.map((scene, index) => `
                <div class="scene-card" id="sceneCard${index}">
                    <div class="scene-header">
                        <span class="scene-number">镜头 ${index + 1}</span>
                    </div>
                    
                    <div class="form-group">
                        <label>画面描述</label>
                        <textarea class="editable-content" id="sceneDesc${index}" rows="2">${scene.description || ''}</textarea>
                    </div>
                    
                    <div class="form-group">
                        <label>旁白文字</label>
                        <input type="text" id="sceneNarration${index}" value="${scene.narration || ''}">
                    </div>
                    
                    <div class="prompt-section">
                        <div class="prompt-label">📷 图片生成Prompt（英文）</div>
                        <textarea class="editable-content" id="sceneImagePrompt${index}" rows="3">${scene.image_prompt || ''}</textarea>
                    </div>
                    
                    <div class="prompt-section">
                        <div class="prompt-label">🎬 视频生成Prompt（英文）</div>
                        <textarea class="editable-content" id="sceneVideoPrompt${index}" rows="2">${scene.video_prompt || ''}</textarea>
                    </div>
                </div>
            `).join('');
        }
        
        // Step 3 → Step 4: 生成图片
        async function startGenerateImages() {
            // 保存编辑后的分镜数据
            const scenes = projectData.storyboard.scenes;
            scenes.forEach((scene, index) => {
                scene.description = document.getElementById(`sceneDesc${index}`).value;
                scene.narration = document.getElementById(`sceneNarration${index}`).value;
                scene.image_prompt = document.getElementById(`sceneImagePrompt${index}`).value;
                scene.video_prompt = document.getElementById(`sceneVideoPrompt${index}`).value;
            });
            
            // 初始化scenes数据
            projectData.scenes = scenes.map((scene, index) => ({
                ...scene,
                index: index,
                images: [],
                selectedImage: null,
                videoUrl: null
            }));
            
            showStep(4);
            renderImagesContainer();
            
            // 逐个镜头生成图片
            for (let i = 0; i < projectData.scenes.length; i++) {
                await generateImagesForScene(i);
            }
            
            checkCanGenerateVideos();
        }
        
        // 渲染图片选择容器
        function renderImagesContainer() {
            const container = document.getElementById('imagesContainer');
            
            container.innerHTML = projectData.scenes.map((scene, index) => `
                <div class="scene-card">
                    <div class="scene-header">
                        <span class="scene-number">镜头 ${index + 1}</span>
                        <button class="btn btn-secondary" onclick="regenerateImagesForScene(${index})" style="padding: 8px 16px; font-size: 14px;">
                            🔄 重新生成
                        </button>
                    </div>
                    <p style="color: rgba(255,255,255,0.6); margin-bottom: 10px;">${scene.description}</p>
                    <div class="images-grid" id="imagesGrid${index}">
                        <div class="image-option"><div class="image-placeholder">生成中...</div></div>
                        <div class="image-option"><div class="image-placeholder">生成中...</div></div>
                        <div class="image-option"><div class="image-placeholder">生成中...</div></div>
                    </div>
                </div>
            `).join('');
        }
        
        // 为指定镜头生成图片
        async function generateImagesForScene(sceneIndex) {
            const scene = projectData.scenes[sceneIndex];
            const grid = document.getElementById(`imagesGrid${sceneIndex}`);
            
            grid.innerHTML = `
                <div class="image-option"><div class="image-placeholder">生成中...</div></div>
                <div class="image-option"><div class="image-placeholder">生成中...</div></div>
                <div class="image-option"><div class="image-placeholder">生成中...</div></div>
            `;
            
            try {
                const resp = await fetch(`${API_BASE}/api/generate-images`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        prompt: scene.image_prompt,
                        count: 3
                    })
                });
                
                const data = await resp.json();
                
                if (data.success) {
                    scene.images = data.images.filter(img => img.url).map(img => img.url);
                    scene.selectedImage = scene.images.length > 0 ? scene.images[0] : null;
                    renderImagesGrid(sceneIndex);
                } else {
                    grid.innerHTML = `<p style="color: #f44336;">生成失败：${data.error || '未知错误'}</p>`;
                }
            } catch (e) {
                grid.innerHTML = `<p style="color: #f44336;">请求失败：${e.message}</p>`;
            }
            
            checkCanGenerateVideos();
        }
        
        // 重新生成指定镜头的图片
        async function regenerateImagesForScene(sceneIndex) {
            projectData.scenes[sceneIndex].images = [];
            projectData.scenes[sceneIndex].selectedImage = null;
            checkCanGenerateVideos();
            await generateImagesForScene(sceneIndex);
        }
        
        // 渲染图片选择网格
        function renderImagesGrid(sceneIndex) {
            const scene = projectData.scenes[sceneIndex];
            const grid = document.getElementById(`imagesGrid${sceneIndex}`);
            
            if (scene.images.length === 0) {
                grid.innerHTML = '<p style="color: rgba(255,255,255,0.5);">暂无图片</p>';
                return;
            }
            
            grid.innerHTML = scene.images.map((url, imgIndex) => `
                <div class="image-option ${scene.selectedImage === url ? 'selected' : ''}" 
                     onclick="selectImage(${sceneIndex}, ${imgIndex})">
                    <img src="${url}" alt="图片${imgIndex + 1}">
                    <div class="check-mark">✓</div>
                </div>
            `).join('');
        }
        
        // 选择图片
        function selectImage(sceneIndex, imgIndex) {
            const scene = projectData.scenes[sceneIndex];
            scene.selectedImage = scene.images[imgIndex];
            renderImagesGrid(sceneIndex);
            checkCanGenerateVideos();
        }
        
        // 检查是否可以生成视频
        function checkCanGenerateVideos() {
            const allSelected = projectData.scenes.every(scene => scene.selectedImage);
            document.getElementById('generateVideosBtn').disabled = !allSelected;
        }
        
        // Step 4 → Step 5: 生成视频
        async function startGenerateVideos() {
            showStep(5);
            
            const statusList = document.getElementById('videoStatusList');
            const progressBar = document.getElementById('videoProgress');
            const progressText = document.getElementById('videoProgressText');
            
            // 初始化状态列表
            statusList.innerHTML = projectData.scenes.map((scene, index) => `
                <div class="status-item" id="videoStatus${index}">
                    <div class="status-icon pending" id="videoIcon${index}">⏳</div>
                    <div>
                        <strong>镜头 ${index + 1}</strong>
                        <p style="color: rgba(255,255,255,0.5); font-size: 13px;">${scene.description.substring(0, 30)}...</p>
                    </div>
                </div>
            `).join('');
            
            let completed = 0;
            const total = projectData.scenes.length;
            
            // 逐个生成视频
            for (let i = 0; i < projectData.scenes.length; i++) {
                const scene = projectData.scenes[i];
                const icon = document.getElementById(`videoIcon${i}`);
                
                icon.className = 'status-icon loading';
                icon.textContent = '🔄';
                progressText.textContent = `正在生成镜头 ${i + 1}/${total} 的视频...`;
                
                try {
                    const resp = await fetch(`${API_BASE}/api/generate-video`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            image_url: scene.selectedImage,
                            prompt: scene.video_prompt,
                            duration: 5
                        })
                    });
                    
                    const data = await resp.json();
                    
                    if (data.success) {
                        scene.videoUrl = data.video_url;
                        icon.className = 'status-icon done';
                        icon.textContent = '✅';
                    } else {
                        icon.className = 'status-icon error';
                        icon.textContent = '❌';
                    }
                } catch (e) {
                    icon.className = 'status-icon error';
                    icon.textContent = '❌';
                }
                
                completed++;
                progressBar.style.width = `${(completed / total) * 100}%`;
            }
            
            progressText.textContent = '全部完成！';
            
            // 延迟后显示完成页面
            setTimeout(() => {
                showCompletePage();
            }, 1000);
        }
        
        // 显示完成页面
        function showCompletePage() {
            document.getElementById('finalCopywriting').textContent = projectData.copywriting;
            
            const videosContainer = document.getElementById('resultVideos');
            videosContainer.innerHTML = projectData.scenes.map((scene, index) => {
                if (scene.videoUrl) {
                    return `
                        <div class="result-video-item">
                            <video src="${scene.videoUrl}" controls></video>
                            <div class="video-info">
                                <span>镜头 ${index + 1}</span>
                                <a href="${scene.videoUrl}" download class="btn btn-secondary" style="padding: 5px 10px; font-size: 12px;">下载</a>
                            </div>
                        </div>
                    `;
                } else {
                    return `
                        <div class="result-video-item">
                            <div style="padding: 40px; text-align: center; color: rgba(255,255,255,0.5);">
                                镜头 ${index + 1} 生成失败
                            </div>
                        </div>
                    `;
                }
            }).join('');
            
            showStep('complete');
            saveToHistory();
        }
        
        // 复制文案
        function copyCopywriting() {
            navigator.clipboard.writeText(projectData.copywriting).then(() => {
                alert('文案已复制到剪贴板！');
            });
        }
        
        // 下载全部
        function downloadAll() {
            projectData.scenes.forEach((scene, index) => {
                if (scene.videoUrl) {
                    const a = document.createElement('a');
                    a.href = scene.videoUrl;
                    a.download = `镜头${index + 1}.mp4`;
                    a.click();
                }
            });
        }
        
        // 发送到飞书
        async function notifyFeishu() {
            const videos = projectData.scenes.filter(s => s.videoUrl).map((s, i) => `镜头${i+1}: ${s.videoUrl}`).join('\n');
            const message = `🎉 视频生成完成！\n\n产品：${projectData.productName}\n\n📝 文案：\n${projectData.copywriting}\n\n🎬 视频链接：\n${videos}`;
            
            try {
                await fetch(`${API_BASE}/api/notify`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message })
                });
                alert('已发送到飞书群！');
            } catch (e) {
                alert('发送失败：' + e.message);
            }
        }
        
        // 新建任务
        function startNew() {
            projectData = {
                productName: '',
                productFeatures: '',
                style: '科技简约',
                numScenes: 4,
                copywriting: '',
                storyboard: null,
                scenes: []
            };
            
            document.getElementById('productName').value = '';
            document.getElementById('productFeatures').value = '';
            document.getElementById('styleSelect').value = '科技简约';
            document.getElementById('numScenes').value = '4';
            
            showStep(1);
        }
        
        // 保存到历史记录
        function saveToHistory() {
            const history = JSON.parse(localStorage.getItem('videoHistory') || '[]');
            
            history.unshift({
                id: Date.now(),
                productName: projectData.productName,
                style: projectData.style,
                copywriting: projectData.copywriting,
                scenes: projectData.scenes.map(s => ({
                    description: s.description,
                    videoUrl: s.videoUrl
                })),
                createdAt: new Date().toISOString()
            });
            
            // 只保留最近20条
            if (history.length > 20) {
                history.pop();
            }
            
            localStorage.setItem('videoHistory', JSON.stringify(history));
        }
        
        // 显示历史记录
        function showHistory() {
            document.querySelectorAll('.step-content').forEach(el => el.classList.add('hidden'));
            document.getElementById('stepsIndicator').classList.add('hidden');
            document.getElementById('historyPage').classList.remove('hidden');
            
            const history = JSON.parse(localStorage.getItem('videoHistory') || '[]');
            const list = document.getElementById('historyList');
            
            if (history.length === 0) {
                list.innerHTML = '<p style="color: rgba(255,255,255,0.5); text-align: center; padding: 40px;">暂无历史记录</p>';
                return;
            }
            
            list.innerHTML = history.map(item => {
                const date = new Date(item.createdAt);
                const dateStr = `${date.getMonth()+1}-${date.getDate()} ${date.getHours()}:${String(date.getMinutes()).padStart(2,'0')}`;
                const videoCount = item.scenes.filter(s => s.videoUrl).length;
                
                return `
                    <div class="history-item" onclick="loadHistory(${item.id})">
                        <div class="history-info">
                            <h4>${item.productName}</h4>
                            <span>${dateStr} · ${item.style} · ${videoCount}个视频</span>
                        </div>
                        <span class="history-status completed">已完成</span>
                    </div>
                `;
            }).join('');
        }
        
        // 隐藏历史记录
        function hideHistory() {
            document.getElementById('historyPage').classList.add('hidden');
            document.getElementById('stepsIndicator').classList.remove('hidden');
            showStep(currentStep);
        }
        
        // 加载历史记录
        function loadHistory(id) {
            const history = JSON.parse(localStorage.getItem('videoHistory') || '[]');
            const item = history.find(h => h.id === id);
            
            if (!item) return;
            
            projectData.productName = item.productName;
            projectData.copywriting = item.copywriting;
            projectData.scenes = item.scenes;
            
            document.getElementById('finalCopywriting').textContent = item.copywriting;
            
            const videosContainer = document.getElementById('resultVideos');
            videosContainer.innerHTML = item.scenes.map((scene, index) => {
                if (scene.videoUrl) {
                    return `
                        <div class="result-video-item">
                            <video src="${scene.videoUrl}" controls></video>
                            <div class="video-info">
                                <span>镜头 ${index + 1}</span>
                                <a href="${scene.videoUrl}" download class="btn btn-secondary" style="padding: 5px 10px; font-size: 12px;">下载</a>
                            </div>
                        </div>
                    `;
                } else {
                    return `
                        <div class="result-video-item">
                            <div style="padding: 40px; text-align: center; color: rgba(255,255,255,0.5);">
                                镜头 ${index + 1} 生成失败
                            </div>
                        </div>
                    `;
                }
            }).join('');
            
            document.getElementById('historyPage').classList.add('hidden');
            document.getElementById('stepsIndicator').classList.remove('hidden');
            showStep('complete');
        }
        
        // ============================================
        // 🚀 一键全自动生成
        // ============================================
        async function oneClickGenerate() {
            projectData.productName = document.getElementById('productName').value.trim();
            projectData.productFeatures = document.getElementById('productFeatures').value.trim();
            projectData.style = document.getElementById('styleSelect').value;
            projectData.numScenes = parseInt(document.getElementById('numScenes').value);
            
            if (!projectData.productName) {
                alert('请输入产品名称');
                return;
            }
            
            // 显示进度页面
            showStep(5);
            const statusList = document.getElementById('videoStatusList');
            const progressBar = document.getElementById('videoProgress');
            const progressText = document.getElementById('videoProgressText');
            
            statusList.innerHTML = `
                <div class="status-item"><div class="status-icon loading">🔄</div><div><strong>生成文案</strong></div></div>
                <div class="status-item"><div class="status-icon pending">⏳</div><div><strong>生成分镜</strong></div></div>
                <div class="status-item"><div class="status-icon pending">⏳</div><div><strong>生成图片</strong></div></div>
                <div class="status-item"><div class="status-icon pending">⏳</div><div><strong>生成视频</strong></div></div>
            `;
            
            try {
                // Step 1: 生成文案
                progressText.textContent = '正在生成文案...';
                progressBar.style.width = '10%';
                
                const copyResp = await fetch(`${API_BASE}/api/generate-copy`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        product_name: projectData.productName,
                        product_features: projectData.productFeatures,
                        style: projectData.style
                    })
                });
                const copyData = await copyResp.json();
                
                if (!copyData.success) {
                    throw new Error('文案生成失败: ' + (copyData.error || '未知错误'));
                }
                projectData.copywriting = copyData.content;
                updateAutoStatus(0, 'done');
                updateAutoStatus(1, 'loading');
                
                // Step 2: 生成分镜
                progressText.textContent = '正在生成分镜...';
                progressBar.style.width = '25%';
                
                const sbResp = await fetch(`${API_BASE}/api/generate-storyboard`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        product_name: projectData.productName,
                        copywriting: projectData.copywriting,
                        style: projectData.style,
                        num_scenes: projectData.numScenes
                    })
                });
                const sbData = await sbResp.json();
                
                if (!sbData.success) {
                    throw new Error('分镜生成失败: ' + (sbData.error || '未知错误'));
                }
                projectData.storyboard = sbData.storyboard;
                updateAutoStatus(1, 'done');
                updateAutoStatus(2, 'loading');
                
                // Step 3: 生成图片（每个镜头生成1张，自动选择）
                const scenes = projectData.storyboard.scenes || [];
                projectData.scenes = [];
                progressText.textContent = `正在生成图片 (0/${scenes.length})...`;
                progressBar.style.width = '40%';
                
                for (let i = 0; i < scenes.length; i++) {
                    const scene = scenes[i];
                    progressText.textContent = `正在生成图片 (${i+1}/${scenes.length})...`;
                    progressBar.style.width = `${40 + (i / scenes.length) * 20}%`;
                    
                    const imgResp = await fetch(`${API_BASE}/api/generate-images`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ prompt: scene.image_prompt, count: 1 })
                    });
                    const imgData = await imgResp.json();
                    
                    const imageUrl = imgData.images?.[0]?.url || null;
                    projectData.scenes.push({
                        ...scene,
                        index: i,
                        selectedImage: imageUrl,
                        video_prompt: scene.video_prompt,
                        videoUrl: null
                    });
                }
                updateAutoStatus(2, 'done');
                updateAutoStatus(3, 'loading');
                
                // Step 4: 生成视频
                progressBar.style.width = '60%';
                
                for (let i = 0; i < projectData.scenes.length; i++) {
                    const scene = projectData.scenes[i];
                    progressText.textContent = `正在生成视频 (${i+1}/${projectData.scenes.length})...`;
                    progressBar.style.width = `${60 + (i / projectData.scenes.length) * 35}%`;
                    
                    if (scene.selectedImage) {
                        const videoResp = await fetch(`${API_BASE}/api/generate-video`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                image_url: scene.selectedImage,
                                prompt: scene.video_prompt || 'slow camera movement, 5 seconds'
                            })
                        });
                        const videoData = await videoResp.json();
                        
                        if (videoData.success) {
                            scene.videoUrl = videoData.video_url;
                        }
                    }
                }
                updateAutoStatus(3, 'done');
                
                progressBar.style.width = '100%';
                progressText.textContent = '全部完成！';
                
                // 显示结果
                setTimeout(() => {
                    showCompletePage();
                }, 1000);
                
            } catch (e) {
                progressText.textContent = '生成失败: ' + e.message;
                alert('生成失败: ' + e.message);
            }
        }
        
        function updateAutoStatus(index, status) {
            const items = document.querySelectorAll('.status-item');
            if (items[index]) {
                const icon = items[index].querySelector('.status-icon');
                icon.className = 'status-icon ' + status;
                icon.textContent = status === 'done' ? '✅' : (status === 'loading' ? '🔄' : '⏳');
            }
        }
        
        // 初始化
        showStep(1);
    </script>
</body>
</html>
'''


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
