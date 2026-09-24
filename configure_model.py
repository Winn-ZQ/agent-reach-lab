"""本机保存模型凭据，或只检查配置状态；不联网、不显示密钥。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / '.local'
CONFIG = PRIVATE / 'model_credentials.json'
MODELS = ['qwen3.8-flash', 'deepseek-v4.1-flash']


def validate_key(key):
    if not key.startswith('sk-') or len(key) < 16 or any(c.isspace() for c in key):
        raise ValueError('密钥格式不符合预期；请在百炼复制通用 API Key。未保存。')


def base_url(workspace):
    if not workspace:
        return 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,127}', workspace):
        raise ValueError('业务空间ID只能包含字母、数字和连字符。未保存。')
    return f'https://{workspace}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'


def private_directory(directory):
    if directory.is_symlink():
        raise ValueError('私密配置目录不能是符号链接。')
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)


def save_new(path, data):
    private_directory(path.parent)
    # 不覆盖既有凭据，避免失误或跟随符号链接。
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def status(path=CONFIG):
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('拒绝检查符号链接配置。')
    if not path.exists():
        print('本机密钥：未配置；没有发起 API 请求。')
        return False
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ValueError('凭据文件权限过宽，请改为仅本人可读写（600）。')
    data = json.loads(path.read_text(encoding='utf-8'))
    validate_key(data.get('api_key', ''))
    print('本机密钥：已配置（格式检查通过，未验证服务端有效性）。')
    print('候选模型：' + '、'.join(MODELS))
    print('用完即停：' + ('用户在配置时确认已开启；程序未向平台验证。'
          if data.get('free_only_user_confirmed') is True else '尚未确认。'))
    print('付费调用：未授权；API 请求：0。')
    return True


def configure():
    if not sys.stdin.isatty():
        raise ValueError('请在本机交互式终端运行，不要通过聊天或命令参数传入密钥。')
    if CONFIG.exists() or CONFIG.is_symlink():
        raise ValueError('已有本机配置，未覆盖；可运行 --check 查看状态。')
    print('为 Qwen3.8-Flash 与 DeepSeek-V4.1-Flash 配置百炼北京接口。')
    print('仅保存至本项目 .local/model_credentials.json，权限600，不联网。')
    print('该文件是本机明文凭据，已忽略Git；不要分享或上传整个 .local 目录。')
    confirmed = input('两款模型的“免费额度用完即停”均已开启并生效？输入 yes 确认：').strip()
    if confirmed != 'yes':
        raise ValueError('请先完成平台保护开关设置；没有保存凭据或调用模型。')
    workspace = input('北京业务空间ID（不知道可直接回车，使用官方仍支持的北京通用域名）：').strip()
    endpoint = base_url(workspace)
    key = getpass.getpass('粘贴百炼通用 API Key 后按回车（输入不显示）：').strip()
    validate_key(key)
    save_new(CONFIG, {'api_key': key, 'base_url': endpoint,
                     'models': MODELS, 'free_only_user_confirmed': True,
                     'paid_calls_authorized': False})
    print('配置已保存。没有显示密钥，没有调用模型；可以关闭此窗口。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='只显示配置状态，不联网')
    args = parser.parse_args()
    try:
        status() if args.check else configure()
    except (ValueError, OSError, EOFError, KeyboardInterrupt):
        # 不输出异常原文，避免JSON解析或文件错误意外暴露凭据。
        print('配置未完成：请检查是否已有配置、终端输入、保护开关、密钥格式及文件权限。未发起API请求。', file=sys.stderr)
        sys.exit(1)
