from fastapi import Query, Request
from fastapi.responses import JSONResponse

from adaptive_scanner_research import export_adaptive_scanner_dataset


def register_adaptive_research_routes(
    app,
    require_session,
):
    @app.get("/auto-trader/adaptive-research-export")
    def adaptive_research_export(
        request: Request,
        horizon_minutes: int = Query(default=15, ge=1, le=1440),
        limit: int = Query(default=5000, ge=1, le=10000),
    ):
        require_session(request)

        data = export_adaptive_scanner_dataset(
            horizon_minutes=horizon_minutes,
            limit=limit,
        )

        return JSONResponse(
            content=data,
            headers={
                "Cache-Control": "no-store, private",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )
