from decimal import Decimal
from apps.collector.ashare.quote_bar_ticker import BucketState, update_bucket


def test_new_bucket_sets_open():
    st = update_bucket(None, Decimal("100"), volume=5)
    assert st.open == st.high == st.low == st.close == Decimal("100")
    assert st.volume == 5


def test_update_tracks_high_low_close():
    st = update_bucket(None, Decimal("100"), volume=5)
    st = update_bucket(st, Decimal("105"), volume=8)
    st = update_bucket(st, Decimal("98"), volume=12)
    assert st.open == Decimal("100")
    assert st.high == Decimal("105")
    assert st.low == Decimal("98")
    assert st.close == Decimal("98")
    assert st.volume == 12  # 累计 volume 取最新


def test_baseline_seeds_ohlc():
    # 用更小周期 bar 算出的基线初始化(重启/中途订阅)
    base = BucketState(open=Decimal("90"), high=Decimal("110"),
                       low=Decimal("88"), close=Decimal("95"), volume=100)
    st = update_bucket(base, Decimal("112"), volume=120)
    assert st.open == Decimal("90")   # open 保持基线
    assert st.high == Decimal("112")  # 新高
    assert st.low == Decimal("88")
    assert st.close == Decimal("112")
