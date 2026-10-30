import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from xhs_collector import keepawake
from xhs_collector.adb import Adb, AdbError, list_devices
from xhs_collector.service import CollectorService, ParamError, parse_params

SERIAL = re.compile(r"^[A-Za-z0-9._:\-]{1,40}$")


def _device(line: str) -> dict:
    parts = line.split()
    model = next((p.split(":", 1)[1] for p in parts if p.startswith("model:")), "")
    return {"serial": parts[0], "state": parts[1] if len(parts) > 1 else "", "model": model}


def install(app: FastAPI, state: dict, add_items, out_dir: Path, service: CollectorService | None = None) -> CollectorService:
    """add_items(ids, category) 是协程函数：把商品加入监控池并触发首次抓取。"""
    svc = service or CollectorService(out_dir)
    state["collector"] = svc

    def check_serial(serial: str | None) -> str | None:
        if serial and not SERIAL.match(serial):
            raise HTTPException(400, "设备序列号不合法")
        return serial or None

    @app.get("/api/collector/status")
    def status():
        return svc.status()

    @app.post("/api/collector/start")
    async def start(body: dict = Body(...)):
        loop = asyncio.get_running_loop()
        try:
            params = parse_params(body)
            check_serial(params.serial)
            svc.start(params, on_finish=lambda records, category: asyncio.run_coroutine_threadsafe(
                add_items([r.item_id for r in records], category), loop).result(timeout=60))
        except ParamError as e:
            raise HTTPException(400, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        return {"started": True}

    @app.post("/api/collector/control/{action}")
    def control(action: str):
        if action not in ("stop", "pause", "resume"):
            raise HTTPException(404)
        getattr(svc, action)()
        return {"ok": True}

    @app.get("/api/collector/records")
    def records():
        return svc.records()

    @app.get("/api/collector/download")
    def download(kind: str = "xlsx"):
        if kind not in ("csv", "xlsx"):
            raise HTTPException(400, "格式只能是 csv 或 xlsx")
        try:
            path = svc.export(kind)
        except ParamError as e:
            raise HTTPException(400, str(e)) from e
        return FileResponse(path, filename=f"小红书采集结果-{datetime.now():%Y%m%d-%H%M%S}.{kind}")

    @app.get("/api/collector/devices")
    def devices():
        try:
            return [_device(l) for l in list_devices()]
        except Exception as e:
            raise HTTPException(503, f"读取设备失败：{type(e).__name__}") from e

    @app.get("/api/collector/screenshot")
    def screenshot(serial: str | None = None):
        try:
            data = Adb(check_serial(serial)).screenshot_bytes()
        except AdbError as e:
            raise HTTPException(503, str(e)) from e
        return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/collector/awake")
    def awake_status(serial: str | None = None):
        try:
            return keepawake.status(Adb(check_serial(serial)))
        except AdbError as e:
            raise HTTPException(503, str(e)) from e

    @app.post("/api/collector/awake")
    def awake_set(body: dict = Body(...)):
        try:
            adb = Adb(check_serial(body.get("serial")))
            if body.get("on"):
                keepawake.enable(adb, out_dir / "awake.json")
                adb.shell("input keyevent KEYCODE_WAKEUP")
            else:
                keepawake.restore(adb, out_dir / "awake.json")
            return keepawake.status(adb)
        except AdbError as e:
            raise HTTPException(503, str(e)) from e

    return svc
