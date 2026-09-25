import test from 'node:test';
import assert from 'node:assert/strict';
import {createState} from '../app/frontend-v2/renderer/core/state.mjs';
import {createActions} from '../app/frontend-v2/renderer/core/actions.mjs';

const storage=new Map();
globalThis.localStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
globalThis.window=Object.assign(new EventTarget(),{setTimeout,clearTimeout});
globalThis.CustomEvent=class extends Event {constructor(type,options={}){super(type);this.detail=options.detail;}};

test('approval and withdrawal bind the exact result and persist UI state',async()=>{
 const state=createState();
 const item=state.addMessage('assistant','真实执行的结果',false,{meta:{gatewayRequestId:'req_'+'a'.repeat(64)}});
 const calls=[];
 const runtime={compositionFeedback:async payload=>{calls.push(payload);return payload.mode==='inspect'?{ok:true,result_version:'b'.repeat(64)}:
  {ok:true,saved:true,status:payload.mode==='accept'?'accepted':'withdrawn',experience_id:'cex_'+'c'.repeat(64)};}};
 const actions=createActions({state,runtime});
 await actions.rememberComposition(item);
 assert.equal(calls[1].request_id,item.meta.gatewayRequestId);
 assert.equal(calls[1].result_version,'b'.repeat(64));
 assert.equal(state.snapshot().messages.find(m=>m.id===item.id).meta.compositionRemembered,true);
 const reopened=createState();
 assert.equal(reopened.snapshot().messages.find(m=>m.id===item.id).meta.gatewayRequestId,item.meta.gatewayRequestId);
 assert.equal(reopened.snapshot().messages.find(m=>m.id===item.id).meta.compositionRemembered,true);
 await actions.rememberComposition(state.snapshot().messages.find(m=>m.id===item.id),'withdraw');
 assert.equal(state.snapshot().messages.find(m=>m.id===item.id).meta.compositionRemembered,false);
});

test('explicit natural feedback saves the source task without executing it again',async()=>{
 storage.clear(); const state=createState();
 const id='req_'+'d'.repeat(64);
 state.addMessage('assistant','销售表已完成',false,{meta:{gatewayRequestId:id}});
 const calls=[];
 const runtime={send:async()=>{throw new Error('feedback must not execute a task');},compositionFeedback:async payload=>{
  calls.push(payload);return {ok:true,saved:true,feedback_only:true,status:'accepted',experience_id:'cex_test',message:'已记住这次做法'};
 }};
 await createActions({state,runtime}).sendMessage('我认可，记住这次做法，以后复用');
 assert.equal(calls[0].request_id,id);
 assert.equal(calls[0].mode,'interpret');
 assert.equal(state.snapshot().messages.at(-1).content,'已记住这次做法');
 assert.equal(state.snapshot().messages.at(-1).meta.origin,'composition_feedback');
 assert.equal(state.snapshot().busy,false);
});

test('switching conversations during approval cannot update the new conversation',async()=>{
 storage.clear(); const state=createState();
 const item=state.addMessage('assistant','原任务',false,{meta:{gatewayRequestId:'req_'+'e'.repeat(64)}});
 const sourceSession=state.snapshot().activeSessionId;
 const runtime={compositionFeedback:async payload=>{
  if(payload.mode==='inspect') return {ok:true,result_version:'f'.repeat(64)};
  state.startNewConversation({force:true});
  return {ok:true,saved:true,status:'accepted',experience_id:'cex_saved'};
 }};
 await createActions({state,runtime}).rememberComposition(item);
 assert.equal(state.snapshot().messages.length,0);
 state.switchConversation(sourceSession);
 assert.equal(state.snapshot().messages[0].meta.compositionRemembered,true);
});

test('feedback completion drains queued turns without duplicating user messages',async()=>{
 storage.clear(); const state=createState();
 state.addMessage('assistant','原任务',false,{meta:{gatewayRequestId:'req_'+'a'.repeat(64)}});
 let resolveFirst; let calls=0;
 const runtime={send:async()=>{throw new Error('feedback must not execute a task');},compositionFeedback:async()=>{
  calls++;
  if(calls===1) await new Promise(resolve=>{resolveFirst=resolve;});
  return {ok:true,saved:true,feedback_only:true,status:'accepted',message:'已记住'};
 }};
 const actions=createActions({state,runtime});
 const first=actions.sendMessage('认可，记住这个做法');
 await actions.sendMessage('以后复用这次做法');
 resolveFirst(); await first;
 for(let i=0;i<10&&(calls<2||state.snapshot().busy);i++) await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls,2);
 assert.equal(state.snapshot().messages.filter(item=>item.role==='user'&&item.content==='以后复用这次做法').length,1);
 assert.equal(state.snapshot().busy,false);
});

test('a task finishing in a background conversation keeps its approval binding',async()=>{
 storage.clear(); const state=createState();
 const sourceSession=state.snapshot().activeSessionId;
 const requestId='req_'+'9'.repeat(64);
 const runtime={send:async()=>{
  state.startNewConversation({force:true});
  return {ok:true,phase:'completed',stdout:'统计文件已完成',gatewayRequestId:requestId};
 }};
 await createActions({state,runtime}).sendMessage('请生成销售汇总文件',[],{useStream:false});
 assert.equal(state.snapshot().messages.length,0);
 state.switchConversation(sourceSession);
 const result=state.snapshot().messages.find(item=>item.role==='assistant');
 assert.equal(result.content,'统计文件已完成');
 assert.equal(result.meta.gatewayRequestId,requestId);
 const reopened=createState();
 assert.equal(reopened.snapshot().messages.find(item=>item.id===result.id).meta.gatewayRequestId,requestId);
});
