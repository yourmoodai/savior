import {connect} from 'node:net';

export async function exchange_at(endpoint,request,timeout,connect_socket=connect) {
  const expires=Date.now()+timeout;
  while(true) {
    try {return await exchange_once(endpoint,request,Math.max(1,expires-Date.now()),connect_socket);}
    catch(error) {
      if(!error.retry_connect || Date.now()>=expires) throw error;
      await new Promise(resolve=>setTimeout(resolve,Math.min(25,expires-Date.now())));
    }
  }
}

function exchange_once(endpoint,request,timeout,connect_socket) {
  return new Promise((done,reject)=>{
    const socket=connect_socket(endpoint);
    let connected=false,settled=false;
    let size=0; const chunks=[];
    const deadline=setTimeout(()=>finish(Error('review_timeout')),timeout);
    function finish(error,value) {
      if(settled)return;
      settled=true;clearTimeout(deadline);socket.destroy();
      if(error)reject(error);else done(value);
    }
    function decode() {
      try {
        const response=JSON.parse(Buffer.concat(chunks).toString('utf8'));
        if(response.version===1 && response.status==='error' && response.code==='review_response_too_large') {
          finish(Error('review_response_too_large'));return;
        }
        if(response.version!==1 || response.status!=='ok')throw Error('invalid_review_response');
        finish(null,response.value);
      } catch {finish(Error('review_request_failed'));}
    }
    socket.on('close',()=>finish(Error('review_closed')));
    socket.on('error',error=>{
      const failure=Error('review_unavailable');
      // Retry only a failed pipe connection: a submitted mutation must never replay.
      failure.retry_connect=!connected && typeof endpoint==='string' && endpoint.startsWith('\\\\.\\pipe\\') && ['EBUSY','ENOENT','ECONNREFUSED'].includes(error.code);
      finish(failure);
    });
    socket.on('connect',()=>{connected=true;socket.write(JSON.stringify(request)+'\n');});
    socket.on('data',chunk=>{
      if(settled)return;
      size+=chunk.length;
      if(size>16*1024*1024){finish(Error('review_response_too_large'));return;} // 16 MiB
      chunks.push(chunk);
      // The response newline completes the exchange independently of Windows
      // pipe disconnect/reconnect notifications and peer handle lifetime.
      if(chunk.includes(10))decode();
    });
    socket.on('end',decode);
  });
}
