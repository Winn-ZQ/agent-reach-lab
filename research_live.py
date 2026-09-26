"""本机真实网页研究任务；一次提交，自动获取、准备、分析及复核。"""
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import re
import threading
import time
import uuid

from configure_model import private_directory
from flow_backend import BackendStopped, ResponseBackend
from model_gateway import ModelResponses
from research_acquisition import search_public_web, collect_candidates, SearchError
from research_budget import WebBudget
from research_evidence import build_evidence, verify_package
from research_flow import digest, encoded, parse, run

CATALOG = [
 ('web','公开网页','网页与订阅',['搜索候选','读取正文']),
 ('rss','RSS / Atom','网页与订阅',['读取订阅源']),
 ('xhs','小红书','社区与社交',['搜索笔记','读取笔记','读取评论']),
 ('reddit','Reddit','社区与社交',['搜索帖子','读取帖子与评论']),
 ('x','Twitter / X','社区与社交',['搜索','读取推文']),
 ('facebook','Facebook','社区与社交',['搜索','主页与动态']),
 ('instagram','Instagram','社区与社交',['搜索','近期帖子']),
 ('v2ex','V2EX','社区与社交',['帖子','回复']),
 ('youtube','YouTube','视频与音频',['视频搜索','字幕']),
 ('bilibili','B站','视频与音频',['搜索','字幕']),
 ('podcast','小宇宙','视频与音频',['音频转录']),
 ('github','GitHub','代码与项目',['仓库搜索','公开文件']),
 ('linkedin','LinkedIn','职业与企业',['公开页面','公司与职位']),
 ('boss','Boss直聘','职业与企业',['岗位搜索','职位正文']),
 ('xueqiu','雪球','财经社区',['行情搜索','帖子'])]


def now(): return datetime.now(timezone.utc).isoformat()


def save(path,data):
    temp=path.with_suffix('.tmp')
    temp.write_text(encoded(data)); temp.chmod(0o600); temp.replace(path)


class GuardedResponses(ResponseBackend):
    def __init__(self,backend,check):
        self.backend,self.check=backend,check
        self.mode,self.provenance,self.binding_scope=backend.mode,backend.provenance,backend.binding_scope
    def check_scope(self,value): self.backend.check_scope(value)
    def snapshot(self): return self.backend.snapshot()
    def respond(self,*args,**kwargs):
        self.check()
        result=self.backend.respond(*args,**kwargs)
        self.check()  # 已发请求仍记账，取消后的返回不生成新结论。
        return result


class LiveStore:
    def __init__(self,directory,search=None,collector=None,backend_factory=None,budget=None):
        self.directory=Path(directory); private_directory(self.directory)
        self.lock=threading.RLock(); self.active=None; self.tasks={}; self.cancel_flags={}
        self.search=search or search_public_web; self.collector=collector
        self.backend_factory=backend_factory or ModelResponses
        self.budget=budget or WebBudget(self.directory.parent/'web-budget')
        for path in self.directory.glob('*/task.json'):
            if not re.fullmatch('[a-f0-9]{32}',path.parent.name) or path.is_symlink() or path.parent.is_symlink(): continue
            try:
                task=parse(path.read_text())
                if task['id']!=path.parent.name: continue
                if task['status'] in ('running','cancelling'):
                    task.update(status='interrupted',reason='interrupted'); save(path,task)
                self.tasks[task['id']]=task
            except (ValueError,OSError,KeyError,TypeError): pass

    def capabilities(self):
        return [{'id':i,'name':n,'group':g,'operations':[{'name':o,'status':'configured_not_checked' if i=='web' else 'not_connected'} for o in ops],
                 'executable':i=='web'} for i,n,g,ops in CATALOG]

    def listing(self):
        with self.lock: return deepcopy(sorted(self.tasks.values(),key=lambda t:t['created_at'],reverse=True))

    def _update(self,tid,**fields):
        with self.lock:
            task=self.tasks[tid]; task.update(fields,updated_at=now()); save(self.directory/tid/'task.json',task)

    def _event(self,tid,stage,message):
        with self.lock:
            events=self.tasks[tid]['events']+[{'at':now(),'stage':stage,'message':message}]
            self._update(tid,stage=stage,events=events)

    def _acquire(self):
        if self.active: raise RuntimeError('task busy')
        fd=os.open(self.directory/'worker.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd); raise RuntimeError('task busy') from None
        return fd

    def observe_quota(self,data):
        with self.lock:
            fd=self._acquire()
            try: return self.budget.observe(data)
            finally:
                fcntl.flock(fd,fcntl.LOCK_UN); os.close(fd)

    def start(self,data, retry_from=None, continuation_kind=None):
        if not isinstance(data,dict) or set(data)!={'question','region','period','sources','required_sources','request_id','parent_task_id'}: raise ValueError('fields')
        for key,maximum in [('question',600),('region',80),('period',80)]:
            if not isinstance(data[key],str) or len(data[key])>maximum: raise ValueError('length')
        if not data['question'].strip() or not re.fullmatch(r'[a-f0-9-]{32,36}',data['request_id']): raise ValueError('request')
        known={x[0] for x in CATALOG}
        for key in ('sources','required_sources'):
            if not isinstance(data[key],list) or any(not isinstance(s,str) for s in data[key]) or len(set(data[key]))!=len(data[key]) or not set(data[key])<=known: raise ValueError('sources')
        if not data['sources'] or not set(data['required_sources'])<=set(data['sources']): raise ValueError('scope')
        with self.lock:
            for task in self.tasks.values():
                if task['request_id']==data['request_id']:
                    if task['input']!={k:v for k,v in data.items() if k!='request_id'} or task.get('retry_from')!=retry_from or task.get('continuation_kind')!=continuation_kind: raise ValueError('request conflict')
                    return deepcopy(task)
            parent=data['parent_task_id']
            if parent is not None and (parent not in self.tasks or self.tasks[parent]['status'] in ('running','cancelling')): raise ValueError('parent')
            if retry_from and any(t.get('retry_from')==retry_from for t in self.tasks.values()):
                raise ValueError('citation retry already created')
            fd=self._acquire(); tid=uuid.uuid4().hex
            directory=self.directory/tid
            try:
                directory.mkdir(mode=0o700)
                task={'id':tid,'request_id':data['request_id'],'input':{k:v for k,v in data.items() if k!='request_id'},
                      'question':data['question'],'mode':'live_api','created_at':now(),'updated_at':now(),
                      'status':'running','stage':'scope','reason':None,'events':[],
                      'limits':{'max_sources':3,'max_model_calls':3 if continuation_kind == 'review_recheck' else 2 if continuation_kind in ('review_revision','transport_recovery') else 5,'max_revisions':1,'active_seconds':600},
                      'skipped_sources':[], 'retry_from':retry_from, 'continuation_kind':continuation_kind}
                self.tasks[tid]=task; save(directory/'task.json',task)
            except Exception: os.close(fd); raise
            self.active=tid; self.cancel_flags[tid]=threading.Event()
            threading.Thread(target=self._work,args=(tid,fd),daemon=True).start()
            return deepcopy(task)

    def retry_citations(self,tid,request_id):
        with self.lock:
            parent=self.tasks[tid]
            if parent['status']!='stopped' or parent['reason'] not in ('format_revision_limit','no_change') or parent.get('retry_from'):
                raise ValueError('not a citation retry candidate')
            directory=self.directory/tid
            state=parse((directory/'flow/state.json').read_text())
            if state.get('review_status')!='blocked_by_validation' or not isinstance(state.get('final_draft'),dict):
                raise ValueError('missing failed draft')
            case=verify_package(directory,parse((directory/'analysis-input.json').read_text()))
            if state['evidence_sha256']!=digest(case) or state['versions'][-1]['draft_sha256']!=digest(state['final_draft']):
                raise ValueError('snapshot mismatch')
            return self.start({**parent['input'],'parent_task_id':tid,'request_id':request_id},retry_from=tid)

    def cancel(self,tid):
        with self.lock:
            task=self.tasks[tid]
            if self.active==tid:
                self.cancel_flags[tid].set(); self._update(tid,status='cancelling',reason='cancelled')
            elif task['status']=='waiting_user': self._update(tid,status='cancelled',reason='cancelled')
            return deepcopy(task)

    def continue_review(self,tid,request_id):
        with self.lock:
            parent=self.tasks[tid];directory=self.directory/tid
            state=parse((directory/'flow/state.json').read_text())
            if (parent['status']!='stopped' or parent['reason']!='budget_exhausted' or parent.get('retry_from')
                    or state.get('revision')!=0 or state.get('review_status')!='revise' or not state.get('reviews')):
                raise ValueError('not a review continuation candidate')
            case=verify_package(directory,parse((directory/'analysis-input.json').read_text()))
            review=state['reviews'][-1]
            from research_flow import validate_draft, validate_review
            if (state['evidence_sha256']!=digest(case) or review['evidence_sha256']!=digest(case)
                    or review['draft_sha256']!=digest(state['final_draft'])
                    or validate_draft(state['final_draft'],case) or validate_review(review['result'],case,state['final_draft'])
                    or any(i['action']=='fetch' for i in review['result']['issues'])):
                raise ValueError('invalid continuation snapshot')
            return self.start({**parent['input'],'parent_task_id':tid,'request_id':request_id},
                              retry_from=tid,continuation_kind='review_revision')

    def retry_connection(self,tid,request_id):
        """仅恢复首个关联修正的连接失败；旧用量保守占用，新预算独立受限。"""
        with self.lock:
            parent=self.tasks[tid];directory=self.directory/tid
            state=parse((directory/'flow/state.json').read_text())
            if (parent['status']!='stopped' or parent['reason']!='transport_or_usage_error'
                    or parent.get('continuation_kind')!='review_revision'
                    or state.get('revision')!=0 or len(state.get('versions',[]))!=1
                    or state.get('api_calls',0)+state.get('simulated_calls',0)!=1
                    or len(state.get('reviews',[]))!=1 or not state['reviews'][0].get('inherited')):
                raise ValueError('not a connection recovery candidate')
            case=verify_package(directory,parse((directory/'analysis-input.json').read_text()))
            review=state['reviews'][0]
            from research_flow import validate_draft,validate_review
            if (state['evidence_sha256']!=digest(case) or review['evidence_sha256']!=digest(case)
                    or review['draft_sha256']!=digest(state['final_draft'])
                    or validate_draft(state['final_draft'],case) or validate_review(review['result'],case,state['final_draft'])
                    or review['result']['verdict']!='revise'
                    or any(i['action']=='fetch' for i in review['result']['issues'])):
                raise ValueError('invalid recovery snapshot')
            return self.start({**parent['input'],'parent_task_id':tid,'request_id':request_id},
                              retry_from=tid,continuation_kind='transport_recovery')

    def retry_review(self,tid,request_id):
        """复核传输失败后，从固定草稿重新复核一次；最多复核、修正、再复核三次。"""
        with self.lock:
            parent=self.tasks[tid];directory=self.directory/tid
            state=parse((directory/'flow/state.json').read_text())
            if (parent['status']!='stopped' or parent['reason']!='review_transport_or_usage_error'
                    or parent.get('retry_from') or state.get('review_status')!='failed'):
                raise ValueError('not a review recheck candidate')
            case=verify_package(directory,parse((directory/'analysis-input.json').read_text()))
            from research_flow import validate_draft
            if (state['evidence_sha256']!=digest(case) or not state.get('versions')
                    or state['versions'][-1]['draft_sha256']!=digest(state.get('final_draft'))
                    or validate_draft(state.get('final_draft'),case)):
                raise ValueError('invalid recheck snapshot')
            return self.start({**parent['input'],'parent_task_id':tid,'request_id':request_id},
                              retry_from=tid,continuation_kind='review_recheck')

    def resume(self,tid):
        with self.lock:
            task=self.tasks[tid]
            if self.active==tid: return deepcopy(task)
            if task['status']!='waiting_user' or task['reason'] not in ('quota_observation_required','quota_observation_stale','free_quota_insufficient','daily_call_limit','model_configuration_invalid'): raise ValueError('cannot resume')
            fd=self._acquire(); self.active=tid; self.cancel_flags[tid]=threading.Event()
            self._update(tid,status='running',reason=None)
            threading.Thread(target=self._work,args=(tid,fd),daemon=True).start()
            return deepcopy(task)

    def _work(self,tid,fd):
        directory=self.directory/tid; begun=time.monotonic(); backend=None
        def check():
            if self.cancel_flags[tid].is_set(): raise BackendStopped('cancelled')
            if time.monotonic()-begun>600: raise BackendStopped('time_limit')
        def changed(state):
            check()
            event=state['events'][-1]
            stage=event.get('stage') or self.tasks[tid]['stage']
            self._update(tid,stage=stage)
        try:
            task=self.tasks[tid]; scope=task['input']
            missing=[s for s in scope['required_sources'] if s!='web']
            # 当前只自动匹配公开网页；明确出现的平台需求不能被静默降级。
            for sid,name,_,_ in CATALOG:
                if sid!='web' and name.lower() in scope['question'].lower() and sid not in scope['sources']: missing.append(sid)
            if missing:
                self._update(tid,status='waiting_user',reason='required_source_unavailable',missing_sources=missing); return
            skipped=[s for s in scope['sources'] if s!='web']
            self._update(tid,skipped_sources=skipped)
            if 'web' not in scope['sources']: raise BackendStopped('no_available_source')
            check()
            package_path=directory/'analysis-input.json'
            initial_draft=None;initial_review=None
            if task.get('retry_from'):
                parent_dir=self.directory/task['retry_from']
                parent_package=parse((parent_dir/'analysis-input.json').read_text())
                parent_case=verify_package(parent_dir,parent_package)
                parent_state=parse((parent_dir/'flow/state.json').read_text())
                initial_draft=parent_state['final_draft']
                if task.get('continuation_kind') in ('review_revision','transport_recovery'):
                    initial_review=parent_state['reviews'][-1]
                if parent_state['evidence_sha256']!=digest(parent_case) or parent_state['versions'][-1]['draft_sha256']!=digest(initial_draft):
                    raise ValueError('snapshot changed')
                if not package_path.exists():
                    # 仅复制已校验的固定来源快照；不复制旧稿的通过状态或预算。
                    (directory/'sources').mkdir(mode=0o700)
                    for filename in ('run.json','search.json'):
                        save(directory/filename,parse((parent_dir/filename).read_text()))
                    for filename in ['manifest.json']+[f'source-{c["rank"]}.json' for c in parse((parent_dir/'search.json').read_text())['results']]:
                        src=parent_dir/'sources'/filename
                        if src.exists():
                            if src.is_symlink(): raise ValueError('linked source')
                            save(directory/'sources'/filename,parse(src.read_text()))
                    save(package_path,parent_package)
                self._event(tid,'prepare','沿用父任务已校验证据与草稿'+('及复核意见' if initial_review else '')+'；不重新搜索，新任务独立记账')
            if not package_path.exists():
                self._event(tid,'search','搜索最多 3 个公开网页候选')
                query=' '.join(filter(None,[scope['question'],scope['region'],scope['period']]))[:600]
                search=self.search(query,'按问题寻找相关公开原文，优先原始发布者；保留时间和地区适用限制。',max_results=3)
                save(directory/'search.json',search); check()
                self._event(tid,'collect','逐页保存原文；失败来源不进入分析')
                def collector(url,**kwargs):
                    from collect_page import collect
                    check(); result=(self.collector or collect)(url,**kwargs); check(); return result
                manifest=collect_candidates(search,directory/'sources',max_retries=1,collector=collector)
                save(directory/'run.json',{'id':tid,'status':'completed','input':scope,'created_at':task['created_at']})
                check(); self._event(tid,'prepare','校验来源哈希并准备分析证据')
                package=build_evidence(directory); save(package_path,package)
            else: package=parse(package_path.read_text())
            case=verify_package(directory,package); check()
            self._event(tid,'budget','检查模型额度并预留独立复核预算')
            requested_calls=3 if task.get('continuation_kind')=='review_recheck' else 2 if initial_review else 4 if initial_draft else 5
            grant=self.budget.issue(tid,digest(case),scope['parent_task_id'],requested_calls=requested_calls)
            call_limit=parse(grant.read_text())['max_calls']
            self._update(tid,limits={**task['limits'],'max_model_calls':call_limit})
            backend=self.backend_factory(grant,digest(case)); check()
            with backend.session():
                state=run(case,GuardedResponses(backend,check),directory/'flow',call_limit=call_limit,on_event=changed,initial_draft=initial_draft,initial_review=initial_review)
            check()
            self._update(tid,status=state['execution_status'],stage='finished',reason=state['stop_reason'])
        except BackendStopped as exc:
            waiting=exc.code in ('quota_observation_required','quota_observation_stale','free_quota_insufficient','daily_call_limit','model_configuration_invalid')
            self._update(tid,status='cancelled' if exc.code=='cancelled' else 'waiting_user' if waiting else 'stopped',reason=exc.code)
        except SearchError as exc:
            self._update(tid,status='failed',reason='search_'+exc.kind)
        except ValueError:
            self._update(tid,status='stopped',reason='evidence_or_response_invalid')
        except Exception:
            self._update(tid,status='failed',reason='worker_failed')
        finally:
            try:
                if backend: self.budget.settle(tid,backend.snapshot())
            finally:
                with self.lock:
                    self.active=None; self.cancel_flags.pop(tid,None)
                    fcntl.flock(fd,fcntl.LOCK_UN); os.close(fd)

    def detail(self,tid):
        with self.lock:
            task=deepcopy(self.tasks[tid]); directory=self.directory/tid
            state={'events':[],'versions':[],'reviews':[],'api_calls':0,'review_status':'not_run','final_draft':None}
            case={'task':task['question'],'sources':[],'questions':{}}
            path=directory/'analysis-input.json'
            if path.exists(): case=verify_package(directory,parse(path.read_text()))
            path=directory/'flow/state.json'
            if path.exists(): state=parse(path.read_text())
            if task['status'] in ('cancelled','cancelling','interrupted','failed'):
                state.update(execution_status=task['status'],review_status='not_completed',stop_reason=task['reason'])
            if task['status']=='waiting_user': state.update(stop_reason=task['reason'])
            # 无效模型复核只暴露校验问题，不能让前端遍历未校验的任意结构。
            for review in state.get('reviews',[]):
                if review.get('validation_issues'):
                    review['result']={'verdict':'invalid','checks':[],
                        'issues':[{'description':str(x),'action':'校验失败'} for x in review['validation_issues']]}
            # 不把无效结构当报告；原始响应仅留本机。
            draft=state.get('final_draft')
            if not isinstance(draft,dict) or (state.get('versions') and state['versions'][-1].get('validation_issues')):
                state['final_draft']=None
            search_path=directory/'search.json'; manifest_path=directory/'sources/manifest.json'
            candidates=parse(search_path.read_text()).get('results',[]) if search_path.exists() else []
            records=parse(manifest_path.read_text()).get('records',[]) if manifest_path.exists() else []
            return {'task':task,'state':state,'case':case,'candidates':[{k:c.get(k) for k in ('rank','title','url')} for c in candidates], 'records':records}
