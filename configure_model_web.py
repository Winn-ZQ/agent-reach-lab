"""一次性本机密钥配置页：仅监听127.0.0.1，不调用任何外部服务。"""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import secrets
import time
from urllib.parse import parse_qs

from configure_model import CONFIG, MODELS, base_url, save_new, validate_key

STYLE = '''
*{box-sizing:border-box}body{margin:0;background:#f4f6fa;color:#182235;font:16px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:660px;margin:7vh auto;padding:38px;background:white;border:1px solid #e1e6ef;border-radius:20px}
h1{font-size:28px;margin:8px 0 14px}p{color:#526077}label{display:block;font-weight:600;margin-top:22px}
input[type=password],input[type=text]{width:100%;padding:12px;border:1px solid #b9c5d5;border-radius:8px;font:inherit}
button{background:#2458cf;color:white;border:0;border-radius:9px;padding:13px 24px;font:inherit;cursor:pointer;width:100%;margin-top:25px}
.tag{font-size:13px;color:#286043;background:#e7f4ed;padding:5px 12px;border-radius:30px;display:inline-block}
.small{font-size:13px;color:#65718a}.models{background:#f3f6fc;border-radius:10px;padding:13px 18px}
@media(max-width:700px){main{margin:20px 14px;padding:24px}}
'''


def page(body):
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>资料助手 · 本机模型配置</title><style>' + STYLE + '</style>'
            '<main>' + body + '</main></html>').encode('utf-8')


def make_server(config_path=CONFIG, port=0, lifetime=900):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # URL、请求体和密钥均不写访问日志。

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def reply(self, code, body):
            payload = page(body)
            self.send_response(code)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            # WebKit 对 no-referrer 的普通表单POST可能发送 Origin:null，
            # 与严格同源校验冲突。same-origin保留本机提交来源，跨站仍不带引用。
            self.send_header('Referrer-Policy', 'same-origin')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy',
                             "default-src 'none'; style-src 'unsafe-inline'; "
                             "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def allowed(self):
            return (self.headers.get('Host') == self.server.host
                    and time.monotonic() < self.server.deadline)

        def do_GET(self):
            if not self.allowed():
                self.reply(403, '<h1>页面已过期或访问地址不正确</h1>')
                return
            if self.path == '/saved' and self.server.saved:
                self.reply(200, '<span class="tag">配置已保存</span><h1>本机配置完成</h1>'
                           '<p>密钥已保存在这台电脑上。没有调用模型，也没有向外部服务发送密钥。</p>'
                           '<p>现在返回聊天，告诉助手“已保存”。接下来将检查配置并接通模型。</p>'
                           '<p class="small">当前只授权使用免费额度。此窗口可以关闭。</p>')
                return
            if self.path != self.server.form_path or self.server.saved:
                self.reply(404, '<h1>此配置入口不可用</h1>')
                return
            if config_path.exists() or config_path.is_symlink():
                self.reply(409, '<h1>已存在配置</h1><p>为避免覆盖，已停止新配置。返回聊天检查现有配置。</p>')
                return
            self.reply(200, '<span class="tag">仅本机 · 不调用模型</span>'
                       '<h1>连接你的模型</h1><p>将百炼通用 API Key 保存到本机，供资料助手之后调用模型。</p>'
                       '<div class="models">千问 Qwen3.8-Flash<br>DeepSeek-V4.1-Flash<br>'
                       '<span class="small">百炼北京 · 先使用免费额度</span></div>'
                       '<form method="post" action="/save" autocomplete="off">'
                       '<input type="hidden" name="csrf" value="' + self.server.csrf + '">'
                       '<label for="key">百炼 API Key</label>'
                       '<input id="key" name="api_key" type="password" required minlength="16" maxlength="512" '
                       'autocomplete="off" spellcheck="false" placeholder="粘贴你创建的通用 API Key">'
                       '<label for="workspace">业务空间 ID（选填）</label>'
                       '<input id="workspace" name="workspace" type="text" maxlength="128" '
                       'autocomplete="off" placeholder="不知道可以留空">'
                       '<p class="small">留空时使用官方北京通用接口地址。</p>'
                       '<label><input name="free_only" type="checkbox" value="yes" required> '
                       '确认两款模型的“免费额度用完即停”均已开启并生效</label>'
                       '<button type="submit">保存到本机</button></form>'
                       '<p class="small">仅发送给这台电脑的本机服务，不经过聊天。密钥以明文保存到项目 '
                       '.local/model_credentials.json，仅当前用户可读写，已排除 Git 跟踪。'
                       '不要上传或分享 .local 文件夹。此页面15分钟内有效。</p>')

        def do_POST(self):
            rejection_checks = {
                'host_or_expired': not self.allowed(),
                'path': self.path != '/save',
                'origin': self.headers.get('Origin') != self.server.origin,
                'fetch_site': self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin'),
                'transfer_encoding': self.headers.get('Transfer-Encoding') is not None,
                'content_type': self.headers.get('Content-Type', '').split(';')[0] != 'application/x-www-form-urlencoded',
            }
            reasons = [name for name, rejected in rejection_checks.items() if rejected]
            if reasons:
                # 只打印固定诊断分类，不打印请求头、正文、密钥或任意用户输入。
                print('配置提交校验拒绝：' + ','.join(reasons), flush=True)
                self.reply(403, '<h1>请求被拒绝</h1><p>请从本机配置页提交。</p>')
                return
            if self.server.saved:
                self.reply(409, '<h1>配置已保存，请勿重复提交</h1>')
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 8192:
                    raise ValueError('invalid body size')
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError('incomplete body')
                fields = parse_qs(raw.decode('utf-8'), keep_blank_values=True, max_num_fields=4)
                if set(fields) != {'csrf', 'api_key', 'workspace', 'free_only'} or any(len(v) != 1 for v in fields.values()):
                    raise ValueError('invalid fields')
                if not secrets.compare_digest(fields['csrf'][0], self.server.csrf) or fields['free_only'] != ['yes']:
                    raise ValueError('invalid confirmation')
                key = fields['api_key'][0].strip()
                validate_key(key)
                if len(key) > 512:
                    raise ValueError('key too long')
                endpoint = base_url(fields['workspace'][0].strip())
                save_new(config_path, {'api_key': key, 'base_url': endpoint, 'models': MODELS,
                                      'free_only_user_confirmed': True, 'paid_calls_authorized': False})
            except (ValueError, OSError, UnicodeError):
                self.reply(400, '<h1>未能保存</h1><p>请检查输入及保护开关确认；已有配置不会被覆盖。'
                           '返回上一页重试，密钥不要发送到聊天。</p>')
                return
            self.server.saved = True
            self.server.deadline = min(self.server.deadline, time.monotonic() + 120)
            self.send_response(303)
            self.send_header('Location', '/saved')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', '0')
            self.end_headers()
            print('本机配置已保存；未发起模型API请求。', flush=True)

    server = HTTPServer(('127.0.0.1', port), Handler)
    server.host = f'127.0.0.1:{server.server_port}'
    server.origin = f'http://{server.host}'
    server.form_path = '/setup/' + secrets.token_urlsafe(24)
    server.csrf = secrets.token_urlsafe(32)
    server.deadline = time.monotonic() + lifetime
    server.saved = False
    server.timeout = 1
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    with make_server(port=args.port) as server:
        print('本机配置地址：' + server.origin + server.form_path, flush=True)
        try:
            while time.monotonic() < server.deadline:
                server.handle_request()
        except KeyboardInterrupt:
            pass
        print('配置页面服务已关闭。', flush=True)
