"""Stateful HTTP MCP Tasks fixture with real asynchronous file side effects.

This is development evidence only, never a vendor application or production
task service. The original Gateway and model driver remain unmodified.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
import time


def start(out):
    objects=out/'remote-objects';objects.mkdir()
    sessions=set();jobs={};results={};cancellations={};events=[];lock=threading.RLock()
    def save():
        (out/'mcp-events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
        (out/'remote-tasks.json').write_text(json.dumps(jobs,ensure_ascii=False,indent=2))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def answer(self,value,status=200,sid=None):
            raw=json.dumps(value).encode();self.send_response(status)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
            if sid:self.send_header('Mcp-Session-Id',sid)
            self.end_headers();self.wfile.write(raw)
        def do_DELETE(self):
            with lock:sessions.discard(self.headers.get('Mcp-Session-Id'))
            self.answer({})
        def do_POST(self):
            call=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            method=call['method'];params=call.get('params',{});sid=self.headers.get('Mcp-Session-Id')
            with lock:
                events.append({'call':call,'session':sid});save()
                if method=='initialize':
                    sid=secrets.token_hex(16);sessions.add(sid)
                    result={'protocolVersion':'2025-11-25','capabilities':{'tools':{},'tasks':{'list':{},'cancel':{},'requests':{'tools':{'call':{}}}}}}
                elif sid not in sessions:self.answer({},404);return
                elif 'id' not in call:self.answer({},202);return
                elif method=='tools/list':
                    result={'tools':[
                        {'name':'create_later','description':'Create one real record asynchronously. delay_seconds may be 1..300; use 300 for a cancellation trial.',
                         'execution':{'taskSupport':'required'},'inputSchema':{'type':'object','properties':{'label':{'type':'string'},'value':{'type':'integer'},'delay_seconds':{'type':'integer','minimum':1,'maximum':300}},'required':['label','value','delay_seconds'],'additionalProperties':False}},
                        {'name':'read_record','description':'Reopen the actual record by ID after a completed task result.',
                         'inputSchema':{'type':'object','properties':{'id':{'type':'string'}},'required':['id'],'additionalProperties':False}}]}
                elif method=='tools/call' and params['name']=='create_later':
                    if 'task' not in params:raise ValueError('task request required')
                    args=params['arguments'];oid=secrets.token_hex(8);job_id=secrets.token_hex(16)
                    now=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
                    jobs[job_id]={'taskId':job_id,'status':'working','createdAt':now,'lastUpdatedAt':now,'ttl':600000,'pollInterval':1000}
                    cancelled=cancellations[job_id]=threading.Event()
                    def work():
                        if cancelled.wait(args['delay_seconds']):return
                        with lock:
                            if jobs[job_id]['status']=='cancelled':return
                            value={'id':oid,'label':args['label'],'value':args['value'],'version':1}
                            (objects/(oid+'.json')).write_text(json.dumps(value,ensure_ascii=False))
                            results[job_id]={'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False)}],
                                '_meta':{'io.modelcontextprotocol/related-task':{'taskId':job_id}}}
                            jobs[job_id].update(status='completed',lastUpdatedAt=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()));save()
                    threading.Thread(target=work,daemon=True).start()
                    result={'task':dict(jobs[job_id])};save()
                elif method=='tools/call' and params['name']=='read_record':
                    oid=params['arguments']['id']
                    if len(oid)!=16 or any(c not in '0123456789abcdef' for c in oid):raise ValueError('id')
                    result={'content':[{'type':'text','text':(objects/(oid+'.json')).read_text()}]}
                elif method=='tasks/list':result={'tasks':list(jobs.values())}
                elif method=='tasks/get':result=jobs[params['taskId']]
                elif method=='tasks/result':result=results[params['taskId']]
                elif method=='tasks/cancel':
                    job_id=params['taskId'];result=jobs[job_id]
                    if result['status']=='working':
                        result.update(status='cancelled',lastUpdatedAt=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
                        cancellations[job_id].set();save()
                else:raise ValueError('method')
                self.answer({'jsonrpc':'2.0','id':call['id'],'result':result},sid=sid)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server
