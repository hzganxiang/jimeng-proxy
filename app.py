"""
即梦AI Flask代理服务 - 专业版 v4.2
=====================================
功能：
1. 网页操作：一键全自动 + 分步生成
2. 飞书群@触发：快速生成
"""

from flask import Flask, request, jsonify, Response
import requests
import json
import os
import re
import time
import threading
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

projects = {}

STYLE_TEMPLATES = {
    "科技简约": {"copy_style": "简洁专业，突出科技感", "image_suffix": "minimalist tech style, clean white background, 8K", "video_suffix": "slow smooth camera movement, 5 seconds"},
    "年轻时尚": {"copy_style": "活泼有趣，贴近年轻人", "image_suffix": "vibrant colorful background, dynamic angle, 8K", "video_suffix": "energetic movement, 5 seconds"},
    "商务专业": {"copy_style": "稳重大气，强调效率", "image_suffix": "elegant business setting, dramatic lighting, 8K", "video_suffix": "slow elegant camera pan, 5 seconds"},
    "电竞酷炫": {"copy_style": "热血激情，强调性能", "image_suffix": "RGB lighting effects, cyberpunk style, 8K", "video_suffix": "fast dynamic movement, 5 seconds"}
}

# ============================================
# 工具函数
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
        if not token: return None
        img_resp = requests.get(image_url, timeout=60)
        if img_resp.status_code != 200: return None
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
        print(f"[图片] 生成请求: {prompt[:50]}...")
        response = requests.post(IMAGE_API_ENDPOINT, headers=headers, json=payload, timeout=120)
        result = response.json()
        print(f"[图片] 返回: {str(result)[:200]}...")
        if "data" in result and len(result["data"]) > 0:
            url = result["data"][0].get("url", "")
            print(f"[图片] 成功: {url[:50]}...")
            return {"success": True, "image_url": url}
        error_msg = result.get("error", {}).get("message", str(result))
        print(f"[图片] 失败: {error_msg}")
        return {"success": False, "error": error_msg}
    except Exception as e:
        print(f"[图片] 异常: {e}")
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
        print(f"[视频] 创建任务，图片: {image_url[:50]}..., 提示词: {prompt[:50]}...")
        response = requests.post(VIDEO_API_ENDPOINT, headers=headers, json=payload, timeout=30)
        result = response.json()
        print(f"[视频] 创建任务返回: {result}")
        if "id" in result:
            return {"success": True, "task_id": result["id"]}
        return {"success": False, "error": result.get("error", {}).get("message", str(result))}
    except Exception as e:
        print(f"[视频] 创建任务异常: {e}")
        return {"success": False, "error": str(e)}

def query_video_task(task_id):
    try:
        headers = {"Authorization": f"Bearer {ARK_API_KEY}"}
        response = requests.get(f"{VIDEO_API_ENDPOINT}/{task_id}", headers=headers, timeout=30)
        result = response.json()
        return result
    except Exception as e:
        print(f"[视频] 查询异常: {e}")
        return {"error": str(e)}

def wait_for_video(task_id, max_wait=600):
    """等待视频生成完成，最多等待10分钟"""
    start = time.time()
    print(f"[视频] 开始等待任务: {task_id}")
    while time.time() - start < max_wait:
        result = query_video_task(task_id)
        status = result.get("status", "")
        print(f"[视频] 任务 {task_id} 状态: {status}")
        
        if status == "succeeded":
            # 尝试多种可能的返回格式
            video_url = None
            if "content" in result:
                content = result["content"]
                if isinstance(content, dict):
                    video_url = content.get("video_url") or content.get("url") or content.get("video")
                elif isinstance(content, str):
                    video_url = content
            if not video_url:
                video_url = result.get("video_url") or result.get("url") or result.get("output", {}).get("video_url")
            
            print(f"[视频] 完成，URL: {video_url}")
            if video_url:
                return {"success": True, "video_url": video_url}
            else:
                print(f"[视频] 警告：状态成功但无URL，完整返回: {result}")
                return {"success": False, "error": "视频URL为空"}
                
        elif status in ["failed", "cancelled"]:
            error_msg = result.get("error", {}).get("message", "") or result.get("message", "") or f"任务{status}"
            print(f"[视频] 失败: {error_msg}")
            return {"success": False, "error": error_msg}
        
        time.sleep(5)
    
    print(f"[视频] 超时")
    return {"success": False, "error": "生成超时（10分钟）"}

# ============================================
# 网页HTML
# ============================================

HTML_PAGE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI视频生成器</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); min-height: 100vh; color: #fff; }
        .container { max-width: 900px; margin: 0 auto; padding: 20px; }
        .header { display: flex; justify-content: space-between; align-items: center; padding: 20px 0; border-bottom: 1px solid rgba(255,255,255,0.1); margin-bottom: 30px; }
        .header h1 { font-size: 24px; }
        .header-btn { background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.2); color: #fff; padding: 10px 20px; border-radius: 8px; cursor: pointer; }
        .header-btn:hover { background: rgba(255,255,255,0.2); }
        .card { background: rgba(255,255,255,0.05); border-radius: 16px; padding: 30px; margin-bottom: 20px; border: 1px solid rgba(255,255,255,0.1); }
        .card-title { font-size: 18px; margin-bottom: 20px; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 8px; color: rgba(255,255,255,0.8); font-size: 14px; }
        .form-group input, .form-group textarea, .form-group select { width: 100%; padding: 12px 16px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.2); background: rgba(255,255,255,0.05); color: #fff; font-size: 16px; }
        .form-group input:focus, .form-group textarea:focus, .form-group select:focus { outline: none; border-color: #4CAF50; background: rgba(255,255,255,0.1); }
        .form-group textarea { min-height: 100px; resize: vertical; }
        .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .btn { padding: 14px 28px; border-radius: 8px; border: none; font-size: 16px; font-weight: 600; cursor: pointer; transition: all 0.3s; display: inline-flex; align-items: center; gap: 8px; }
        .btn-primary { background: linear-gradient(135deg, #4CAF50, #45a049); color: #fff; }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 4px 15px rgba(76, 175, 80, 0.4); }
        .btn-secondary { background: rgba(255,255,255,0.1); color: #fff; border: 1px solid rgba(255,255,255,0.2); }
        .btn-secondary:hover { background: rgba(255,255,255,0.2); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-group { display: flex; gap: 15px; margin-top: 20px; flex-wrap: wrap; }
        .btn-auto { background: linear-gradient(135deg, #FF6B6B, #FF8E53); color: #fff; }
        .progress-container { margin: 20px 0; }
        .progress-bar { width: 100%; height: 8px; background: rgba(255,255,255,0.1); border-radius: 4px; overflow: hidden; }
        .progress-fill { height: 100%; background: linear-gradient(90deg, #4CAF50, #8BC34A); transition: width 0.3s; }
        .progress-text { text-align: center; margin-top: 10px; color: rgba(255,255,255,0.7); }
        .status-item { display: flex; align-items: center; gap: 15px; padding: 12px 0; border-bottom: 1px solid rgba(255,255,255,0.1); }
        .status-icon { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 18px; }
        .status-icon.pending { background: rgba(255,255,255,0.1); }
        .status-icon.loading { background: #FF9800; animation: pulse 1s infinite; }
        .status-icon.done { background: #4CAF50; }
        .status-icon.error { background: #f44336; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .result-videos { display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; margin: 20px 0; }
        .result-video-item { background: rgba(0,0,0,0.2); border-radius: 12px; overflow: hidden; }
        .result-video-item video { width: 100%; display: block; }
        .result-video-item .video-info { padding: 10px 15px; display: flex; justify-content: space-between; align-items: center; }
        .content-box { background: rgba(0,0,0,0.2); border-radius: 8px; padding: 20px; margin: 15px 0; white-space: pre-wrap; line-height: 1.8; }
        .hidden { display: none !important; }
        @media (max-width: 600px) { .form-row { grid-template-columns: 1fr; } .result-videos { grid-template-columns: 1fr; } .btn-group { flex-direction: column; } }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎬 AI视频生成器</h1>
            <button class="header-btn" onclick="showHistory()">📋 历史</button>
        </div>
        
        <div class="card" id="step1">
            <div class="card-title">📝 产品信息</div>
            <div class="form-group">
                <label>产品名称 *</label>
                <input type="text" id="productName" placeholder="例如：联想小新Pro16笔记本">
            </div>
            <div class="form-group">
                <label>产品卖点</label>
                <textarea id="productFeatures" placeholder="例如：16英寸2.5K屏，i7处理器，32G内存"></textarea>
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
                    <label>镜头数</label>
                    <select id="numScenes">
                        <option value="3">3个</option>
                        <option value="4" selected>4个</option>
                        <option value="5">5个</option>
                    </select>
                </div>
            </div>
            <div class="btn-group">
                <button class="btn btn-auto" onclick="oneClickGenerate()">🚀 一键全自动</button>
            </div>
        </div>
        
        <div class="card hidden" id="progressCard">
            <div class="card-title">⏳ 生成中...</div>
            <div id="statusList"></div>
            <div class="progress-container">
                <div class="progress-bar"><div class="progress-fill" id="progressBar" style="width:0%"></div></div>
                <div class="progress-text" id="progressText">准备中...</div>
            </div>
        </div>
        
        <div class="card hidden" id="resultCard">
            <div class="card-title">🎉 生成完成！</div>
            <div class="form-group">
                <label>📝 营销文案</label>
                <div class="content-box" id="resultCopy"></div>
                <button class="btn btn-secondary" onclick="copyText()">📋 复制文案</button>
            </div>
            <div class="form-group">
                <label>🎬 视频片段</label>
                <div class="result-videos" id="resultVideos"></div>
            </div>
            <div class="btn-group">
                <button class="btn btn-secondary" onclick="downloadAll()">📥 全部下载</button>
                <button class="btn btn-secondary" onclick="sendFeishu()">📤 发飞书</button>
                <button class="btn btn-primary" onclick="restart()">✨ 新建</button>
            </div>
        </div>

        <div class="card hidden" id="historyCard">
            <div class="card-title">📋 历史记录 <button class="btn btn-secondary" onclick="hideHistory()" style="float:right;padding:5px 15px;">返回</button></div>
            <div id="historyList" style="margin-top:20px;"></div>
        </div>
    </div>
    
    <script>
        var projectData = { copywriting: "", scenes: [] };
        
        async function oneClickGenerate() {
            var name = document.getElementById("productName").value.trim();
            var features = document.getElementById("productFeatures").value.trim();
            var style = document.getElementById("styleSelect").value;
            var numScenes = parseInt(document.getElementById("numScenes").value);
            
            if (!name) { alert("请输入产品名称"); return; }
            
            document.getElementById("step1").classList.add("hidden");
            document.getElementById("progressCard").classList.remove("hidden");
            
            var statusList = document.getElementById("statusList");
            var progressBar = document.getElementById("progressBar");
            var progressText = document.getElementById("progressText");
            
            statusList.innerHTML = "<div class='status-item'><div class='status-icon loading' id='s1'>🔄</div><div>生成文案</div></div>" +
                "<div class='status-item'><div class='status-icon pending' id='s2'>⏳</div><div>生成分镜</div></div>" +
                "<div class='status-item'><div class='status-icon pending' id='s3'>⏳</div><div>生成图片</div></div>" +
                "<div class='status-item'><div class='status-icon pending' id='s4'>⏳</div><div>生成视频</div></div>";
            
            try {
                progressText.textContent = "生成文案中...";
                progressBar.style.width = "10%";
                
                var resp = await fetch("/api/generate-copy", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({product_name: name, product_features: features, style: style})
                });
                var data = await resp.json();
                if (!data.success) throw new Error("文案失败: " + (data.error || "未知错误"));
                projectData.copywriting = data.content;
                document.getElementById("s1").className = "status-icon done";
                document.getElementById("s1").textContent = "✅";
                document.getElementById("s2").className = "status-icon loading";
                document.getElementById("s2").textContent = "🔄";
                
                progressText.textContent = "生成分镜中...";
                progressBar.style.width = "25%";
                
                resp = await fetch("/api/generate-storyboard", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({product_name: name, copywriting: projectData.copywriting, style: style, num_scenes: numScenes})
                });
                data = await resp.json();
                if (!data.success) throw new Error("分镜失败: " + (data.error || "未知错误"));
                var scenes = data.storyboard.scenes || [];
                document.getElementById("s2").className = "status-icon done";
                document.getElementById("s2").textContent = "✅";
                document.getElementById("s3").className = "status-icon loading";
                document.getElementById("s3").textContent = "🔄";
                
                projectData.scenes = [];
                for (var i = 0; i < scenes.length; i++) {
                    progressText.textContent = "生成图片 " + (i+1) + "/" + scenes.length;
                    progressBar.style.width = (25 + (i/scenes.length)*25) + "%";
                    
                    resp = await fetch("/api/generate-images", {
                        method: "POST", headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({prompt: scenes[i].image_prompt, count: 1})
                    });
                    data = await resp.json();
                    projectData.scenes.push({
                        image_url: data.images && data.images[0] ? data.images[0].url : null,
                        video_prompt: scenes[i].video_prompt,
                        video_url: null
                    });
                }
                document.getElementById("s3").className = "status-icon done";
                document.getElementById("s3").textContent = "✅";
                document.getElementById("s4").className = "status-icon loading";
                document.getElementById("s4").textContent = "🔄";
                
                for (var i = 0; i < projectData.scenes.length; i++) {
                    var s = projectData.scenes[i];
                    if (!s.image_url) {
                        console.log("镜头" + (i+1) + " 无图片，跳过");
                        continue;
                    }
                    progressText.textContent = "生成视频 " + (i+1) + "/" + projectData.scenes.length + "（约需1-2分钟）";
                    progressBar.style.width = (50 + (i/projectData.scenes.length)*45) + "%";
                    
                    console.log("开始生成视频" + (i+1) + ", 图片URL:", s.image_url);
                    resp = await fetch("/api/generate-video", {
                        method: "POST", headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({image_url: s.image_url, prompt: s.video_prompt || "slow camera movement, product showcase"})
                    });
                    data = await resp.json();
                    console.log("视频" + (i+1) + " 结果:", data);
                    if (data.success && data.video_url) {
                        s.video_url = data.video_url;
                        console.log("视频" + (i+1) + " 成功:", s.video_url);
                    } else {
                        console.log("视频" + (i+1) + " 失败:", data.error);
                    }
                }
                document.getElementById("s4").className = "status-icon done";
                document.getElementById("s4").textContent = "✅";
                
                progressBar.style.width = "100%";
                progressText.textContent = "完成！";
                
                setTimeout(showResult, 500);
                saveHistory(name);
                
            } catch (e) {
                progressText.textContent = "失败: " + e.message;
                alert("生成失败: " + e.message);
            }
        }
        
        function showResult() {
            document.getElementById("progressCard").classList.add("hidden");
            document.getElementById("resultCard").classList.remove("hidden");
            document.getElementById("resultCopy").textContent = projectData.copywriting;
            
            console.log("显示结果，场景数:", projectData.scenes.length);
            var html = "";
            for (var i = 0; i < projectData.scenes.length; i++) {
                var s = projectData.scenes[i];
                console.log("镜头" + (i+1) + ":", s);
                if (s.video_url) {
                    html += "<div class='result-video-item'><video src='" + s.video_url + "' controls playsinline></video><div class='video-info'><span>镜头" + (i+1) + "</span><a href='" + s.video_url + "' target='_blank' class='btn btn-secondary' style='padding:5px 10px;font-size:12px;'>下载</a></div></div>";
                } else if (s.image_url) {
                    html += "<div class='result-video-item'><img src='" + s.image_url + "' style='width:100%;'><div class='video-info'><span>镜头" + (i+1) + " (仅图片)</span></div></div>";
                } else {
                    html += "<div class='result-video-item'><div style='padding:40px;text-align:center;color:rgba(255,255,255,0.5);'>镜头" + (i+1) + " 生成失败</div></div>";
                }
            }
            document.getElementById("resultVideos").innerHTML = html;
        }
        
        function copyText() {
            navigator.clipboard.writeText(projectData.copywriting).then(function() { alert("已复制！"); });
        }
        
        function downloadAll() {
            for (var i = 0; i < projectData.scenes.length; i++) {
                var s = projectData.scenes[i];
                if (s.video_url) {
                    var a = document.createElement("a");
                    a.href = s.video_url;
                    a.download = "镜头" + (i+1) + ".mp4";
                    a.click();
                }
            }
        }
        
        async function sendFeishu() {
            var videos = [];
            for (var i = 0; i < projectData.scenes.length; i++) {
                if (projectData.scenes[i].video_url) {
                    videos.push("镜头"+(i+1)+": "+projectData.scenes[i].video_url);
                }
            }
            var msg = "🎉 视频生成完成！\n\n📝 文案：\n" + projectData.copywriting + "\n\n🎬 视频：\n" + videos.join("\n");
            await fetch("/api/notify", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({message:msg})});
            alert("已发送到飞书！");
        }
        
        function restart() {
            document.getElementById("resultCard").classList.add("hidden");
            document.getElementById("step1").classList.remove("hidden");
            projectData = {copywriting:"", scenes:[]};
        }
        
        function saveHistory(name) {
            var h = JSON.parse(localStorage.getItem("videoHistory") || "[]");
            h.unshift({id:Date.now(), name:name, copywriting:projectData.copywriting, scenes:projectData.scenes, time:new Date().toLocaleString()});
            if (h.length > 20) h.pop();
            localStorage.setItem("videoHistory", JSON.stringify(h));
        }
        
        function showHistory() {
            document.getElementById("step1").classList.add("hidden");
            document.getElementById("historyCard").classList.remove("hidden");
            var h = JSON.parse(localStorage.getItem("videoHistory") || "[]");
            if (h.length === 0) {
                document.getElementById("historyList").innerHTML = "<p style='color:rgba(255,255,255,0.5);text-align:center;'>暂无记录</p>";
            } else {
                var html = "";
                for (var i = 0; i < h.length; i++) {
                    var item = h[i];
                    html += "<div style='padding:15px;background:rgba(255,255,255,0.05);border-radius:10px;margin-bottom:10px;cursor:pointer;' onclick='loadHistory("+item.id+")'><strong>"+item.name+"</strong><br><span style='font-size:13px;color:rgba(255,255,255,0.5);'>"+item.time+"</span></div>";
                }
                document.getElementById("historyList").innerHTML = html;
            }
        }
        
        function hideHistory() {
            document.getElementById("historyCard").classList.add("hidden");
            document.getElementById("step1").classList.remove("hidden");
        }
        
        function loadHistory(id) {
            var h = JSON.parse(localStorage.getItem("videoHistory") || "[]");
            for (var i = 0; i < h.length; i++) {
                if (h[i].id === id) {
                    projectData.copywriting = h[i].copywriting;
                    projectData.scenes = h[i].scenes;
                    document.getElementById("historyCard").classList.add("hidden");
                    showResult();
                    document.getElementById("resultCard").classList.remove("hidden");
                    break;
                }
            }
        }
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    return Response(HTML_PAGE, mimetype='text/html')

@app.route('/health')
def health():
    return jsonify({"status": "ok", "version": "v4.2"})

# ============================================
# API
# ============================================

@app.route('/api/generate-copy', methods=['POST'])
def api_generate_copy():
    data = request.get_json() or {}
    name = data.get("product_name", "").strip()
    features = data.get("product_features", "").strip()
    style = data.get("style", "科技简约")
    if not name:
        return jsonify({"success": False, "error": "请输入产品名称"}), 400
    cfg = STYLE_TEMPLATES.get(style, STYLE_TEMPLATES["科技简约"])
    sys_prompt = f"你是电商短视频文案专家。风格：{cfg['copy_style']}。生成30秒营销文案，80-120字，分4段。"
    return jsonify(chat_completion(sys_prompt, f"产品：{name}\n特点：{features}\n\n生成文案："))

@app.route('/api/generate-storyboard', methods=['POST'])
def api_generate_storyboard():
    data = request.get_json() or {}
    name = data.get("product_name", "").strip()
    copy = data.get("copywriting", "").strip()
    style = data.get("style", "科技简约")
    num = data.get("num_scenes", 4)
    if not name or not copy:
        return jsonify({"success": False, "error": "缺少参数"}), 400
    cfg = STYLE_TEMPLATES.get(style, STYLE_TEMPLATES["科技简约"])
    sys_prompt = f'分镜师，生成{num}个分镜，只输出JSON：{{"scenes":[{{"scene_id":1,"description":"描述","image_prompt":"英文提示词, {cfg["image_suffix"]}","video_prompt":"英文视频提示词, {cfg["video_suffix"]}"}}]}}'
    result = chat_completion(sys_prompt, f"产品：{name}\n文案：{copy}")
    if result.get("success"):
        try:
            c = result["content"]
            s, e = c.find("{"), c.rfind("}") + 1
            if s >= 0 and e > s:
                return jsonify({"success": True, "storyboard": json.loads(c[s:e])})
        except:
            pass
        return jsonify({"success": False, "error": "JSON解析失败"})
    return jsonify(result)

@app.route('/api/generate-images', methods=['POST'])
def api_generate_images():
    data = request.get_json() or {}
    prompt = data.get("prompt", "").strip()
    count = min(data.get("count", 1), 3)
    if not prompt:
        return jsonify({"success": False, "error": "请输入提示词"}), 400
    images = []
    for i in range(count):
        r = generate_image(prompt)
        images.append({"index": i+1, "url": r.get("image_url") if r.get("success") else None})
    return jsonify({"success": True, "images": images})

@app.route('/api/generate-video', methods=['POST'])
def api_generate_video():
    data = request.get_json() or {}
    img = data.get("image_url", "").strip()
    prompt = data.get("prompt", "slow movement").strip()
    if not img:
        return jsonify({"success": False, "error": "请提供图片URL"}), 400
    task = create_video_task(img, prompt, 5)
    if not task.get("success"):
        return jsonify(task)
    return jsonify(wait_for_video(task["task_id"]))

@app.route('/api/notify', methods=['POST'])
def api_notify():
    data = request.get_json() or {}
    if data.get("message"):
        send_feishu_text(data["message"])
    return jsonify({"success": True})

# ============================================
# 飞书回调
# ============================================

def feishu_quick_generate(prompt, with_video=False):
    try:
        img = generate_image(prompt)
        if not img.get("success"):
            send_feishu_text(f"❌ 图片失败：{img.get('error')}")
            return
        image_url = img["image_url"]
        image_key = upload_image_to_feishu(image_url)
        video_url = None
        if with_video:
            task = create_video_task(image_url, f"{prompt}，产品展示", 5)
            if task.get("success"):
                v = wait_for_video(task["task_id"])
                video_url = v.get("video_url") if v.get("success") else None
        content = [[{"tag": "text", "text": f"📝 {prompt}"}]]
        if image_key:
            content.append([{"tag": "img", "image_key": image_key}])
        content.append([{"tag": "a", "text": "🖼️原图", "href": image_url}])
        if video_url:
            content.append([{"tag": "a", "text": "🎬视频", "href": video_url}])
        send_feishu_message("✅ 完成", content)
    except Exception as e:
        send_feishu_text(f"❌ 失败：{e}")

@app.route('/feishu-callback', methods=['POST'])
def feishu_callback():
    data = request.get_json() or {}
    if 'challenge' in data:
        return jsonify({"challenge": data['challenge']})
    try:
        if data.get('header', {}).get('event_type') == 'im.message.receive_v1':
            msg = data.get('event', {}).get('message', {})
            if msg.get('message_type') != 'text':
                return jsonify({"code": 0})
            text = json.loads(msg.get('content', '{}')).get('text', '')
            prompt = re.sub(r'@\S+', '', text).strip()
            if "统计" in text:
                send_feishu_text(f"📊 项目数：{len(projects)}")
                return jsonify({"code": 0})
            clean = prompt.replace("视频", "").strip()
            if clean and len(clean) >= 2:
                with_video = "视频" in text
                threading.Thread(target=feishu_quick_generate, args=(clean, with_video), daemon=True).start()
    except Exception as e:
        print(f"飞书回调异常: {e}")
    return jsonify({"code": 0})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 即梦AI v4.2 启动 - 端口: {port}")
    app.run(host="0.0.0.0", port=port)
