"""Execution-local metadata lookup under the existing exclusive run lock.

Only lookup is cached. Reusing a response still reads and verifies its body via
RunStore.get_response. No receipt, reservation, or authority proof is modified.
"""
from collections import defaultdict


class ReceiptLookup:
    def __init__(self, receipts):
        self.pages = defaultdict(list)
        self.acp = defaultdict(list)
        for receipt in receipts:
            self.add(receipt)

    def add(self, receipt):
        if receipt.get('access_state') != 'NORMAL':
            return
        if receipt.get('kind') == 'PAGE':
            self.pages[receipt.get('request_url')].append(receipt)
        elif receipt.get('kind') == 'ACP':
            self.acp[receipt.get('page_key')].append(receipt)
