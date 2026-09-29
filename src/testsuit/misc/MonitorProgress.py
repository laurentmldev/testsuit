"""Thread-safe, nestable progress tracking (MonitorProgress) and the console callbacks that display it.

A progress callback has the signature cb(percent, msg, msgSeverity): percent is None for a plain message,
msg is None, a string or a list of strings, and msgSeverity is one of success|info|warn|warning|error|secondary.
"""
import threading
import queue
import sys

from collections.abc import Callable

# signature of the functions receiving progress updates, see module docstring
ProgressCallback = Callable[[float | None, str | list | None, str | None], None]


def consoleProgressCb(
    percent: float | None = None,
    msg: str | list | None = None,
    msgSeverity: str = "info"
) -> None:
    """Print each progress update on its own line ("[severity] percent% message"); errors go to stderr.

    :param percent: progress percent, or None for a plain message
    :param msg: message, or list of messages printed on separate lines
    :param msgSeverity: success|info|warn|error|secondary
    """

    if sys.is_finalizing():
        return
    
    if (msg is None or len(msg)==0 or len(msg)==1 and len(msg[0])==0) and percent is None:
        return
    
    stdfile = sys.stderr if msgSeverity == "error" else sys.stdout
    
    percentStr=""
    if percent is not None:
        percentStr=f"{percent:.2f}%"
    msgStr=msg
    if isinstance(msg, list):
        msgStr=" \n - ".join(msg)
    
    
    print("[" + msgSeverity + f"] {percentStr} {msgStr}", file=stdfile, flush=True)


def consoleSilentProgressCb(
    percent: float | None = None,
    msg: str | list | None = None,
    msgSeverity: str = "info"
) -> None:
    """Like consoleProgressCb, but only prints "error" and "warn" messages."""

    if sys.is_finalizing():
        return
    
    if msgSeverity!="error" and msgSeverity!="warn": return 
    consoleProgressCb(percent,msg,msgSeverity)
    
def consoleRichProgressCb(
    percent: float | None = None,
    msg: str | list | None = None,
    msgSeverity: str = "info"
) -> None:
    """Display progress as a single progress bar line rewritten in place.

    error/warn/success messages are printed on their own line. Same parameters as consoleProgressCb.
    """
    
    MAX_MSG_LEN=120
    
    if sys.is_finalizing():
        return
    
    # Thread-local storage for per-thread line-length tracking
    if not hasattr(consoleRichProgressCb, '_tls'):
        consoleRichProgressCb._tls = threading.local()
    tls = consoleRichProgressCb._tls

    if not hasattr(tls, 'prev_percent'):
        tls.prev_percent = 0

    stdfile = sys.stderr if msgSeverity == "error" else sys.stdout
    if not percent: 
        percent = tls.prev_percent
    else:
        tls.prev_percent = percent

    msgStr=""    
    if isinstance(msg, list):
            msgStr= " - ".join(msg)
    else:
        msgStr=str(msg)
    
    msgStr = msgStr.replace("\r", "").replace("\n", " ").strip()
    
    if len(msgStr.strip())>0:
        msgStr="[" + msgSeverity + "] " + msgStr.strip()

    if "error" in msgSeverity or "warn" in msgSeverity or "success" in msgSeverity:
        print("\n"+msgStr, file=stdfile, flush=True)
        return
          
    bar_len = 50
    filled = int(bar_len * percent / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    endStr = "\n" if percent == 100 else ""
    textStr = "\t" + msgStr
    line = f"\r[{bar}] {(percent):.1f}% {textStr}{endStr}"
    
    if len(line)>MAX_MSG_LEN:
        line=line[:MAX_MSG_LEN-4]+"..."
        
    if len(msgStr)>0:
        line += " " * (MAX_MSG_LEN - len(line))
    
    print(line, file=stdfile, flush=True, end="")


#####################################################################################



class MonitorProgress:
    """
    Thread-safe progress tracker for N items to be completed in parallel, with recursive nesting.
    
    Call flow is following:
    1. create root myProgressTracker=MonitorProgress(<nb steps at this level>, name="Name")
    2.a acknowledge completion of a step: myProgressTracker.complete_item("My Item Name")
    2.b or acknowledge completion of n anonymous steps at once: myProgressTracker.complete_n(3)
    3.c or consider 1 step as a complexe task to be subdivided: 
            mySubTracker = myProgressTracker.child("My SubTask Title")
            some_complex_function(...,monitorProgress=mySubTracker)
            
        Then some_complex_function is in charge to define how many sub-steps it will need to complete this big step:
        def some_complex_function(...,monitorProgress):
            monitorProgress.set_total_items(4)
            ...
            monitorProgress.complete_n(1)
            ... etc
            
    Each completion (or just simple msg) is stored in a thread-safe queue.
    A dedicated Thread dequeue the messages and forward them to some 'progressCallback' functions to handle them.
    
    3 progressCb are provided at the top of this file (consoleProgressCb, consoleSilentProgressCb,
    consoleRichProgressCb). Only the top-level monitor's callback is used: children forward everything to their parent.

    Note: to debug your progress pbs, activate 'debug' flag, it will dump state of each subMonitor at every step
    """

    def __init__(
        self,
        total_items: int | None = None,
        progressCb: ProgressCallback = consoleProgressCb,
        parent: "MonitorProgress | None" = None,
        name: str | None = None,
        allowOverTotalItems: bool = False,
        debug: bool = False, # use this flag for debug/investigate
    ) -> None:
        """
        :param total_items: number of steps at this level (can be set later with set_total_items())
        :param progressCb: receives the progress updates (top-level monitor only)
        :param parent: parent monitor; use parent.child() rather than passing it directly
        :param name: name shown in messages and in dumpProgressTree()
        :param allowOverTotalItems: complete_n() beyond total_items caps at 100% instead of raising
        :param debug: dump the whole progress tree at every update
        """
        # enfore case when explicitly called with null progressCb
        if not progressCb: progressCb=consoleProgressCb
        
        self._total: int | None = None
        if total_items is not None:
            self._total=int(total_items)
        self._cb: ProgressCallback = progressCb
        self._completed_ids: set[str] = set()
        self._completed_count: int = 0
        self._lock: threading.RLock = threading.RLock()
        self._parent: MonitorProgress | None = parent
        self._name: str | None = name
        self._sub_monitors: dict[str, MonitorProgress] = {}
        self._allowOverTotalItems=allowOverTotalItems
        self._debug=debug
        
        # --- Dedicated flush thread for top-level monitors ---
        self._is_top_level = parent is None
        self._msg_queue: queue.Queue | None = None
        self._flush_thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._closed = False

        if self._is_top_level:
            self._msg_queue = queue.Queue()
            self._stop_event = threading.Event()
            self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
            self._flush_thread.start()

    # ------------------------------------------------------------------
    # Flush thread
    # ------------------------------------------------------------------

    def _flush_loop(self) -> None:
        """Background thread: drain the queue and call _cb."""
        
        while not self._stop_event.is_set():
            if sys.is_finalizing():
                return
            try:
                item = self._msg_queue.get(timeout=0.4)                
            except queue.Empty:
                continue
            # Drain all currently queued items
            items = [item]
            while True:
                try:
                    items.append(self._msg_queue.get_nowait())
                except queue.Empty:
                    break
            if sys.is_finalizing():
                return
            for percent, msg, msgSeverity in items:
                if sys.is_finalizing():
                    return
                self._cb(percent, msg, msgSeverity)

                
        # Final drain on shutdown
        while True:
            if sys.is_finalizing():
                return
            try:
                item = self._msg_queue.get_nowait()
            except queue.Empty:
                break
            if sys.is_finalizing():
                return
            self._cb(*item)
            
        self._cb(self.get_percent(), msg=None, msgSeverity=None)
                
    def _post_msg(self, percent: float | None, msg: list | None, msgSeverity: str) -> None:
        """Queue an update for the flush thread (top-level monitor only)."""
        self._msg_queue.put((percent, msg, msgSeverity))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _percent_unlocked(self) -> float | None:
        """Calculate overall percent, None while this monitor or a child has no total. MUST be called with self._lock held."""
        
        if self._total is None:
            return None
        
        if self._total == 0:
            return 100.0
        
        progress = float(len(self._completed_ids)) + self._completed_count
        
        # Add progress from sub-monitors
        for sub in self._sub_monitors.values():
            with sub._lock:
                subPercent=sub._percent_unlocked()
                if subPercent is None: 
                    return None
                progress += subPercent / 100.0
                
        return (progress / self._total) * 100.0

    def _propagate(self, msg: str, msgSeverity: str, update_percent: bool = False) -> None:
        """Propagate a message/progress update to parent or post to the queue.
        
        When update_percent is True, recalculate our own percent and bubble it up.
        When False, propagate message-only (no percent recalculation).
        """
        if update_percent:
            with self._lock:
                percent = self._percent_unlocked()
        else:
            percent = None

        if self._parent is not None:
            self._parent._propagate(msg, msgSeverity, update_percent=update_percent)
        else:
            msg=[msg] if not isinstance(msg,list) and msg is not None else msg
            self._post_msg(percent=percent, msg=msg, msgSeverity=msgSeverity)
            
            if self._debug: self.dumpProgressTree()
        
    def close(self) -> None:
        """Explicitly mark parent item as completed and detach."""
        if self._closed:
            return
        self._closed = True

        if self._parent is not None and self._name is not None:
            
            if self._total is None:
                self._total=1
            
            self._parent.complete_item(self._name)
            self._parent = None
            self._name = None

        # Stop the flush thread for top-level monitors
        if self._is_top_level and self._stop_event is not None:
            self._stop_event.set()
            self._flush_thread.join(timeout=2.0)
            
    def _dumpProgressTreeRecursive(self, monitor: "MonitorProgress", prefix: str) -> None:
        """Internal recursive helper for dumpProgressTree."""
        with monitor._lock:
            name = monitor._name if monitor._name else "root"
            total = monitor._total if monitor._total is not None else "N/A"
            try:
                pct = monitor._percent_unlocked()
            except Exception:
                pct = -1.0
            completed = len(monitor._completed_ids) + monitor._completed_count
            subs = list(monitor._sub_monitors.values())
            
        pct_str = f"{pct:.1f}%" if pct is not None and pct >= 0 else "N/A"
        treeChar="O──" if monitor._is_top_level else "├──"
        # done inside = steps directly marked as completed (not coming from a child monitorProgress)
        print(f"{prefix}{treeChar} {name} ({pct_str} | expected: {total} | directly done: {completed})", flush=True)
        
        for i, sub in enumerate(subs):
            is_last = i == len(subs) - 1
            extension = "    " if is_last else "│   "
            self._dumpProgressTreeRecursive(sub, prefix + extension)

    def __enter__(self) -> "MonitorProgress":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
        
    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_name(self) -> str | None:
        return self._name
    
    def get_total_items(self) -> int | None:
        return self._total
    
    def set_total_items(self, total_items: int, resetTotalItems: bool = False) -> None:
        """Set the number of steps at this level.

        :raises Exception: if already set, unless resetTotalItems is True
        """
        if self._total is not None and not resetTotalItems:
            raise Exception(f"cannot set_total_items of {self._name} to {total_items}: already set to {self._total}")
        #print(f"#### MonitorProgress {self._name} total items set to {total_items}")
        self._total=int(total_items)
            
    def complete_item(self, item_id: str, msg: str = "", msgSeverity: str = "info") -> None:
        """Mark one item as completed. Thread-safe. Idempotent (safe to call twice)."""
        
        if self._total is None:
            raise Exception("cannot complete progress: total items not provided (yet)")
        with self._lock:
            if self.is_complete:
                raise Exception(f"{self.get_name()} progress already fully completed, cannot complete additional item '{item_id}'")
            self._completed_ids.add(item_id)
        self._propagate(msg, msgSeverity, update_percent=True)
        
    def complete_n(self, n: int, msg: str = "", msgSeverity: str = "info") -> None:
        """Mark n anonymous items as completed. Thread-safe."""
        
        if self._total is None:
            raise Exception("cannot complete progress: total items not provided (yet)")
        
        if n < 0:
            raise ValueError("n must be >= 0")
        with self._lock:
            if self._completed_count + n > self._total:
                if self._allowOverTotalItems:
                    self._completed_count=self._total
                else:
                    raise Exception(f"{self.get_name()}: cannot complete additional {n} items: progress would be {self._completed_count + n} (greater than total {self._total})")
            else:
                self._completed_count += n
        self._propagate(msg, msgSeverity, update_percent=True)        
        
    def complete_all(self,msg: str = "", msgSeverity: str = "info") -> None:
        """Mark all items as completed. Thread-safe. Idempotent (safe to call twice)."""
        
        if self._total is None:
            raise Exception("cannot complete progress: total items not provided (yet)")
        
        with self._lock:
            self._completed_count=self._total
        self._propagate(msg, msgSeverity, update_percent=True)
        
    def msg(self, msg: str | list = "", msgSeverity: str = "info") -> None:
        """Post a message without updating progress. Works even if total_items is not set."""
        if not isinstance(msg,(str,list)):
            raise Exception(f"expected str or list, got {type(msg)}")
    
        self._propagate(msg, msgSeverity, update_percent=False)

    def child(
        self,
        name: str,
        total_sub_items: int | None = None,
        progressCb: ProgressCallback | None = None,
        allowOverTotalItems: bool = False,
        renameIfExist: bool = False
    ) -> "MonitorProgress":
        """Create a child monitor counting as one step of this one: the step completes when the child closes
        (or its own steps are all done).

        :param name: child name, unique among this monitor's children
        :param total_sub_items: number of steps of the child (can be set later)
        :param progressCb: kept for compatibility; children forward their updates to this monitor
        :param renameIfExist: suffix the name with a number instead of raising if it is already used
        """
        
        if name in self._sub_monitors:
            if renameIfExist:
                name=name+f"_{len(self._sub_monitors)+1}"
            else:
                raise Exception(f"{self.get_name()}: cannot create child MonitorProgress, name already used: '{name}'")
        
        if progressCb is None:
            progressCb = lambda percent, msg, msgSeverity: self._propagate(msg, msgSeverity, update_percent=percent is not None)

        sub = MonitorProgress(
            total_sub_items, progressCb,
            parent=self, name=name, allowOverTotalItems=allowOverTotalItems
        )
        with self._lock:
            self._sub_monitors[name] = sub
        return sub

    def get_percent(self) -> float | None:
        """Return current overall completion percent (0-100), None if some total is not set yet. Thread-safe."""
        with self._lock:
            return self._percent_unlocked()
        
    def set_debug(self, debug: bool) -> None:
        self._debug=debug

    @property
    def is_complete(self) -> bool:
        """Return True when all items (including sub-monitors) are done."""        
        return self._total is not None and self.get_percent() >= 100.0
    
    def dumpProgressTree(self, prefix: str = "", fromRoot: bool = True) -> None:
        """Dump full current progress tree to console for debugging."""
        if fromRoot:
            root = self
            while root._parent is not None:
                root = root._parent
            self._dumpProgressTreeRecursive(root, "")
        else:
            self._dumpProgressTreeRecursive(self, prefix)
