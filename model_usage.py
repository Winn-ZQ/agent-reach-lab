"""验证供应商计量字段，不读取配置或发起请求。"""

def numeric_usage(raw):
    if not isinstance(raw, dict):
        raise ValueError('missing usage')
    result = {k: raw[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
              if type(raw.get(k)) is int and raw[k] >= 0}
    if len(result) != 3 or result['total_tokens'] != result['prompt_tokens'] + result['completion_tokens']:
        raise ValueError('invalid usage')
    for name in ('prompt_tokens_details', 'completion_tokens_details'):
        if isinstance(raw.get(name), dict):
            result[name] = {k: v for k, v in raw[name].items() if type(v) is int and v >= 0}
    return result

