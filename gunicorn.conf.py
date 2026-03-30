# Gunicorn配置
bind = "0.0.0.0:8080"
workers = 2
timeout = 600  # 10分钟超时
keepalive = 5
