import logging
from logging.handlers import RotatingFileHandler
from functools import wraps
from typing import List, Optional
import asyncio
from datetime import datetime, timedelta
from urllib.parse import urlparse
import aiohttp
from config import *

# 设置日志
def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, LOG_LEVEL))
    
    formatter = logging.Formatter(LOG_FORMAT)
    
    # 文件处理器
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_SIZE,
        backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger

# 并发控制
class DownloadManager:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        self.active_downloads = {}
        self.request_counts = {}
        
    async def can_download(self, ip: str) -> bool:
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)
        
        # 清理旧的请求记录
        self.request_counts = {
            k: v for k, v in self.request_counts.items()
            if v['timestamp'] > minute_ago
        }
        
        # 检查请求频率
        if ip in self.request_counts:
            if self.request_counts[ip]['count'] >= MAX_REQUESTS_PER_MINUTE:
                return False
            self.request_counts[ip]['count'] += 1
        else:
            self.request_counts[ip] = {'count': 1, 'timestamp': now}
        
        return True

    def is_valid_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            return any(domain in parsed.netloc for domain in ALLOWED_DOMAINS)
        except:
            return False

# 错误处理装饰器
def handle_errors(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.exception(f"Error in {func.__name__}: {str(e)}")
            raise
    return wrapper

# 文件清理
async def cleanup_old_files():
    while True:
        try:
            current_time = datetime.now()
            for file in DOWNLOAD_DIR.glob("*"):
                if file.is_file():
                    file_time = datetime.fromtimestamp(file.stat().st_mtime)
                    # 删除超过7天的文件
                    if (current_time - file_time).days > 7:
                        file.unlink()
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Error cleaning up files: {e}")
        
        await asyncio.sleep(24 * 60 * 60)  # 每24小时运行一次 