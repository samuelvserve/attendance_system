"""Main FastAPI application for Attendance Processing System"""
from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks, Form
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from pathlib import Path
import os
import shutil
import tempfile
import asyncio
import json
import pandas as pd
import numpy as np
from datetime import datetime
from typing import List, Dict, Any
import warnings
import uuid
import aiofiles
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import core modules
from .core.processing import (
    process_biometric_data, process_timechamp_data, process_manualtime_data,
    create_pivot_table, create_highlighted_data, save_to_excel
)
from .core.models import ProcessingResponse, WebSocketMessage

# Initialize FastAPI app
app = FastAPI(
    title=os.getenv("APP_NAME", "Attendance Processing System"),
    version=os.getenv("APP_VERSION", "2.0.0"),
    description="Process biometric, timechamp, and manual attendance data with real-time progress"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create directories
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
REPORT_DIR = Path(os.getenv("REPORT_DIR", "reports"))
UPLOAD_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)

# Static files and templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Fix: Initialize Jinja2Templates with proper configuration
template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
templates = Jinja2Templates(directory=template_dir)

# Store active WebSocket connections
active_connections: List[WebSocket] = []
processing_tasks: Dict[str, Dict[str, Any]] = {}

# ============================================================================
# WEBSOCKET MANAGER
# ============================================================================

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        # keyed by client_id string for O(1) per-user lookup
        self.clients: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.clients[client_id] = websocket

    def disconnect(self, websocket: WebSocket, client_id: str):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.clients.pop(client_id, None)

    def get_ws(self, client_id: str) -> WebSocket:
        """Return the WebSocket for a given client_id, or None."""
        return self.clients.get(client_id)

    async def send_message(self, websocket: WebSocket, message: Dict):
        try:
            await websocket.send_json(message)
        except:
            pass

    async def broadcast(self, message: Dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

    async def send_log(self, websocket: WebSocket, message: str, log_type: str = "info"):
        log_data = {
            "type": "log",
            "message": message,
            "log_type": log_type,
            "timestamp": datetime.now().isoformat()
        }
        await self.send_message(websocket, log_data)

    async def send_progress(self, websocket: WebSocket, progress: int, message: str = None):
        progress_data = {
            "type": "progress",
            "progress": progress,
            "message": message,
            "timestamp": datetime.now().isoformat()
        }
        await self.send_message(websocket, progress_data)

    async def send_complete(self, websocket: WebSocket, data: Dict):
        complete_data = {
            "type": "complete",
            "data": data,
            "timestamp": datetime.now().isoformat()
        }
        await self.send_message(websocket, complete_data)

    async def send_error(self, websocket: WebSocket, error_message: str):
        error_data = {
            "type": "error",
            "message": error_message,
            "timestamp": datetime.now().isoformat()
        }
        await self.send_message(websocket, error_data)

manager = ConnectionManager()

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Serve the main HTML interface"""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"title": os.getenv("APP_NAME", "Attendance Processing System")}
    )

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, client_id: str = None):
    """WebSocket endpoint for real-time updates.
    Each browser tab passes a stable client_id query param so the server can
    route progress messages to exactly the right connection.
    """
    if not client_id:
        client_id = str(uuid.uuid4())
    await manager.connect(websocket, client_id)
    # Immediately tell the client which id was assigned (useful if server generated it)
    await manager.send_message(websocket, {"type": "connected", "client_id": client_id})
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await manager.send_message(websocket, {"type": "pong", "timestamp": datetime.now().isoformat()})
            except:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket, client_id)

@app.post("/api/upload")
async def upload_files(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    websocket_id: str = Form(None),
    output_format: str = Form("excel")
):
    """Upload and process attendance files"""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    
    # Look up the WebSocket for this specific client — O(1), no cross-user leakage
    target_websocket = manager.get_ws(websocket_id) if websocket_id else None
    # If client_id unknown (no WS yet), fall back to first available connection
    if target_websocket is None and manager.active_connections:
        target_websocket = manager.active_connections[0]

    # Create task ID
    task_id = str(uuid.uuid4())
    
    # Save uploaded files
    temp_dir = tempfile.mkdtemp()
    saved_files = []
    
    try:
        for file in files:
            file_path = os.path.join(temp_dir, file.filename)
            async with aiofiles.open(file_path, 'wb') as f:
                content = await file.read()
                await f.write(content)
            saved_files.append(file_path)
        
        # Process files in background
        background_tasks.add_task(
            process_files_background,
            saved_files,
            temp_dir,
            task_id,
            target_websocket
        )
        
        return JSONResponse({
            "status": "processing",
            "message": "Files uploaded successfully. Processing started.",
            "task_id": task_id
        })
        
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))

async def process_files_background(file_paths: List[str], temp_dir: str, task_id: str, websocket: WebSocket):
    """Background task to process files with real-time updates"""
    try:
        await manager.send_log(websocket, "🚀 Starting file processing...", "info")
        await manager.send_progress(websocket, 5, "Initializing...")

        # Build a sync log_cb that fires WebSocket messages via asyncio
        import asyncio
        loop = asyncio.get_event_loop()

        def log_cb(msg, log_type="info"):
            asyncio.run_coroutine_threadsafe(
                manager.send_log(websocket, msg, log_type), loop
            )

        if len(file_paths) == 1:
            infile = file_paths[0]
            await manager.send_log(websocket, f"📄 Reading file: {os.path.basename(infile)}", "info")
            await manager.send_progress(websocket, 10, "Reading file...")

            ext = os.path.splitext(infile)[1].lower()
            if ext == '.csv':
                import charset_normalizer
                with open(infile, 'rb') as f:
                    rawdata = f.read()
                encoding_result = charset_normalizer.from_bytes(rawdata).best()
                encoding = encoding_result.encoding if encoding_result else 'utf-8'
                df = pd.read_csv(infile, sep=None, engine='python', encoding=encoding, dtype=str)
            elif ext in ('.xls', '.xlsx', '.xlsm'):
                df = pd.read_excel(infile)
            else:
                raise ValueError(f"Unsupported file type: {ext}")

            df = df.rename(columns=lambda x: x.strip())
            df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
            for column in df.columns:
                df[column] = df[column].replace(r'^\s+|\s+$', '', regex=True)

            await manager.send_progress(websocket, 20, "Detecting file type...")

            if 'Punch Records' in df.columns:
                await manager.send_log(websocket, "🔄 Detected: Biometric data", "info")
                await manager.send_progress(websocket, 25, "Processing Biometric data...")
                df = process_biometric_data(df, log_cb=log_cb)
                source_type = 'Biometric Attendance'
            elif 'Expected Hours' in df.columns:
                await manager.send_log(websocket, "🔄 Detected: Timechamp data", "info")
                await manager.send_progress(websocket, 25, "Processing Timechamp data...")
                df = process_timechamp_data(df, log_cb=log_cb)
                source_type = 'Timechamp Attendance'
            elif 'Manual Hours' in df.columns:
                await manager.send_log(websocket, "🔄 Detected: Manual Tracking data", "info")
                await manager.send_progress(websocket, 25, "Processing Manual Tracking data...")
                df = process_manualtime_data(df, log_cb=log_cb)
                source_type = 'Manualtime Attendance'
            else:
                raise ValueError("Unknown file format — check that file has correct headers (Punch Records / Expected Hours / Manual Hours).")

        else:
            # Multi-file: process each file individually then consolidate
            await manager.send_log(websocket, f"📚 Multi-file mode: {len(file_paths)} files detected", "info")
            await manager.send_progress(websocket, 10, "Processing individual files...")

            processed_dfs = []
            source_types  = []
            for i, infile in enumerate(file_paths, 1):
                fname = os.path.basename(infile)
                await manager.send_log(websocket, f"📄 [{i}/{len(file_paths)}] Reading: {fname}", "info")
                pct = 10 + int((i - 1) / len(file_paths) * 30)
                await manager.send_progress(websocket, pct, f"Processing file {i}/{len(file_paths)}...")

                ext = os.path.splitext(infile)[1].lower()
                if ext == '.csv':
                    df_temp = pd.read_csv(infile, sep=None, engine='python', encoding='utf-8', dtype=str)
                elif ext in ('.xls', '.xlsx', '.xlsm'):
                    df_temp = pd.read_excel(infile, dtype=str)
                else:
                    await manager.send_log(websocket, f"⚠️  Skipping unsupported file: {fname}", "warning")
                    continue

                df_temp = df_temp.rename(columns=lambda x: x.strip())
                df_temp = df_temp.map(lambda x: x.strip() if isinstance(x, str) else x)

                if 'Punch Records' in df_temp.columns:
                    processed_dfs.append(process_biometric_data(df_temp, log_cb=log_cb))
                    source_types.append('Biometric')
                elif 'Expected Hours' in df_temp.columns:
                    processed_dfs.append(process_timechamp_data(df_temp, log_cb=log_cb))
                    source_types.append('Timechamp')
                elif 'Manual Hours' in df_temp.columns:
                    processed_dfs.append(process_manualtime_data(df_temp, log_cb=log_cb))
                    source_types.append('Manual')
                else:
                    await manager.send_log(websocket, f"⚠️  Could not detect type for: {fname} — skipping", "warning")

            if not processed_dfs:
                raise ValueError("No valid files could be processed.")

            await manager.send_log(websocket, f"🗂️  Consolidating {len(processed_dfs)} processed files...", "info")
            await manager.send_progress(websocket, 42, "Consolidating files...")
            df = file_consolidate(processed_dfs, log_cb=log_cb)
            unique_sources = list(dict.fromkeys(source_types))
            source_type = ' and '.join(unique_sources) + ' Consolidated'

        await manager.send_progress(websocket, 50, "Creating reports...")
        await manager.send_log(websocket, "📊 Creating pivot table...", "info")

        pivot_table = create_pivot_table(df, log_cb=log_cb)
        
        await manager.send_progress(websocket, 70, "Creating highlighted data...")
        await manager.send_log(websocket, "⚠️ Identifying highlighted users...", "info")
        highlight_data = create_highlighted_data(df, log_cb=log_cb)
        
        await manager.send_progress(websocket, 85, "Saving to Excel...")
        await manager.send_log(websocket, "💾 Saving Excel report...", "info")
        
        # Save to Excel
        file_path, file_name = save_to_excel(df, pivot_table, highlight_data, source_type, log_cb=log_cb)
        
        await manager.send_progress(websocket, 95, "Finalizing...")

        # Unwrap Styler → plain DataFrame for all downstream operations
        df_raw             = df.data            if hasattr(df,             'data') else df
        highlight_data_raw = highlight_data.data if hasattr(highlight_data, 'data') else highlight_data

        # Get summary statistics
        summary = {
            "total_records": len(df_raw),
            "employees": int(df_raw['Employee ID'].nunique()) if 'Employee ID' in df_raw.columns else 0,
            "date_range": {
                "start": str(df_raw['Date'].min()) if 'Date' in df_raw.columns else None,
                "end":   str(df_raw['Date'].max()) if 'Date' in df_raw.columns else None
            },
            "attendance_summary": {
                k: int(v) for k, v in df_raw['Attendance'].value_counts().to_dict().items()
            } if 'Attendance' in df_raw.columns else {},
            "source_type": source_type,
            "file_name": file_name
        }

        # Build table previews — all values must be JSON-safe
        def safe_val(v):
            """Convert any value to a JSON-safe Python type."""
            if v is None:
                return None
            if isinstance(v, float) and (v != v):   # nan check
                return None
            if isinstance(v, (str, int, float, bool)):
                return v
            return str(v)

        def df_to_preview(frame, max_rows=100):
            # Accept both plain DataFrame and Styler
            raw = frame.data if hasattr(frame, 'data') else frame
            preview = raw.head(max_rows).copy()
            preview = preview.where(pd.notnull(preview), None)
            return {
                "columns": list(preview.columns),
                "rows": [
                    [safe_val(cell) for cell in row]
                    for row in preview.values.tolist()
                ],
                "total": len(raw)
            }

        table_data = {
            "detailed":    df_to_preview(df),
            "highlighted": df_to_preview(highlight_data),
            "summary":     df_to_preview(pivot_table)
        }
        
        # Send completion
        await manager.send_progress(websocket, 100, "Complete!")
        await manager.send_log(websocket, "✅ Processing completed successfully!", "success")
        
        # Send file URL
        file_url = f"/api/download/{os.path.basename(file_path)}"
        await manager.send_complete(websocket, {
            "file_url": file_url,
            "summary": summary,
            "file_name": file_name,
            "tables": table_data
        })
        
        # Clean up temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)
        
    except Exception as e:
        import traceback
        error_msg = f"❌ Error: {str(e)}"
        await manager.send_log(websocket, error_msg, "error")
        await manager.send_log(websocket, f"📋 {traceback.format_exc()}", "debug")
        await manager.send_error(websocket, str(e))
        shutil.rmtree(temp_dir, ignore_errors=True)

@app.get("/api/download/{filename}")
async def download_file(filename: str):
    """Stream the processed Excel file.

    We stream manually so that:
      1. Content-Length / X-File-Size survive the ASGI layer (no buffering
         middleware strips them before the client sees them).
      2. The browser fetch() ReadableStream can report real progress.
    """
    from fastapi.responses import StreamingResponse
    import asyncio

    temp_dir = tempfile.gettempdir()
    file_path = None
    for root, dirs, files_list in os.walk(temp_dir):
        if filename in files_list:
            file_path = os.path.join(root, filename)
            break

    if not file_path:
        raise HTTPException(status_code=404, detail="File not found")

    file_size = os.path.getsize(file_path)
    CHUNK = 64 * 1024  # 64 KB chunks

    async def file_streamer():
        with open(file_path, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                yield chunk
                await asyncio.sleep(0)  # yield control to event loop

    headers = {
        "Content-Length": str(file_size),
        "X-File-Size": str(file_size),
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Access-Control-Expose-Headers": "Content-Length, X-File-Size",
    }
    return StreamingResponse(
        file_streamer(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_connections": len(manager.active_connections),
        "version": os.getenv("APP_VERSION", "2.0.0")
    }

@app.get("/api/status/{task_id}")
async def get_task_status(task_id: str):
    """Get status of a processing task"""
    return {"status": "not_found", "task_id": task_id}