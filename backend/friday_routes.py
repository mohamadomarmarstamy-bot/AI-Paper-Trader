from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from friday_research import capture, latest
from pro_ticker_http_security import require_same_origin
from universe import latest_friday_scan

router = APIRouter()


@router.get('/ai-friday')
def friday_page():
    return FileResponse(Path(__file__).parent / 'frontend' / 'friday.html')


@router.get('/ai-friday/research')
def friday_status():
    import os
    return {'execution_enabled': False, 'passive_capture': os.getenv('FRIDAY_RESEARCH_ENABLED', '').lower() == 'true', 'latest': latest()}


@router.post('/ai-friday/research')
def friday_capture(request: Request):
    require_same_origin(request)
    return {'message': capture(latest_friday_scan()), **friday_status()}


async def research_worker():
    """Opt-in passive capture. Never initiate a scan or share a broker queue."""
    import asyncio
    import logging
    import sqlite3
    last_finished = None
    while True:
        scan = latest_friday_scan()
        if scan and scan[0] != last_finished:
            try:
                await asyncio.to_thread(capture, scan)
                last_finished = scan[0]
            except sqlite3.Error:
                logging.getLogger(__name__).exception('Friday research capture failed')
        await asyncio.sleep(15)
