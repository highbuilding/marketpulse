"""mini_racer (akshare 内部 JS 解析) 在并发调用时会 crash 进程,
所有需要 mini_racer 的 akshare 接口共用此 Lock 串行化。

涉及接口:stock_zh_a_spot / fund_etf_category_sina /
        stock_sector_spot / stock_sector_detail 等。
"""
import asyncio

mini_racer_lock = asyncio.Lock()
