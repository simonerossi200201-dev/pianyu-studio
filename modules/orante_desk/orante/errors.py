# -*- coding: utf-8 -*-
class OranteError(Exception):
    """给人看的错误：message 说发生了什么，hint 说该怎么办。"""

    def __init__(self, message: str, hint: str = "", *, code: str = "", extra: dict | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code or ""
        self.extra = dict(extra or {})

    def to_dict(self):
        d = {"ok": False, "error": self.message, "hint": self.hint}
        if self.code:
            d["code"] = self.code
        if self.extra:
            d.update(self.extra)
        return d


class NeedRedownload(OranteError):
    """闸3：下载地址过不了闸。该段保持 submitted，重新取 URL，不算失败、不消耗重生次数。"""


class CreditHalted(OranteError):
    """余额守卫拦下提交。Job 状态是 halted（已拦停），不是 failed。段状态不动。"""

    def __init__(self, b0, b, recent=None):
        msg = f"Flow 余额从 {b0} 降到 {b}，Orante 已停止提交，先查 57 号报告"
        super().__init__(
            msg,
            "手动在 UI 里生成也会让余额降。点「我知道，继续」后本 Job 不再拦。",
        )
        self.b0 = b0
        self.b = b
        self.recent = list(recent or [])


class FlowThrottled(OranteError):
    """Google 把这个 Flow 账号暂时限流了。段不算失败，账号进入冷却，不要重试。"""

    def __init__(self, account_name: str = "", code: str = "",
                 err_n=None, raw: str = "", until: str = ""):
        name = account_name or "这个"
        msg = f"{name} 这个 Flow 账号被 Google 暂时限流了（短时间内提交太多）"
        super().__init__(
            msg,
            "这是 Google 那边的保护机制，不是软件的问题。等冷却结束或换一个 Flow 账号。",
            code="flow_throttled",
        )
        self.account_name = name
        self.flow_code = code or "PUBLIC_ERROR_UNUSUAL_ACTIVITY"
        self.err_n = err_n
        self.raw = raw or ""
        self.until = until or ""
