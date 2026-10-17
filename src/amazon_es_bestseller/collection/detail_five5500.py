"""Five lanes (three plus two) share one queue, dispatch lock and stop signal."""
from .detail_dual5500 import TwoRequestScheduler


class FiveRequestScheduler(TwoRequestScheduler):
    lane_count = 5
