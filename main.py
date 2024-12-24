from fastapi import FastAPI, WebSocket, Request, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketDisconnect
import yt_dlp
import json
import asyncio
import socket
import os
from datetime import datetime
from utils import setup_logger, DownloadManager, handle_errors, cleanup_old_files
from config import *
from typing import Optional
import re

# 设置日志
logger = setup_logger(__name__)

# 创建应用
app = FastAPI(title="YouTube Downloader")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# 挂载静态文件
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/downloads", StaticFiles(directory=str(DOWNLOAD_DIR)), name="downloads")

# 下载管理器
download_manager = DownloadManager()

# CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    logger.info("应用启动...")
    # 启动文件清理任务
    asyncio.create_task(cleanup_old_files())

def load_videos_info():
    try:
        if VIDEOS_INFO_FILE.exists():
            with open(VIDEOS_INFO_FILE, "r", encoding="utf-8") as f:
                videos = json.load(f)
                for video in videos:
                    if isinstance(video.get('filename'), (str, Path)):
                        video['filename'] = str(video['filename'])
                    else:
                        logger.warning(f"视频信息中的 filename 格式不正确: {video}")
                        video['filename'] = "未知文件名"
                return videos
        return []
    except Exception as e:
        logger.error(f"加载视频信息时出错: {e}")
        logger.exception(e)
        return []

def save_videos_info(videos):
    try:
        with open(VIDEOS_INFO_FILE, "w", encoding="utf-8") as f:
            json.dump(videos, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存视频信息时出错: {e}")
        logger.exception(e)

@app.get("/")
@handle_errors
async def home(request: Request):
    videos = load_videos_info()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "videos": videos}
    )

@app.websocket("/ws/download")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client = websocket.client.host
    
    try:
        while True:
            try:
                data = await websocket.receive_json()
                
                if not await download_manager.can_download(client):
                    await websocket.send_json({
                        'status': 'error',
                        'error': '请求过于频繁，请稍后再试'
                    })
                    continue
                
                if data['action'] == 'download':
                    url = data['url']
                    format_id = data.get('format_id', 'best')  # 获取选择的格式
                    
                    if not download_manager.is_valid_url(url):
                        await websocket.send_json({
                            'status': 'error',
                            'error': '无效的URL'
                        })
                        continue
                    
                    async with download_manager.semaphore:
                        await download_video(url, websocket, format_id)  # 传递 format_id
                        
            except WebSocketDisconnect:
                logger.debug("WebSocket连接已由客户端关闭")
                break
            except Exception as e:
                logger.error(f"WebSocket处理错误: {e}")
                logger.exception(e)
                try:
                    await websocket.send_json({
                        'status': 'error',
                        'error': str(e)
                    })
                except Exception:
                    pass
                break
    finally:
        try:
            await websocket.close()
        except Exception:
            pass

async def get_video_preview(url: str) -> dict:
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'proxy': PROXY
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise Exception("无法获取视频信息")
            
            # 获取缩略图URL
            thumbnail = info.get('thumbnail', '')
            
            # 获取可用的格式
            formats = []
            for f in info.get('formats', []):
                if f.get('filesize'):
                    format_info = {
                        'format_id': f.get('format_id'),
                        'ext': f.get('ext'),
                        'quality': f.get('quality', 0),
                        'filesize': f.get('filesize', 0),
                        'filesize_mb': round(f.get('filesize', 0) / 1024 / 1024, 2),
                        'format_note': f.get('format_note', ''),
                    }
                    if format_info['filesize_mb'] <= MAX_FILE_SIZE / 1024 / 1024:
                        formats.append(format_info)
            
            return {
                'title': info.get('title', 'Unknown Title'),
                'duration': info.get('duration', 0),
                'uploader': info.get('uploader', 'Unknown Uploader'),
                'thumbnail': thumbnail,
                'formats': formats,
                'description': info.get('description', ''),
            }
    except Exception as e:
        logger.error(f"获取视频预览信息时出错: {e}")
        raise

@app.post("/api/preview")
@handle_errors
async def preview_video(request: Request):
    data = await request.json()
    url = data.get('url')
    
    if not url:
        raise HTTPException(status_code=400, detail="URL不能为空")
    
    if not download_manager.is_valid_url(url):
        raise HTTPException(status_code=400, detail="无效的URL")
    
    try:
        preview_info = await get_video_preview(url)
        return JSONResponse(content=preview_info)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def download_video(url: str, websocket: WebSocket, format_id: str = 'best'):
    def progress_hook(d):
        try:
            if d['status'] == 'downloading':
                progress = {
                    'status': 'downloading',
                    'downloaded_bytes': d.get('downloaded_bytes', 0),
                    'total_bytes': d.get('total_bytes', 0),
                    'speed': d.get('speed', 0),
                    'eta': d.get('eta', 0)
                }
                asyncio.create_task(websocket.send_json(progress))
            elif d['status'] == 'finished':
                progress = {'status': 'finished'}
                asyncio.create_task(websocket.send_json(progress))
        except Exception as e:
            logger.error(f"进度回调错误: {e}")

    try:
        logger.info(f"开始下载视频: {url}")
        await websocket.send_json({
            'status': 'info',
            'message': '正在获取视频信息...'
        })
        
        # 配置下载选项
        ydl_opts = {
            'format': format_id,
            'outtmpl': str(DOWNLOAD_DIR / '%(title)s.%(ext)s'),
            'progress_hooks': [progress_hook],
            'socket_timeout': 30,
            'retries': 10,
            'ignoreerrors': True,
            'nocheckcertificate': True,
            'quiet': False,
            'no_warnings': False,
            'proxy': PROXY
        }
        
        # 先获取视频信息但不下载
        with yt_dlp.YoutubeDL({'quiet': True, 'proxy': PROXY}) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise Exception("无法获取视频信息")
            
            # 检查文件大小（如果可用）
            filesize = info.get('filesize') or info.get('filesize_approx')
            if filesize and filesize > MAX_FILE_SIZE:
                raise Exception(f"视频文件过大（{round(filesize/1024/1024, 2)}MB），超过限制（{round(MAX_FILE_SIZE/1024/1024)}MB）")
            
            # 如果没有文件大小信息，检查所选格式
            if format_id != 'best':
                for f in info.get('formats', []):
                    if f.get('format_id') == format_id:
                        format_size = f.get('filesize')
                        if format_size and format_size > MAX_FILE_SIZE:
                            raise Exception(f"所选格式文件过大（{round(format_size/1024/1024, 2)}MB），超过限制（{round(MAX_FILE_SIZE/1024/1024)}MB）")
        
        # 如果文件大小检查通过，继续下载
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise Exception("无法获取视频信息")
            
            filename = os.path.basename(ydl.prepare_filename(info))
            
            # 下载完成后检查实际文件大小
            actual_size = os.path.getsize(DOWNLOAD_DIR / filename)
            if actual_size > MAX_FILE_SIZE:
                # 如果文件过大，删除它
                os.remove(DOWNLOAD_DIR / filename)
                raise Exception(f"下载的文件过大（{round(actual_size/1024/1024, 2)}MB），超过限制（{round(MAX_FILE_SIZE/1024/1024)}MB）")
            
            video_info = {
                'title': info.get('title', 'Unknown Title'),
                'duration': info.get('duration', 0),
                'uploader': info.get('uploader', 'Unknown Uploader'),
                'description': info.get('description', ''),
                'filename': filename,
                'download_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'filesize': actual_size
            }
            
            videos = load_videos_info()
            videos.append(video_info)
            save_videos_info(videos)
            
            await websocket.send_json({
                'status': 'complete',
                'video_info': video_info
            })
            logger.info(f"视频下载完成: {video_info['title']}")
            
    except Exception as e:
        logger.error(f"下载视频时出错: {e}")
        logger.exception(e)
        await websocket.send_json({
            'status': 'error',
            'error': str(e)
        })

if __name__ == "__main__":
    import uvicorn
    
    def is_port_in_use(port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('0.0.0.0', port))
                return False
            except OSError:
                return True

    port = 8000
    while is_port_in_use(port) and port < 8020:
        port += 1
    
    if port >= 8020:
        logger.error("无法找到可用的端口")
        exit(1)
    
    logger.info(f"服务器将在 http://localhost:{port} 启动")
    
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True
    )
    server = uvicorn.Server(config)
    server.run()