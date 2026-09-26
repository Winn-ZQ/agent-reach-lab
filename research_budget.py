"""网页任务预算：本机额度观察＋持久分配记录；不能刷新旧任务消耗。"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os

from configure_model import CONFIG, MODELS, private_directory, save_new
from flow_backend import BackendStopped
from model_smoke import load_config
from model_gateway import checked_grant

ROLES = dict(analysis=MODELS[0], repair=MODELS[0], review=MODELS[1])
DAILY_BASE_CALLS = 12
DAILY_CALL_LIMIT = 64  # 质量改进阶段：双提示词对照、旧例回归、新题各有界执行；旧账本不变。
MAX_TASK_CALLS = 5


def stamp():
    return datetime.now(timezone.utc)


class WebBudget:
    def __init__(self, root, config_path=CONFIG):
        self.root = Path(root)
        private_directory(self.root)
        self.config_path = config_path
        self.path = self.root/'quota.json'

    def observe(self, data):
        if (set(data) != {'remaining_tokens', 'stop_when_used_up'} or data['stop_when_used_up'] is not True
                or set(data['remaining_tokens']) != set(MODELS)
                or any(type(n) is not int or not 0 <= n <= 100000000 for n in data['remaining_tokens'].values())):
            raise ValueError('invalid quota observation')
        temp = self.path.with_suffix('.tmp')
        if temp.exists(): temp.unlink()
        save_new(temp, {**data, 'observed_at':stamp().isoformat(), 'basis':'本机用户核对控制台后的记录，非自动查询余额'})
        temp.replace(self.path)
        return self.status()

    def status(self):
        status = {'ready':False, 'reason':'quota_observation_required', 'max_calls_per_task':MAX_TASK_CALLS,
                  'base_calls_per_day':DAILY_BASE_CALLS, 'budget_policy':'bounded_adaptive',
                  'max_calls_per_day':DAILY_CALL_LIMIT, 'token_limit_per_model':300000,
                  'models':ROLES, 'paid_calls_authorized':False}
        try:
            load_config(self.config_path)
        except (ValueError, OSError, TypeError, KeyError):
            status['reason']='model_configuration_invalid'
            return status
        try:
            quota=json.loads(self.path.read_text())
            observed=datetime.fromisoformat(quota['observed_at'])
            if observed > stamp() or stamp()-observed > timedelta(hours=24):
                status['reason']='quota_observation_stale'; return status
            allocations=[json.loads(p.read_text()) for p in self.root.glob('allocation-*.json')]
            remaining=dict(quota['remaining_tokens'])
            for allocation in allocations:
                if datetime.fromisoformat(allocation['created_at']) >= observed:
                    for model,n in allocation['charged_tokens'].items(): remaining[model]-=n
            today=stamp().date().isoformat()
            today_allocations=[a for a in allocations if a['created_at'].startswith(today)]
            # 旧记录没有请求结算时仍保守占用；不凭Token数猜调用次数。
            calls=sum(a.get('charged_calls',a['max_calls']) if a.get('settled') else a['max_calls'] for a in today_allocations)
            actual=sum(a.get('actual_calls',0) for a in today_allocations)
            status.update(remaining_tokens=remaining, occupied_calls_today=calls,
                          settled_actual_calls_today=actual, next_task_max_calls=min(MAX_TASK_CALLS,max(0,DAILY_CALL_LIMIT-calls)),
                          current_daily_call_limit=max([DAILY_BASE_CALLS]+[a.get('daily_call_limit',DAILY_BASE_CALLS) for a in today_allocations]),
                          observed_at=quota['observed_at'])
            if calls+2 > DAILY_CALL_LIMIT: status['reason']='daily_call_limit'
            elif min(remaining.values()) < 300000: status['reason']='free_quota_insufficient'
            else: status.update(ready=True,reason='ready')
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return status

    def issue(self, task_id, case_hash, parent, requested_calls=4, transport_mode='json'):
        if type(requested_calls) is not int or not 2 <= requested_calls <= MAX_TASK_CALLS:
            raise ValueError('invalid stage budget')
        if transport_mode not in ('json','sse'): raise ValueError('invalid transport mode')
        status=self.status()
        if not status['ready']: raise BackendStopped(status['reason'])
        limits={m:300000 for m in MODELS}
        now=stamp()
        if status['next_task_max_calls'] < requested_calls:
            raise BackendStopped('daily_call_limit')
        max_calls=requested_calls
        needed=status['occupied_calls_today']+max_calls
        daily_limit=max(status['current_daily_call_limit'],min(DAILY_CALL_LIMIT,
            DAILY_BASE_CALLS+4*((max(0,needed-DAILY_BASE_CALLS)+3)//4)))
        allocation={'created_at':now.isoformat(), 'max_calls':max_calls, 'charged_tokens':limits,
                    'task_id':task_id, 'settled':False,
                    'budget_policy':'bounded_adaptive', 'daily_call_limit':daily_limit,
                    'transport_mode':transport_mode,
                    'adjustment_reason':'按本任务阶段预留完整步骤，基础12、每次增加4、当前质量改进阶段上限64；旧占用保留。'}
        grant={'approved':True,'free_only':True,'paid_calls_authorized':False,
               'grant_id':'web-'+task_id,'case_sha256':case_hash,
               'authorization_note':'用户已授权网页提交研究及合理范围模型调用；仅在已核对免费额度内执行。',
               'parent_run':parent or 'web-task-'+task_id, 'expires_at':(now+timedelta(hours=2)).isoformat(),
               'max_calls':max_calls,'roles':ROLES,'token_limits':limits,'transport_mode':transport_mode,
               'free_quota_observation':{m:{'remaining_tokens':status['remaining_tokens'][m],
                    'stop_when_used_up':True,'observed_at':status['observed_at']} for m in MODELS}}
        # 独占任务锁下先持久占用，崩溃时保守保留，不重复分配。
        save_new(self.root/f'allocation-{task_id}.json',allocation)
        path=self.root/f'grant-{task_id}.json'
        save_new(path,grant)
        checked_grant(path,case_hash)
        return path

    def settle(self, task_id, snapshot):
        path=self.root/f'allocation-{task_id}.json'
        if not path.exists(): return
        data=json.loads(path.read_text())
        calls=snapshot.get('api_calls',0)+snapshot.get('simulated_calls',0)
        if type(calls) is not int or not 0 <= calls <= data['max_calls']:
            raise ValueError('invalid call settlement')
        data['actual_calls']=calls
        data['charged_calls']=data['max_calls'] if snapshot.get('usage_unknown',True) else calls
        data['settlement_basis']='actual_calls_with_unknown_usage_reservation'
        if not snapshot.get('usage_unknown',True):
            data['charged_tokens']={m:snapshot.get('known_tokens_by_model',{}).get(m,0) for m in MODELS}
        data['settled']=True
        temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(data)); os.chmod(temp,0o600); temp.replace(path)
