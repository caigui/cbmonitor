# -*- coding: utf-8 -*-
"""
钉钉机器人通知工具（cbmonitor 自包含版，纯标准库实现，不依赖 dingtalkchatbot SDK）

复用与 etfarb-master/backend/dingtalk_utils.py 相同的钉钉群机器人 webhook / secret，
保持 C 策略集提醒与其它提醒进同一个群。

静默窗口：12:30–13:00 不发送（与既有约定一致），send_alert 返回 False。
"""
import base64
import datetime
import hashlib
import hmac
import json
import urllib.parse
import urllib.request

WEBHOOK = 'https://oapi.dingtalk.com/robot/send?access_token=7cc49fb6a3b71f21dd3c0b85db4982067f1fa2eb264e20e4d2b1150d29745999'
SECRET = 'SEC10d71ad2ff4ebc9c55c8e542318dbc6ad81a5a2390c29d81bd4b04abb89a2dda'

_SILENT_START = datetime.time(12, 30)
_SILENT_END = datetime.time(13, 0)


class DingTalkNotifier:
    def __init__(self, webhook=WEBHOOK, secret=SECRET):
        self.webhook = webhook
        self.secret = secret

    def _signed_url(self):
        if not self.secret:
            return self.webhook
        ts = str(int(round(datetime.datetime.now().timestamp() * 1000)))
        secret_enc = self.secret.encode('utf-8')
        string_to_sign = '%s\n%s' % (ts, self.secret)
        string_to_sign_enc = string_to_sign.encode('utf-8')
        hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return '%s&timestamp=%s&sign=%s' % (self.webhook, ts, sign)

    def _post_markdown(self, title, text, at_all=True):
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": text},
            "at": {"isAtAll": bool(at_all)},
        }
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            self._signed_url(), data=data,
            headers={"Content-Type": "application/json;charset=utf-8"})
        # 显式绕过系统代理（本机常配代理，走代理打钉钉会失败）
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=15) as r:
            return r.read().decode('utf-8', 'ignore')

    def send_alert(self, message, title):
        """发送 markdown 消息。返回 True=已发送；False=静默窗口或失败。"""
        now = datetime.datetime.now().time()
        if _SILENT_START <= now < _SILENT_END:
            return False
        try:
            self._post_markdown(title, message, at_all=True)
            return True
        except Exception as e:
            print("[DingTalk] 发送失败: %s" % e)
            return False
