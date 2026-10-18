# -*- coding: utf-8 -*-
"""prio_test.py 的子进程：抢一次账号锁，占住一会儿，打印拿到的时刻。"""
import json
import os
import sys
import time

jid, tid, acc, hold, data, root = sys.argv[1:7]
os.environ["ORANTE_DATA"] = data
sys.path.insert(0, root)
from orante import locks  # noqa: E402


class J:
    def __init__(self):
        self.id, self.task_id, self.kind = jid, tid, "generate"

    def log(self, m):
        pass

    def check(self):
        pass


L = locks.AccountLock(acc, J())
L.acquire()
t = time.time()
time.sleep(float(hold))
L.release()
print(json.dumps({"task": tid, "t": t}))
