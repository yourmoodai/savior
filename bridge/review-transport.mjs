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
    let connected=false;
    let size=0; const chunks=[];
    const deadline=setTimeout(()=>socket.destroy(Error('review_timeout')),timeout);
    socket.on('close',()=>{clearTimeout(deadline);reject(Error('review_closed'));});
    socket.on('error',error=>{
      const failure=Error('review_unavailable');
      // Retry only a failed pipe connection: a submitted mutation must never replay.
      failure.retry_connect=!connected && typeof endpoint==='string' && endpoint.startsWith('\\\\.\\pipe\\') && ['EBUSY','ENOENT','ECONNREFUSED'].includes(error.code);
      reject(failure);
    });
    socket.on('connect',()=>{connected=true;socket.write(JSON.stringify(request)+'\n');});
    socket.on('data',chunk=>{size+=chunk.length;if(size>16*1024*1024){socket.destroy(Error('review_response_too_large'));return;}chunks.push(chunk);}); // 16 MiB
    socket.on('end',()=>{try{const response=JSON.parse(Buffer.concat(chunks).toString('utf8'));if(response.version===1 && response.status==='error' && response.code==='review_response_too_large'){reject(Error('review_response_too_large'));return;}if(response.version!==1 || response.status!=='ok') throw Error('invalid_review_response');done(response.value);}catch{reject(Error('review_request_failed'));}});
  });
}
