#!/usr/bin/env python3
"""CBMonitor 采集失败告警（复用 arbtest/health_monitor 的凭据读取方式，不新增配置源）。

用法：
  notify.py "消息文本"     发送 Telegram
  notify.py --check        只校验凭据可读，不发送（部署自检用）

由 collect_once.py 的 CB_ALERT_CMD 调用；凭据一律从
/home/ubuntu/account_info/secrets.py 读取，不落盘、不进日志。
"""
import sys
import importlib.util
import urllib.request
import urllib.parse

SECRETS = "/home/ubuntu/account_info/secrets.py"


def load_credentials():
    spec = importlib.util.spec_from_file_location("sec", SECRETS)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.get_secret("TELEGRAM_BOT_TOKEN", ""), m.get_secret("TELEGRAM_CHAT_ID", "")


def main() -> int:
    args = sys.argv[1:]
    try:
        token, chat = load_credentials()
    except Exception as e:
        print("读取凭据失败: %s" % e)
        return 1
    if not token or not chat:
        print("凭据缺失")
        return 1
    if args and args[0] == "--check":
        print("CREDENTIALS_OK")
        return 0

    text = "[CBMonitor] " + " ".join(args)
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "cbmonitor"})
        r = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        ok = '"ok":true' in r or '"ok": true' in r
        print("TG_OK" if ok else "TG_FAIL")
        return 0 if ok else 1
    except Exception as e:
        print("发送失败: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
