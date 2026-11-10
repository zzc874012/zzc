"""支持 python -m zAgent 启动。"""

from .bootstrap import main

if __name__ == "__main__":
    raise SystemExit(main())
