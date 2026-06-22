"""Main entry point for the Attendance Processing System"""
import uvicorn
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))
    debug = os.getenv("DEBUG", "True").lower() == "true"
    
    print(f"""
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║   📋 ATTENDANCE PROCESSING SYSTEM                        ║
║   Version: 2.0.0                                         ║
║                                                           ║
║   🌐 Server: http://{host}:{port}                        ║
║   📚 API Docs: http://{host}:{port}/docs                ║
║                                                           ║
║   Press CTRL+C to stop                                   ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
    """)
    
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=debug,
        log_level="info"
    )