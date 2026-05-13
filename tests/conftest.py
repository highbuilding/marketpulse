import os

# 阻止 lifespan 在测试中触发真实 akshare 拉取 A 股目录
os.environ["MARKETPULSE_SKIP_DIR_BOOTSTRAP"] = "1"
