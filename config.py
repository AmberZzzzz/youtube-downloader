import os
from pathlib import Path

# 基础配置
BASE_DIR = Path(__file__).parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
VIDEOS_INFO_FILE = BASE_DIR / "videos_info.json"

# 下载配置
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB
MAX_CONCURRENT_DOWNLOADS = 3
SUPPORTED_FORMATS = ['mp4', 'webm', 'mp3']
DEFAULT_FORMAT = 'mp4'

# 代理配置
PROXY = 'socks5://127.0.0.1:10808'

# 安全配置
MAX_REQUESTS_PER_MINUTE = 30
ALLOWED_DOMAINS = ['youtube.com', 'youtu.be']

# 日志配置
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"
LOG_LEVEL = "DEBUG"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_MAX_SIZE = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT = 5

# 创建必要的目录
for directory in [DOWNLOAD_DIR, STATIC_DIR, LOG_DIR]:
    directory.mkdir(exist_ok=True) 