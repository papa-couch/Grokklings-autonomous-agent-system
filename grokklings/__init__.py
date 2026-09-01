"""Grokklings — a colony of workers that sorts through a stream of tasks.

Phase 1: intake -> dispatcher -> slots -> output, with a journal and deduplication.
"""

from .model import Contribution, Decision, SlotSpec, Task, TaskStatus, Verdict

__all__ = ["Contribution", "Decision", "SlotSpec", "Task", "TaskStatus", "Verdict"]
__version__ = "0.1.0"
