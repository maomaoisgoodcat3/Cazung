import uvicorn

from .config import load_config


def main():
    cfg = load_config()
    uvicorn.run("cazung.main:app", host=cfg.server.host, port=int(cfg.server.port), log_level="warning")


if __name__ == "__main__":
    main()
