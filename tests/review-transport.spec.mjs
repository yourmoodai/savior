import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:net';
import {exchange_at} from '../bridge/review-transport.mjs';
import {EventEmitter} from 'node:events';

test('pipe connection retry is bounded and never replays a submitted request',async()=>{
  for(const after_connect of [false,true]) {
    let attempts=0,writes=0;
    const connector=()=>{
      const socket=new EventEmitter();
      socket.write=()=>{writes++;};
      socket.destroy=()=>socket.emit('close');
      const attempt=++attempts;
      queueMicrotask(()=>{
        if(attempt===1) {
          if(after_connect)socket.emit('connect');
          socket.emit('error',Object.assign(Error('busy'),{code:'EBUSY'}));socket.emit('close');
        } else {
          socket.emit('connect');
          socket.emit('data',Buffer.from('{"version":1,"status":"ok","value":42}'));
          socket.emit('end');socket.emit('close');
        }
      });
      return socket;
    };
    const result=exchange_at('\\\\.\\pipe\\synthetic',{command:'consent'},200,connector);
    if(after_connect) {await assert.rejects(result,/review_unavailable/);assert.equal(attempts,1);}
    else {assert.equal(await result,42);assert.equal(attempts,2);}
    assert.equal(writes,1);
  }
  let attempts=0;
  const start=Date.now();
  await assert.rejects(exchange_at('\\\\.\\pipe\\synthetic',{},60,()=>{
    attempts++;const socket=new EventEmitter();socket.destroy=()=>socket.emit('close');
    queueMicrotask(()=>{socket.emit('error',Object.assign(Error('busy'),{code:'EBUSY'}));socket.emit('close');});
    return socket;
  }));
  assert.ok(attempts>1 && attempts<10);
  assert.ok(Date.now()-start<1000);
});

async function with_server(handle,check) {
  const sockets=new Set();
  const server=createServer(socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket));socket.on('error',()=>{});handle(socket);});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try {await check({host:'127.0.0.1',port:server.address().port});}
  finally {for(const socket of sockets)socket.destroy();await new Promise(resolve=>server.close(resolve));}
}
test('review transport rejects missing and truncated replies',async()=>{
  for(const body of ['', '{"version":1'])await with_server(socket=>socket.once('data',()=>socket.end(body)),async endpoint=>{
    await assert.rejects(exchange_at(endpoint,{command:'status'},500),/review_request_failed|review_closed/);
  });
});
test('review deadline bounds silent and trickling peers',async()=>{
  for(const trickle of [false,true])await with_server(socket=>{
    socket.on('data',()=>{});
    if(trickle){const timer=setInterval(()=>socket.write(' '),5);socket.on('close',()=>clearInterval(timer));}
  },async endpoint=>{
    const start=Date.now();
    await assert.rejects(exchange_at(endpoint,{command:'status'},50));
    assert.ok(Date.now()-start<1000);
  });
});
test('review transport accepts one complete protocol response',async()=>{
  await with_server(socket=>socket.once('data',()=>socket.end(JSON.stringify({version:1,status:'ok',value:{ready:true}}))),async endpoint=>{
    assert.deepEqual(await exchange_at(endpoint,{command:'status'},500),{ready:true});
  });
});
test('a framed reply completes without waiting for the peer to disconnect',async()=>{
  await with_server(socket=>socket.once('data',()=>{
    socket.write('{"version":1,"status":"ok","value":');
    setTimeout(()=>socket.write('42}\n'),10);
  }),async endpoint=>{
    assert.equal(await exchange_at(endpoint,{command:'status'},500),42);
  });
});
test('multiple replies in one frame are rejected',async()=>{
  await with_server(socket=>socket.once('data',()=>socket.write('{"version":1,"status":"ok"}\n{}\n')),async endpoint=>{
    await assert.rejects(exchange_at(endpoint,{command:'status'},500),/review_request_failed/);
  });
});
test('oversized response errors remain explicit and the next request succeeds',async()=>{
  let requests=0;
  await with_server(socket=>socket.once('data',()=>socket.end(JSON.stringify(++requests===1
    ? {version:1,status:'error',code:'review_response_too_large'}
    : {version:1,status:'ok',value:{ready:true}})+'\n')),async endpoint=>{
    await assert.rejects(exchange_at(endpoint,{command:'export'},500),/review_response_too_large/);
    assert.deepEqual(await exchange_at(endpoint,{command:'status'},500),{ready:true});
  });
});
