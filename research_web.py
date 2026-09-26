"""本机研究工作台：公开网页采集、证据准备、离线流程回放和预算预检。"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import parse_qs, quote, urlsplit
import uuid

from configure_model import PRIVATE, private_directory
from research_exports import export_csv, export_markdown
from research_flow import OfflineResponses, encoded, parse, prepare_case, run
from research_acquisition import SearchError, collect_candidates, search_public_web
from research_evidence import build_evidence, handoff_preview, verify_package, load_record
from run_research import preflight
from research_live import LiveStore

ROOT=Path(__file__).resolve().parent
WEB=ROOT/'web'
SCENARIOS=ROOT/'runs/offline-flow-2026-09-23/scenarios'
CHOICES={
    'quote-repair':('引用修正后通过','发现引文错误，修正一次，再进入模拟复核。'),
    'invalid-review':('复核结果无效','复核自称通过，但引用了不存在的来源编号。'),
    'budget-stop':('剩余名额不足','保留草稿，避免开始无法完成复核的修正。'),
    'revision-limit':('达到修正上限','修正一次仍有问题，停止并保留两个版本。'),
}


def now():
    return datetime.now(timezone.utc).isoformat()


def plan_preview(data):
    if not isinstance(data, dict) or set(data) != {'question','region','period','sources'}:
        raise ValueError('invalid plan fields')
    question=str(data.get('question','')).strip()
    region=str(data.get('region','')).strip()
    period=str(data.get('period','')).strip()
    if not question or len(question)>600 or len(region)>80 or len(period)>80:
        raise ValueError('invalid plan length')
    if data.get('sources') != ['web']:
        raise ValueError('unsupported source')
    return {'mode':'offline_plan_preview','execution_status':'not_started','real_api_calls':0,
            'input':{'question':question,'region':region,'period':period,'sources':['web']},
            'steps':[{'id':'scope','owner':'程序','title':'确认研究范围','output':'问题、地区、时间范围和来源约束'},
                     {'id':'collect','owner':'Agent-Reach 获取层','title':'获取公开网页资料','output':'带来源ID、原文片段和采集状态的证据包'},
                     {'id':'analyze','owner':'分析角色','title':'整理事实与未知','output':'逐题回答、限制和待验证假设'},
                     {'id':'review','owner':'独立复核角色','title':'检查引用与推断边界','output':'通过、修正一次或停止'},
                     {'id':'deliver','owner':'程序','title':'生成结果包','output':'报告、CSV和过程记录'}],
            'not_connected':['小红书','实时模型调用'],
            'notice':'这是计划预览，不会搜索、登录、读取Cookie或调用模型。'}


class AcquisitionStore:
    """实时公开网页采集的最小后台状态；不负责模型分析。"""
    def __init__(self, directory=PRIVATE/'web-acquisitions'):
        self.directory=Path(directory)
        private_directory(self.directory)
        self.lock=threading.RLock()
        self.runs={}
        self.active=None
        for path in self.directory.glob('*/run.json'):
            if not re.fullmatch(r'[a-f0-9]{32}',path.parent.name) or path.is_symlink() or path.parent.is_symlink():
                continue
            try:
                item=parse(path.read_text())
                if item['id']!=path.parent.name:
                    continue
                if item['status']=='running':
                    item['status']='interrupted'; item['updated_at']=now(); save(path,item)
                self.runs[item['id']]=item
            except (ValueError,OSError,KeyError,TypeError):
                continue

    def listing(self):
        with self.lock:
            return deepcopy(sorted(self.runs.values(),key=lambda item:item['created_at'],reverse=True))

    def start(self,data,request_id):
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-f0-9-]{32,36}',request_id):
            raise ValueError('invalid acquisition request')
        plan=plan_preview(data)
        with self.lock:
            for item in self.runs.values():
                if item['request_id']==request_id:
                    if item['input']!=plan['input']:
                        raise ValueError('request conflict')
                    return deepcopy(item)
            if self.active:
                raise RuntimeError('acquisition busy')
            run_id=uuid.uuid4().hex
            directory=self.directory/run_id
            directory.mkdir(mode=0o700)
            item={'id':run_id,'request_id':request_id,'status':'running','mode':'public_web_acquisition',
                  'input':plan['input'],'created_at':now(),'updated_at':now(),'max_results':3}
            save(directory/'run.json',item)
            self.runs[run_id]=item; self.active=run_id
            threading.Thread(target=self._work,args=(run_id,plan),daemon=True).start()
            return deepcopy(item)

    def _work(self,run_id,plan):
        directory=self.directory/run_id
        query=plan['input']['question']
        if plan['input']['region']:
            query += ' 地区：'+plan['input']['region']
        if plan['input']['period']:
            query += ' 时间范围：'+plan['input']['period']
        query=query[:600]
        objective='围绕研究问题寻找可公开读取的网页资料；保留来源标题、网址和摘要，排除需要登录或明显无关的结果。'
        if plan['input']['region']:
            objective += '优先覆盖地区：'+plan['input']['region']+'。'
        if plan['input']['period']:
            objective += '关注时间范围：'+plan['input']['period']+'。'
        try:
            search=search_public_web(query,objective,max_results=3)
            save(directory/'search.json',search)
            manifest=collect_candidates(search,directory/'sources',max_retries=1)
            with self.lock:
                item=self.runs[run_id]
                item.update(status='completed',updated_at=now(),search_count=len(search['results']),
                            fetched_count=sum(1 for record in manifest['records'] if record['status'].startswith('fetched')))
                save(directory/'run.json',item); self.active=None
        except SearchError as exc:
            with self.lock:
                item=self.runs[run_id]; item.update(status='failed',updated_at=now(),
                    error={'kind':exc.kind,'message':str(exc),'retryable':exc.retryable})
                save(directory/'run.json',item); self.active=None
        except Exception:
            with self.lock:
                item=self.runs[run_id]; item.update(status='failed',updated_at=now(),
                    error={'kind':'worker_failed','message':'公开网页获取未完成，请检查本机配置。','retryable':False})
                save(directory/'run.json',item); self.active=None

    def detail(self,run_id):
        with self.lock:
            if run_id not in self.runs:
                raise KeyError('acquisition missing')
            item=deepcopy(self.runs[run_id]); directory=self.directory/run_id
            search={}; manifest={}
            path=directory/'search.json'
            if path.exists():
                try: search=parse(path.read_text())
                except (ValueError,OSError,TypeError): pass
            path=directory/'sources/manifest.json'
            if path.exists():
                try: manifest=parse(path.read_text())
                except (ValueError,OSError,TypeError): pass
            candidates=[]
            for candidate in search.get('results',[]):
                candidates.append({key:candidate.get(key) for key in ('rank','title','url','published','author')})
            records=manifest.get('records',[])
            return {'run':item,'candidates':candidates,'records':records,
                    'evidence':self.evidence(run_id),
                    'notice':'网页已获取但尚未进入模型分析或独立复核。' if item['status']=='completed' else None}

    def evidence(self,run_id,create=False):
        with self.lock:
            if run_id not in self.runs:
                raise KeyError('acquisition missing')
            directory=self.directory/run_id
            path=directory/'analysis-input.json'
            if not create and not path.exists():
                return {'status':'not_prepared'}
            try:
                if path.exists():
                    package=load_record(path)
                    case=verify_package(directory,package)
                else:
                    package=build_evidence(directory)
                    case=prepare_case(package['case'],package['run_date'])
                    save(path,package)
                return {'status':'prepared','case_sha256':package['case_sha256'],
                        'input_bytes':package['input_bytes'],'case':case,'preflight':preflight(case),
                        'analysis_status':'not_run','review_status':'not_run','api_calls':0}
            except ValueError as exc:
                return {'status':'blocked','message':str(exc),'api_calls':0}
            except (OSError,KeyError,TypeError):
                return {'status':'blocked','message':'本机来源记录不完整，无法准备分析资料。','api_calls':0}

    def handoff(self,run_id):
        evidence=self.evidence(run_id)
        if evidence.get('status')!='prepared':
            return evidence
        return handoff_preview(evidence)


def save(path,data):
    temp=path.with_suffix('.tmp')
    temp.write_text(encoded(data)+'\n')
    temp.replace(path)


class TaskStore:
    def __init__(self,directory=PRIVATE/'web-runs'):
        self.directory=Path(directory)
        private_directory(self.directory)
        self.lock=threading.RLock()
        self.tasks={}
        self.active=None
        for path in self.directory.glob('*/task.json'):
            if not re.fullmatch(r'[a-f0-9]{32}',path.parent.name) or path.is_symlink() or path.parent.is_symlink():
                continue
            try:
                task=parse(path.read_text())
                if task['id']!=path.parent.name or task['scenario_id'] not in CHOICES:
                    continue
                if task['status']=='running':
                    final=path.parent/'flow/result.json'
                    state=parse(final.read_text()) if final.exists() else {}
                    task['status']=state.get('execution_status') if state.get('execution_status') in ('completed','stopped') else 'interrupted'
                    task['updated_at']=now()
                    save(path,task)
                self.tasks[task['id']]=task
            except (ValueError,OSError,KeyError,TypeError):
                continue

    def scenario(self,scenario_id):
        if scenario_id not in CHOICES:
            raise ValueError('unknown scenario')
        return parse((SCENARIOS/(scenario_id+'.json')).read_text())

    def options(self):
        result=[]
        for sid,(title,description) in CHOICES.items():
            scenario=self.scenario(sid)
            case=prepare_case(scenario['case'],scenario['run_date'])
            result.append({'id':sid,'title':title,'description':description,'question':case['task'],
                           'sampling_facts':case['sampling_facts'],'call_limit':scenario['call_limit']})
        return result

    def listing(self):
        with self.lock:
            return deepcopy(sorted(self.tasks.values(),key=lambda t:t['created_at'],reverse=True))

    def start(self,scenario_id,request_id):
        if scenario_id not in CHOICES or not re.fullmatch(r'[a-f0-9-]{32,36}',request_id):
            raise ValueError('invalid task')
        with self.lock:
            for task in self.tasks.values():
                if task['request_id']==request_id:
                    if task['scenario_id']!=scenario_id:
                        raise ValueError('request conflict')
                    return deepcopy(task)
            if self.active:
                raise RuntimeError('task busy')
            scenario=self.scenario(scenario_id)
            task_id=uuid.uuid4().hex
            directory=self.directory/task_id
            directory.mkdir(mode=0o700)
            task={'id':task_id,'request_id':request_id,'scenario_id':scenario_id,
                  'title':CHOICES[scenario_id][0],'question':scenario['case']['task'],
                  'created_at':now(),'updated_at':now(),'status':'running','mode':'offline_simulation'}
            save(directory/'task.json',task)
            self.tasks[task_id]=task
            self.active=task_id
            threading.Thread(target=self._work,args=(task_id,scenario),daemon=True).start()
            return deepcopy(task)

    def _work(self,task_id,scenario):
        def changed(state):
            with self.lock:
                task=self.tasks[task_id]
                task['updated_at']=now()
                task['last_action']=state['events'][-1]['action']
                save(self.directory/task_id/'task.json',task)
        try:
            case=prepare_case(scenario['case'],scenario['run_date'])
            state=run(case,OfflineResponses(scenario['responses'],scenario['provenance']),
                      self.directory/task_id/'flow',scenario['call_limit'],on_event=changed)
            status=state['execution_status']
        except Exception:
            status='failed'  # 不向页面泄露路径、异常原文或供应商内容。
        with self.lock:
            self.tasks[task_id].update(status=status,updated_at=now())
            save(self.directory/task_id/'task.json',self.tasks[task_id])
            self.active=None

    def detail(self,task_id):
        with self.lock:
            if task_id not in self.tasks:
                raise KeyError('task missing')
            task=deepcopy(self.tasks[task_id])
            scenario=self.scenario(task['scenario_id'])
            directory=self.directory/task_id/'flow'
            path=directory/'state.json'
            state=parse(path.read_text()) if path.exists() else {'events':[],'versions':[],'reviews':[], 'api_calls':0}
            if task['status']=='interrupted':
                state.update(execution_status='stopped',review_status='failed',stop_reason='interrupted')
            if task['status']=='failed':
                state.update(execution_status='stopped',review_status='failed',stop_reason='worker_failed')
            versions=[]
            for n in (0,1):
                file=directory/f'draft-{n}.json'
                if file.exists():
                    versions.append({'revision':n,'draft':parse(file.read_text())})
            case=prepare_case(scenario['case'],scenario['run_date'])
            # 仅开放结果对象，不提供任意文件读取或原始模型请求／响应下载。
            return {'task':task,'state':state,'case':case,'versions':versions}


def make_server(directory=PRIVATE/'web-runs',port=0):
    # 首次启动没有 .local；只创建任务父目录，不要求先配置模型。
    parent=Path(directory).parent
    if parent.is_symlink():
        raise ValueError('任务父目录不能是符号链接')
    if not parent.exists():
        private_directory(parent)
    store=TaskStore(directory)
    acquisitions=AcquisitionStore(Path(directory).parent/'web-acquisitions')
    live=LiveStore(Path(directory).parent/'live-research')
    dispatch_lock=threading.RLock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def send(self,status,body,content_type='application/json; charset=utf-8',headers=None):
            if isinstance(body,(dict,list)):
                body=json.dumps(body,ensure_ascii=False).encode()
            elif isinstance(body,str): body=body.encode()
            self.send_response(status)
            for key,value in {'Content-Type':content_type,'Content-Length':str(len(body)),
                    'Cache-Control':'no-store','Referrer-Policy':'same-origin','X-Content-Type-Options':'nosniff',
                    'X-Frame-Options':'DENY',
                    'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",**(headers or {})}.items():
                self.send_header(key,value)
            self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass

        def host_ok(self):
            return self.headers.get('Host')==self.server.host

        def authorized(self):
            try:
                cookie=SimpleCookie(self.headers.get('Cookie',''))
                header=self.headers.get('X-Session-Token','')
                cookie_value=cookie['research_session'].value if 'research_session' in cookie else ''
                return self.host_ok() and (secrets.compare_digest(cookie_value,self.server.session)
                                           or secrets.compare_digest(header,self.server.session))
            except (KeyError,ValueError):return False

        def do_GET(self):
            path=urlsplit(self.path).path
            if not self.host_ok():
                self.send(403,{'error':'仅允许本机地址访问'});return
            if path==self.server.entry:
                self.send(303,b'',headers={'Location':'/?session='+quote(self.server.session,safe=''),
                                           'Set-Cookie':f'research_session={self.server.session}; HttpOnly; SameSite=Strict; Path=/'})
                return
            query=parse_qs(urlsplit(self.path).query)
            query_session=query.get('session',[''])[0]
            root_token=(path in ('/','/legacy') and secrets.compare_digest(query_session,self.server.session))
            if path in ('/app.js','/style.css','/result.css','/workspace.js','/workspace.css'):
                try:
                    self.send(200,(WEB/path[1:]).read_bytes(),'application/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
                except OSError:
                    self.send(500,{'error':'静态资源不可用'})
                return
            result_token=(path=='/result' and secrets.compare_digest(query_session,self.server.session))
            if not self.authorized() and not root_token and not result_token:
                self.send(403,{'error':'请从启动时提供的本机入口打开页面'});return
            try:
                if path in ('/','/legacy'):
                    page=(WEB/('workspace.html' if path=='/' else 'index.html')).read_text()
                    if root_token:
                        page=page.replace('<body>', '<body data-session='+json.dumps(self.server.session)+'>')
                    self.send(200,page,'text/html; charset=utf-8');return
                if path=='/result':
                    page=(WEB/'result.html').read_text()
                    page=page.replace('<body>', '<body data-session='+json.dumps(self.server.session)+'>')
                    self.send(200,page,'text/html; charset=utf-8');return
                if path=='/api/bootstrap':
                    self.send(200,{'csrf':self.server.csrf,'scenarios':store.options(),'live_execution_enabled':False});return
                if path=='/api/live/bootstrap':
                    self.send(200,{'csrf':self.server.csrf,'capabilities':live.capabilities(),'budget':live.budget.status()});return
                if path=='/api/live/tasks':
                    self.send(200,live.listing());return
                match_live=re.fullmatch(r'/api/live/tasks/([a-f0-9]{32})(?:/(report\.md|materials\.csv))?',path)
                if match_live:
                    detail=live.detail(match_live[1])
                    if not match_live[2]:self.send(200,detail);return
                    if detail['task']['status'] in ('running','cancelling'):
                        self.send(409,{'error':'请等待本次任务结束后导出'});return
                    body=export_markdown(detail) if match_live[2]=='report.md' else export_csv(detail)
                    kind='text/markdown' if match_live[2]=='report.md' else 'text/csv'
                    self.send(200,body,kind+'; charset=utf-8',{'Content-Disposition':f'attachment; filename="{match_live[1]}-{match_live[2]}"'});return
                if path=='/api/tasks':
                    self.send(200,store.listing());return
                if path=='/api/acquisitions':
                    self.send(200,acquisitions.listing());return
                match_acq=re.fullmatch(r'/api/acquisitions/([a-f0-9]{32})',path)
                if match_acq:
                    self.send(200,acquisitions.detail(match_acq[1]));return
                match=re.fullmatch(r'/api/tasks/([a-f0-9]{32})(?:/(report\.md|materials\.csv))?',path)
                if match:
                    detail=store.detail(match[1])
                    if not match[2]:self.send(200,detail);return
                    if detail['task']['status']=='running':self.send(409,{'error':'请等待本次任务结束后导出'});return
                    if match[2]=='report.md':
                        text,kind=export_markdown(detail),'text/markdown; charset=utf-8'
                    else:text,kind=export_csv(detail),'text/csv; charset=utf-8'
                    self.send(200,text,kind,{'Content-Disposition':f'attachment; filename="{match[1]}-{match[2]}"'});return
                self.send(404,{'error':'未找到页面或任务'})
            except KeyError:self.send(404,{'error':'未找到任务'})
            except (ValueError,OSError,TypeError):self.send(500,{'error':'无法读取本机记录'})

        def do_POST(self):
            with dispatch_lock:
                self.handle_post()

        def handle_post(self):
            if (not self.authorized() or self.headers.get('Origin')!=self.server.origin
                    or self.headers.get('Sec-Fetch-Site') not in (None,'same-origin')
                    or not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),self.server.csrf)
                    or self.headers.get('Transfer-Encoding') is not None
                    or self.headers.get('Content-Type','').split(';')[0]!='application/json'):
                self.send(403,{'error':'请在本机工作台提交操作'});return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=4096:raise ValueError()
                raw=self.rfile.read(size)
                if len(raw)!=size:raise ValueError()
                data=parse(raw.decode())
                if self.path in ('/api/live/tasks','/api/tasks','/api/acquisitions') and (live.active or store.active or acquisitions.active):
                    # 重复提交当前相同任务可返回原记录，其余任务互斥。
                    entries=live.tasks.values() if self.path=='/api/live/tasks' else store.tasks.values() if self.path=='/api/tasks' else acquisitions.runs.values()
                    if not any(t.get('request_id')==data.get('request_id') for t in entries):
                        raise RuntimeError('busy')
                if self.path=='/api/live/tasks':
                    self.send(202,live.start(data));return
                if self.path=='/api/live/quota':
                    if live.active: raise RuntimeError('busy')
                    self.send(200,live.observe_quota(data));return
                retry_match=re.fullmatch(r'/api/live/tasks/([a-f0-9]{32})/retry-citations',self.path)
                if retry_match and set(data)=={'request_id'}:
                    if store.active or acquisitions.active: raise RuntimeError('busy')
                    self.send(202,live.retry_citations(retry_match[1],data['request_id']));return
                review_match=re.fullmatch(r'/api/live/tasks/([a-f0-9]{32})/continue-review',self.path)
                if review_match and set(data)=={'request_id'}:
                    if store.active or acquisitions.active: raise RuntimeError('busy')
                    self.send(202,live.continue_review(review_match[1],data['request_id']));return
                recovery_match=re.fullmatch(r'/api/live/tasks/([a-f0-9]{32})/retry-connection',self.path)
                if recovery_match and set(data)=={'request_id'}:
                    if store.active or acquisitions.active: raise RuntimeError('busy')
                    self.send(202,live.retry_connection(recovery_match[1],data['request_id']));return
                recheck_match=re.fullmatch(r'/api/live/tasks/([a-f0-9]{32})/retry-review',self.path)
                if recheck_match and set(data)=={'request_id'}:
                    if store.active or acquisitions.active: raise RuntimeError('busy')
                    self.send(202,live.retry_review(recheck_match[1],data['request_id']));return
                live_action=re.fullmatch(r'/api/live/tasks/([a-f0-9]{32})/(cancel|resume)',self.path)
                if live_action and data=={}:
                    if live_action[2]=='resume' and (store.active or acquisitions.active): raise RuntimeError('busy')
                    self.send(200,live.cancel(live_action[1]) if live_action[2]=='cancel' else live.resume(live_action[1]));return
                if self.path=='/api/tasks' and set(data)=={'scenario_id','request_id'}:
                    self.send(202,store.start(data['scenario_id'],data['request_id']));return
                if self.path=='/api/preflight' and set(data)=={'scenario_id'}:
                    scenario=store.scenario(data['scenario_id'])
                    case=prepare_case(scenario['case'],scenario['run_date'])
                    result=preflight(case)
                    result['live_execution_enabled']=False
                    result['notice']='此页面仅预检，不读取密钥或调用模型。自由提问与实时检索尚未接入。'
                    self.send(200,result);return
                if self.path=='/api/plan-preview' and set(data)=={'question','region','period','sources'}:
                    self.send(200,plan_preview(data));return
                if self.path=='/api/acquisitions' and set(data)=={'question','region','period','sources','request_id'}:
                    payload={key:data[key] for key in ('question','region','period','sources')}
                    self.send(202,acquisitions.start(payload,data['request_id']));return
                prepare_match=re.fullmatch(r'/api/acquisitions/([a-f0-9]{32})/prepare',self.path)
                if prepare_match and data=={}:
                    self.send(200,acquisitions.evidence(prepare_match[1],create=True));return
                handoff_match=re.fullmatch(r'/api/acquisitions/([a-f0-9]{32})/handoff-preview',self.path)
                if handoff_match and data=={}:
                    self.send(200,acquisitions.handoff(handoff_match[1]));return
                self.send(404,{'error':'此操作尚未开放'})
            except RuntimeError:self.send(409,{'error':'已有任务正在运行，请稍后再试'})
            except (ValueError,KeyError,TypeError,UnicodeError):self.send(400,{'error':'操作参数不正确'})
            except OSError:self.send(500,{'error':'无法保存本机任务'})

    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    server.host=f'127.0.0.1:{server.server_port}'
    server.origin='http://'+server.host
    server.entry='/app/'+secrets.token_urlsafe(24)
    server.session=secrets.token_urlsafe(32)
    server.csrf=secrets.token_urlsafe(32)
    server.store=store
    server.live=live
    return server


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=0)
    args=parser.parse_args()
    with make_server(port=args.port) as server:
        print('资料研究工作台：'+server.origin+server.entry,flush=True)
        print('仅本机监听；新版网页自动研究按免费额度与预算执行，小红书未接入。',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
