"""固定角色配置；不是自动选模器，不自动升级或批准付费调用。"""
from copy import deepcopy

SUPPORTED_MODELS = ('qwen3.8-flash', 'deepseek-v4.1-flash', 'qwen3.8-max')
STAGES = ('analysis', 'review', 'repair')
DEFAULT_OPTIONS = dict(enable_thinking=False, max_completion_tokens=4096, timeout_seconds=90)
THINKING_OPTIONS = dict(enable_thinking=True, max_completion_tokens=8192, timeout_seconds=180)
PROFILE_IDS = ('legacy', 'qwen-review-thinking-v1', 'deepseek-review-thinking-v1',
               'max-review-thinking-v1', 'qwen-thinking-v1',
               'qwen-review-thinking-json-v2', 'max-review-thinking-json-v2',
               'deepseek-review-thinking-budget-v2')


def profile(name):
    if name not in PROFILE_IDS:
        raise ValueError('unknown model profile')
    roles = dict(analysis=SUPPORTED_MODELS[0], repair=SUPPORTED_MODELS[0], review=SUPPORTED_MODELS[1])
    options = {stage: deepcopy(DEFAULT_OPTIONS) for stage in STAGES}
    if name != 'legacy':
        roles['review'] = (SUPPORTED_MODELS[2] if name.startswith('max-') else
                           SUPPORTED_MODELS[1] if name.startswith('deepseek-') else SUPPORTED_MODELS[0])
        options['review'] = deepcopy(THINKING_OPTIONS)
    if name == 'qwen-thinking-v1':
        options = {stage: deepcopy(THINKING_OPTIONS) for stage in STAGES}
    if name == 'deepseek-review-thinking-budget-v2':
        options['review'].update(max_completion_tokens=16384, timeout_seconds=240)
    return roles, options


def role_options(grant, stage):
    """旧grant未设置options时保留旧请求语义；配置跟随grant哈希固定。"""
    if 'role_options' not in grant:
        return deepcopy(DEFAULT_OPTIONS)
    options = grant['role_options']
    if not isinstance(options, dict) or set(options) != set(STAGES):
        raise ValueError('invalid role options')
    for value in options.values():
        if (not isinstance(value, dict) or set(value) != set(DEFAULT_OPTIONS)
                or type(value['enable_thinking']) is not bool
                or type(value['max_completion_tokens']) is not int
                or not 512 <= value['max_completion_tokens'] <= 16384
                or type(value['timeout_seconds']) is not int
                or not 30 <= value['timeout_seconds'] <= 240
                or value['enable_thinking'] and value['max_completion_tokens'] < 8192):
            raise ValueError('invalid role options')
    return deepcopy(options[stage])


def output_capacity(grant, stage):
    # 官方接口允许总输出上限有最多10 Token偏差，同样预留并检查。
    return role_options(grant, stage)['max_completion_tokens'] + (10 if 'role_options' in grant else 0)


def request_options(grant, stage):
    opt = role_options(grant, stage)
    result = {'enable_thinking': opt['enable_thinking']}
    key = 'max_completion_tokens' if 'role_options' in grant else 'max_tokens'
    result[key] = opt['max_completion_tokens']
    model = grant['roles'][stage]
    if opt['enable_thinking']:
        if model.startswith('qwen'):
            result['thinking_budget'] = 4096
        else:
            result['reasoning_effort'] = 'low'
    elif model.startswith('qwen'):
        result['response_format'] = {'type': 'json_object'}
    if model.startswith('qwen') and grant.get('model_profile','').endswith('-json-v2'):
        result['response_format'] = {'type': 'json_object'}
    return result
