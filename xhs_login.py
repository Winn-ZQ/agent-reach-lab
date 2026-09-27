"""显式生成本机小红书登录二维码，由用户扫码；不读取浏览器Cookie。"""
import base64
import os
from pathlib import Path
import time

from collect_xhs import ROOT, XhsError, call_tool, content_text
from configure_model import private_directory


def create_qrcode(client=call_tool, directory=ROOT / '.local/xhs-service'):
    payload = client('get_login_qrcode', {})
    for block in payload.get('content', []):
        if isinstance(block, dict) and block.get('type') == 'image' and block.get('mimeType') == 'image/png':
            try:
                data = base64.b64decode(block['data'], validate=True)
            except (ValueError, KeyError):
                raise XhsError('invalid_login_image') from None
            if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data) > 2_000_000:
                raise XhsError('invalid_login_image')
            directory = Path(directory); private_directory(directory)
            path = directory / f'login-{time.time_ns()}.png'
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream: stream.write(data)
            return path
    if '已处于登录状态' in content_text(payload):
        return None
    raise XhsError('login_image_missing')


if __name__ == '__main__':
    try:
        path = create_qrcode()
        print(str(path) if path else '已登录，无需重新扫码。')
    except XhsError as exc:
        print('登录入口暂不可用：' + str(exc)); raise SystemExit(2)
