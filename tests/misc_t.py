
import sys,os,pytest,math
import pandas as pd

# add deps folder (relative path to this module)
sys.path.append(os.path.realpath(os.path.dirname( __file__[:-1] if __file__.endswith('.pyc') else __file__ ) +os.sep+".."))

from testsuit.misc.MonitorProgress import MonitorProgress,consoleSilentProgressCb,consoleProgressCb


def test_MonitorProgressNoThread():
    """Ensure MonitorProgress works properly"""

    mp=MonitorProgress(name="global",total_items=4,progressCb=consoleProgressCb)

    mp.complete_n(1)
    assert(mp.get_percent()==25)

    sub1 = mp.child("sub1",4)
    sub1.complete_n(3)
    mp.msg(f"### main={mp.get_percent()} sub1={sub1.get_percent()}")
    assert(sub1.get_percent()==75)
    assert(mp.get_percent()==43.75)
    
    sub2 = mp.child("sub2")
    sub2.set_total_items(10)
    
    mp.msg(f"### main={mp.get_percent()} sub1={sub1.get_percent()} sub2={sub2.get_percent()}")
    assert(mp.get_percent()==43.75)
    assert(sub1.get_percent()==75)
    assert(sub2.get_percent()==0)
    
    sub2.complete_n(1)
    mp.msg(f"### main={mp.get_percent()} sub1={sub1.get_percent()} sub2={sub2.get_percent()}")
    assert(mp.get_percent()==46.25)
    assert(sub1.get_percent()==75)
    assert(sub2.get_percent()==10)
    
    sub2.complete_n(3)
    mp.msg(f"### main={mp.get_percent()} sub1={sub1.get_percent()} sub2={sub2.get_percent()}")
    assert(mp.get_percent()==53.75)
    assert(sub1.get_percent()==75)
    assert(sub2.get_percent()==40)
    
    sub1.complete_n(1)
    mp.msg(f"### main={mp.get_percent()} sub1={sub1.get_percent()} sub2={sub2.get_percent()}")
    assert(mp.get_percent()==60)
    assert(sub1.get_percent()==100)
    assert(sub2.get_percent()==40)
    
    
    sub2.complete_n(6)
    mp.msg(f"### main={mp.get_percent()} sub1={sub1.get_percent()} sub2={sub2.get_percent()}")
    assert(mp.get_percent()==75)
    assert(sub1.get_percent()==100)
    assert(sub2.get_percent()==100)
    
    mp.complete_n(1)
    mp.msg(f"### main={mp.get_percent()} sub1={sub1.get_percent()} sub2={sub2.get_percent()}")
    assert(mp.get_percent()==100)
    assert(sub1.get_percent()==100)
    assert(sub2.get_percent()==100)
    
    #import time
    #time.sleep(1)
    
def test_MonitorProgressMT():
    """Ensure MonitorProgress works properly in multi-threading"""

    import threading

    mp = MonitorProgress(name="global", total_items=4, progressCb=consoleSilentProgressCb)

    mp.complete_n(1)
    assert mp.get_percent() == 25

    sub1 = mp.child("sub1", 4)
    sub2 = mp.child("sub2")
    sub2.set_total_items(10)

    def run_sub1():
        sub1.complete_n(3)
        sub1.complete_n(1)

    def run_sub2():
        sub2.complete_n(1)
        sub2.complete_n(3)
        sub2.complete_n(6)

    t1 = threading.Thread(target=run_sub1)
    t2 = threading.Thread(target=run_sub2)

    t1.start()
    t2.start()

    t1.join()
    t2.join()

    mp.complete_n(1)
    mp.msg(f"### main={mp.get_percent()} sub1={sub1.get_percent()} sub2={sub2.get_percent()}")
    assert mp.get_percent() == 100
    assert sub1.get_percent() == 100
    assert sub2.get_percent() == 100