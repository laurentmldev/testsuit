


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

def test_monitor_progress_shares_one_thread_and_keeps_order():
    import threading
    from testsuit.misc.MonitorProgress import MonitorProgress

    received = []
    def cb(percent, msg, msgSeverity):
        received.append((percent, msg))

    MonitorProgress(name="warmup", progressCb=cb).close()
    threads_before = threading.active_count()
    monitors = [MonitorProgress(total_items=2, name=f"m{i}", progressCb=cb) for i in range(50)]
    assert threading.active_count() == threads_before

    received.clear()
    mp = monitors[0]
    mp.complete_n(1, msg="first")
    mp.complete_n(1, msg="second")
    mp.close()
    # close() waits until pending updates and the final one are delivered, in order
    assert received == [(50.0, ["first"]), (100.0, ["second"]), (100.0, None)]


def test_get_logger_without_create_logger(tmp_path):
    """A library or unit test using testsuit without calling create_logger() gets a default logger
    (it used to raise 'no logger defined!')."""
    import subprocess,sys
    (tmp_path / "broken.h5").write_text("not an HDF5 file")
    code = ("from testsuit.misc.logger import get_logger\n"
            "from testsuit.datatools.DataFileMgrs.FolderParamMgr import FolderParamMgr\n"
            f"FolderParamMgr({str(tmp_path)!r},ignoreCorruptedFile=True)\n"
            "assert get_logger().name=='testsuit'\n")
    proc = subprocess.run([sys.executable,"-c",code],capture_output=True,text=True)
    assert proc.returncode==0, proc.stderr
    assert "Unable to load 1/1 file(s)" in proc.stdout
