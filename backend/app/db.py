import os, sqlite3
from contextlib import contextmanager
from pathlib import Path

def db_path() -> Path:
    d = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "pantryfifo.db"

def connect():
    # autocommit(isolation_level=None): 多步写入一律走 write_tx 显式事务;
    # timeout: 并发写(消费 vs 消费 / 消费 vs 过期下架)争行时等待而非立刻报 locked。
    c = sqlite3.connect(db_path(), timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    return c

@contextmanager
def write_tx(c):
    """BEGIN IMMEDIATE: 进事务即取写锁, 事务内的读快照与写入对其它写者原子。

    这样"读到 on_shelf → 计算扣减 → 落库"之间不会被另一笔消费或
    过期下架插入, 资格判定看到的行状态就是落库时的行状态。
    """
    c.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        c.execute("ROLLBACK")
        raise
    else:
        c.execute("COMMIT")
