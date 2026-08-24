"""Отдельный процесс эмулятора: python -m sue.emulator --port 8001"""

from __future__ import annotations

import argparse

import uvicorn
from fastapi import FastAPI

from sue import __version__
from sue.adapter_1c import CONTRACT_VERSION
from sue.emulator.service import router


def build_app() -> FastAPI:
    app = FastAPI(
        title="Эмулятор выгрузки 1С:Розница",
        description=(
            "Учебный HTTP-сервис обмена по контракту 2.0. "
            "Не является подключением к действующей информационной базе 1С."
        ),
        version=__version__,
    )
    app.include_router(router)
    return app


app = build_app()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Эмулятор HTTP-выгрузки 1С:Розница")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args(argv)
    print(
        f"Эмулятор выгрузки 1С (контракт {CONTRACT_VERSION}) "
        f"http://{args.host}:{args.port}/hs/exchange/ping"
    )
    uvicorn.run("sue.emulator.__main__:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
