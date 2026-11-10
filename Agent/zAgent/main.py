"""zAgent 启动脚本：python main.py [--ui tui|cli]"""

import sys
from pathlib import Path

# 直接运行 python main.py 时，sys.path[0] 是脚本所在目录 zAgent/，
# 其中并没有 zagent 子包；把父目录 Agent/ 加入搜索路径后，
# 才能按真实包名 zAgent 导入 zAgent.bootstrap。
_PARENT_DIR = Path(__file__).resolve().parent.parent
if str(_PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(_PARENT_DIR))

from zAgent.bootstrap import main

if __name__ == "__main__":
    raise SystemExit(main())
