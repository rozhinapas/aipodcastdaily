# gunicorn.conf.py
workers = 1
bind = "0.0.0.0:80"
timeout = 300          # 5 دقیقه
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
loglevel = "info"


