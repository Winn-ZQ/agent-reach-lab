"""研究调度器和响应来源之间的最小接口；不含网络和凭据。"""
from abc import ABC, abstractmethod

MAX_INPUT_BYTES = 180000
MAX_OUTPUT_TOKENS = 4096
TOKEN_OVERHEAD = 1024
REVIEW_CAPACITY = MAX_INPUT_BYTES + MAX_OUTPUT_TOKENS + TOKEN_OVERHEAD


class BackendStopped(Exception):
    """code须由程序生成，不携带供应商错误或凭据。"""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ResponseBackend(ABC):
    mode = 'unconfigured'
    provenance = ''
    binding_scope = 'unconfigured'

    def check_scope(self, case_hash):
        pass

    @abstractmethod
    def respond(self, stage, messages, reserve_review=False):
        raise NotImplementedError

    @abstractmethod
    def snapshot(self):
        raise NotImplementedError
