"""Rate limiting. In-memory, so it is per server process (fine for one worker; use Redis for many)."""
import threading, time
from collections import defaultdict, deque

class RateLimiter:
    def __init__(self, max_hits, window_sec, clock=time.monotonic):
        self.max, self.window, self.clock = max_hits, window_sec, clock
        self.hits, self.lock = defaultdict(deque), threading.Lock()
    def allow(self, key):
        with self.lock:
            q, t = self.hits[key], self.clock()
            while q and q[0] <= t - self.window: q.popleft()
            if len(q) >= self.max: return False
            q.append(t); return True
    def retry_after(self, key):
        with self.lock:
            q = self.hits[key]
            return max(1, int(q[0] + self.window - self.clock())) if q else 1

class LoginThrottle:
    """Blocks a (ip, username) pair after too many FAILED logins within the window."""
    def __init__(self, max_fails, window_sec, clock=time.monotonic):
        self.max, self.window, self.clock = max_fails, window_sec, clock
        self.fails, self.lock = defaultdict(deque), threading.Lock()
    def _prune(self, key):
        q, t = self.fails[key], self.clock()
        while q and q[0] <= t - self.window: q.popleft()
        return q
    def blocked(self, key):
        with self.lock: return len(self._prune(key)) >= self.max
    def fail(self, key):
        with self.lock: self._prune(key).append(self.clock())
    def reset(self, key):
        with self.lock: self.fails.pop(key, None)
