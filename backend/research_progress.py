"""Cooperative cancellation and observable stages, scoped to one answer request."""
from contextvars import ContextVar
from threading import Event

_current = ContextVar('research_control', default=None)


class ResearchCancelled(Exception):
    pass


class ResearchControl:
    def __init__(self, emit):
        self.cancelled = Event()
        self.emit = emit

    def check(self):
        if self.cancelled.is_set():
            raise ResearchCancelled()

    def stage(self, name, message):
        self.check()
        self.emit({'type': 'progress', 'stage': name, 'message': message})


def checkpoint():
    control = _current.get()
    if control:
        control.check()


def stage(name, message):
    control = _current.get()
    if control:
        control.stage(name, message)
