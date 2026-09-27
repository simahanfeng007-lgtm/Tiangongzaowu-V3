"""Controlled local MCP service; never labelled as a vendor application."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading

def start(out):
    objects = out / 'remote-objects'
    objects.mkdir()
    events = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            call = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            events.append(call)
            (out/'mcp-events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
            if 'id' not in call:
                self.send_response(202);self.end_headers();return
            method = call['method']
            if method == 'initialize':
                result = {'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'controlled-ontology-fixture','version':'1'}}
            elif method == 'tools/list':
                second = bool(call.get('params',{}).get('cursor'))
                result = {'tools':([{'name':'read_record','description':'Reopen the actual record by its returned id.',
                    'inputSchema':{'type':'object','properties':{'id':{'type':'string'}},'required':['id'],'additionalProperties':False}}] if second else
                    [{'name':'create_record','description':'Create a record once, return id and version. Then discover next page for readback.',
                    'inputSchema':{'type':'object','properties':{'label':{'type':'string'},'value':{'type':'integer'}},'required':['label','value'],'additionalProperties':False}}])}
                if not second: result['nextCursor']='read-page'
            elif method == 'tools/call':
                params=call['params'];arguments=params['arguments']
                if params['name']=='create_record':
                    oid=secrets.token_hex(8);value={'id':oid,'version':1,'label':arguments['label'],'value':arguments['value']}
                    (objects/(oid+'.json')).write_text(json.dumps(value,ensure_ascii=False))
                elif params['name']=='read_record':
                    oid=arguments['id']
                    if len(oid)!=16 or any(c not in '0123456789abcdef' for c in oid): raise ValueError('invalid id')
                    value=json.loads((objects/(oid+'.json')).read_text())
                else: raise ValueError('unknown tool')
                result={'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False)}],'structuredContent':value}
            else: raise ValueError('unknown method')
            body=json.dumps({'jsonrpc':'2.0','id':call['id'],'result':result}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server
